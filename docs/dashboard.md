# Private systems dashboard and calendar

The first delivery combines live service visibility with a persistent personal calendar. Work Outlook connection and web service controls are deferred. The dashboard consumes the existing homed registry and managers; it never becomes a second service supervisor.

## Acceptance contract

- Authentication protects every systems, logs and calendar API, including historical endpoint names. Account creation is local administration, not public registration.
- Browser writes require the exact configured origin, a session-bound CSRF token and bounded JSON. Non-loopback listeners require TLS and explicit permitted origins. The listener accepts only loopback, private LAN and Tailscale client ranges.
- Observations retain their original time through collection failure and process restart. The browser ages them even if requests stop succeeding. Unknown health is never a confirmed healthy aggregate. Intentionally stopped services remain distinguishable from failing health.
- Service details use declared identity, separate manager and health state, dependencies, exposure and checked timestamps. `web_url` identifies the app separately from its health endpoint. Logs require a registered path and are bounded and redacted.
- Calendar records belong to the authenticated account and survive reopening the database. Forms, agenda, day/week/month views, whole-series recurrence, all-day occasions, edits and deletion use that source. Failed writes retain edits; optimistic revisions reject stale overwrites.
- Exports are versioned JSON. Restore validates the entire file before a transactional merge, preserves unrelated events and stable IDs, and rejects stale/conflicting records without partial changes. A repeat identical restore changes zero records.
- London timezone changes are covered by literal expected dates and offsets. Invalid initial local times receive a clear error. Generated recurrence gaps are skipped, overlaps occur once, and expansion is bounded.
- Calendar data, registry contents, passwords, tokens and deployment topology are private files, excluded from the repository. Frontend dependencies are served locally, with pinned source versions, licenses and SHA-256 hashes.

## Runtime and storage

The server is native Python, independent of the container engine. Install its pinned dependencies in an environment created by the intended service interpreter. Password hashing uses PyCA cryptography so it also works with Apple's Python 3.9, whose standard library does not expose scrypt. Its state directory defaults to `dashboard/` beside the active registry, or `--state-dir`. It contains three transactional SQLite files: `auth.sqlite3`, `calendar.sqlite3` and `observations.sqlite3`. Each has schema version checks and private file permissions. Calendar export/restore omits accounts and sessions, so restoring events does not restore another person's access.

`homed account create USERNAME` prompts for a passphrase of at least 14 characters and creates a dashboard administrator. There are no default credentials. Additional administrator accounts have separate calendars but can see the systems overview; this release is not a shared-services portal for untrusted users.

Sessions expire after seven days, or a day idle, and logout revokes them. Password hashes use scrypt (N=131072, r=8, p=1), and bearer session tokens are hashed at rest. Failed login attempts are throttled and password hashing runs serially to bound memory. HTTP connections have a ten-second socket timeout and a maximum of 32 concurrent workers; TLS handshakes occupy a worker rather than blocking the listener. Serve HTTPS on LAN/Tailscale with a certificate trusted by the intended devices:

```sh
homed serve --host 0.0.0.0 --port 8765 \
  --cert /private/path/dashboard.crt --key /private/path/dashboard.key \
  --origin https://dashboard.example:8765
```

Repeat `--origin` for other exact trusted addresses. Origins contain the scheme, hostname and optional port, with no trailing slash. A native non-loopback listener requires TLS. Forwarded headers are not trusted. The application does not issue certificates, change trust stores, alter routers or publish itself.

To keep homed on loopback HTTP while Tailscale Serve terminates trusted HTTPS,
configure the public browser origin explicitly:

```sh
tailscale serve --bg --https=8443 http://127.0.0.1:8765
homed serve --host 127.0.0.1 --port 8765 \
  --proxy-origin https://dashboard.example.ts.net:8443
```

`--proxy-origin` is repeatable and accepts exact HTTPS origins, including the
default port form `https://dashboard.example.ts.net`. It is rejected unless the
native listener is loopback. Requests still require a configured `Host`, exact
`Origin` on writes, the local account session, and its CSRF token. The server
does not trust forwarded headers or Tailscale identity headers. Direct local
HTTP remains available and receives a non-Secure cookie; requests using a
configured HTTPS proxy host receive a Secure cookie.

A single existing native service owner should run the final deployment. Preserve the prior launcher and checkout until verified replacement. Do not add a competing watchdog. The existing installation is untouched by the build checkout.

## Service metadata and freshness

The optional service-level `web_url` must be HTTP(S), without credentials or query/fragment secrets. The health URL stays separate. Existing registries work without it; missing app links remain missing. A service may declare `logs.path`; the browser cannot choose filesystem paths. Only the final 16 KiB are read. Pattern redaction covers common credential assignments, bearer/JWT tokens, private-key blocks, and credential-bearing URLs; no generic log redactor can identify every secret embedded in arbitrary prose, so register only appropriate operational logs.

Collection runs in one server-owned background loop. HTTP requests read the persisted snapshot rather than starting duplicate collectors. Status reads use existing drivers and health checks, with bounded worker concurrency. Service failures and collector failure are distinct. Current collection failure retains old data and visibly ages it. The last 200 observed state changes are retained.

Storage lists local mounted disk volumes, excluding network mounts and aliases. APFS/container views must not be added together as independent physical capacity. A mount's free capacity is not backup evidence; volumes on a single physical disk share its failure risk. Next-run information, full resource history, backup/restore evidence and detailed job receipts remain unavailable where the registry supplies no source; the dashboard says so.

## Calendar semantics

Times are stored as local wall clock plus an IANA timezone; all-day ends are exclusive dates in the API and exports. The form shows an inclusive "Through" date, so a single-day occasion starts and ends on the same displayed date. Recurrence supports daily, weekly, monthly and yearly intervals, optionally limited by count or inclusive end date. Edits/deletes apply to the whole series in this first release; a recurring drag opens the series editor. There is no Outlook sync or imported work event cache yet.

Exports contain personal event records, UUIDs, revisions and audit timestamps. A backup with an older revision cannot roll back newer local data. Divergent records at the same revision are conflicts. Higher revisions can update older records. Restore reports both records reviewed and records changed. Each account is limited to 5,000 source events and an 8 MiB canonical JSON export; writes that exceed either limit are rejected transactionally. The HTTP restore limit is 16 MiB to allow the indented export format. Keep an independent protected copy of exports; another directory or volume on the same disk is not independent backup.

## Verification and rollback

Run `python -m unittest` in the installed environment. HTTP tests need permission to bind temporary loopback ports; a sandbox bind denial is not a failed service. Browser verification must include desktop and phone layouts, sign-in, calendar save/reload/edit, failed save retention, and visibly ageing service data.

Before replacing a deployment, export the calendar, take consistent SQLite backups and retain its certificate/key and private configuration. Stop the new launcher and return to the prior checkout/launcher to roll back the UI. Preserve new databases; old versions must not modify a newer schema. A separate restore into an empty state directory is the safe recovery test.
