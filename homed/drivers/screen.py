"""screen-session driver."""

from __future__ import annotations

import shlex

from ..model import ManagerState, Service
from .base import BaseDriver, DriverResult, command_detail


class ScreenDriver(BaseDriver):
    def _session(self, service: Service) -> str:
        return str(service.options.get("session") or service.name)

    def status(self, service: Service) -> DriverResult:
        proc = self.runner.run(["screen", "-ls", self._session(service)])
        output = (proc.stdout or proc.stderr or "").strip()
        if self._session(service) in output:
            return DriverResult(ManagerState.RUNNING, output)
        return DriverResult(ManagerState.STOPPED, output or command_detail(proc))

    def up(self, service: Service) -> DriverResult:
        command = service.options.get("start")
        if not command:
            return DriverResult(ManagerState.ERROR, "missing options.start")
        args = command if isinstance(command, list) else shlex.split(str(command))
        proc = self.runner.run(args)
        if proc.returncode == 0:
            return DriverResult(ManagerState.RUNNING, command_detail(proc))
        return DriverResult(ManagerState.ERROR, command_detail(proc))

    def down(self, service: Service) -> DriverResult:
        proc = self.runner.run(["screen", "-S", self._session(service), "-X", "quit"])
        if proc.returncode == 0:
            return DriverResult(ManagerState.STOPPED, "screen quit requested")
        return DriverResult(ManagerState.ERROR, command_detail(proc))
