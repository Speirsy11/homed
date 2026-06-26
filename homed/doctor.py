"""Static and live-ish checks for a homed registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from .model import Exposure, HealthKind, Intent
from .registry import Registry


@dataclass(frozen=True)
class DoctorIssue:
    level: str
    where: str
    message: str

    def to_dict(self) -> Dict[str, str]:
        return {"level": self.level, "where": self.where, "message": self.message}


def inspect(registry: Registry) -> List[DoctorIssue]:
    issues: List[DoctorIssue] = []
    names = set(registry.names())
    mode_groups: Dict[str, List[str]] = {}

    for service in registry:
        where = f"services.{service.name}"
        if service.intent is Intent.ALWAYS and service.health.kind is HealthKind.NONE:
            issues.append(
                DoctorIssue("warning", f"{where}.health", "always-on service has no health check")
            )
        if service.exposure is Exposure.PUBLIC:
            issues.append(
                DoctorIssue(
                    "warning",
                    f"{where}.exposure",
                    "public exposure should be deliberate and documented",
                )
            )
        for ref in service.requires + service.after + service.conflicts:
            if ref not in names:
                issues.append(DoctorIssue("error", where, f"references unknown service {ref!r}"))
        if service.mode_group:
            mode_groups.setdefault(service.mode_group, []).append(service.name)
        if service.logs.get("path") and str(service.logs["path"]).endswith(".env"):
            issues.append(DoctorIssue("error", f"{where}.logs.path", "log path must not point at an env file"))

    for group, members in mode_groups.items():
        if len(members) > 1:
            for member in members:
                svc = registry.get(member)
                assert svc is not None
                missing = [other for other in members if other != member and other not in svc.conflicts]
                if missing:
                    issues.append(
                        DoctorIssue(
                            "warning",
                            f"services.{member}.conflicts",
                            f"mode group {group!r} should declare conflicts with {', '.join(missing)}",
                        )
                    )
    return issues
