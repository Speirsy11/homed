"""launchd driver for user/root agents and daemons."""

from __future__ import annotations

import subprocess

from ..model import ManagerState, Service
from .base import BaseDriver, DriverResult, command_detail


class LaunchdDriver(BaseDriver):
    def _label(self, service: Service) -> str:
        return str(service.options.get("label") or service.name)

    def status(self, service: Service) -> DriverResult:
        try:
            proc = self.runner.run(["launchctl", "print", f"gui/{_uid()}/{self._label(service)}"])
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return DriverResult(ManagerState.UNKNOWN, str(exc))
        if proc.returncode != 0:
            return DriverResult(ManagerState.NOT_FOUND, command_detail(proc))
        text = proc.stdout or ""
        if "state = running" in text or "pid =" in text:
            return DriverResult(ManagerState.RUNNING, "launchd reports running")
        return DriverResult(ManagerState.STOPPED, "launchd job exists but is not running")

    def up(self, service: Service) -> DriverResult:
        proc = self.runner.run(["launchctl", "kickstart", "-k", f"gui/{_uid()}/{self._label(service)}"])
        if proc.returncode == 0:
            return DriverResult(ManagerState.RUNNING, "kickstart requested")
        return DriverResult(ManagerState.ERROR, command_detail(proc))

    def down(self, service: Service) -> DriverResult:
        proc = self.runner.run(["launchctl", "kill", "TERM", f"gui/{_uid()}/{self._label(service)}"])
        if proc.returncode == 0:
            return DriverResult(ManagerState.STOPPED, "TERM requested")
        return DriverResult(ManagerState.ERROR, command_detail(proc))


def _uid() -> int:
    import os

    return os.getuid()
