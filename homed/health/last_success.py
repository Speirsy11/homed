"""last-success marker health checks."""

from __future__ import annotations

import time
from pathlib import Path

from ..model import HealthCheck, HealthState


def check_last_success(health: HealthCheck) -> HealthState:
    if not health.path:
        return HealthState.UNKNOWN
    path = Path(health.path).expanduser()
    if not path.exists():
        return HealthState.UNHEALTHY
    if health.max_age_seconds is None:
        return HealthState.HEALTHY
    age = time.time() - path.stat().st_mtime
    return HealthState.HEALTHY if age <= health.max_age_seconds else HealthState.DEGRADED
