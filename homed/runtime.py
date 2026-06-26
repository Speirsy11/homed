"""Runtime orchestration across registry, drivers, and health checks."""

from __future__ import annotations

from typing import Iterable, List

from .drivers import get_driver
from .health import check as check_health
from .model import Service, ServiceStatus


def status_for(service: Service) -> ServiceStatus:
    driver_result = get_driver(service.driver).status(service)
    health_state = check_health(service.health)
    detail = "; ".join(part for part in (driver_result.detail, "") if part)
    return ServiceStatus(
        name=service.name,
        driver=service.driver,
        intent=service.intent,
        exposure=service.exposure,
        manager_state=driver_result.state,
        health_state=health_state,
        detail=detail,
    )


def statuses_for(services: Iterable[Service]) -> List[ServiceStatus]:
    return [status_for(service) for service in services]
