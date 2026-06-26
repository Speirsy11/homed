"""Drivers: the adapters that talk to real process managers.

Each driver knows how to ask one real manager (Docker, launchd, screen, or a
plain process table) about a service's state and how to drive it up/down. They
shell out to the manager's own CLI *internally* -- homed code never invokes the
``homed`` CLI itself.

Two hard rules apply to every driver:

* Status is read-only and must never start or stop anything.
* ``up``/``down`` are conservative: they perform the single obvious action and
  report what happened, rather than trying to reconcile arbitrary state.
"""

from __future__ import annotations

from typing import Dict, Type

from ..model import Driver
from .base import BaseDriver, CommandRunner, DriverResult, SubprocessRunner
from .docker import DockerDriver
from .launchd import LaunchdDriver
from .manual import ManualDriver
from .process import ProcessDriver
from .screen import ScreenDriver

_REGISTRY: Dict[Driver, Type[BaseDriver]] = {
    Driver.DOCKER: DockerDriver,
    Driver.LAUNCHD: LaunchdDriver,
    Driver.SCREEN: ScreenDriver,
    Driver.PROCESS: ProcessDriver,
    Driver.MANUAL: ManualDriver,
}


def get_driver(driver: Driver, runner: CommandRunner = None) -> BaseDriver:
    """Instantiate the driver implementation for *driver*.

    *runner* lets tests substitute a fake command runner; in production it
    defaults to a real subprocess runner.
    """
    cls = _REGISTRY.get(driver)
    if cls is None:  # pragma: no cover - all enum members are registered
        raise KeyError(f"no driver registered for {driver!r}")
    return cls(runner or SubprocessRunner())


__all__ = [
    "BaseDriver",
    "CommandRunner",
    "DriverResult",
    "SubprocessRunner",
    "DockerDriver",
    "LaunchdDriver",
    "ScreenDriver",
    "ProcessDriver",
    "ManualDriver",
    "get_driver",
]
