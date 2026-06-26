"""HTTP health checks."""

from __future__ import annotations

import urllib.error
import urllib.request

from ..model import HealthCheck, HealthState


def check_http(health: HealthCheck) -> HealthState:
    if not health.url:
        return HealthState.UNKNOWN
    try:
        with urllib.request.urlopen(health.url, timeout=health.timeout_seconds) as res:
            return HealthState.HEALTHY if res.status == health.expect_status else HealthState.UNHEALTHY
    except (urllib.error.URLError, TimeoutError, OSError):
        return HealthState.UNHEALTHY
