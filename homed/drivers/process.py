"""Process-table driver."""

from __future__ import annotations

import shlex
import subprocess

from ..model import ManagerState, Service
from .base import BaseDriver, DriverResult, command_detail


class ProcessDriver(BaseDriver):
    def status(self, service: Service) -> DriverResult:
        pattern = str(service.options.get("match") or "")
        if not pattern:
            return DriverResult(ManagerState.UNKNOWN, "missing options.match")
        proc = self.runner.run(["pgrep", "-fl", pattern])
        if proc.returncode == 0:
            return DriverResult(ManagerState.RUNNING, (proc.stdout or "").strip())
        return DriverResult(ManagerState.STOPPED, command_detail(proc))

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
        command = service.options.get("stop")
        if command:
            args = command if isinstance(command, list) else shlex.split(str(command))
            proc = self.runner.run(args)
        else:
            pattern = str(service.options.get("match") or "")
            proc = self.runner.run(["pkill", "-f", pattern])
        if proc.returncode == 0:
            return DriverResult(ManagerState.STOPPED, command_detail(proc))
        return DriverResult(ManagerState.ERROR, command_detail(proc))
