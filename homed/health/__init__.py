"""Health-check dispatcher."""

from __future__ import annotations

from ..model import HealthCheck, HealthKind, HealthState
from .command import check_command
from .http import check_http
from .last_success import check_last_success
from .tcp import check_tcp


def check(health: HealthCheck) -> HealthState:
    if health.kind is HealthKind.NONE:
        return HealthState.NOT_CHECKED
    if health.kind is HealthKind.HTTP:
        return check_http(health)
    if health.kind is HealthKind.TCP:
        return check_tcp(health)
    if health.kind is HealthKind.COMMAND:
        return check_command(health)
    if health.kind is HealthKind.LAST_SUCCESS:
        return check_last_success(health)
    return HealthState.UNKNOWN
