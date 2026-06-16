/*
 * Vanilla JS controller for the Phase 1 UI.
 *
 * One file, no dependencies, no build. When Phase 1.5 lands the React
 * bundle, this whole file is replaced — but the contract with the
 * backend (the /api/* endpoints) stays exactly the same, so the
 * server.py never has to change.
 *
 * Pattern: a tiny "store" object holds form state; render() reads from
 * the store and updates the DOM. We don't do incremental DOM diffing
 * (we just set .value), but we DO keep all state mutations going through
 * the store so the React migration is mechanical.
 */

(function () {
  "use strict";

  // ----- DOM handles -------------------------------------------------------
  const $ = (sel) => document.querySelector(sel);
  const els = {
    health: $("#health"),
    envPathDisplay: $("#env-path-display"),
    banner: $("#banner-noncompliant"),
    bannerKeys: $("#noncompliant-keys"),
    selectVertical: $("#select-vertical"),
    selectOverlay: $("#select-overlay"),
    productsGrid: $("#products-grid"),
    selectSite: $("#select-site"),
    inputApiKey: $("#input-api-key"),
    inputAppKey: $("#input-app-key"),
    inputDisplayName: $("#input-display-name"),
    inputEmitInterval: $("#input-emit-interval"),
    inputOtelEndpoint: $("#input-otel-endpoint"),
    selectOtelProtocol: $("#select-otel-protocol"),
    btnTest: $("#btn-test"),
    btnSave: $("#btn-save"),
    testResult: $("#test-result"),
    saveResult: $("#save-result"),
  };

  // Sentinel used by the backend's env_manager. Keep in sync.
  const KEEP_EXISTING = "__DD_DEMO_UI_KEEP_EXISTING__";

  // Store: what we know about the world. The .env values here are the
  // *masked* versions returned by GET /api/env — never the cleartext.
  // (Note: op:// references are NOT masked server-side; they're not
  // secrets and the user needs to see them to edit.)
  const store = {
    verticals: [],            // [{name, display_name, overlays: [...]}]
    products: [],             // catalog from /api/products: [{key,label,group,description,default,drives_flag?}]
    sites: [],
    env: {},                  // masked plain values; references shown verbatim
    nonCompliant: [],         // SECRET_KEYS still holding plain values on disk
    apiKeyDirty: false,       // true once the user types in the API key field
    appKeyDirty: false,
    activeTab: "configure",   // configure | simulator | deploy
    // Per-process status snapshots (refreshed via polling + status updates
    // returned from start/stop). Keyed by logical name.
    processes: {
      "simulator":    { state: "idle", pid: null, started_at: null, uptime_seconds: null, exit_code: null, last_error: null },
      "setup":        { state: "idle", pid: null, started_at: null, uptime_seconds: null, exit_code: null, last_error: null },
      "teardown":     { state: "idle", pid: null, started_at: null, uptime_seconds: null, exit_code: null, last_error: null },
      "teardown-all": { state: "idle", pid: null, started_at: null, uptime_seconds: null, exit_code: null, last_error: null },
    },
    // Active EventSource by process name (so we can close on tab switch /
    // restart and avoid leaks). One per process.
    logStreams: {},
    // Which process the Deploy tab's log pane is currently showing (most
    // recently started among setup/teardown/teardown-all).
    deployLogSource: null,
    // Status tab: container + Datadog resource state.
    status: {
      containers: { data: null, error: null, loading: false, ts: null },
      dd: { data: null, error: null, loading: false, ts: null },
    },
    // Interval ID for status auto-refresh (cleared when leaving the tab).
    statusTimer: null,
  };

  // ----- HTTP helpers ------------------------------------------------------
  async function api(path, init) {
    const r = await fetch(path, init);
    const ct = r.headers.get("content-type") || "";
    const body = ct.includes("json") ? await r.json() : await r.text();
    if (!r.ok) {
      const msg = (body && body.detail) || `HTTP ${r.status}`;
      throw new Error(msg);
    }
    return body;
  }

  async function getJSON(path) { return api(path); }
  async function postJSON(path, payload) {
    return api(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  // ----- Renderers ---------------------------------------------------------
  function renderVerticals() {
    els.selectVertical.innerHTML = "";
    for (const v of store.verticals) {
      const opt = document.createElement("option");
      opt.value = v.name;
      opt.textContent = `${v.display_name} (${v.name})`;
      els.selectVertical.appendChild(opt);
    }
    // Restore selection from env, falling back to first.
    const fromEnv = store.env.DD_DEMO_VERTICAL;
    if (fromEnv && store.verticals.some((v) => v.name === fromEnv)) {
      els.selectVertical.value = fromEnv;
    }
    renderOverlays();
  }

  function renderOverlays() {
    const chosen = els.selectVertical.value;
    const vertical = store.verticals.find((v) => v.name === chosen);
    els.selectOverlay.innerHTML = "";
    const none = document.createElement("option");
    none.value = "";
    none.textContent = "— none —";
    els.selectOverlay.appendChild(none);
    if (!vertical) return;
    for (const name of vertical.overlays) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      els.selectOverlay.appendChild(opt);
    }
    // Restore from env if still applicable to the chosen vertical.
    const fromEnv = store.env.DD_DEMO_SUB_VERTICAL || "";
    if (fromEnv && vertical.overlays.includes(fromEnv)) {
      els.selectOverlay.value = fromEnv;
    }
  }

  // Which product keys should be checked: the saved DD_DEMO_PRODUCTS list
  // if present, else the catalog's defaults (first-run convenience). Only
  // available products are ever checked — an unavailable SKU can't be
  // demonstrated, so even a stale .env entry for one is ignored.
  function selectedProductKeys() {
    const available = new Set(
      store.products.filter((p) => p.available !== false).map((p) => p.key)
    );
    const raw = (store.env.DD_DEMO_PRODUCTS || "").trim();
    if (raw) {
      return new Set(
        raw.split(",").map((s) => s.trim()).filter((k) => k && available.has(k))
      );
    }
    return new Set(
      store.products.filter((p) => p.default && p.available !== false).map((p) => p.key)
    );
  }

  function renderProducts() {
    const grid = els.productsGrid;
    if (!grid) return;
    grid.innerHTML = "";
    if (!store.products.length) {
      grid.innerHTML = '<p class="muted">No product catalog available.</p>';
      return;
    }
    const checked = selectedProductKeys();

    // Group preserving catalog order of first appearance.
    const groups = [];
    const byGroup = {};
    for (const p of store.products) {
      const g = p.group || "Other";
      if (!byGroup[g]) { byGroup[g] = []; groups.push(g); }
      byGroup[g].push(p);
    }

    for (const g of groups) {
      const groupEl = document.createElement("div");
      groupEl.className = "product-group";
      const h = document.createElement("h3");
      h.className = "product-group-title muted";
      h.textContent = g;
      groupEl.appendChild(h);

      for (const p of byGroup[g]) {
        const available = p.available !== false;
        const label = document.createElement("label");
        label.className = "checkbox product-item" + (available ? "" : " unavailable");
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.value = p.key;
        cb.dataset.product = p.key;
        cb.checked = available && checked.has(p.key);
        cb.disabled = !available;
        label.appendChild(cb);
        const text = document.createElement("span");
        text.className = "product-text";
        const flagNote = available && p.drives_flag ? ` (sets ${p.drives_flag})` : "";
        const availNote = available ? "" : ' <em class="product-unavailable">— not yet available</em>';
        text.innerHTML =
          `<strong>${p.label}</strong>${flagNote}${availNote}` +
          (p.description ? `<br><span class="muted">${p.description}</span>` : "");
        label.appendChild(text);
        groupEl.appendChild(label);
      }
      grid.appendChild(groupEl);
    }
  }

  // Read the currently-checked product keys from the DOM.
  function checkedProductKeys() {
    return Array.from(
      els.productsGrid.querySelectorAll('input[type="checkbox"][data-product]:checked')
    ).map((cb) => cb.value);
  }

  function renderSites() {
    els.selectSite.innerHTML = "";
    for (const s of store.sites) {
      const opt = document.createElement("option");
      opt.value = s;
      opt.textContent = s;
      els.selectSite.appendChild(opt);
    }
    if (store.env.DD_SITE && store.sites.includes(store.env.DD_SITE)) {
      els.selectSite.value = store.env.DD_SITE;
    }
  }

  function renderEnvFields() {
    // For op:// references the server returns the literal value (not
    // masked). For plain secrets (transitional, pre-migration) the server
    // returns "*****abcd". Either way we drop it into the input as-is.
    els.inputApiKey.value = store.env.DD_API_KEY || "";
    els.inputAppKey.value = store.env.DD_APP_KEY || "";
    els.inputDisplayName.value = store.env.DISPLAY_NAME || "";
    els.inputEmitInterval.value = store.env.EMIT_INTERVAL || "";
    els.inputOtelEndpoint.value = store.env.OTEL_EXPORTER_OTLP_ENDPOINT || "";
    els.selectOtelProtocol.value = store.env.OTEL_EXPORTER_OTLP_PROTOCOL || "";
    store.apiKeyDirty = false;
    store.appKeyDirty = false;
  }

  function renderBanner() {
    if (store.nonCompliant.length === 0) {
      els.banner.hidden = true;
      return;
    }
    els.banner.hidden = false;
    els.bannerKeys.textContent = store.nonCompliant.join(", ");
  }

  function setResult(el, msg, kind /* "ok" | "err" | "warn" | "" */) {
    el.textContent = msg || "";
    el.className = "muted"; // reset
    if (kind) el.classList.add(`result-${kind}`);
  }

  // ----- Save / test handlers ---------------------------------------------
  function collectEnvPayload() {
    // Build the POST /api/env body. Empty strings on optional fields
    // are sent as "" (the backend's MANAGED_KEYS guard still accepts
    // them — they clear the value). Secrets get KEEP_EXISTING unless
    // the user actually typed something new.
    const products = checkedProductKeys();
    const payload = {
      DD_SITE: els.selectSite.value,
      DD_DEMO_VERTICAL: els.selectVertical.value,
      DD_DEMO_SUB_VERTICAL: els.selectOverlay.value,
      DD_DEMO_PRODUCTS: products.join(","),
      // Database Monitoring is the one product with a real container toggle.
      // Derive DD_DEMO_DBM from the selection so the picker actually drives
      // the stack `make up` starts.
      DD_DEMO_DBM: products.includes("dbm") ? "true" : "false",
      DISPLAY_NAME: els.inputDisplayName.value,
      EMIT_INTERVAL: els.inputEmitInterval.value,
      OTEL_EXPORTER_OTLP_ENDPOINT: els.inputOtelEndpoint.value,
      OTEL_EXPORTER_OTLP_PROTOCOL: els.selectOtelProtocol.value,
      DD_API_KEY: store.apiKeyDirty ? els.inputApiKey.value : KEEP_EXISTING,
      DD_APP_KEY: store.appKeyDirty ? els.inputAppKey.value : KEEP_EXISTING,
    };
    return payload;
  }

  async function onSave() {
    setResult(els.saveResult, "saving…", "");
    els.btnSave.disabled = true;
    try {
      const body = await postJSON("/api/env", collectEnvPayload());
      store.env = body.values || {};
      store.nonCompliant = body.non_compliant_secret_keys || [];
      renderEnvFields();
      renderProducts();
      renderBanner();
      setResult(els.saveResult, "saved to .env", "ok");
    } catch (e) {
      // PlainSecretRejected returns 400 with a long message — show the
      // first sentence inline; full message stays available via console.
      const first = (e.message || "").split(".")[0] + ".";
      console.error("save failed:", e.message);
      setResult(els.saveResult, first, "err");
    } finally {
      els.btnSave.disabled = false;
    }
  }

  async function onTest() {
    setResult(els.testResult, "testing…", "");
    els.btnTest.disabled = true;
    try {
      const payload = {
        DD_SITE: els.selectSite.value,
        DD_API_KEY: store.apiKeyDirty ? els.inputApiKey.value : KEEP_EXISTING,
        DD_APP_KEY: store.appKeyDirty ? els.inputAppKey.value : KEEP_EXISTING,
      };
      const r = await postJSON("/api/env/test", payload);
      if (r.ok) {
        setResult(els.testResult, "✓ both keys valid", "ok");
      } else if (r.api_key_ok && !r.app_key_ok) {
        // Common failure mode: user pasted the API key into both fields.
        setResult(els.testResult, r.error || "APP key invalid", "warn");
      } else {
        setResult(els.testResult, r.error || "validation failed", "err");
      }
    } catch (e) {
      setResult(els.testResult, e.message, "err");
    } finally {
      els.btnTest.disabled = false;
    }
  }

  // ============================================================================
  // Tabs
  // ============================================================================

  function switchTab(name) {
    store.activeTab = name;
    document.querySelectorAll(".tab").forEach((b) => {
      b.classList.toggle("active", b.dataset.tab === name);
    });
    document.querySelectorAll(".tab-panel").forEach((p) => {
      p.hidden = p.dataset.tabPanel !== name;
    });
    // Refresh deploy summary when we land on that tab.
    if (name === "deploy") {
      renderDeploySummary();
    }
    // Status tab: refresh immediately and start auto-refresh timer.
    if (name === "status") {
      refreshStatusTab();  // fire-and-forget; button shows loading state
      if (!store.statusTimer) {
        store.statusTimer = setInterval(() => refreshStatusTab(), 15_000);
      }
    } else if (store.statusTimer) {
      clearInterval(store.statusTimer);
      store.statusTimer = null;
    }
  }

  // ============================================================================
  // Process control: shared start/stop/status for simulator + deploy tabs.
  // ============================================================================

  // Map logical process → which log pane to write its lines into.
  // Simulator gets its own pane; setup/teardown/teardown-all share the
  // deploy pane (whichever was most recently started).
  function paneForProcess(name) {
    if (name === "simulator") {
      return document.querySelector('[data-log-pane="simulator"]');
    }
    return document.querySelector('[data-log-pane="deploy"]');
  }
  function autoscrollCheckboxFor(name) {
    if (name === "simulator") return document.querySelector("#sim-autoscroll");
    return document.querySelector("#deploy-autoscroll");
  }
  function logSourceLabelFor(name) {
    // Returns the .log-source span next to whichever pane this process owns.
    const pane = paneForProcess(name);
    return pane.closest(".card-log").querySelector(".log-source");
  }

  function renderProcessRow(name) {
    const row = document.querySelector(`.process-row[data-process="${name}"]`);
    if (!row) return;
    const s = store.processes[name];
    const pill = row.querySelector(".status-pill");
    const uptimeEl = row.querySelector(".process-uptime");
    const startBtn = row.querySelector('[data-action="start"]');
    const stopBtn = row.querySelector('[data-action="stop"]');

    // Pill: special "exited-error" state if the last run failed.
    let pillState = s.state;
    if (s.state === "exited" && s.exit_code != null && s.exit_code !== 0) {
      pillState = "exited-error";
    }
    pill.dataset.state = pillState;
    pill.textContent = s.state === "exited" && s.exit_code != null
      ? `exited ${s.exit_code}`
      : s.state;

    // Uptime / last-error annotation.
    if (s.state === "running" || s.state === "stopping") {
      uptimeEl.textContent = formatDuration(s.uptime_seconds);
    } else if (s.state === "exited") {
      uptimeEl.textContent = s.last_error ? `(${s.last_error})` : "";
    } else {
      uptimeEl.textContent = "";
    }

    // Button enablement reflects state — UI shouldn't let you click
    // "Start" on a running process or "Stop" on an idle one. Backend
    // enforces the same with 409s as the backstop.
    startBtn.disabled = (s.state === "running" || s.state === "stopping");
    stopBtn.disabled = !(s.state === "running" || s.state === "stopping");
  }

  function renderAllProcessRows() {
    Object.keys(store.processes).forEach(renderProcessRow);
  }

  function formatDuration(seconds) {
    if (seconds == null) return "";
    const s = Math.floor(seconds);
    const m = Math.floor(s / 60);
    const rs = s % 60;
    if (m === 0) return `${rs}s`;
    const h = Math.floor(m / 60);
    const rm = m % 60;
    if (h === 0) return `${m}m ${rs}s`;
    return `${h}h ${rm}m ${rs}s`;
  }

  // Per-line classification for the log pane. Keep it cheap; called on
  // every line, possibly hundreds per second from compose during demo
  // chaos.
  //
  // The classifier looks for log-level TOKENS, not free-text substrings.
  // A line that contains the word "error" as part of a sentence (e.g.
  // "Transient error encountered while ... retrying") is NOT classified
  // as an error — the log-level is the WARNING that precedes the message,
  // not the noun "error" inside it. Word-bounded ALL CAPS (Python's
  // standard logging format) and tab-delimited lowercase (OTel collector
  // format) are the two we trust.
  //
  // Python `logging` conventions (used by the simulator):
  //     2026-05-14 19:48 - service.foo - ERROR   - <message>
  //     2026-05-14 19:48 - service.foo - WARNING - <message containing "error">
  //
  // OTel collector zap format:
  //     2026-05-14T19:48:42Z\terror\tprovider/provider.go\t<message>
  //     2026-05-14T19:48:42Z\twarn\tagentcomponents/zaplogger.go\t<message>
  //
  // status=5XX is a strong signal of a real server-side error even when
  // the surrounding line is at INFO level (simulator's chaos injections
  // log INFO lines that include status=500). Same for 4XX → warn.
  function classifyLine(line) {
    if (/\bERROR\b/.test(line)) return "err";
    if (/\bWARN(ING)?\b/.test(line)) return "warn";
    if (/\terror\b/.test(line)) return "err";
    if (/\twarn(ing)?\b/.test(line)) return "warn";
    if (/\bstatus=5\d\d\b/.test(line)) return "err";
    if (/\bstatus=4\d\d\b/.test(line)) return "warn";
    return "";
  }

  function appendLogLine(pane, line) {
    const div = document.createElement("div");
    div.className = "log-line";
    const cls = classifyLine(line);
    if (cls) div.classList.add(cls);
    div.textContent = line;
    pane.appendChild(div);

    // Cap the pane at ~5000 lines to match the server-side buffer; older
    // lines fall off the top. Keeps the DOM from getting huge during long
    // simulator runs.
    while (pane.children.length > 5000) pane.removeChild(pane.firstChild);
  }

  function maybeAutoscroll(pane, name) {
    const checkbox = autoscrollCheckboxFor(name);
    if (checkbox && checkbox.checked) {
      pane.scrollTop = pane.scrollHeight;
    }
  }

  // Open an EventSource for the named process; route lines to the right
  // pane; auto-close when the server emits `event: end`.
  function startLogStream(name) {
    // Close any existing stream first (e.g. user clicked Start twice
    // very quickly).
    stopLogStream(name);

    const pane = paneForProcess(name);
    const sourceLabel = logSourceLabelFor(name);
    sourceLabel.textContent = name;

    const es = new EventSource(`/api/processes/${name}/logs`);
    store.logStreams[name] = es;

    es.onmessage = (e) => {
      // The server JSON-escapes embedded newlines/quotes; unescape.
      const line = e.data
        .replace(/\\n/g, "\n")
        .replace(/\\r/g, "")
        .replace(/\\\\/g, "\\");
      appendLogLine(pane, line);
      maybeAutoscroll(pane, name);
    };

    es.addEventListener("end", () => {
      stopLogStream(name);
      // Refresh status now that the process exited.
      refreshProcess(name);
    });

    es.onerror = () => {
      // EventSource will normally auto-reconnect on transport error.
      // For our purposes (the server cleanly closes on EOF) any error
      // post-EOF is harmless; we just close. Pre-EOF errors mean network
      // is broken; we close so the user can manually retry.
      stopLogStream(name);
    };
  }

  function stopLogStream(name) {
    const es = store.logStreams[name];
    if (es) {
      es.close();
      delete store.logStreams[name];
    }
  }

  function clearLogPane(target) {
    // target = "simulator" | "deploy"
    const pane = document.querySelector(`[data-log-pane="${target}"]`);
    if (pane) pane.innerHTML = "";
  }

  async function refreshProcess(name) {
    try {
      const s = await getJSON(`/api/processes/${name}/status`);
      store.processes[name] = s;
      renderProcessRow(name);
    } catch (e) {
      console.error("status refresh failed for", name, e);
    }
  }

  async function refreshAllProcesses() {
    try {
      const list = await getJSON("/api/processes");
      for (const s of list) store.processes[s.name] = s;
      renderAllProcessRows();
    } catch (e) {
      // 503 (supervisor disabled) is expected in some test configs; don't
      // shout in those cases.
      if (!/503/.test(e.message)) {
        console.error("processes refresh failed", e);
      }
    }
  }

  async function onProcessStart(name) {
    try {
      const s = await postJSON(`/api/processes/${name}/start`, {});
      store.processes[name] = s;
      renderProcessRow(name);
      // Clear the pane so users don't see stale output mixed with new.
      const pane = paneForProcess(name);
      pane.innerHTML = "";
      startLogStream(name);
      // For deploy-tab processes, mark this as the source the pane is showing.
      if (name !== "simulator") store.deployLogSource = name;
    } catch (e) {
      alert(`Failed to start ${name}:\n\n${e.message}`);
    }
  }

  async function onProcessStop(name) {
    try {
      const s = await postJSON(`/api/processes/${name}/stop`, {});
      store.processes[name] = s;
      renderProcessRow(name);
    } catch (e) {
      alert(`Failed to stop ${name}:\n\n${e.message}`);
    }
  }

  // ============================================================================
  // Deploy summary (read-only view of which vertical/overlay actions apply to)
  // ============================================================================

  function renderDeploySummary() {
    const v = document.querySelector("#deploy-vertical");
    const o = document.querySelector("#deploy-overlay");
    if (v) v.textContent = store.env.DD_DEMO_VERTICAL || "(unset)";
    if (o) o.textContent = store.env.DD_DEMO_SUB_VERTICAL || "(none)";
  }

  // ============================================================================
  // Status tab: containers + Datadog resources
  // ============================================================================

  function escapeHTML(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function formatAge(ts) {
    if (!ts) return "";
    const s = Math.floor((Date.now() - ts) / 1000);
    if (s < 5) return "just now";
    if (s < 60) return `${s}s ago`;
    return `${Math.floor(s / 60)}m ago`;
  }

  function renderContainers() {
    const el = document.getElementById("containers-content");
    const tsEl = document.getElementById("containers-ts");
    const { data, error, loading, ts } = store.status.containers;
    if (tsEl) tsEl.textContent = loading ? "refreshing…" : ts ? `updated ${formatAge(ts)}` : "";
    if (!el) return;

    if (loading && !data) { el.innerHTML = '<p class="muted">Loading…</p>'; return; }
    if (error && !data)   { el.innerHTML = `<p class="result-err">${escapeHTML(error)}</p>`; return; }
    if (!data || data.length === 0) {
      el.innerHTML = '<p class="muted">No containers running.</p>';
      return;
    }

    const rows = data.map((c) => {
      // Map docker state strings to the pill data-state values the CSS already knows.
      const stateAttr = c.state === "running" ? "running"
                      : c.state === "exited"  ? "exited"
                      : "idle";
      return `<tr>
        <td><code>${escapeHTML(c.service)}</code></td>
        <td class="muted">${escapeHTML(c.name)}</td>
        <td><span class="status-pill" data-state="${stateAttr}">${escapeHTML(c.state)}</span></td>
        <td class="muted">${escapeHTML(c.health || "—")}</td>
      </tr>`;
    }).join("");

    el.innerHTML = `<table class="status-table">
      <thead><tr><th>Service</th><th>Container</th><th>State</th><th>Health</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  }

  function renderDDResources() {
    const el = document.getElementById("dd-resources-content");
    const tsEl = document.getElementById("dd-resources-ts");
    const descEl = document.getElementById("dd-resources-desc");
    const { data, error, loading, ts } = store.status.dd;
    if (tsEl) tsEl.textContent = loading ? "refreshing…" : ts ? `updated ${formatAge(ts)}` : "";
    if (!el) return;

    if (loading && !data) { el.innerHTML = '<p class="muted">Loading…</p>'; return; }
    if (error && !data)   { el.innerHTML = `<p class="result-err">${escapeHTML(error)}</p>`; return; }
    if (!data) { el.innerHTML = '<p class="muted">Waiting for data…</p>'; return; }

    // Update the description subtitle to show which vertical is being counted.
    if (descEl) {
      descEl.textContent = data.vertical
        ? `Resources for vertical: ${data.vertical} — currently deployed in your Datadog org.`
        : "Toolkit-managed resources currently deployed in your Datadog org.";
    }

    const RESOURCE_TYPES = ["monitors", "dashboards", "notebooks", "slos", "workflows"];
    const badges = RESOURCE_TYPES.map((r) => {
      const count = data[r];
      const cls   = count == null ? "none" : count > 0 ? "active" : "none";
      const label = count == null ? "—" : String(count);
      return `<div class="resource-badge">
        <span class="resource-count ${cls}">${label}</span>
        <span class="resource-label">${r}</span>
      </div>`;
    }).join("");

    const warn = data.error
      ? `<p class="result-warn" style="margin-top:12px;font-size:12px;">⚠ ${escapeHTML(data.error)}</p>`
      : "";
    el.innerHTML = `<div class="resource-grid">${badges}</div>${warn}`;
  }

  async function refreshContainers() {
    store.status.containers.loading = true;
    store.status.containers.ts = Date.now();  // stamp immediately so user sees activity
    renderContainers();
    try {
      const r = await getJSON("/api/status/containers");
      store.status.containers.data  = r.containers || [];
      store.status.containers.error = r.error || null;
      store.status.containers.ts    = Date.now();
    } catch (e) {
      store.status.containers.error = e.message;
    } finally {
      store.status.containers.loading = false;
    }
    renderContainers();
  }

  async function refreshDDResources() {
    store.status.dd.loading = true;
    store.status.dd.ts = Date.now();
    renderDDResources();
    try {
      const r = await getJSON("/api/status/datadog");
      store.status.dd.data  = r;
      store.status.dd.error = r.error || null;
      store.status.dd.ts    = Date.now();
    } catch (e) {
      store.status.dd.error = e.message;
    } finally {
      store.status.dd.loading = false;
    }
    renderDDResources();
  }

  async function refreshStatusTab() {
    const btn = document.getElementById("btn-refresh-status");
    if (btn) { btn.disabled = true; btn.textContent = "Refreshing…"; }
    try {
      await Promise.all([refreshContainers(), refreshDDResources()]);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "Refresh"; }
    }
  }

  // ----- Wire-up & init ----------------------------------------------------
  function wire() {
    els.selectVertical.addEventListener("change", renderOverlays);
    els.inputApiKey.addEventListener("input", () => { store.apiKeyDirty = true; });
    els.inputAppKey.addEventListener("input", () => { store.appKeyDirty = true; });
    els.btnSave.addEventListener("click", onSave);
    els.btnTest.addEventListener("click", onTest);

    // Tabs.
    document.querySelectorAll(".tab").forEach((btn) => {
      btn.addEventListener("click", () => switchTab(btn.dataset.tab));
    });

    // Process start/stop buttons. Delegate from each process-row.
    document.querySelectorAll(".process-row").forEach((row) => {
      const name = row.dataset.process;
      row.querySelector('[data-action="start"]').addEventListener("click", async () => {
        // Some buttons (teardown-all) want a typed confirmation before firing.
        const btn = row.querySelector('[data-action="start"]');
        const confirmMsg = btn.dataset.confirm;
        if (confirmMsg) {
          const answer = window.prompt(confirmMsg, "");
          if ((answer || "").toLowerCase() !== "yes") return;
        }
        await onProcessStart(name);
      });
      row.querySelector('[data-action="stop"]').addEventListener("click", () => onProcessStop(name));
    });

    // "Clear" buttons in log toolbars.
    document.querySelectorAll('[data-log-action="clear"]').forEach((b) => {
      b.addEventListener("click", () => clearLogPane(b.dataset.logTarget));
    });

    // Status tab refresh button.
    const btnRefresh = document.getElementById("btn-refresh-status");
    if (btnRefresh) {
      btnRefresh.addEventListener("click", () => refreshStatusTab());
    }

    // Periodic status refresh while any process is running. 2s cadence
    // is plenty for uptime display; live state changes still flow via
    // start/stop responses and the SSE `end` event.
    setInterval(() => {
      const anyRunning = Object.values(store.processes).some(
        (p) => p.state === "running" || p.state === "stopping"
      );
      if (anyRunning) refreshAllProcesses();
    }, 2000);
  }

  async function init() {
    wire();

    // Health check first; failure here means the rest is doomed.
    try {
      const h = await getJSON("/api/health");
      els.health.textContent = `v${h.version} · ${h.env_exists ? ".env loaded" : "no .env"}`;
      els.health.dataset.state = "ok";
      els.envPathDisplay.textContent = h.env_path;
    } catch (e) {
      els.health.textContent = `error: ${e.message}`;
      els.health.dataset.state = "err";
      return;
    }

    // Load reference data in parallel.
    try {
      const [verticals, sites, env, products] = await Promise.all([
        getJSON("/api/verticals"),
        getJSON("/api/sites"),
        getJSON("/api/env"),
        getJSON("/api/products"),
      ]);
      store.verticals = verticals;
      store.sites = sites;
      store.env = env.values || {};
      store.nonCompliant = env.non_compliant_secret_keys || [];
      store.products = products || [];
    } catch (e) {
      els.health.textContent = `load error: ${e.message}`;
      els.health.dataset.state = "err";
      return;
    }

    renderSites();
    renderVerticals();
    renderProducts();
    renderEnvFields();
    renderBanner();
    renderDeploySummary();

    // Process status — non-fatal if supervisor is disabled (503).
    await refreshAllProcesses();
  }

  init();
})();
