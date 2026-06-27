"""A small, read-only local dashboard for homed.

This is a thin HTTP presentation layer over the same building blocks the CLI
uses: it loads the registry, asks drivers for manager state and health checks
for health state, and runs the doctor. It performs no lifecycle actions -- there
are no ``up``/``down``/``restart`` routes -- so the worst a misdirected request
can do is read state.

Boundaries preserved here:

* The server never shells out itself; it composes :func:`homed.runtime.statuses_for`,
  :func:`homed.registry.load`, and :func:`homed.doctor.inspect`, which own all
  external calls through drivers and health helpers.
* The registry is passed through :func:`homed.sanitize.sanitize_registry` before
  it is served, so secret-looking values never reach the browser.
* It binds to loopback (127.0.0.1) by default. LAN exposure is opt-in via an
  explicit host argument.
"""

from __future__ import annotations

import json
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from . import __version__
from . import config as config_mod
from . import doctor as doctor_mod
from . import registry as registry_mod
from .registry import Registry
from .runtime import statuses_for
from .sanitize import sanitize_registry

WEB_DIR = Path(__file__).resolve().parent / "web"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# Route -> (filename, content-type). An explicit allowlist keeps static serving
# free of any path-traversal surface.
_STATIC_ROUTES: Dict[str, Tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


# --- payload builders ------------------------------------------------------
#
# Kept as plain functions so they can be exercised directly in tests without an
# HTTP round-trip.


def status_payload(registry: Registry) -> Dict[str, Any]:
    """Same shape as ``homed status --json``: ``{"services": [...]}``."""
    return {"services": [s.to_dict() for s in statuses_for(registry)]}


def registry_payload(registry: Registry) -> Dict[str, Any]:
    """The registry dump, with secret-looking values redacted."""
    return sanitize_registry(registry.to_dict())


def doctor_payload(registry: Registry) -> Dict[str, Any]:
    """Same shape as ``homed doctor --json``."""
    issues = [i.to_dict() for i in doctor_mod.inspect(registry)]
    return {"ok": not any(i["level"] == "error" for i in issues), "issues": issues}


def meta_payload(path: Path, registry: Registry) -> Dict[str, Any]:
    """Cheap header metadata that does not require touching any driver."""
    return {
        "version": __version__,
        "config_path": str(path),
        "service_count": len(registry),
        "generated_at": time.time(),
    }


# --- HTTP layer ------------------------------------------------------------


def make_handler(config_path: Optional[Path] = None) -> type:
    """Build a request-handler class bound to a config path.

    The path is resolved lazily on each request via :func:`homed.registry.load`,
    so editing the config and refreshing the page shows new state without a
    server restart.
    """
    resolved = config_path or config_mod.config_path()

    api_routes: Dict[str, Callable[[Registry], Dict[str, Any]]] = {
        "/api/status": status_payload,
        "/api/registry": registry_payload,
        "/api/doctor": doctor_payload,
    }

    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = f"homed/{__version__}"
        config_path = resolved

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            route = self.path.split("?", 1)[0]
            if route in _STATIC_ROUTES:
                self._serve_static(*_STATIC_ROUTES[route])
            elif route == "/api/meta":
                self._serve_meta()
            elif route in api_routes:
                self._serve_api(api_routes[route])
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": f"not found: {route}"})

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            # The dashboard is read-only by design; refuse mutations loudly.
            self._send_json(
                HTTPStatus.METHOD_NOT_ALLOWED,
                {"error": "homed dashboard is read-only"},
            )

        # -- handlers --

        def _serve_static(self, filename: str, content_type: str) -> None:
            try:
                body = (WEB_DIR / filename).read_bytes()
            except FileNotFoundError:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": f"missing asset: {filename}"},
                )
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _serve_meta(self) -> None:
            try:
                reg = registry_mod.load(self.config_path)
            except (FileNotFoundError, config_mod.ConfigError) as exc:
                self._send_json(HTTPStatus.OK, self._config_error(exc))
                return
            self._send_json(HTTPStatus.OK, meta_payload(self.config_path, reg))

        def _serve_api(self, builder: Callable[[Registry], Dict[str, Any]]) -> None:
            try:
                reg = registry_mod.load(self.config_path)
            except (FileNotFoundError, config_mod.ConfigError) as exc:
                self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, self._config_error(exc))
                return
            try:
                payload = builder(reg)
            except Exception as exc:  # noqa: BLE001 - never crash the request loop
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)}
                )
                return
            self._send_json(HTTPStatus.OK, payload)

        def _config_error(self, exc: Exception) -> Dict[str, Any]:
            kind = (
                "config_not_found"
                if isinstance(exc, FileNotFoundError)
                else "config_error"
            )
            return {
                "error": str(exc),
                "error_kind": kind,
                "config_path": str(self.config_path),
            }

        # -- helpers --

        def _send_json(self, status: HTTPStatus, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:  # noqa: D401 - quiet by default
            """Suppress the default per-request stderr logging."""

    return DashboardHandler


def make_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    config_path: Optional[Path] = None,
) -> ThreadingHTTPServer:
    """Create (but do not start) the dashboard HTTP server."""
    return ThreadingHTTPServer((host, port), make_handler(config_path))


def serve(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    config_path: Optional[Path] = None,
    open_browser: bool = False,
) -> int:
    """Run the dashboard until interrupted. Returns a process exit code."""
    httpd = make_server(host, port, config_path)
    bound_host, bound_port = httpd.server_address[0], httpd.server_address[1]
    url = f"http://{bound_host}:{bound_port}/"
    print(f"homed dashboard on {url}  (read-only; Ctrl-C to stop)")
    if bound_host not in ("127.0.0.1", "localhost", "::1"):
        print(f"warning: bound to {bound_host}; the dashboard is reachable beyond loopback")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping homed dashboard")
    finally:
        httpd.server_close()
    return 0
