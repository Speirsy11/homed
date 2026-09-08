"use strict";
const $ = (s, r = document) => r.querySelector(s),
  $$ = (s, r = document) => [...r.querySelectorAll(s)];
const S = {
  session: null,
  dashboard: null,
  dashError: null,
  page: "overview",
  filter: "all",
  search: "",
  calendar: null,
  upcoming: [],
  draft: null,
  draftUser: null,
  lastFocus: null,
  generation: 0,
};
const meta = {
  overview: ["Overview", "Your Mac, at a glance"],
  services: ["Services", "Live state from the service registry"],
  calendar: ["Calendar", "Personal dates, stored on this Mac"],
  activity: ["Activity", "Observed changes and registered routines"],
  storage: ["Storage", "Physical disks reported by this Mac"],
};
function n(tag, a = {}, kids = []) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(a)) {
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on") && typeof v === "function")
      e.addEventListener(k.slice(2), v);
    else if (v != null) e.setAttribute(k, String(v));
  }
  for (const c of Array.isArray(kids) ? kids : [kids])
    if (c != null)
      e.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return e;
}
function ico(d) {
  const ns = "http://www.w3.org/2000/svg",
    s = document.createElementNS(ns, "svg"),
    p = document.createElementNS(ns, "path");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("aria-hidden", "true");
  p.setAttribute("d", d);
  s.append(p);
  return s;
}
function date(v) {
  if (v == null || v === "") return null;
  const d = new Date(typeof v === "number" && v < 1e12 ? v * 1000 : v);
  return isNaN(d) ? null : d;
}
function age(v) {
  const d = date(v);
  return d ? Math.max(0, Math.floor((Date.now() - d) / 1000)) : Infinity;
}
function ago(v) {
  const s = age(v);
  return !isFinite(s)
    ? "time unavailable"
    : s < 5
      ? "just now"
      : s < 60
        ? `${s}s ago`
        : s < 3600
          ? `${Math.floor(s / 60)}m ago`
          : s < 86400
            ? `${Math.floor(s / 3600)}h ago`
            : `${Math.floor(s / 86400)}d ago`;
}
function when(v) {
  const d = date(v);
  return d
    ? new Intl.DateTimeFormat("en-GB", {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: "Europe/London",
      }).format(d)
    : "Unavailable";
}
function londonDateTime(d) {
  return window.luxon?.DateTime.fromJSDate(d).setZone("Europe/London");
}
function isoDay(d) {
  return (
    londonDateTime(d)?.toFormat("yyyy-MM-dd") || d.toISOString().slice(0, 10)
  );
}
function inputTime(d) {
  return (
    londonDateTime(d)?.toFormat("yyyy-MM-dd'T'HH:mm") ||
    d.toISOString().slice(0, 16)
  );
}
function shiftLocal(value, duration, format) {
  const shifted = window.luxon.DateTime.fromISO(value, {
    zone: "Europe/London",
  }).plus(duration);
  return shifted.toFormat(format);
}
function inclusiveAllDayEnd(serverEnd, start) {
  return serverEnd
    ? shiftLocal(serverEnd.slice(0, 10), { days: -1 }, "yyyy-MM-dd")
    : start.slice(0, 10);
}
function exclusiveAllDayEnd(displayEnd) {
  return shiftLocal(displayEnd, { days: 1 }, "yyyy-MM-dd");
}
function bytes(v) {
  if (!Number.isFinite(v)) return "Unavailable";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (Math.abs(v) >= 1000 && i < 4) {
    v /= 1000;
    i++;
  }
  return `${v >= 10 || !i ? v.toFixed(0) : v.toFixed(1)} ${u[i]}`;
}
function msg(e, t) {
  e.textContent = t || "";
  e.hidden = !t;
}
function err(body, fallback) {
  return typeof body?.error === "string" && body.error.trim()
    ? body.error
    : fallback;
}
async function api(path, o = {}) {
  const generation = S.generation,
    c = new AbortController(),
    t = setTimeout(() => c.abort(), o.timeout || 8000),
    h = new Headers(o.headers || {});
  h.set("Accept", "application/json");
  if (o.write) {
    h.set("Content-Type", "application/json");
    if (S.session?.csrf_token) h.set("X-CSRF-Token", S.session.csrf_token);
  }
  try {
    const r = await fetch(path, {
        method: o.method || "GET",
        headers: h,
        body: o.body === undefined ? undefined : JSON.stringify(o.body),
        credentials: "same-origin",
        signal: c.signal,
      }),
      b = await r.json().catch(() => ({}));
    if (generation !== S.generation) {
      const staleError = Error(
        "Session changed while the request was running.",
      );
      staleError.staleSession = true;
      throw staleError;
    }
    if (r.status === 401 && !["/api/session", "/api/login"].includes(path)) {
      expire();
      const authError = Error("Your session expired. Sign in to continue.");
      authError.authExpired = true;
      throw authError;
    }
    if (!r.ok) {
      const e = Error(err(b, `Request failed (${r.status})`));
      e.status = r.status;
      throw e;
    }
    return b;
  } catch (e) {
    if (e.name === "AbortError") throw Error("The request timed out.");
    throw e;
  } finally {
    clearTimeout(t);
  }
}
function inactiveRequest(e) {
  return e.staleSession || e.authExpired;
}
function toast(t) {
  const e = $("#toast");
  msg(e, t);
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (e.hidden = true), 4000);
}
function empty(title, copy) {
  return n("div", { class: "empty-state" }, [
    ico("M12 8v5m0 3h.01M5 20h14L12 4Z"),
    n("strong", { text: title }),
    n("span", { text: copy }),
  ]);
}
function showLogin(setup, expired) {
  $("#app").hidden = true;
  $("#login-screen").hidden = false;
  $("#login-title").textContent = setup
    ? "Setup required"
    : expired
      ? "Session expired"
      : "Welcome to homed";
  $("#login-copy").textContent = setup
    ? "No account exists yet. On this Mac, run homed’s local setup command to create the first account, then return here. There are no default credentials."
    : expired
      ? "Sign in again to continue. Your unsaved event draft is still here in this tab."
      : "Sign in to see this Mac’s services and calendar.";
  $("#login-form").hidden = !!setup;
  if (!setup) setTimeout(() => $("#login-username").focus(), 0);
}
function formData(validate = false) {
  const all = $("#event-all-day").checked,
    f = $("#event-frequency").value,
    end = $('input[name="recurrence_end"]:checked')?.value || "never",
    displayStart = $("#event-start").value,
    displayEnd = $("#event-end").value || null,
    p = {
      title: $("#event-title").value.trim(),
      start: displayStart,
      end: all && displayEnd ? exclusiveAllDayEnd(displayEnd) : displayEnd,
      all_day: all,
      timezone: $("#event-timezone").value,
      category: $("#event-category").value,
      location: $("#event-location").value.trim(),
      notes: $("#event-notes").value.trim(),
      recurrence:
        f === "none"
          ? null
          : {
              frequency: f,
              interval: +$("#event-interval").value || 1,
              until: end === "until" ? $("#event-until").value || null : null,
              count: end === "count" ? +$("#event-count").value || null : null,
            },
    };
  if ($("#event-id").value) p.id = $("#event-id").value;
  if ($("#event-revision").value !== "")
    p.revision = isNaN(+$("#event-revision").value)
      ? $("#event-revision").value
      : +$("#event-revision").value;
  if (validate) {
    if (!p.title) throw Error("Enter a title.");
    if (!p.start)
      throw Error(all ? "Choose a start date." : "Choose a start time.");
    if (displayEnd && displayEnd < displayStart)
      throw Error(
        all
          ? "The through date cannot be before the start date."
          : "End must be after start.",
      );
    if (p.recurrence?.until && p.recurrence.until < p.start.slice(0, 10))
      throw Error("The recurrence end date cannot be before the event starts.");
  }
  return p;
}
function clearPrivate() {
  S.dashboard = null;
  S.upcoming = [];
  if (S.calendar) {
    S.calendar.destroy();
    S.calendar = null;
  }
  for (const q of [
    "#calendar",
    "#overview-stats",
    "#overview-services",
    "#upcoming",
    "#overview-issues",
    "#services-grid",
    "#changes",
    "#routines",
    "#storage-list",
    "#drawer-content",
  ])
    $(q).replaceChildren();
  closeDrawer();
  if ($("#event-dialog").open) $("#event-dialog").close();
  if ($("#confirm-dialog").open) $("#confirm-dialog").close();
  $("#confirm-copy").textContent = "";
  $("#confirm-error").textContent = "";
  $("#toast").hidden = true;
}
function expire() {
  if ($("#event-dialog").open) {
    S.draft = formData();
    S.draftUser = S.session?.user?.username || null;
  }
  S.generation++;
  S.session = null;
  clearPrivate();
  showLogin(false, true);
}
async function session() {
  try {
    const s = await api("/api/session", { timeout: 6000 });
    s.authenticated ? enter(s) : showLogin(s.setup_required, false);
  } catch (e) {
    showLogin(false, false);
    msg($("#login-error"), `Could not reach homed: ${e.message}`);
  }
}
async function enter(s) {
  S.generation++;
  S.session = s;
  $("#login-screen").hidden = true;
  $("#app").hidden = false;
  const u = s.user?.username || "User";
  $("#username").textContent = u;
  $("#user-avatar").textContent = u[0].toUpperCase();
  nav(location.hash.slice(1) || S.page);
  await Promise.allSettled([refresh(), upcoming()]);
  if (S.draft && S.draftUser === u) {
    nav("calendar");
    const d = S.draft;
    S.draft = null;
    S.draftUser = null;
    openEvent(d, !!d.id);
    msg(
      $("#event-error"),
      "Session restored. Review your unsaved changes, then save again.",
    );
  } else {
    S.draft = null;
    S.draftUser = null;
  }
}
async function refresh() {
  $("#refresh").classList.add("spinning");
  try {
    S.dashboard = await api("/api/dashboard");
    S.dashError = null;
  } catch (e) {
    if (inactiveRequest(e)) return;
    S.dashError = e.message;
  } finally {
    $("#refresh").classList.remove("spinning");
    render();
  }
}
function stale() {
  return (
    !S.dashboard ||
    !!S.dashError ||
    !!S.dashboard.error ||
    S.dashboard.stale === true ||
    age(S.dashboard.observed_at) > (+S.dashboard.stale_after_seconds || 90)
  );
}
function tone(s) {
  const max = +S.dashboard?.stale_after_seconds || 90;
  if (stale() || s.collection_error || s.error || age(s.observed_at) > max)
    return "unknown";
  if (s.intent === "manual" && s.manager_state === "stopped") return "neutral";
  if (!s.observed_at || ["unknown", "not_checked"].includes(s.health_state))
    return "unknown";
  if (s.ok === true && s.health_state === "healthy") return "healthy";
  return s.health_state === "degraded" ? "warning" : "critical";
}
function label(s) {
  const t = tone(s);
  return t === "neutral"
    ? "Stopped by design"
    : t === "unknown"
      ? stale()
        ? "Stale"
        : s.health_state === "not_checked"
          ? "Not checked"
          : "Unknown"
      : t === "healthy"
        ? "Healthy"
        : t === "warning"
          ? "Degraded"
          : s.health_state === "healthy"
            ? `Manager ${String(s.manager_state || "unavailable").replaceAll("_", " ")}`
            : s.health_state === "unhealthy"
              ? "Unhealthy"
              : s.health_state || s.manager_state || "Unavailable";
}
function pill(l, t) {
  return n("span", { class: `status-pill ${t}` }, [
    n("span", { class: "status-dot" }),
    String(l).replaceAll("_", " "),
  ]);
}
function freshness() {
  const b = $("#global-banner"),
    stamp = $("#last-updated");
  if (!S.dashboard) {
    stamp.textContent = S.dashError
      ? "Status unavailable"
      : "Waiting for status";
    b.className = "banner critical";
    msg(b, S.dashError ? `Live status unavailable: ${S.dashError}` : "");
    return;
  }
  stamp.textContent = S.dashboard.observed_at
    ? `Observed ${ago(S.dashboard.observed_at)}`
    : "Observation time unavailable";
  stamp.title = when(S.dashboard.observed_at);
  if (S.dashError) {
    b.className = "banner warning";
    msg(
      b,
      `Refresh failed: ${S.dashError}. Showing data observed ${ago(S.dashboard.observed_at)}.`,
    );
  } else if (stale()) {
    b.className = "banner warning";
    msg(
      b,
      `Status is stale. Last observation: ${when(S.dashboard.observed_at)}.`,
    );
  } else if (S.dashboard.error) {
    b.className = "banner warning";
    msg(b, `Collector reported: ${S.dashboard.error}`);
  } else if (S.dashboard.collecting) {
    b.className = "banner neutral";
    msg(
      b,
      "Collecting fresh status. Values are from the previous completed observation.",
    );
  } else msg(b, "");
}
function stat(l, v, d, t, path, filter) {
  const e = n("button", { class: `stat-card ${t || ""}`, type: "button" }, [
    n("span", { class: "stat-label", text: l }),
    n("strong", { text: String(v) }),
    n("span", { class: "stat-detail", text: d }),
  ]);
  e.onclick = () => {
    if (filter) {
      S.filter = filter;
      S.search = "";
      $("#service-search").value = "";
    }
    nav(path);
  };
  return e;
}
function serviceRow(s, compact = false) {
  const t = tone(s),
    e = n(
      "button",
      { class: `service-row ${compact ? "compact-row" : ""}`, type: "button" },
      [
        n("span", { class: `service-icon ${t}` }, [
          ico("M5 6h14v5H5Zm0 7h14v5H5Z"),
        ]),
        n("span", { class: "service-main" }, [
          n("strong", { text: s.name || "Unnamed service" }),
          n("small", {
            text:
              s.description ||
              [s.driver, s.exposure].filter(Boolean).join(" · ") ||
              "No description provided",
          }),
        ]),
        pill(label(s), t),
      ],
    );
  e.onclick = () => drawer(s);
  return e;
}
function render() {
  freshness();
  if (!S.dashboard) {
    for (const [q, a, b] of [
      [
        "#overview-services",
        "No live status",
        "homed has not returned a dashboard snapshot.",
      ],
      ["#overview-issues", "Issues unavailable", "Issue data is unavailable."],
      ["#services-grid", "Services unavailable", "Try refreshing."],
      ["#changes", "Changes unavailable", "No snapshot is available."],
      ["#routines", "Routines unavailable", "No routine data is available."],
      [
        "#storage-list",
        "Storage unavailable",
        "No physical disk data is available.",
      ],
    ])
      $(q).replaceChildren(empty(a, b));
    return;
  }
  renderStats();
  renderServices();
  renderIssues();
  renderActivity();
  renderStorage();
}
function renderStats() {
  const ss = S.dashboard.services || [],
    good = ss.filter((s) => tone(s) === "healthy").length,
    bad = ss.filter((s) => ["critical", "warning"].includes(tone(s))).length,
    unk = ss.filter((s) => tone(s) === "unknown").length,
    ds = S.dashboard.storage || [],
    root = ds.find((d) => d.path === "/" || d.mount_point === "/");
  $("#overview-stats").replaceChildren(
    stat(
      "Services",
      ss.length,
      ss.length ? `${good} confirmed healthy` : "No services reported",
      good && good === ss.length ? "healthy" : "",
      "services",
    ),
    stat(
      "Attention",
      bad,
      unk ? `${unk} unknown or stale` : "No unknown states",
      bad ? "critical" : "",
      "services",
      "attention",
    ),
    stat(
      "Calendar",
      S.upcoming.length,
      "events in the next 14 days",
      "",
      "calendar",
    ),
    stat(
      "System free",
      root && Number.isFinite(root.free_bytes) ? bytes(root.free_bytes) : "—",
      root ? "on the system volume" : "System volume unavailable",
      "",
      "storage",
    ),
  );
}
function renderServices() {
  const ss = S.dashboard.services || [];
  $("#overview-services").replaceChildren(
    ...(ss.length
      ? ss.slice(0, 6).map((s) => serviceRow(s, true))
      : [empty("No services", "The registry returned no entries.")]),
  );
  const filters = [
    ["all", "All"],
    ["attention", "Attention"],
    ["healthy", "Healthy"],
    ["neutral", "Intentional stops"],
  ];
  $("#service-filters").replaceChildren(
    ...filters.map(([v, l]) => {
      const b = n("button", {
        type: "button",
        class: S.filter === v ? "active" : "",
        "aria-pressed": S.filter === v,
        text: l,
      });
      b.onclick = () => {
        S.filter = v;
        renderServices();
      };
      return b;
    }),
  );
  const q = S.search.toLowerCase(),
    visible = ss.filter((s) => {
      const t = tone(s),
        fm =
          S.filter === "all" ||
          (S.filter === "attention" &&
            ["warning", "critical", "unknown"].includes(t)) ||
          t === S.filter;
      return (
        fm &&
        (!q ||
          [s.name, s.description, s.driver, s.exposure, ...(s.tags || [])]
            .join(" ")
            .toLowerCase()
            .includes(q))
      );
    });
  $("#services-grid").replaceChildren(
    ...(visible.length
      ? visible.map((s) => serviceRow(s))
      : [
          empty(
            "No matching services",
            ss.length
              ? "Try another search or filter."
              : "The registry returned no services.",
          ),
        ]),
  );
}
function renderIssues() {
  const services = S.dashboard.services || [],
    order = { critical: 0, warning: 1, unknown: 2 },
    concerns = services
      .filter((s) => tone(s) in order)
      .sort((a, b) => order[tone(a)] - order[tone(b)]),
    is = [
      ...concerns.slice(0, 6).map((s) => ({
        service: s.name,
        level: tone(s) === "critical" ? "error" : "warning",
        message: `${label(s)} · observed ${ago(s.observed_at)}`,
      })),
      ...(S.dashboard.issues || []),
    ];
  $("#overview-issues").replaceChildren(
    ...(is.length
      ? is.map((i) =>
          n("div", { class: `issue-item ${i.level || "warning"}` }, [
            n("span", { class: "issue-mark" }),
            n("span", {}, [
              n("strong", { text: i.service || i.code || "System" }),
              n("small", { text: i.message || "No detail provided" }),
            ]),
          ]),
        )
      : [
          empty(
            "No reported issues",
            stale()
              ? "Status is stale, so this is not a current all-clear."
              : services.length
                ? "Current service checks and registry checks reported no issue."
                : "No services are registered yet.",
          ),
        ]),
  );
  if (concerns.length > 6) {
    const more = n("button", {
      type: "button",
      class: "text-button",
      text: `View all ${concerns.length} services needing review`,
    });
    more.onclick = () => {
      S.filter = "attention";
      S.search = "";
      $("#service-search").value = "";
      nav("services");
    };
    $("#overview-issues").append(more);
  }
}
function renderActivity() {
  const cs = S.dashboard.changes || [];
  $("#changes").replaceChildren(
    ...(cs.length
      ? cs.map((c) =>
          n("div", { class: "timeline-item" }, [
            n("span", { class: "timeline-dot" }),
            n("div", {}, [
              n("strong", { text: c.service || "Unknown service" }),
              n("p", {
                text: `${c.previous || "unknown"} → ${c.current || "unknown"}`,
              }),
              n("time", { text: c.at ? when(c.at) : "Time unavailable" }),
            ]),
          ]),
        )
      : [
          empty(
            "No observed changes",
            "Changes appear after an observed state transition.",
          ),
        ]),
  );
  const rs = S.dashboard.routines || [];
  $("#routines").replaceChildren(
    ...(rs.length
      ? rs.map((r) => {
          const fresh =
              !stale() &&
              !r.error &&
              age(r.observed_at) <= (+S.dashboard.stale_after_seconds || 90),
            rt = fresh && r.health_state === "healthy" ? "healthy" : "unknown";
          return n("div", { class: "routine-item" }, [
            n("div", { class: "routine-head" }, [
              n("strong", { text: r.name || "Unnamed routine" }),
              pill(
                fresh
                  ? r.health_state || r.manager_state || "unknown"
                  : "stale",
                rt,
              ),
            ]),
            n("p", {
              text: r.description || r.detail || "No description provided",
            }),
            n("small", {
              text: `${r.next_due ? `Next: ${when(r.next_due)}` : "Next run schedule unavailable"} · ${r.last_success_at ? `Last success: ${when(r.last_success_at)}` : "No success history reported"}`,
            }),
          ]);
        })
      : [
          empty(
            "No routines registered",
            "Registry cron services appear here when available.",
          ),
        ]),
  );
}
function renderStorage() {
  const ds = S.dashboard.storage || [];
  $("#storage-list").replaceChildren(
    ...(ds.length
      ? ds.map((d) => {
          const total = +d.total_bytes,
            free = +d.free_bytes,
            used = Number.isFinite(d.used_bytes) ? +d.used_bytes : total - free,
            p =
              total > 0 && Number.isFinite(used)
                ? Math.max(0, Math.min(100, (used / total) * 100))
                : null,
            bar = n(
              "div",
              {
                class: "meter",
                role: "meter",
                "aria-label": `${d.name || "Disk"} usage`,
              },
              [n("span")],
            );
          if (p != null) bar.firstChild.style.width = `${p}%`;
          return n("section", { class: "panel storage-card" }, [
            n("div", { class: "storage-head" }, [
              n("span", { class: "storage-icon" }, [
                ico(
                  "M5 7c0-1.7 3.1-3 7-3s7 1.3 7 3v10c0 1.7-3.1 3-7 3s-7-1.3-7-3Zm0 0c0 1.7 3.1 3 7 3s7-1.3 7-3",
                ),
              ]),
              n("div", {}, [
                n("h2", { text: d.name || "Unnamed physical disk" }),
                n("p", { text: d.path || "Device path unavailable" }),
              ]),
            ]),
            d.error
              ? n("div", { class: "inline-message", text: d.error })
              : bar,
            n("div", { class: "storage-numbers" }, [
              n("strong", {
                text: p == null ? "Usage unavailable" : `${bytes(used)} used`,
              }),
              n("span", {
                text:
                  Number.isFinite(free) && Number.isFinite(total)
                    ? `${bytes(free)} free of ${bytes(total)}`
                    : "Capacity unavailable",
              }),
            ]),
            n("div", { class: "storage-meta" }, [
              n("span", {
                text:
                  d.protection === "single-disk"
                    ? "Single disk · no redundancy reported"
                    : "Protection unknown",
              }),
              n("span", {
                text: d.observed_at
                  ? `Observed ${ago(d.observed_at)}`
                  : "Observation time unavailable",
              }),
            ]),
          ]);
        })
      : [
          empty(
            "No physical disks reported",
            "Aliases are omitted; data appears when the collector reports it.",
          ),
        ]),
  );
  msg($("#backup-note"), S.dashboard.backup_note || "");
}
async function upcoming() {
  const a = new Date(),
    b = new Date(+a + 14 * 864e5);
  try {
    const d = await api(
      `/api/events?start=${encodeURIComponent(isoDay(a))}&end=${encodeURIComponent(isoDay(b))}`,
    );
    S.upcoming = (d.events || []).sort((x, y) =>
      String(x.start).localeCompare(String(y.start)),
    );
    renderUpcoming();
    if (S.dashboard) renderStats();
  } catch (e) {
    if (inactiveRequest(e)) return;
    $("#upcoming").replaceChildren(empty("Upcoming unavailable", e.message));
  }
}
function renderUpcoming() {
  const box = $("#upcoming");
  box.replaceChildren(
    ...(S.upcoming.length
      ? S.upcoming.slice(0, 6).map((e) => {
          const d = date(e.start),
            b = n("button", { class: "upcoming-item", type: "button" }, [
              n("time", {}, [
                n("strong", {
                  text: d
                    ? new Intl.DateTimeFormat("en-GB", {
                        day: "2-digit",
                      }).format(d)
                    : "—",
                }),
                n("span", {
                  text: d
                    ? new Intl.DateTimeFormat("en-GB", {
                        month: "short",
                      }).format(d)
                    : "Unknown",
                }),
              ]),
              n("span", {}, [
                n("strong", { text: e.title || "Untitled event" }),
                n("small", {
                  text: e.allDay
                    ? "All day"
                    : d
                      ? new Intl.DateTimeFormat("en-GB", {
                          timeStyle: "short",
                        }).format(d)
                      : "Time unavailable",
                }),
              ]),
            ]);
          b.onclick = () => editEvent(e.extendedProps?.event_id || e.id);
          return b;
        })
      : [
          empty("Nothing scheduled", "No personal events in the next 14 days."),
        ]),
  );
}
function nav(page) {
  if (!meta[page]) page = "overview";
  S.page = page;
  if (location.hash !== `#${page}`) history.replaceState(null, "", `#${page}`);
  $$("[data-page-panel]").forEach(
    (p) => (p.hidden = p.dataset.pagePanel !== page),
  );
  $$("[data-page]").forEach((b) => {
    const a = b.dataset.page === page;
    b.classList.toggle("active", a);
    b.setAttribute("aria-current", a ? "page" : "false");
  });
  $("#page-title").textContent = meta[page][0];
  $("#page-eyebrow").textContent = meta[page][1];
  if (page === "calendar") initCalendar();
  if (page === "services" && S.dashboard) renderServices();
  requestAnimationFrame(() => S.calendar?.updateSize());
}
function detail(k, v) {
  return n("div", { class: "detail-row" }, [
    n("dt", { text: k }),
    n("dd", {
      text:
        v == null || v === "" ? "Unavailable" : String(v).replaceAll("_", " "),
    }),
  ]);
}
function drawer(s) {
  S.lastFocus = document.activeElement;
  const t = tone(s),
    box = $("#drawer-content"),
    intro = n("div", { class: "drawer-intro" }, [
      n("span", { class: `service-icon large ${t}` }, [
        ico("M5 6h14v5H5Zm0 7h14v5H5Z"),
      ]),
      n("div", {}, [
        n("h2", { id: "drawer-title", text: s.name || "Service details" }),
        n("p", { text: s.description || "No description provided" }),
      ]),
    ]),
    dl = n("dl", { class: "detail-list" }, [
      detail("Current state", label(s)),
      detail("Manager", s.manager_state),
      detail("Health", s.health_state),
      detail("Driver", s.driver),
      detail("Intent", s.intent),
      detail("Exposure", s.exposure),
      detail(
        "Observed",
        s.observed_at ? `${when(s.observed_at)} (${ago(s.observed_at)})` : null,
      ),
      detail("Detail", s.detail),
      detail("Health check", s.health?.kind),
      detail("Requires", (s.requires || []).join(", ") || null),
      detail("Starts after", (s.after || []).join(", ") || null),
    ]),
    actions = n("div", { class: "drawer-actions" });
  for (const l of s.links || [])
    try {
      const u = new URL(l.url, location.origin);
      if (["http:", "https:"].includes(u.protocol))
        actions.append(
          n("a", {
            class: "button",
            href: u.href,
            target: "_blank",
            rel: "noopener noreferrer",
            text: l.label || "Open link",
          }),
        );
    } catch {}
  if (s.logs_available)
    actions.append(
      n("button", {
        class: "button",
        type: "button",
        text: "View recent logs",
        onclick: () => logs(s.name),
      }),
    );
  box.replaceChildren(
    intro,
    n("h3", { text: "Status details" }),
    dl,
    s.tags?.length
      ? n(
          "div",
          { class: "tag-list" },
          s.tags.map((x) => n("span", { text: x })),
        )
      : null,
    actions,
  );
  $("#service-drawer").hidden = false;
  document.body.classList.add("no-scroll");
  setTimeout(() => $("[data-close-drawer]", $(".drawer")).focus(), 0);
}
async function logs(name) {
  const box = $("#drawer-content"),
    sec = n("section", { class: "logs-section" }, [
      n("h3", { text: "Recent logs" }),
      n("pre", { text: "Loading…" }),
    ]);
  box.append(sec);
  try {
    const d = await api(`/api/services/${encodeURIComponent(name)}/logs`);
    $("pre", sec).textContent = d.text || "No log lines returned.";
    if (d.truncated)
      sec.append(n("small", { text: "Output was truncated by homed." }));
  } catch (e) {
    if (inactiveRequest(e)) return;
    $("pre", sec).textContent = `Logs unavailable: ${e.message}`;
  }
}
function closeDrawer() {
  const o = $("#service-drawer");
  if (o.hidden) return;
  o.hidden = true;
  document.body.classList.remove("no-scroll");
  S.lastFocus?.focus?.();
}
function initCalendar() {
  if (S.calendar || !window.FullCalendar?.Calendar) return;
  const plug = window.FullCalendar?.Luxon3,
    plugins = plug ? [plug.default || plug] : [];
  if (!plugins.length || !window.luxon) {
    msg(
      $("#calendar-error"),
      "Calendar unavailable: the local timezone library could not be loaded.",
    );
    return;
  }
  $("#calendar-zone-note").textContent = "Times shown in Europe/London";
  S.calendar = new FullCalendar.Calendar($("#calendar"), {
    plugins,
    timeZone: "Europe/London",
    initialView: innerWidth < 700 ? "listWeek" : "dayGridMonth",
    firstDay: 1,
    locale: "en-gb",
    nowIndicator: true,
    editable: true,
    eventDurationEditable: false,
    headerToolbar: {
      left: "prev,next today",
      center: "title",
      right: "dayGridMonth,timeGridWeek,timeGridDay,listWeek",
    },
    buttonText: {
      today: "Today",
      month: "Month",
      week: "Week",
      day: "Day",
      list: "List",
    },
    events: async (i, ok, fail) => {
      try {
        const d = await api(
          `/api/events?start=${encodeURIComponent(isoDay(i.start))}&end=${encodeURIComponent(isoDay(i.end))}`,
        );
        msg($("#calendar-error"), "");
        ok(d.events || []);
      } catch (e) {
        if (inactiveRequest(e)) return;
        msg($("#calendar-error"), e.message);
        fail(e);
      }
    },
    eventClick: (i) => editEvent(i.event.extendedProps?.event_id || i.event.id),
    eventDrop: drop,
    dateClick: (i) =>
      openEvent({
        start: i.allDay ? i.dateStr : inputTime(i.date),
        all_day: i.allDay,
      }),
  });
  S.calendar.render();
}
function blank(seed = {}) {
  const now = window.luxon.DateTime.now()
    .setZone("Europe/London")
    .plus({ hours: 1 })
    .startOf("hour");
  const allDay = !!seed.all_day;
  const start =
    seed.start || now.toFormat(allDay ? "yyyy-MM-dd" : "yyyy-MM-dd'T'HH:mm");
  const derivedEnd = allDay
    ? shiftLocal(start, { days: 1 }, "yyyy-MM-dd")
    : shiftLocal(start, { hours: 1 }, "yyyy-MM-dd'T'HH:mm");
  return {
    id: "",
    revision: "",
    title: "",
    start,
    end: seed.end === undefined ? derivedEnd : seed.end,
    all_day: allDay,
    timezone: "Europe/London",
    category: "personal",
    location: "",
    notes: "",
    recurrence: null,
    ...seed,
  };
}
function set(id, v) {
  $(id).value = v == null ? "" : String(v);
}
function openEvent(record = {}, editing = false) {
  const d = editing ? record : blank(record);
  $("#event-all-day").checked = !!d.all_day;
  $("#event-start").type = d.all_day ? "date" : "datetime-local";
  $("#event-end").type = d.all_day ? "date" : "datetime-local";
  for (const [id, v] of [
    ["#event-id", d.id],
    ["#event-revision", d.revision],
    ["#event-title", d.title],
    ["#event-start", String(d.start || "").slice(0, d.all_day ? 10 : 16)],
    [
      "#event-end",
      d.all_day
        ? inclusiveAllDayEnd(String(d.end || ""), String(d.start || ""))
        : String(d.end || "").slice(0, 16),
    ],
    ["#event-timezone", d.timezone || "Europe/London"],
    ["#event-category", d.category || "personal"],
    ["#event-location", d.location],
    ["#event-notes", d.notes],
    ["#event-frequency", d.recurrence?.frequency || "none"],
    ["#event-interval", d.recurrence?.interval || 1],
    ["#event-until", d.recurrence?.until || ""],
    ["#event-count", d.recurrence?.count || 1],
  ])
    set(id, v);
  const mode = d.recurrence?.until
      ? "until"
      : d.recurrence?.count
        ? "count"
        : "never",
    radio = $(`input[name="recurrence_end"][value="${mode}"]`);
  if (radio) radio.checked = true;
  $("#event-dialog-title").textContent = editing
    ? d.recurrence
      ? "Edit recurring event"
      : "Edit event"
    : "New event";
  $("#series-notice").hidden = !(editing && d.recurrence);
  $("#delete-event").hidden = !editing;
  $("#delete-event").textContent = d.recurrence
    ? "Delete series"
    : "Delete event";
  msg($("#event-error"), "");
  allDay();
  recur();
  $("#event-dialog").showModal();
  setTimeout(() => $("#event-title").focus(), 0);
}
function allDay() {
  const a = $("#event-all-day").checked;
  const startInput = $("#event-start"),
    endInput = $("#event-end"),
    oldStart = startInput.value,
    oldEnd = endInput.value,
    targetType = a ? "date" : "datetime-local",
    changingType = startInput.type !== targetType;
  if (changingType) {
    startInput.type = targetType;
    endInput.type = targetType;
    if (a) {
      startInput.value = oldStart.slice(0, 10);
      endInput.value = (oldEnd || oldStart).slice(0, 10);
    } else {
      const startDate = oldStart.slice(0, 10),
        endDate = (oldEnd || oldStart).slice(0, 10);
      startInput.value = startDate ? `${startDate}T09:00` : "";
      endInput.value = endDate
        ? `${endDate}T${endDate === startDate ? "10:00" : "09:00"}`
        : "";
    }
  }
  $("#start-label").textContent = a ? "Start date" : "Starts";
  $("#end-label").textContent = a ? "Through" : "Ends";
}
function recur() {
  const yes = $("#event-frequency").value !== "none";
  $("#interval-wrap").hidden = !yes;
  $("#recurrence-end").hidden = !yes;
}
async function editEvent(id) {
  try {
    openEvent(await api(`/api/events/${encodeURIComponent(id)}`), true);
  } catch (e) {
    if (inactiveRequest(e)) return;
    toast(`Could not open event: ${e.message}`);
  }
}
async function saveEvent(ev) {
  ev.preventDefault();
  let p;
  try {
    p = formData(true);
  } catch (e) {
    msg($("#event-error"), e.message);
    return;
  }
  const id = p.id;
  delete p.id;
  const generation = S.generation;
  $("#save-event").disabled = true;
  try {
    await api(id ? `/api/events/${encodeURIComponent(id)}` : "/api/events", {
      method: id ? "PUT" : "POST",
      write: true,
      body: p,
    });
    $("#event-dialog").close();
    S.calendar?.refetchEvents();
    await upcoming();
    toast(id ? "Event updated." : "Event created.");
  } catch (e) {
    if (inactiveRequest(e)) return;
    msg(
      $("#event-error"),
      `${e.status === 409 ? "This event changed elsewhere. Your edits are preserved. " : ""}${e.message}`,
    );
  } finally {
    if (generation === S.generation) $("#save-event").disabled = false;
  }
}
function confirm(title, copy, label, action, danger = false) {
  $("#confirm-title").textContent = title;
  $("#confirm-copy").textContent = copy;
  msg($("#confirm-error"), "");
  const b = $("#confirm-action");
  b.textContent = label;
  b.classList.toggle("danger", danger);
  b.onclick = async () => {
    const generation = S.generation;
    b.disabled = true;
    try {
      await action();
      $("#confirm-dialog").close();
    } catch (e) {
      if (inactiveRequest(e)) return;
      msg(
        $("#confirm-error"),
        e.status === 409
          ? `${e.message} Refresh and review before retrying.`
          : e.message,
      );
    } finally {
      if (generation === S.generation) b.disabled = false;
    }
  };
  $("#confirm-dialog").showModal();
  setTimeout(() => b.focus(), 0);
}
function removeEvent() {
  const p = formData();
  confirm(
    p.recurrence ? "Delete recurring series?" : "Delete event?",
    p.recurrence
      ? "This removes the whole recurring series and every occurrence."
      : "This event will be permanently removed.",
    "Delete",
    async () => {
      await api(`/api/events/${encodeURIComponent(p.id)}`, {
        method: "DELETE",
        write: true,
        body: { revision: p.revision },
      });
      $("#event-dialog").close();
      S.calendar?.refetchEvents();
      await upcoming();
      toast("Event deleted.");
    },
    true,
  );
}
async function drop(i) {
  const id = i.event.extendedProps?.event_id || i.event.id;
  if (i.event.extendedProps?.recurrence) {
    i.revert();
    await editEvent(id);
    msg(
      $("#event-error"),
      "Recurring events are edited as a whole series. Update the series dates here.",
    );
    return;
  }
  try {
    const p = await api(`/api/events/${encodeURIComponent(id)}`);
    p.start = p.all_day ? isoDay(i.event.start) : inputTime(i.event.start);
    p.end = i.event.end
      ? p.all_day
        ? isoDay(i.event.end)
        : inputTime(i.event.end)
      : null;
    delete p.id;
    delete p.source;
    delete p.created_at;
    delete p.updated_at;
    await api(`/api/events/${encodeURIComponent(id)}`, {
      method: "PUT",
      write: true,
      body: p,
    });
    await upcoming();
    toast("Event moved.");
  } catch (e) {
    if (inactiveRequest(e)) return;
    i.revert();
    toast(`Move failed: ${e.message}`);
  }
}
async function exportCal() {
  try {
    const d = await api("/api/calendar/export", { timeout: 15000 }),
      u = URL.createObjectURL(
        new Blob([JSON.stringify(d, null, 2)], { type: "application/json" }),
      ),
      a = n("a", {
        href: u,
        download: `homed-calendar-${isoDay(new Date())}.json`,
      });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(u), 1000);
    toast("Calendar exported.");
  } catch (e) {
    if (inactiveRequest(e)) return;
    toast(`Export failed: ${e.message}`);
  }
}
async function restore(ev) {
  const f = ev.target.files?.[0];
  ev.target.value = "";
  if (!f) return;
  let p;
  try {
    p = JSON.parse(await f.text());
    if (p.schema_version !== 1 || !Array.isArray(p.events))
      throw Error("This is not a schema version 1 homed calendar export.");
  } catch (e) {
    toast(`Could not read restore file: ${e.message}`);
    return;
  }
  confirm(
    "Merge calendar backup?",
    `This merges ${p.events.length} event record${p.events.length === 1 ? "" : "s"} into the current calendar. Existing events are not cleared first; homed resolves matching records using its restore rules.`,
    "Merge backup",
    async () => {
      const d = await api("/api/calendar/restore", {
        method: "POST",
        write: true,
        body: p,
        timeout: 30000,
      });
      S.calendar?.refetchEvents();
      await upcoming();
      toast(
        `Restored ${d.restored ?? 0} event record${d.restored === 1 ? "" : "s"}.`,
      );
    },
  );
}
$("#login-form").onsubmit = async (e) => {
  e.preventDefault();
  const b = $('button[type="submit"]', e.currentTarget);
  b.disabled = true;
  msg($("#login-error"), "");
  try {
    const s = await api("/api/login", {
      method: "POST",
      write: true,
      body: {
        username: $("#login-username").value,
        password: $("#login-password").value,
      },
    });
    if (!s.authenticated) throw Error("Sign in was not accepted.");
    $("#login-password").value = "";
    await enter(s);
  } catch (x) {
    msg($("#login-error"), x.message);
    $("#login-password").focus();
  } finally {
    b.disabled = false;
  }
};
$("#logout").onclick = $("#mobile-logout").onclick = async () => {
  try {
    await api("/api/logout", { method: "POST", write: true, body: {} });
  } catch (e) {
    toast(`Sign out failed: ${e.message}`);
    return;
  }
  S.generation++;
  S.draft = null;
  S.draftUser = null;
  S.session = null;
  clearPrivate();
  showLogin(false, false);
};
$$("[data-page]").forEach((b) => (b.onclick = () => nav(b.dataset.page)));
$$("[data-go]").forEach((b) => (b.onclick = () => nav(b.dataset.go)));
$("#refresh").onclick = refresh;
$("#service-search").oninput = (e) => {
  S.search = e.target.value.trim();
  renderServices();
};
$$("[data-close-drawer]").forEach((b) => (b.onclick = closeDrawer));
$("#new-event").onclick = () => openEvent();
$("#event-form").onsubmit = saveEvent;
$("#event-all-day").onchange = allDay;
$("#event-frequency").onchange = recur;
$$("[data-close-event]").forEach(
  (b) => (b.onclick = () => $("#event-dialog").close()),
);
$("#delete-event").onclick = removeEvent;
$("#export-calendar").onclick = exportCal;
$("#restore-calendar").onclick = () => $("#restore-file").click();
$("#restore-file").onchange = restore;
$("#confirm-cancel").onclick = () => $("#confirm-dialog").close();
onhashchange = () => nav(location.hash.slice(1));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#service-drawer").hidden) closeDrawer();
  if (e.key === "Tab" && !$("#service-drawer").hidden) {
    const f = $$(
      'button,a[href],input,select,textarea,[tabindex]:not([tabindex="-1"])',
      $(".drawer"),
    ).filter((x) => !x.disabled);
    if (e.shiftKey && document.activeElement === f[0]) {
      e.preventDefault();
      f.at(-1).focus();
    } else if (!e.shiftKey && document.activeElement === f.at(-1)) {
      e.preventDefault();
      f[0].focus();
    }
  }
});
setInterval(() => {
  if (S.session) {
    freshness();
    if (S.dashboard) {
      renderStats();
      renderServices();
      if (S.page === "storage") renderStorage();
    }
  }
}, 1000);
setInterval(() => {
  if (S.session) refresh();
}, 15000);
session();
