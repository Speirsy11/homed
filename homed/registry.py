"""The registry: load config and build the typed service model.

This module is the bridge between raw config dicts (produced by
:mod:`homed.config`) and the typed domain model (:mod:`homed.model`). It owns
the only conversion from ``dict`` to :class:`~homed.model.Service`.

A :class:`Registry` is an immutable, ordered collection of services plus the
path it was loaded from. Nothing here performs lifecycle I/O; drivers and
health checks live elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from . import config as config_mod
from .config import ConfigError
from .model import (
    Driver,
    Exposure,
    HealthCheck,
    HealthKind,
    Intent,
    Service,
)


@dataclass(frozen=True)
class Registry:
    """An ordered, name-indexed set of declared services."""

    services: List[Service]
    path: Optional[Path] = None

    def __post_init__(self) -> None:
        index: Dict[str, Service] = {}
        for svc in self.services:
            index[svc.name] = svc
        object.__setattr__(self, "_index", index)

    def __iter__(self) -> Iterator[Service]:
        return iter(self.services)

    def __len__(self) -> int:
        return len(self.services)

    def __contains__(self, name: object) -> bool:
        return name in self._index  # type: ignore[attr-defined]

    def get(self, name: str) -> Optional[Service]:
        return self._index.get(name)  # type: ignore[attr-defined]

    def names(self) -> List[str]:
        return [s.name for s in self.services]

    def select(self, names: List[str]) -> List[Service]:
        """Return services for *names* in registry order, erroring on unknowns."""
        unknown = [n for n in names if n not in self]
        if unknown:
            raise ConfigError(f"unknown service(s): {', '.join(sorted(unknown))}")
        wanted = set(names)
        return [s for s in self.services if s.name in wanted]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": str(self.path) if self.path else None,
            "services": {s.name: s.to_dict() for s in self.services},
        }


# --- building --------------------------------------------------------------


def build_service(name: str, spec: Dict[str, Any]) -> Service:
    """Construct a :class:`Service` from a validated spec dict."""
    if not isinstance(spec, dict):
        raise ConfigError(f"service {name!r}: definition must be a mapping")
    try:
        driver = Driver.from_value(spec["driver"])
    except KeyError as exc:
        raise ConfigError(f"service {name!r}: missing 'driver'") from exc
    except ValueError as exc:
        raise ConfigError(f"service {name!r}: {exc}") from exc

    try:
        intent = Intent.from_value(spec.get("intent", Intent.MANUAL.value))
        exposure = Exposure.from_value(spec.get("exposure", Exposure.LOOPBACK.value))
    except ValueError as exc:
        raise ConfigError(f"service {name!r}: {exc}") from exc

    return Service(
        name=name,
        driver=driver,
        description=str(spec.get("description", "") or ""),
        web_url=spec.get("web_url"),
        intent=intent,
        exposure=exposure,
        health=build_health(name, spec.get("health")),
        after=_str_list(spec.get("after")),
        requires=_str_list(spec.get("requires")),
        conflicts=_str_list(spec.get("conflicts")),
        mode_group=spec.get("mode_group"),
        tags=_str_list(spec.get("tags")),
        options=dict(spec.get("options") or {}),
        logs=dict(spec.get("logs") or {}),
    )


def build_health(name: str, spec: Any) -> HealthCheck:
    if spec is None:
        return HealthCheck()
    if not isinstance(spec, dict):
        raise ConfigError(f"service {name!r}: 'health' must be a mapping")
    try:
        kind = HealthKind.from_value(spec.get("kind", HealthKind.NONE.value))
    except ValueError as exc:
        raise ConfigError(f"service {name!r}: {exc}") from exc
    command = spec.get("command")
    if isinstance(command, str):
        command = [command]
    elif command is not None:
        command = [str(c) for c in command]
    return HealthCheck(
        kind=kind,
        url=spec.get("url"),
        expect_status=int(spec.get("expect_status", 200)),
        host=spec.get("host"),
        port=_opt_int(spec.get("port")),
        command=command,
        path=spec.get("path"),
        max_age_seconds=_opt_int(spec.get("max_age_seconds")),
        timeout_seconds=float(spec.get("timeout_seconds", 5.0)),
    )


def build_registry(data: Dict[str, Any], path: Optional[Path] = None) -> Registry:
    """Build a :class:`Registry` from a raw config dict.

    The caller is expected to have validated *data* first; this still raises
    :class:`ConfigError` on the structural problems it cannot tolerate.
    """
    services_spec = data.get("services")
    if not isinstance(services_spec, dict):
        raise ConfigError("config has no 'services' mapping")
    services = [build_service(name, spec) for name, spec in services_spec.items()]
    return Registry(services=services, path=path)


def load(path: Optional[Path] = None) -> Registry:
    """Resolve, read, and build the registry from the active config file."""
    resolved = path or config_mod.config_path()
    data = config_mod.load_raw(resolved)
    return build_registry(data, path=resolved)


# --- helpers ---------------------------------------------------------------


def _str_list(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    raise ConfigError(f"expected a list, got {type(value).__name__}")


def _opt_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    return int(value)
