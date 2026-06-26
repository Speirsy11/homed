"""Docker/OrbStack driver."""

from __future__ import annotations

import subprocess

from ..model import ManagerState, Service
from .base import BaseDriver, DriverResult, command_detail


class DockerDriver(BaseDriver):
    def _container(self, service: Service) -> str:
        return str(service.options.get("container") or service.name)

    def status(self, service: Service) -> DriverResult:
        try:
            proc = self.runner.run(
                ["docker", "inspect", "-f", "{{.State.Status}}", self._container(service)]
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return DriverResult(ManagerState.UNKNOWN, str(exc))
        if proc.returncode != 0:
            return DriverResult(ManagerState.NOT_FOUND, command_detail(proc))
        status = (proc.stdout or "").strip()
        if status == "running":
            return DriverResult(ManagerState.RUNNING, status)
        if status in {"exited", "created", "paused", "restarting", "dead"}:
            return DriverResult(ManagerState.STOPPED, status)
        return DriverResult(ManagerState.UNKNOWN, status or command_detail(proc))

    def up(self, service: Service) -> DriverResult:
        proc = self.runner.run(["docker", "start", self._container(service)])
        if proc.returncode == 0:
            return DriverResult(ManagerState.RUNNING, command_detail(proc))
        return DriverResult(ManagerState.ERROR, command_detail(proc))

    def down(self, service: Service) -> DriverResult:
        proc = self.runner.run(["docker", "stop", self._container(service)])
        if proc.returncode == 0:
            return DriverResult(ManagerState.STOPPED, command_detail(proc))
        return DriverResult(ManagerState.ERROR, command_detail(proc))

    def logs(self, service: Service) -> DriverResult:
        proc = self.runner.run(["docker", "logs", "--tail", "80", self._container(service)])
        state = ManagerState.UNKNOWN if proc.returncode == 0 else ManagerState.ERROR
        return DriverResult(state, command_detail(proc))
