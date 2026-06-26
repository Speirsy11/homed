"""Shared driver primitives."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import List, Optional, Protocol

from ..model import ManagerState, Service


@dataclass(frozen=True)
class DriverResult:
    state: ManagerState
    detail: str = ""


class CommandRunner(Protocol):
    def run(self, args: List[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
        ...


class SubprocessRunner:
    def run(self, args: List[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
        return subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )


class BaseDriver:
    def __init__(self, runner: Optional[CommandRunner] = None) -> None:
        self.runner = runner or SubprocessRunner()

    def status(self, service: Service) -> DriverResult:
        raise NotImplementedError

    def up(self, service: Service) -> DriverResult:
        raise NotImplementedError

    def down(self, service: Service) -> DriverResult:
        raise NotImplementedError

    def restart(self, service: Service) -> DriverResult:
        down_result = self.down(service)
        if down_result.state is ManagerState.ERROR:
            return down_result
        return self.up(service)

    def logs(self, service: Service) -> DriverResult:
        return DriverResult(ManagerState.UNKNOWN, "logs are not implemented for this driver")


def command_detail(proc: subprocess.CompletedProcess) -> str:
    text = (proc.stdout or proc.stderr or "").strip()
    return text if text else f"exit {proc.returncode}"
