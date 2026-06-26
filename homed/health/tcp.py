"""TCP health checks."""

from __future__ import annotations

import socket

from ..model import HealthCheck, HealthState


def check_tcp(health: HealthCheck) -> HealthState:
    host = health.host or "127.0.0.1"
    if health.port is None:
        return HealthState.UNKNOWN
    try:
        with socket.create_connection((host, health.port), timeout=health.timeout_seconds):
            return HealthState.HEALTHY
    except OSError:
        return HealthState.UNHEALTHY
