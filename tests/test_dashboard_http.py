import json
import http.client
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from homed import server
from homed.cli import build_parser


class DashboardHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Path(self.tmp.name) / 'services.yaml'
        self.config.write_text('services:\n  optional:\n    driver: manual\n    intent: manual\n')
        self.httpd = server.make_server(port=0, config_path=self.config, collect=False)
        self.httpd.app.auth.create_account('charlie', 'a long test passphrase')
        self.origin = 'http://127.0.0.1:' + str(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = None
        self.csrf = None

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, route, method='GET', data=None, origin=None, csrf=True):
        headers = {'Origin': origin or self.origin}
        if self.cookie:
            headers['Cookie'] = self.cookie
        if self.csrf and csrf:
            headers['X-CSRF-Token'] = self.csrf
        if data is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(self.origin + route, method=method, headers=headers,
                                     data=json.dumps(data).encode() if data is not None else None)
        try:
            response = urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = json.loads(response.read())
            return response.status, body, response.headers

    def login(self):
        status, body, headers = self.request('/api/login', 'POST', {'username':'charlie','password':'a long test passphrase'})
        self.assertEqual(status, 200)
        self.cookie = headers['Set-Cookie'].split(';')[0]
        self.csrf = body['csrf_token']
        return headers

    def test_private_data_requires_login_and_cookie_is_http_only(self):
        self.assertEqual(self.request('/api/dashboard')[0], 401)
        self.assertEqual(self.request('/api/events?start=2026-09-01&end=2026-10-01')[0], 401)
        headers = self.login()
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        self.assertEqual(self.request('/api/dashboard')[0], 200)

    def test_calendar_write_rejects_other_origin_and_missing_csrf(self):
        self.login()
        event = {'title':'Dinner', 'start':'2026-09-09T18:00', 'end':'2026-09-09T19:00', 'timezone':'Europe/London','all_day':False,'category':'personal'}
        self.assertEqual(self.request('/api/events','POST',event,origin='https://unrelated.example')[0],403)
        self.assertEqual(self.request('/api/events','POST',event,csrf=False)[0],403)
        status, created, _ = self.request('/api/events','POST',event)
        self.assertEqual(status,201)
        self.assertEqual(self.request('/api/events/'+created['id'])[1]['title'],'Dinner')
        self.assertEqual(self.request('/api/logout','POST',{})[0],200)
        self.assertEqual(self.request('/api/events/'+created['id'])[0],401)


if __name__ == '__main__':
    unittest.main()


class DashboardSecurityTests(DashboardHTTPTests):
    def test_unrecognized_host_and_unauthenticated_legacy_routes_are_rejected(self):
        for route in ('/api/status','/api/registry','/api/doctor','/api/meta'):
            self.assertEqual(self.request(route)[0],401)
        req=urllib.request.Request(self.origin+'/api/session',headers={'Host':'attacker.example'})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req,timeout=5)
        self.assertEqual(error.exception.code,403)
        error.exception.close()

    def test_calendar_belongs_to_signed_in_account_and_rejects_revision_conflict(self):
        self.login()
        event={'title':'Private date','start':'2026-09-09','end':'2026-09-10','all_day':True,'category':'occasion'}
        status,created,_=self.request('/api/events','POST',event)
        self.assertEqual(status,201)
        changed={**event,'title':'Updated date','revision':created['revision']}
        self.assertEqual(self.request('/api/events/'+created['id'],'PUT',changed)[0],200)
        self.assertEqual(self.request('/api/events/'+created['id'],'PUT',changed)[0],409)
        self.httpd.app.auth.create_account('other','another long test passphrase')
        self.cookie=None;self.csrf=None
        status,body,headers=self.request('/api/login','POST',{'username':'other','password':'another long test passphrase'})
        self.assertEqual(status,200)
        self.cookie=headers['Set-Cookie'].split(';')[0];self.csrf=body['csrf_token']
        self.assertEqual(self.request('/api/events/'+created['id'])[0],404)
        self.assertEqual(self.request('/api/calendar/export')[1]['events'],[])
        self.assertEqual(self.request('/api/events/'+created['id'],'DELETE',{'revision':2})[0],404)

    def test_lifecycle_commands_are_not_web_operations(self):
        self.login()
        self.assertEqual(self.request('/api/services/optional/restart','POST',{})[0],405)


class NetworkPolicyTests(unittest.TestCase):
    def test_external_listener_requires_tls_and_explicit_origins_before_binding(self):
        with self.assertRaisesRegex(ValueError,'requires TLS'):
            server.make_server(host='0.0.0.0',port=0)


class ProxyOriginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Path(self.tmp.name) / 'services.yaml'
        self.config.write_text('services:\n  fixture:\n    driver: manual\n')
        self.proxy_origin = 'https://dashboard.example:8443'
        self.httpd = server.make_server(
            port=0, config_path=self.config, collect=False,
            proxy_origins=[self.proxy_origin],
        )
        self.httpd.app.auth.create_account('fixture', 'a valid fixture passphrase')
        self.port = self.httpd.server_address[1]
        self.local_origin = f'http://127.0.0.1:{self.port}'
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, method, path, host, origin=None, body=None, cookie=None, csrf=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        headers = {'Host': host}
        if origin is not None:
            headers['Origin'] = origin
        if body is not None:
            headers['Content-Type'] = 'application/json'
        if cookie is not None:
            headers['Cookie'] = cookie
        if csrf is not None:
            headers['X-CSRF-Token'] = csrf
        connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read())
        result = response.status, payload, response.headers
        connection.close()
        return result

    def test_local_http_and_exact_https_proxy_origin_both_authenticate(self):
        status, local_login, local_headers = self.request(
            'POST', '/api/login', f'127.0.0.1:{self.port}', self.local_origin,
            {'username': 'fixture', 'password': 'a valid fixture passphrase'},
        )
        self.assertEqual(status, 200)
        self.assertNotIn('; Secure', local_headers['Set-Cookie'])
        local_cookie = local_headers['Set-Cookie'].split(';')[0]
        self.assertTrue(self.request('GET', '/api/session', f'127.0.0.1:{self.port}', cookie=local_cookie)[1]['authenticated'])

        status, proxy_login, proxy_headers = self.request(
            'POST', '/api/login', 'dashboard.example:8443', self.proxy_origin,
            {'username': 'fixture', 'password': 'a valid fixture passphrase'},
        )
        self.assertEqual(status, 200)
        self.assertIn('; Secure', proxy_headers['Set-Cookie'])
        proxy_cookie = proxy_headers['Set-Cookie'].split(';')[0]
        csrf = proxy_login['csrf_token']
        self.assertTrue(self.request('GET', '/api/session', 'dashboard.example:8443', cookie=proxy_cookie)[1]['authenticated'])
        event = {'title':'Proxy date','start':'2026-09-09','end':'2026-09-10','all_day':True,'category':'occasion'}
        self.assertEqual(
            self.request('POST', '/api/events', 'dashboard.example:8443', self.proxy_origin, event, proxy_cookie)[0],
            403,
        )
        self.assertEqual(
            self.request('POST', '/api/events', 'dashboard.example:8443', self.proxy_origin, event, proxy_cookie, csrf)[0],
            201,
        )
        self.assertEqual(self.request('GET', '/api/dashboard', 'dashboard.example:8443', cookie=proxy_cookie)[0], 200)
        self.assertEqual(self.request('GET', '/api/session', 'unconfigured.example:8443', cookie=proxy_cookie)[0], 403)

    def test_proxy_origin_cli_and_validation_are_explicit(self):
        args = build_parser().parse_args(['serve', '--proxy-origin', 'https://dashboard.example:8443'])
        self.assertEqual(args.proxy_origins, ['https://dashboard.example:8443'])
        with self.assertRaisesRegex(ValueError, 'loopback'):
            server.make_server(
                host='0.0.0.0', port=0, config_path=self.config, collect=False,
                certfile=Path(self.tmp.name)/'cert', keyfile=Path(self.tmp.name)/'key',
                origins=['https://native.example:8765'], proxy_origins=['https://dashboard.example'],
            )
        for origin in (
            'http://dashboard.example:8443', 'https://user@dashboard.example:8443',
            'https://@dashboard.example:8443', 'https://:secret@dashboard.example:8443',
            'https://dashboard.example:8443/', 'https://dashboard.example:',
            'https://dashboard.example:0', 'https://dashboard.example:70000', None,
        ):
            with self.subTest(origin=origin):
                with self.assertRaises(ValueError):
                    server.make_server(port=0, config_path=self.config, collect=False, proxy_origins=[origin])
        allowed = server.make_server(
            port=0, config_path=self.config, collect=False,
            proxy_origins=['https://dashboard.example'],
        )
        allowed.server_close()


class TLSAndConcurrencyTests(unittest.TestCase):
    def test_trusted_tls_sets_secure_cookie_and_slow_handshake_does_not_block_login(self):
        import shutil,socket,ssl,subprocess,http.client
        if not shutil.which('openssl'):
            self.skipTest('openssl is required to create an isolated test certificate')
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            cert,key=base/'cert.pem',base/'key.pem'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            config=base/'services.yaml';config.write_text('services:\n  fixture:\n    driver: manual\n')
            httpd=server.make_server(port=0,config_path=config,certfile=cert,keyfile=key,collect=False,max_connections=2)
            httpd.app.auth.create_account('fixture','a valid fixture passphrase')
            port=httpd.server_address[1];origin='https://localhost:'+str(port)
            thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
            held=socket.create_connection(('127.0.0.1',port),timeout=5)
            try:
                connection=http.client.HTTPSConnection('localhost',port,context=ssl.create_default_context(cafile=str(cert)),timeout=5)
                connection.request('POST','/api/login',body=json.dumps({'username':'fixture','password':'a valid fixture passphrase'}),headers={'Origin':origin,'Content-Type':'application/json'})
                response=connection.getresponse()
                self.assertEqual(response.status,200)
                self.assertIn('; Secure',response.getheader('Set-Cookie'))
                response.read();connection.close()
            finally:
                held.close();httpd.shutdown();httpd.server_close();thread.join()

    def test_excess_slow_connections_are_closed_instead_of_creating_more_workers(self):
        import socket
        with tempfile.TemporaryDirectory() as td:
            config=Path(td)/'services.yaml';config.write_text('services:\n  fixture:\n    driver: manual\n')
            httpd=server.make_server(port=0,config_path=config,collect=False,max_connections=1)
            port=httpd.server_address[1]
            thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
            first=socket.create_connection(('127.0.0.1',port),timeout=5)
            second=None
            try:
                first.sendall(b'GET /api/session HTTP/1.1\r\n')
                second=socket.create_connection(('127.0.0.1',port),timeout=5)
                second.sendall(('GET /api/session HTTP/1.1\r\nHost: 127.0.0.1:'+str(port)+'\r\n\r\n').encode())
                try:
                    body=second.recv(100)
                except ConnectionResetError:
                    body=b''
                self.assertEqual(body,b'')
            finally:
                first.close()
                if second: second.close()
                httpd.shutdown();httpd.server_close();thread.join()
