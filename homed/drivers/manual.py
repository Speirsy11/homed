"""Read-only driver for externally managed services."""

from __future__ import annotations

from ..model import ManagerState, Service
from .base import BaseDriver, DriverResult


class ManualDriver(BaseDriver):
    def status(self, service: Service) -> DriverResult:
        return DriverResult(ManagerState.UNKNOWN, "manual service; no manager state")

    def up(self, service: Service) -> DriverResult:
        return DriverResult(ManagerState.ERROR, "manual service cannot be started by homed")

    def down(self, service: Service) -> DriverResult:
        return DriverResult(ManagerState.ERROR, "manual service cannot be stopped by homed")
