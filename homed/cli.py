"""Command-line interface for homed."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from . import config
from . import doctor
from . import registry as registry_mod
from .drivers import get_driver
from .output import json_dumps, render_issues, render_status
from .runtime import statuses_for


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"config not found: {exc}", file=sys.stderr)
        return 2
    except config.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="homed")
    parser.add_argument("--config", type=Path, help="config path (defaults to ~/.config/homed/services.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="show service state")
    status.add_argument("services", nargs="*")
    status.add_argument("--json", action="store_true", dest="json_output")
    status.set_defaults(func=cmd_status)

    for name in ("up", "down", "restart", "logs"):
        p = sub.add_parser(name, help=f"{name} a service")
        p.add_argument("service")
        p.set_defaults(func=globals()[f"cmd_{name}"])

    doc = sub.add_parser("doctor", help="inspect registry for design problems")
    doc.add_argument("--json", action="store_true", dest="json_output")
    doc.set_defaults(func=cmd_doctor)

    cfg = sub.add_parser("config", help="config helpers")
    cfg_sub = cfg.add_subparsers(dest="config_command", required=True)
    cfg_path = cfg_sub.add_parser("path")
    cfg_path.set_defaults(func=cmd_config_path)
    cfg_validate = cfg_sub.add_parser("validate")
    cfg_validate.add_argument("--json", action="store_true", dest="json_output")
    cfg_validate.set_defaults(func=cmd_config_validate)

    reg = sub.add_parser("registry", help="registry helpers")
    reg_sub = reg.add_subparsers(dest="registry_command", required=True)
    reg_dump = reg_sub.add_parser("dump")
    reg_dump.add_argument("--json", action="store_true", dest="json_output")
    reg_dump.set_defaults(func=cmd_registry_dump)

    init = sub.add_parser("init", help="create a local services.yaml from the example")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    serve = sub.add_parser("serve", help="run the read-only local dashboard")
    serve.add_argument("--host", default=None, help="bind host (default 127.0.0.1; loopback-only)")
    serve.add_argument("--port", type=int, default=None, help="bind port (default 8765)")
    serve.add_argument("--open", action="store_true", dest="open_browser", help="open a browser window")
    serve.set_defaults(func=cmd_serve)
    return parser


def _path(args: argparse.Namespace) -> Path:
    return args.config or config.config_path()


def _registry(args: argparse.Namespace) -> registry_mod.Registry:
    return registry_mod.load(_path(args))


def cmd_status(args: argparse.Namespace) -> int:
    reg = _registry(args)
    services = reg.select(args.services) if args.services else list(reg)
    statuses = statuses_for(services)
    if args.json_output:
        print(json_dumps({"services": [s.to_dict() for s in statuses]}))
    else:
        print(render_status(statuses))
    return 0


def _service(args: argparse.Namespace):
    reg = _registry(args)
    svc = reg.get(args.service)
    if svc is None:
        raise config.ConfigError(f"unknown service: {args.service}")
    return svc


def cmd_up(args: argparse.Namespace) -> int:
    svc = _service(args)
    result = get_driver(svc.driver).up(svc)
    print(result.detail or result.state.value)
    return 0 if result.state.value != "error" else 1


def cmd_down(args: argparse.Namespace) -> int:
    svc = _service(args)
    result = get_driver(svc.driver).down(svc)
    print(result.detail or result.state.value)
    return 0 if result.state.value != "error" else 1


def cmd_restart(args: argparse.Namespace) -> int:
    svc = _service(args)
    result = get_driver(svc.driver).restart(svc)
    print(result.detail or result.state.value)
    return 0 if result.state.value != "error" else 1


def cmd_logs(args: argparse.Namespace) -> int:
    svc = _service(args)
    if svc.logs.get("path"):
        print(Path(str(svc.logs["path"])).expanduser().read_text(encoding="utf-8", errors="replace")[-8000:])
        return 0
    result = get_driver(svc.driver).logs(svc)
    print(result.detail)
    return 0 if result.state.value != "error" else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    reg = _registry(args)
    issues = [i.to_dict() for i in doctor.inspect(reg)]
    if args.json_output:
        print(json_dumps({"ok": not any(i["level"] == "error" for i in issues), "issues": issues}))
    else:
        print(render_issues(issues))
    return 1 if any(i["level"] == "error" for i in issues) else 0


def cmd_config_path(args: argparse.Namespace) -> int:
    print(_path(args))
    return 0


def cmd_config_validate(args: argparse.Namespace) -> int:
    data = config.load_raw(_path(args))
    result = config.validate(data)
    if args.json_output:
        print(json_dumps(result.to_dict()))
    else:
        print(render_issues([i.to_dict() for i in result.issues]))
    return 0 if result.ok else 1


def cmd_registry_dump(args: argparse.Namespace) -> int:
    reg = _registry(args)
    if args.json_output:
        print(json_dumps(reg.to_dict()))
    else:
        print(render_status(statuses_for(reg)))
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    path = config.init_config(_path(args), force=args.force)
    print(f"created {path}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from . import server

    return server.serve(
        host=args.host or server.DEFAULT_HOST,
        port=args.port or server.DEFAULT_PORT,
        config_path=_path(args),
        open_browser=args.open_browser,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
