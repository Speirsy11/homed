"""Sanitize a registry dump before it leaves the box.

The dashboard exposes the registry over a (loopback) HTTP API. Even though the
server binds to localhost by default, the registry is the one place where a
careless ``options`` value could carry a token or password, and a screenshot of
the dashboard is the easiest way for one to leak. This module strips anything
that looks like a secret from a registry dict, leaving structure and
non-sensitive fields (paths, hosts, ports, container names) intact.

It operates on the plain ``dict`` produced by :meth:`Registry.to_dict`, not on
the typed model -- sanitization is a presentation concern and stays out of the
domain model and drivers.
"""

from __future__ import annotations

from typing import Any, Dict

REDACTED = "•••redacted•••"

# Substrings that, when found in a key, mark its value as sensitive. Matching is
# case-insensitive and substring-based so ``apiKey``, ``DB_PASSWORD``, and
# ``auth_token`` are all caught.
_SECRET_HINTS = (
    "secret",
    "password",
    "passwd",
    "token",
    "apikey",
    "api_key",
    "auth",
    "credential",
    "cred",
    "private_key",
    "access_key",
)


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in _SECRET_HINTS)


def _scrub(value: Any) -> Any:
    """Recursively redact secret-looking entries inside *value*."""
    if isinstance(value, dict):
        scrubbed: Dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _is_secret_key(key):
                scrubbed[key] = REDACTED
            else:
                scrubbed[key] = _scrub(item)
        return scrubbed
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def sanitize_registry(data: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of a registry dict with secret-looking values redacted.

    The input is the shape produced by :meth:`homed.registry.Registry.to_dict`.
    Only values whose *key* looks sensitive are redacted; everything else is
    preserved so the dashboard can still show config summaries usefully.
    """
    return _scrub(data)
