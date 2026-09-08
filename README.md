# homed

A small declarative control layer and private dashboard for a Mac homelab. Docker, launchd, screen and existing service managers own the processes; homed supplies one registry, a CLI and a browser view.

The dashboard combines live service observations with an editable personal calendar. Systems status and calendar data require sign-in. It works at desktop and phone sizes, and serves its calendar assets locally.

## Install and configure

Requires Python 3.9 or later.

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/homed init
.venv/bin/homed account create YOUR_USERNAME
.venv/bin/homed serve
```

Open `http://127.0.0.1:8765`. Account creation prompts privately for a passphrase; there are no default credentials. Routine calendar entry and service visibility work in the browser.

The private registry defaults to `~/.config/homed/services.yaml`. Override it with `HOMED_CONFIG` or `--config PATH`. Small private databases default to `dashboard/` beside the registry; both `serve` and `account create` accept `--state-dir`.

LAN and Tailscale listeners require a trusted TLS certificate and explicit browser origins. See [dashboard setup, scope and recovery](docs/dashboard.md). Existing deployment launchers are not automatically changed.

## Dashboard

- Overview, searchable service details, separate manager/health state, observed times and explicit stale/unavailable data.
- Activity from observed changes and registry routines, with unknown schedule/history marked clearly.
- Local mounted storage capacity and backup limitations.
- Personal calendar with day/week/month/agenda views, all-day occasions, recurrence, forms, dragging, optimistic edits and versioned export/restore.
- Private sessions, account-scoped events, protected writes and bounded declared logs.

Work Outlook import and service lifecycle buttons are later work. This release does not change Discord routing or supervise services. Additional dashboard accounts are administrators with separate personal calendars; it is not a general shared portal.

## Registry and CLI

Services declare `driver`, `intent`, `exposure`, `health` and optional dependencies, tags, `web_url`, `logs.path` and driver-specific `options`. Manager state and functional health remain separate. An HTTP health endpoint is not automatically an app link. The public [example](examples/services.example.yaml) and [schema](schema/services.schema.json) contain no local topology.

```sh
homed status --json
homed doctor
homed up SERVICE
homed down SERVICE
homed restart SERVICE
homed logs SERVICE
homed config validate
homed registry dump --json
```

Lifecycle commands remain CLI-only. The web server exposes authenticated `GET /api/dashboard`, historical status/registry/doctor/meta routes, session and personal-calendar APIs. See the [delivery contract](docs/dashboard.md).

## Development

```sh
.venv/bin/python -m unittest
```

Tests cover authentication, HTTP access control, observation ageing, calendar persistence, restore conflicts and London daylight-saving behavior. Frontend assets are committed under `homed/web/vendor` with licenses and a source/hash manifest; there is no frontend runtime build step.
