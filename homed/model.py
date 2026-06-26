"""Domain model for homed.

This module defines the vocabulary of the system: the enums that describe how
a service is managed, exposed, and health-checked, plus the dataclasses that
hold a parsed service definition and a computed status.

Design rules enforced here:

* The model is pure data. It performs no I/O, never shells out, and never
  parses config files. It is constructed from already-parsed dictionaries by
  :mod:`homed.registry`.
* Manager state (is the process running, per its real manager?) and health
  state (does the service actually work?) are kept strictly separate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class _StrEnum(str, Enum):
    """A string-valued enum that prints as its value.

    (Python 3.9 has no ``enum.StrEnum``; this is the conventional backport.)
    """

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)

    @classmethod
    def from_value(cls, value: Any) -> "_StrEnum":
        """Coerce a raw string into a member, with a clear error otherwise."""
        if isinstance(value, cls):
            return value
        try:
            return cls(value)
        except ValueError:
            choices = ", ".join(m.value for m in cls)
            raise ValueError(
                f"{value!r} is not a valid {cls.__name__}; expected one of: {choices}"
            )


class Driver(_StrEnum):
    """Which real manager owns the service's lifecycle."""

    DOCKER = "docker"
    LAUNCHD = "launchd"
    SCREEN = "screen"
    PROCESS = "process"
    MANUAL = "manual"  # homed only observes; it never starts/stops it


class HealthKind(_StrEnum):
    """How to decide whether a running service actually works."""

    HTTP = "http"
    TCP = "tcp"
    COMMAND = "command"
    LAST_SUCCESS = "last_success"
    NONE = "none"


class Intent(_StrEnum):
    """The operator's intent for when this service *should* be up."""

    ALWAYS = "always"  # part of the always-on baseline
    MANUAL = "manual"  # only when explicitly asked
    SELECTED = "selected"  # up only when chosen into an active set
    CRON = "cron"  # driven by a schedule; "up" is transient by design
    EXTERNAL = "external"  # lives off-box; homed only tracks/health-checks it


class Exposure(_StrEnum):
    """The furthest network boundary this service is reachable from."""

    LOOPBACK = "loopback"  # 127.0.0.1 only
    LAN = "lan"  # reachable on the local network
    TAILSCALE = "tailscale"  # reachable over the tailnet
    PUBLIC = "public"  # reachable from the public internet


class ManagerState(_StrEnum):
    """What the real manager reports about the process."""

    RUNNING = "running"
    STOPPED = "stopped"
    NOT_FOUND = "not_found"  # the manager has no record of this unit
    UNKNOWN = "unknown"  # could not be determined (e.g. manager unavailable)
    ERROR = "error"  # the manager reported an error state


class HealthState(_StrEnum):
    """What a health check concluded, independent of manager state."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"
    NOT_CHECKED = "not_checked"  # no health check configured / not run


@dataclass(frozen=True)
class HealthCheck:
    """A declarative health check definition.

    Only the fields relevant to ``kind`` are meaningful; the rest stay at their
    defaults. Validation of which fields are required lives in
    :mod:`homed.config`, not here.
    """

    kind: HealthKind = HealthKind.NONE
    # http
    url: Optional[str] = None
    expect_status: int = 200
    # tcp
    host: Optional[str] = None
    port: Optional[int] = None
    # command
    command: Optional[List[str]] = None
    # last_success
    path: Optional[str] = None
    max_age_seconds: Optional[int] = None
    # shared
    timeout_seconds: float = 5.0

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"kind": self.kind.value}
        if self.kind is HealthKind.HTTP:
            data.update(url=self.url, expect_status=self.expect_status)
        elif self.kind is HealthKind.TCP:
            data.update(host=self.host, port=self.port)
        elif self.kind is HealthKind.COMMAND:
            data.update(command=self.command)
        elif self.kind is HealthKind.LAST_SUCCESS:
            data.update(path=self.path, max_age_seconds=self.max_age_seconds)
        if self.kind is not HealthKind.NONE:
            data["timeout_seconds"] = self.timeout_seconds
        return data


@dataclass(frozen=True)
class Service:
    """A single declared service.

    ``options`` carries driver-specific settings that don't deserve a typed
    field on every service (e.g. the Docker container name, a launchd label, a
    screen session name, or a process command). Drivers read from ``options``;
    nothing else should.
    """

    name: str
    driver: Driver
    description: str = ""
    intent: Intent = Intent.MANUAL
    exposure: Exposure = Exposure.LOOPBACK
    health: HealthCheck = field(default_factory=HealthCheck)
    after: List[str] = field(default_factory=list)
    requires: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    mode_group: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    options: Dict[str, Any] = field(default_factory=dict)
    logs: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "driver": self.driver.value,
            "description": self.description,
            "intent": self.intent.value,
            "exposure": self.exposure.value,
            "health": self.health.to_dict(),
            "after": list(self.after),
            "requires": list(self.requires),
            "conflicts": list(self.conflicts),
            "mode_group": self.mode_group,
            "tags": list(self.tags),
            "options": dict(self.options),
            "logs": dict(self.logs),
        }


@dataclass(frozen=True)
class ServiceStatus:
    """A point-in-time view combining manager state and health state."""

    name: str
    driver: Driver
    intent: Intent
    exposure: Exposure
    manager_state: ManagerState
    health_state: HealthState
    detail: str = ""

    @property
    def ok(self) -> bool:
        """Whether the service looks healthy enough to leave alone."""
        if self.intent in (Intent.MANUAL, Intent.EXTERNAL, Intent.CRON):
            # These are not expected to be continuously up; "ok" just means
            # nothing is actively broken.
            return self.manager_state is not ManagerState.ERROR and (
                self.health_state
                in (
                    HealthState.HEALTHY,
                    HealthState.NOT_CHECKED,
                    HealthState.UNKNOWN,
                )
            )
        running = self.manager_state is ManagerState.RUNNING
        healthy = self.health_state in (
            HealthState.HEALTHY,
            HealthState.NOT_CHECKED,
        )
        return running and healthy

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "driver": self.driver.value,
            "intent": self.intent.value,
            "exposure": self.exposure.value,
            "manager_state": self.manager_state.value,
            "health_state": self.health_state.value,
            "ok": self.ok,
            "detail": self.detail,
        }
