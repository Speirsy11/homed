"""Configuration: where it lives, how it loads, and whether it is valid.

This is the only module that touches config *files*. It resolves the active
config path, reads YAML into a plain dict, validates that dict against the
homed schema (in pure Python, for good error messages), and can scaffold a
fresh config from the bundled example.

Boundary: this module produces/validates raw dicts and delegates building the
typed model to :mod:`homed.registry`. Drivers never see any of this.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import yamlloader
from .model import Driver, Exposure, HealthKind, Intent

CONFIG_ENV_VAR = "HOMED_CONFIG"
_PACKAGE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PACKAGE_DIR.parent
EXAMPLE_CONFIG = _REPO_ROOT / "examples" / "services.example.yaml"


# --- path resolution -------------------------------------------------------


def config_dir() -> Path:
    """Directory that holds the active config (honors ``XDG_CONFIG_HOME``)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "homed"


def config_path() -> Path:
    """Resolve the active config file path.

    Precedence: ``$HOMED_CONFIG`` overrides everything, otherwise it is
    ``<config_dir>/services.yaml``.
    """
    override = os.environ.get(CONFIG_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return config_dir() / "services.yaml"


# --- loading ---------------------------------------------------------------


def load_raw(path: Optional[Path] = None) -> Dict[str, Any]:
    """Read and parse the config file into a dict.

    Prefers PyYAML when it is installed (full YAML support); otherwise falls
    back to the bundled subset loader. Raises ``FileNotFoundError`` if the file
    is absent and ``ConfigError`` on parse failures.
    """
    path = path or config_path()
    if not path.exists():
        raise FileNotFoundError(path)
    text = path.read_text(encoding="utf-8")
    try:
        data = _parse_yaml(text)
    except Exception as exc:  # noqa: BLE001 - normalize to ConfigError
        raise ConfigError(f"failed to parse {path}: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top-level config must be a mapping")
    return data


def _parse_yaml(text: str) -> Any:
    try:
        import yaml  # type: ignore
    except ImportError:
        return yamlloader.load(text)
    return yaml.safe_load(text)


# --- validation ------------------------------------------------------------


class ConfigError(Exception):
    """A configuration problem that prevents homed from operating."""


@dataclass
class Issue:
    """A single validation finding."""

    level: str  # "error" or "warning"
    where: str  # dotted location, e.g. "services.jellyfin.driver"
    message: str

    def to_dict(self) -> Dict[str, str]:
        return {"level": self.level, "where": self.where, "message": self.message}

    def __str__(self) -> str:
        return f"[{self.level}] {self.where}: {self.message}"


@dataclass
class ValidationResult:
    issues: List[Issue]

    @property
    def errors(self) -> List[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> List[Issue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [i.to_dict() for i in self.errors],
            "warnings": [i.to_dict() for i in self.warnings],
        }


_VALID_DRIVERS = {d.value for d in Driver}
_VALID_INTENTS = {i.value for i in Intent}
_VALID_EXPOSURES = {e.value for e in Exposure}
_VALID_HEALTH_KINDS = {h.value for h in HealthKind}

# Driver-specific required option keys.
_DRIVER_REQUIRED_OPTIONS = {
    Driver.DOCKER.value: ["container"],
    Driver.LAUNCHD.value: ["label"],
    Driver.SCREEN.value: ["session"],
    Driver.PROCESS.value: ["match"],
    Driver.MANUAL.value: [],
}


def validate(data: Dict[str, Any]) -> ValidationResult:
    """Validate a raw config dict. Never raises for content problems."""
    issues: List[Issue] = []

    if not isinstance(data, dict):
        issues.append(Issue("error", "<root>", "config must be a mapping"))
        return ValidationResult(issues)

    services = data.get("services")
    if services is None:
        issues.append(Issue("error", "services", "missing required 'services' mapping"))
        return ValidationResult(issues)
    if not isinstance(services, dict):
        issues.append(Issue("error", "services", "'services' must be a mapping of name -> definition"))
        return ValidationResult(issues)
    if not services:
        issues.append(Issue("warning", "services", "no services are defined"))

    names = set(services.keys())
    for name, spec in services.items():
        _validate_service(name, spec, names, issues)

    _validate_cross_service(services, issues)
    return ValidationResult(issues)


def _validate_service(name: str, spec: Any, names: set, issues: List[Issue]) -> None:
    where = f"services.{name}"
    if not isinstance(spec, dict):
        issues.append(Issue("error", where, "service definition must be a mapping"))
        return

    driver = spec.get("driver")
    if driver is None:
        issues.append(Issue("error", f"{where}.driver", "missing required 'driver'"))
    elif driver not in _VALID_DRIVERS:
        issues.append(
            Issue("error", f"{where}.driver", f"invalid driver {driver!r}; expected one of {sorted(_VALID_DRIVERS)}")
        )
    else:
        for key in _DRIVER_REQUIRED_OPTIONS.get(driver, []):
            opts = spec.get("options") or {}
            if not isinstance(opts, dict) or not opts.get(key):
                issues.append(
                    Issue("error", f"{where}.options.{key}", f"driver '{driver}' requires options.{key}")
                )

    intent = spec.get("intent", Intent.MANUAL.value)
    if intent not in _VALID_INTENTS:
        issues.append(Issue("error", f"{where}.intent", f"invalid intent {intent!r}"))

    exposure = spec.get("exposure", Exposure.LOOPBACK.value)
    if exposure not in _VALID_EXPOSURES:
        issues.append(Issue("error", f"{where}.exposure", f"invalid exposure {exposure!r}"))

    _validate_health(where, spec.get("health"), issues)

    for rel in ("after", "requires", "conflicts"):
        refs = spec.get(rel, [])
        if refs in (None, []):
            continue
        if not isinstance(refs, list):
            issues.append(Issue("error", f"{where}.{rel}", f"'{rel}' must be a list"))
            continue
        for ref in refs:
            if ref not in names:
                issues.append(Issue("error", f"{where}.{rel}", f"references unknown service {ref!r}"))
            if ref == name:
                issues.append(Issue("error", f"{where}.{rel}", "a service cannot reference itself"))


def _validate_health(where: str, health: Any, issues: List[Issue]) -> None:
    if health is None:
        return
    if not isinstance(health, dict):
        issues.append(Issue("error", f"{where}.health", "'health' must be a mapping"))
        return
    kind = health.get("kind", HealthKind.NONE.value)
    if kind not in _VALID_HEALTH_KINDS:
        issues.append(Issue("error", f"{where}.health.kind", f"invalid health kind {kind!r}"))
        return
    required = {
        HealthKind.HTTP.value: ["url"],
        HealthKind.TCP.value: ["port"],
        HealthKind.COMMAND.value: ["command"],
        HealthKind.LAST_SUCCESS.value: ["path"],
        HealthKind.NONE.value: [],
    }
    for key in required.get(kind, []):
        if not health.get(key):
            issues.append(Issue("error", f"{where}.health.{key}", f"health kind '{kind}' requires '{key}'"))


def _validate_cross_service(services: Dict[str, Any], issues: List[Issue]) -> None:
    # Public exposure deserves a deliberate nudge in a homelab context.
    for name, spec in services.items():
        if not isinstance(spec, dict):
            continue
        if spec.get("exposure") == Exposure.PUBLIC.value:
            issues.append(
                Issue(
                    "warning",
                    f"services.{name}.exposure",
                    "exposed to the public internet; confirm TLS, auth, and backups are in place",
                )
            )

    # Detect cycles in 'after' (ordering) edges.
    cycle = _find_cycle({n: (s.get("after") or []) for n, s in services.items() if isinstance(s, dict)})
    if cycle:
        issues.append(Issue("error", "services", "dependency cycle in 'after': " + " -> ".join(cycle)))


def _find_cycle(graph: Dict[str, List[str]]) -> Optional[List[str]]:
    WHITE, GREY, BLACK = 0, 1, 2
    color: Dict[str, int] = {n: WHITE for n in graph}
    stack: List[str] = []

    def visit(node: str) -> Optional[List[str]]:
        color[node] = GREY
        stack.append(node)
        for nxt in graph.get(node, []):
            if nxt not in color:
                continue  # unknown refs are reported elsewhere
            if color[nxt] == GREY:
                return stack[stack.index(nxt):] + [nxt]
            if color[nxt] == WHITE:
                found = visit(nxt)
                if found:
                    return found
        stack.pop()
        color[node] = BLACK
        return None

    for n in graph:
        if color[n] == WHITE:
            found = visit(n)
            if found:
                return found
    return None


# --- scaffolding -----------------------------------------------------------


def init_config(path: Optional[Path] = None, force: bool = False) -> Path:
    """Create a config from the bundled example if one does not exist.

    The example contains no secrets; this is a safe, copy-only operation.
    Returns the path written. Raises ``ConfigError`` if it already exists and
    ``force`` is False.
    """
    path = path or config_path()
    if path.exists() and not force:
        raise ConfigError(f"refusing to overwrite existing config at {path} (use force)")
    if not EXAMPLE_CONFIG.exists():  # pragma: no cover - packaging guard
        raise ConfigError(f"bundled example config not found at {EXAMPLE_CONFIG}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(EXAMPLE_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    return path
