# homed

`homed` is a small declarative control layer for Mac mini homelabs.

It is intentionally not a process manager. Docker, launchd, screen, shell
scripts, and cron-like jobs remain the real managers. `homed` gives them a
single registry, one CLI surface, consistent JSON output, and a place for
doctor checks.

## Design

- CLI code does not shell out directly.
- Drivers talk to real managers, but never parse config.
- Health checks report health only; they never start or stop services.
- Manager state and health state are separate.
- The real service registry is private and gitignored.
- The public repo carries examples and schema, not local topology.

## Config

The default config path is:

```bash
~/.config/homed/services.yaml
```

Override it with:

```bash
HOMED_CONFIG=/path/to/services.yaml homed status
homed --config /path/to/services.yaml status
```

Create a local starter config:

```bash
homed init
homed config path
homed config validate
homed registry dump --json
homed serve
```

## Dashboard

`homed serve` starts a small read-only web dashboard:

```bash
homed serve              # http://127.0.0.1:8765 (loopback-only)
homed serve --open       # also open a browser
homed serve --port 9000
homed serve --host 0.0.0.0   # opt in to LAN exposure (prints a warning)
```

The dashboard shows every declared service as a scannable grid, with manager
state and health state as separate badges, filters by intent/exposure/driver, a
doctor panel, and a per-service detail drawer. It is deliberately read-only: it
exposes no `up`/`down`/`restart` routes and never mutates anything.

It is a presentation layer over the same JSON the CLI emits:

- `GET /api/status` — same shape as `homed status --json`
- `GET /api/registry` — registry dump with secret-looking values redacted
- `GET /api/doctor` — same shape as `homed doctor --json`
- `GET /api/meta` — active config path, version, service count

Binds to `127.0.0.1` by default; pass `--host` only if you deliberately want it
reachable beyond loopback.

The committed `examples/services.example.yaml` is sanitized. It uses public,
well-known service names and loopback URLs. Put machine-specific paths, host
names, labels, and local topology in your private config file.

## Commands

```bash
homed status
homed status --json
homed up adguard
homed down adguard
homed restart dashboard
homed logs dashboard
homed doctor
homed config path
homed config validate
homed registry dump --json
homed serve
```

## Service Model

Each service has:

- `driver`: `docker`, `launchd`, `screen`, `process`, or `manual`
- `intent`: `always`, `manual`, `selected`, `cron`, or `external`
- `exposure`: `loopback`, `lan`, `tailscale`, or `public`
- `health`: `http`, `tcp`, `command`, `last_success`, or `none`
- optional `after`, `requires`, `conflicts`, `mode_group`, `tags`, `logs`, and
  driver-specific `options`

## Limitations

This MVP is conservative. It does not install launchd plists, generate Docker
Compose files, or supervise processes. `up` and `down` ask the underlying
manager for the single obvious action. That keeps the code inspectable and
prevents `homed` from becoming a second, worse init system.

## Development

Run tests:

```bash
python3 -m unittest
```
