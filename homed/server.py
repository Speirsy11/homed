"""Authenticated private dashboard; lifecycle actions remain in the CLI."""
from __future__ import annotations

import hmac
import ipaddress
import json
import ssl
import time
import threading
import webbrowser
from http import HTTPStatus
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, unquote

from . import __version__, config as config_mod, registry as registry_mod, doctor as doctor_mod
from .auth import AuthStore, AuthError
from .calendar_store import CalendarStore, CalendarError
from .observations import Observations
from .runtime import statuses_for
from .sanitize import sanitize_registry

WEB_DIR = Path(__file__).resolve().parent / 'web'
DEFAULT_HOST, DEFAULT_PORT = '127.0.0.1', 8765
_STATIC_ROUTES = {'/': ('index.html','text/html; charset=utf-8'),
                  '/index.html': ('index.html','text/html; charset=utf-8'),
                  '/style.css': ('style.css','text/css; charset=utf-8'),
                  '/app.js': ('app.js','text/javascript; charset=utf-8')}
for asset in ('fullcalendar.js','luxon.js','fullcalendar-luxon.js'):
    _STATIC_ROUTES['/vendor/'+asset] = ('vendor/'+asset,'text/javascript; charset=utf-8')


def status_payload(registry):
    return {'services': [s.to_dict() for s in statuses_for(registry)]}


def registry_payload(registry):
    return sanitize_registry(registry.to_dict())


def doctor_payload(registry):
    issues = [i.to_dict() for i in doctor_mod.inspect(registry)]
    return {'ok': not any(i['level']=='error' for i in issues), 'issues': issues}


def meta_payload(path, registry):
    return {'version': __version__, 'config_path': str(path), 'service_count': len(registry), 'generated_at':time.time()}


class Dashboard:
    def __init__(self, config_path, state_dir):
        self.auth = AuthStore(state_dir / 'auth.sqlite3')
        self.calendar = CalendarStore(state_dir / 'calendar.sqlite3')
        self.observations = Observations(config_path, state_dir / 'observations.sqlite3')


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, *args, max_connections=32, **kwargs):
        self._slots = threading.BoundedSemaphore(max_connections)
        self.tls_context = None
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            request.settimeout(10)
            if self.tls_context is not None:
                try:
                    request = self.tls_context.wrap_socket(request, server_side=True)
                except (ssl.SSLError, OSError):
                    self.shutdown_request(request)
                    return
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def server_close(self):
        if hasattr(self, 'app'):
            self.app.observations.stop()
        super().server_close()

    def verify_request(self, request, client_address):
        # No public inbound clients, even if a router forwards the listener.
        try:
            ip = ipaddress.ip_address(client_address[0])
            return any(ip in net for net in self.allowed_networks)
        except ValueError:
            return False


def make_handler(config_path=None):
    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = 'homed/' + __version__

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def do_GET(self):
            self._dispatch('GET')

        def do_POST(self):
            self._dispatch('POST')

        def do_PUT(self):
            self._dispatch('PUT')

        def do_DELETE(self):
            self._dispatch('DELETE')

        def _dispatch(self, method):
            try:
                host = self.headers.get('Host', '')
                if host not in self.server.allowed_hosts or len(self.headers.get_all('Host',[])) != 1:
                    raise AuthError('Unrecognized dashboard address', 403)
                supplied_origin = self.headers.get('Origin')
                if supplied_origin and supplied_origin not in self.server.origins:
                    raise AuthError('This request came from another site',403)
                parsed = urlsplit(self.path)
                route = parsed.path
                if method == 'GET' and route in _STATIC_ROUTES:
                    filename, content_type = _STATIC_ROUTES[route]
                    return self._send(200,(WEB_DIR/filename).read_bytes(),content_type)
                if not route.startswith('/api/'):
                    return self._json(404,{'error':'Not found'})
                session = self.server.app.auth.session(self._token())
                if method == 'GET' and route == '/api/session':
                    result = {'authenticated': bool(session), 'setup_required': not self.server.app.auth.has_accounts()}
                    if session:
                        result.update(session)
                    return self._json(200,result)
                if method == 'POST' and route == '/api/login':
                    self._check_write_origin()
                    body = self._body()
                    login = self.server.app.auth.login(body.get('username'),body.get('password'),self.client_address[0])
                    token = login.pop('token')
                    return self._json(200,{'authenticated':True,**login},{'Set-Cookie':self._cookie(token)})
                if not session:
                    raise AuthError('Sign in to view your dashboard',401)
                if method != 'GET':
                    self._check_write_origin()
                    if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),session['csrf_token']):
                        raise AuthError('Your session changed. Reload before saving.',403)
                owner = session['user']['username']
                calendar = self.server.app.calendar
                if method == 'POST' and route == '/api/logout':
                    self.server.app.auth.logout(self._token())
                    return self._json(200,{'authenticated':False},{'Set-Cookie':self._cookie('',clear=True)})
                if method == 'GET' and route in ('/api/dashboard','/api/status','/api/doctor','/api/meta','/api/registry'):
                    snapshot = self.server.app.observations.snapshot()
                    # Historical endpoints remain authenticated and use the same
                    # cached observation, never a second request-driven collector.
                    if route == '/api/status':
                        snapshot = {k:snapshot[k] for k in ('services','observed_at','error','stale')}
                    elif route == '/api/doctor':
                        snapshot = {'issues':snapshot['issues'],'ok':not snapshot['stale'] and not any(i['level']=='error' for i in snapshot['issues'])}
                    elif route == '/api/meta':
                        snapshot = {'version':__version__, 'service_count':len(snapshot['services']), 'observed_at':snapshot['observed_at']}
                    elif route == '/api/registry':
                        snapshot = {'services':{s['name']:s for s in snapshot['services']}}
                    return self._json(200,snapshot)
                if method == 'GET' and route.startswith('/api/services/') and route.endswith('/logs'):
                    name = unquote(route[len('/api/services/'):-len('/logs')])
                    try:
                        payload = self.server.app.observations.logs(name)
                    except (OSError,ValueError,config_mod.ConfigError):
                        return self._json(404,{'error':'The registered log is unavailable'})
                    return self._json(200,payload)
                if route == '/api/events':
                    if method == 'GET':
                        query = parse_qs(parsed.query)
                        return self._json(200,calendar.list_events(owner,query.get('start',[''])[0],query.get('end',[''])[0]))
                    if method == 'POST':
                        return self._json(201,calendar.create_event(owner,self._body()))
                if route.startswith('/api/events/'):
                    event_id = unquote(route[len('/api/events/'):])
                    if method == 'GET':
                        return self._json(200,calendar.get_event(owner,event_id))
                    if method == 'PUT':
                        body = self._body()
                        revision = body.pop('revision',None)
                        return self._json(200,calendar.update_event(owner,event_id,body,revision))
                    if method == 'DELETE':
                        body = self._body()
                        calendar.delete_event(owner,event_id,body.get('revision'))
                        return self._json(200,{'deleted':True})
                if route == '/api/calendar/export' and method == 'GET':
                    return self._json(200,calendar.export_events(owner),{'Content-Disposition':'attachment; filename="homed-calendar.json"'})
                if route == '/api/calendar/restore' and method == 'POST':
                    result = calendar.restore_events(owner,self._body())
                    return self._json(200,result if isinstance(result,dict) else {'restored':result})
                return self._json(405 if method!='GET' else 404,{'error':'This operation is not available'})
            except (AuthError,CalendarError) as exc:
                self._json(exc.status,{'error':str(exc)})
            except (ValueError,TypeError,UnicodeDecodeError,json.JSONDecodeError):
                self._json(400,{'error':'Invalid request. Check the supplied fields.'})
            except (BrokenPipeError,ConnectionResetError):
                pass
            except Exception:
                # Never return raw database, file-path, command or calendar errors.
                self._json(503,{'error':'The request could not be completed. Your saved data is retained.'})

        def _check_write_origin(self):
            if self.headers.get('Origin') not in self.server.origins:
                raise AuthError('A matching dashboard origin is required',403)
            if self.headers.get('Sec-Fetch-Site') not in (None,'same-origin','none'):
                raise AuthError('Cross-site writes are not allowed',403)

        def _body(self):
            if self.headers.get('Content-Type','').split(';')[0] != 'application/json':
                raise AuthError('Send JSON data',415)
            lengths = self.headers.get_all('Content-Length',[])
            if self.headers.get('Transfer-Encoding') or len(lengths)!=1:
                raise AuthError('A single content length is required',400)
            try:
                length = int(lengths[0])
            except ValueError:
                raise AuthError('Invalid request length',400) from None
            limit = 16*1024*1024 if self.path == '/api/calendar/restore' else 1024*1024
            if not 0 < length <= limit:
                raise AuthError('The request is too large or empty',413)
            raw = self.rfile.read(length)
            if len(raw)!=length:
                raise AuthError('Incomplete request',400)
            data = json.loads(raw)
            if not isinstance(data,dict):
                raise AuthError('Expected a JSON object',400)
            return data

        def _token(self):
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get('Cookie',''))
                return cookie['homed_session'].value if 'homed_session' in cookie else ''
            except CookieError:
                return ''

        def _cookie(self,token,clear=False):
            return ('homed_session='+token+'; Path=/; HttpOnly; SameSite=Strict; Max-Age='+('0' if clear else '604800')+
                    ('; Secure' if self.server.secure else ''))

        def _json(self,status,payload,headers=None):
            self._send(status,json.dumps(payload).encode(),'application/json; charset=utf-8',headers)

        def _send(self,status,body,content_type,headers=None):
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            self.send_header('Permissions-Policy','camera=(), microphone=(), geolocation=()')
            for key,value in (headers or {}).items():
                self.send_header(key,value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self,*args):
            pass
    return DashboardHandler


def make_server(host=DEFAULT_HOST,port=DEFAULT_PORT,config_path=None,state_dir=None,
                origins=None,certfile=None,keyfile=None,collect=True,max_connections=32):
    config_path = Path(config_path or config_mod.config_path())
    state_dir = Path(state_dir or config_path.parent/'dashboard')
    secure = bool(certfile and keyfile)
    loopback = host in ('127.0.0.1','localhost','::1')
    if bool(certfile) != bool(keyfile):
        raise ValueError('Both TLS certificate and key are required')
    if not loopback and (not secure or not origins):
        raise ValueError('LAN/Tailscale hosting requires TLS and explicit --origin addresses')
    httpd = DashboardServer((host,port),make_handler(config_path),max_connections=max_connections)
    try:
        httpd.secure = secure
        scheme = 'https' if secure else 'http'
        httpd.origins = set(origins or [f'{scheme}://127.0.0.1:{httpd.server_address[1]}',f'{scheme}://localhost:{httpd.server_address[1]}'])
        for origin in httpd.origins:
            parsed = urlsplit(origin)
            if parsed.scheme != scheme or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
                raise ValueError('Origins must exactly match the serving scheme and host, without a path')
        httpd.allowed_hosts = {urlsplit(origin).netloc for origin in httpd.origins}
        httpd.allowed_networks = [ipaddress.ip_network(n) for n in ('127.0.0.0/8','10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','100.64.0.0/10','::1/128','fd7a:115c:a1e0::/48')]
        if secure:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(str(certfile),str(keyfile))
            # Handshakes run in bounded request workers, so a client sending no
            # TLS handshake cannot stall the accept loop for all other users.
            httpd.tls_context = context
        httpd.app = Dashboard(config_path,state_dir)
        if collect:
            httpd.app.observations.start()
        return httpd
    except Exception:
        httpd.server_close()
        raise


def serve(host=DEFAULT_HOST,port=DEFAULT_PORT,config_path=None,open_browser=False,
          state_dir=None,origins=None,certfile=None,keyfile=None):
    httpd = make_server(host,port,config_path,state_dir,origins,certfile,keyfile)
    url = sorted(httpd.origins)[0]
    print(f'homed private dashboard on {url}')
    if not httpd.app.auth.has_accounts():
        print('Create a local account with: homed account create USERNAME')
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
