"""Command health checks."""

from __future__ import annotations

import subprocess

from ..model import HealthCheck, HealthState


def check_command(health: HealthCheck) -> HealthState:
    if not health.command:
        return HealthState.UNKNOWN
    try:
        proc = subprocess.run(
            health.command,
            check=False,
            capture_output=True,
            text=True,
            timeout=health.timeout_seconds,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return HealthState.UNHEALTHY
    return HealthState.HEALTHY if proc.returncode == 0 else HealthState.UNHEALTHY
