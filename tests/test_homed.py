import json
import io
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path

from homed import config, registry, server, yamlloader
from homed.cli import main
from homed.doctor import inspect
from homed.drivers.docker import DockerDriver
from homed.model import Driver, Exposure, HealthState, Intent, ManagerState
from homed.health.last_success import check_last_success
from homed.sanitize import REDACTED, sanitize_registry


class FakeRunner:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.calls = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def run(self, args, timeout=10.0):
        self.calls.append(args)

        class Proc:
            pass

        proc = Proc()
        proc.returncode = self.returncode
        proc.stdout = self.stdout
        proc.stderr = self.stderr
        return proc


class RegistryTests(unittest.TestCase):
    def test_yaml_and_registry_parse_example_shape(self):
        raw = yamlloader.load(
            """
services:
  adguard:
    driver: docker
    intent: always
    exposure: lan
    options:
      container: adguardhome
    health:
      kind: tcp
      host: 127.0.0.1
      port: 3000
    tags: [network, dns]
"""
        )
        result = config.validate(raw)
        self.assertTrue(result.ok, result.issues)
        reg = registry.build_registry(raw)
        svc = reg.get("adguard")
        self.assertIsNotNone(svc)
        self.assertEqual(svc.driver, Driver.DOCKER)
        self.assertEqual(svc.intent, Intent.ALWAYS)
        self.assertEqual(svc.exposure, Exposure.LAN)
        self.assertEqual(svc.health.port, 3000)

    def test_validation_catches_unknown_dependency(self):
        raw = {"services": {"a": {"driver": "manual", "after": ["missing"]}}}
        result = config.validate(raw)
        self.assertFalse(result.ok)
        self.assertIn("unknown service", result.errors[0].message)


class DriverTests(unittest.TestCase):
    def test_docker_status_running(self):
        runner = FakeRunner(stdout="running\n")
        result = DockerDriver(runner).status(
            registry.build_service("adguard", {"driver": "docker", "options": {"container": "adguardhome"}})
        )
        self.assertEqual(result.state, ManagerState.RUNNING)
        self.assertEqual(runner.calls[0][:3], ["docker", "inspect", "-f"])


class HealthTests(unittest.TestCase):
    def test_last_success_degraded_when_old(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "last"
            path.write_text("ok")
            health = registry.build_health("job", {"kind": "last_success", "path": str(path), "max_age_seconds": 3600})
            self.assertEqual(check_last_success(health), HealthState.HEALTHY)


class DoctorTests(unittest.TestCase):
    def test_doctor_warns_always_without_health(self):
        reg = registry.build_registry({"services": {"svc": {"driver": "manual", "intent": "always"}}})
        issues = inspect(reg)
        self.assertEqual(issues[0].level, "warning")
        self.assertIn("no health", issues[0].message)


class CliTests(unittest.TestCase):
    def test_config_path_command(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "services.yaml"
            with redirect_stdout(io.StringIO()) as out:
                exit_code = main(["--config", str(path), "config", "path"])
            self.assertEqual(exit_code, 0)
            self.assertEqual(out.getvalue().strip(), str(path))

    def test_registry_dump_json(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "services.yaml"
            path.write_text("services:\n  svc:\n    driver: manual\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(["--config", str(path), "registry", "dump", "--json"]), 0)
            payload = json.loads(out.getvalue())
            self.assertIn("svc", payload["services"])


class SanitizeTests(unittest.TestCase):
    def test_redacts_secret_looking_option_keys(self):
        reg = registry.build_registry(
            {
                "services": {
                    "svc": {
                        "driver": "manual",
                        "options": {
                            "container": "app",
                            "api_token": "abc123",
                            "DB_PASSWORD": "hunter2",
                            "nested": {"auth_key": "zzz", "port": 25565},
                        },
                    }
                }
            }
        )
        clean = sanitize_registry(reg.to_dict())
        opts = clean["services"]["svc"]["options"]
        self.assertEqual(opts["container"], "app")  # benign value preserved
        self.assertEqual(opts["api_token"], REDACTED)
        self.assertEqual(opts["DB_PASSWORD"], REDACTED)
        self.assertEqual(opts["nested"]["auth_key"], REDACTED)
        self.assertEqual(opts["nested"]["port"], 25565)


class ServerPayloadTests(unittest.TestCase):
    def _registry(self):
        return registry.build_registry(
            {"services": {"svc": {"driver": "manual", "intent": "external"}}}
        )

    def test_status_payload_shape_matches_cli(self):
        payload = server.status_payload(self._registry())
        self.assertIn("services", payload)
        self.assertEqual(payload["services"][0]["name"], "svc")
        self.assertEqual(payload["services"][0]["manager_state"], "unknown")

    def test_doctor_payload_shape(self):
        payload = server.doctor_payload(self._registry())
        self.assertIn("ok", payload)
        self.assertIsInstance(payload["issues"], list)


class ServerRouteTests(unittest.TestCase):
    """End-to-end over loopback with manual services (no subprocess calls)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        path = Path(self._tmp.name) / "services.yaml"
        path.write_text(
            "services:\n  svc:\n    driver: manual\n    intent: external\n",
            encoding="utf-8",
        )
        self.httpd = server.make_server(host="127.0.0.1", port=0, config_path=path)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        self._tmp.cleanup()

    def _get(self, route):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{route}", timeout=5) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")

    def test_index_served(self):
        status, body, ctype = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"homed", body)

    def test_api_status_route(self):
        status, body, ctype = self._get("/api/status")
        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        self.assertEqual(json.loads(body)["services"][0]["name"], "svc")

    def test_api_registry_route(self):
        status, body, _ = self._get("/api/registry")
        self.assertEqual(status, 200)
        self.assertIn("svc", json.loads(body)["services"])

    def test_unknown_route_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/nope")
        self.assertEqual(ctx.exception.code, 404)

    def test_post_rejected(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/status", data=b"{}", method="POST"
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 405)


if __name__ == "__main__":
    unittest.main()
