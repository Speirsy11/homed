"use strict";

// homed dashboard — read-only client. Pulls the same JSON the CLI emits and
// renders a scannable view. No mutation endpoints are ever called.

const state = {
  statuses: [],        // from /api/status
  registry: {},        // name -> service spec, from /api/registry
  doctor: { ok: true, issues: [] },
  meta: {},
  search: "",
  facets: { intent: new Set(), exposure: new Set(), driver: new Set() },
  attentionOnly: false,
};

const el = (sel) => document.querySelector(sel);

async function getJSON(path) {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  const body = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, body };
}

async function load() {
  const [meta, status, registry, doctor] = await Promise.all([
    getJSON("/api/meta"),
    getJSON("/api/status"),
    getJSON("/api/registry"),
    getJSON("/api/doctor"),
  ]);

  // A config problem surfaces on every endpoint; show it once and stop.
  const errBody = [status, registry, doctor].map((r) => r.body).find((b) => b && b.error);
  if (errBody) {
    showBanner(errBody);
    state.statuses = [];
    state.registry = {};
    state.doctor = { ok: true, issues: [] };
  } else {
    hideBanner();
    state.statuses = status.body.services || [];
    state.registry = (registry.body && registry.body.services) || {};
    state.doctor = doctor.body || { ok: true, issues: [] };
  }
  state.meta = meta.body || {};

  el("#config-path").textContent = state.meta.config_path || "";
  el("#config-path").title = state.meta.config_path || "";
  el("#version").textContent = state.meta.version ? `homed ${state.meta.version}` : "";
  el("#updated").textContent = new Date().toLocaleTimeString();

  renderSummary();
  renderFilters();
  renderDoctor();
  renderServices();
}

function classify(s) {
  if (s.ok) return "ok";
  if (s.health_state === "degraded") return "warn";
  return "bad";
}

// --- summary ---------------------------------------------------------------

function renderSummary() {
  const total = state.statuses.length;
  const ok = state.statuses.filter((s) => s.ok).length;
  const attention = total - ok;
  const errors = state.doctor.issues.filter((i) => i.level === "error").length;
  const warnings = state.doctor.issues.filter((i) => i.level === "warning").length;

  const stats = [
    { lbl: "Services", num: total, cls: "" },
    { lbl: "Healthy", num: ok, cls: "ok" },
    { lbl: "Attention", num: attention, cls: attention ? "attention" : "", toggle: true },
    { lbl: "Doctor", num: errors + warnings, cls: errors ? "attention" : warnings ? "warn" : "" },
  ];

  el("#summary").innerHTML = "";
  for (const st of stats) {
    const node = document.createElement("div");
    node.className = `stat ${st.cls}` + (st.toggle && state.attentionOnly ? " active" : "");
    node.innerHTML = `<span class="num">${st.num}</span><span class="lbl">${st.lbl}</span>`;
    if (st.toggle) {
      node.addEventListener("click", () => {
        state.attentionOnly = !state.attentionOnly;
        renderSummary();
        renderServices();
      });
    }
    el("#summary").appendChild(node);
  }
}

// --- filters ---------------------------------------------------------------

function facetValues(key) {
  const vals = new Set();
  for (const s of state.statuses) if (s[key]) vals.add(s[key]);
  return [...vals].sort();
}

function renderFilters() {
  const groups = [
    ["intent", facetValues("intent")],
    ["exposure", facetValues("exposure")],
    ["driver", facetValues("driver")],
  ];
  const box = el("#filters");
  box.innerHTML = "";
  for (const [key, values] of groups) {
    for (const value of values) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip" + (state.facets[key].has(value) ? " active" : "");
      chip.textContent = value;
      chip.title = key;
      chip.addEventListener("click", () => {
        const set = state.facets[key];
        set.has(value) ? set.delete(value) : set.add(value);
        renderFilters();
        renderServices();
      });
      box.appendChild(chip);
    }
  }
}

function matches(s) {
  if (state.attentionOnly && s.ok) return false;
  for (const key of ["intent", "exposure", "driver"]) {
    const set = state.facets[key];
    if (set.size && !set.has(s[key])) return false;
  }
  if (state.search) {
    const spec = state.registry[s.name] || {};
    const hay = `${s.name} ${spec.description || ""} ${(spec.tags || []).join(" ")}`.toLowerCase();
    if (!hay.includes(state.search)) return false;
  }
  return true;
}

// --- services --------------------------------------------------------------

function renderServices() {
  const box = el("#services");
  box.innerHTML = "";
  const visible = state.statuses.filter(matches);
  if (!visible.length) {
    box.innerHTML = `<div class="empty">${state.statuses.length ? "No services match the current filters." : "No services declared."}</div>`;
    return;
  }
  for (const s of visible) {
    const spec = state.registry[s.name] || {};
    const card = document.createElement("div");
    card.className = `svc ${classify(s)}`;
    card.innerHTML = `
      <div class="svc-head">
        <span class="svc-name"></span>
        <span class="svc-driver"></span>
      </div>
      ${spec.description ? `<div class="svc-desc"></div>` : ""}
      <div class="svc-badges">
        <span class="badge s-${s.manager_state}">${s.manager_state}</span>
        <span class="badge h-${s.health_state}">${s.health_state}</span>
        <span class="badge muted">${s.intent}</span>
        <span class="badge muted">${s.exposure}</span>
      </div>`;
    card.querySelector(".svc-name").textContent = s.name;
    card.querySelector(".svc-driver").textContent = s.driver;
    if (spec.description) card.querySelector(".svc-desc").textContent = spec.description;
    card.addEventListener("click", () => openDrawer(s.name));
    box.appendChild(card);
  }
}

// --- doctor ----------------------------------------------------------------

function renderDoctor() {
  const box = el("#doctor");
  const issues = state.doctor.issues || [];
  if (!issues.length) {
    box.innerHTML = `<div class="doctor-clean">✓ No issues found.</div>`;
    return;
  }
  box.innerHTML = "";
  const order = { error: 0, warning: 1 };
  issues.sort((a, b) => (order[a.level] ?? 9) - (order[b.level] ?? 9));
  for (const issue of issues) {
    const node = document.createElement("div");
    node.className = `issue ${issue.level}`;
    node.innerHTML = `<span class="lvl">${issue.level}</span><div><div class="where"></div><div class="msg"></div></div>`;
    node.querySelector(".where").textContent = issue.where;
    node.querySelector(".msg").textContent = issue.message;
    box.appendChild(node);
  }
}

// --- detail drawer ---------------------------------------------------------

function row(dt, dd) {
  return `<dt>${dt}</dt><dd>${escapeHTML(String(dd))}</dd>`;
}

function escapeHTML(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function openDrawer(name) {
  const s = state.statuses.find((x) => x.name === name);
  const spec = state.registry[name] || {};
  if (!s) return;
  const health = spec.health || { kind: "none" };
  const deps = [];
  if ((spec.requires || []).length) deps.push(["requires", spec.requires.join(", ")]);
  if ((spec.after || []).length) deps.push(["after", spec.after.join(", ")]);
  if ((spec.conflicts || []).length) deps.push(["conflicts", spec.conflicts.join(", ")]);
  if (spec.mode_group) deps.push(["mode group", spec.mode_group]);

  let html = `
    <h3>${escapeHTML(name)}</h3>
    <div class="sub">${escapeHTML(s.driver)} · ${escapeHTML(s.intent)} · ${escapeHTML(s.exposure)}</div>`;
  if (spec.description) html += `<div>${escapeHTML(spec.description)}</div>`;

  html += `<div class="section"><h4>State</h4><dl class="kv">
    ${row("manager", s.manager_state)}
    ${row("health", s.health_state)}
    ${row("ok", s.ok ? "yes" : "no")}
    ${s.detail ? row("detail", s.detail) : ""}
  </dl></div>`;

  html += `<div class="section"><h4>Health check</h4><dl class="kv">${healthRows(health)}</dl></div>`;

  if (Object.keys(spec.options || {}).length) {
    html += `<div class="section"><h4>Options</h4><dl class="kv">`;
    for (const [k, v] of Object.entries(spec.options)) html += row(k, fmt(v));
    html += `</dl></div>`;
  }

  if (deps.length) {
    html += `<div class="section"><h4>Dependencies</h4><dl class="kv">`;
    for (const [k, v] of deps) html += row(k, v);
    html += `</dl></div>`;
  }

  if ((spec.tags || []).length) {
    html += `<div class="section"><h4>Tags</h4><div class="taglist">${spec.tags.map((t) => `<span class="tag">${escapeHTML(t)}</span>`).join("")}</div></div>`;
  }

  if (spec.logs && spec.logs.path) {
    html += `<div class="section"><h4>Logs</h4><div class="codeblock">${escapeHTML(String(spec.logs.path))}</div></div>`;
  }

  el("#drawer-body").innerHTML = html;
  el("#drawer").hidden = false;
}

function healthRows(health) {
  const rows = [row("kind", health.kind)];
  for (const k of ["url", "expect_status", "host", "port", "path", "max_age_seconds", "timeout_seconds"]) {
    if (health[k] !== undefined && health[k] !== null) rows.push(row(k, fmt(health[k])));
  }
  return rows.join("");
}

function fmt(v) {
  return Array.isArray(v) ? v.join(" ") : typeof v === "object" ? JSON.stringify(v) : v;
}

function closeDrawer() {
  el("#drawer").hidden = true;
}

// --- wiring ----------------------------------------------------------------

function showBanner(err) {
  const banner = el("#banner");
  const kind = err.error_kind === "config_not_found" ? "No config found" : "Config error";
  banner.innerHTML = `<strong>${kind}.</strong> ${escapeHTML(err.error || "")}` +
    (err.config_path ? ` <code>${escapeHTML(err.config_path)}</code>` : "") +
    (err.error_kind === "config_not_found" ? ` — run <code>homed init</code> to scaffold one.` : "");
  banner.hidden = false;
}
function hideBanner() { el("#banner").hidden = true; }

el("#refresh").addEventListener("click", load);
el("#search").addEventListener("input", (e) => {
  state.search = e.target.value.trim().toLowerCase();
  renderServices();
});
document.querySelectorAll("[data-close]").forEach((n) => n.addEventListener("click", closeDrawer));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeDrawer();
  if (e.key === "r" && !/input|textarea/i.test(document.activeElement.tagName)) load();
});

load();
