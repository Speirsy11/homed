import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from homed import config, registry, yamlloader
from homed.cli import main
from homed.doctor import inspect
from homed.drivers.docker import DockerDriver
from homed.model import Driver, Exposure, HealthState, Intent, ManagerState
from homed.health.last_success import check_last_success


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


if __name__ == "__main__":
    unittest.main()
