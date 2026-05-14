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
    selectVertical: $("#select-vertical"),
    selectOverlay: $("#select-overlay"),
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
  const store = {
    verticals: [],            // [{name, display_name, overlays: [...]}]
    sites: [],
    env: {},                  // masked
    apiKeyDirty: false,       // true once the user types in the API key field
    appKeyDirty: false,
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
    // Secrets: show masked value if present; track "dirty" so we know
    // whether to send KEEP_EXISTING or the user's new value on save.
    els.inputApiKey.value = store.env.DD_API_KEY || "";
    els.inputAppKey.value = store.env.DD_APP_KEY || "";
    els.inputDisplayName.value = store.env.DISPLAY_NAME || "";
    els.inputEmitInterval.value = store.env.EMIT_INTERVAL || "";
    els.inputOtelEndpoint.value = store.env.OTEL_EXPORTER_OTLP_ENDPOINT || "";
    els.selectOtelProtocol.value = store.env.OTEL_EXPORTER_OTLP_PROTOCOL || "";
    store.apiKeyDirty = false;
    store.appKeyDirty = false;
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
    const payload = {
      DD_SITE: els.selectSite.value,
      DD_DEMO_VERTICAL: els.selectVertical.value,
      DD_DEMO_SUB_VERTICAL: els.selectOverlay.value,
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
      const masked = await postJSON("/api/env", collectEnvPayload());
      store.env = masked;
      renderEnvFields();
      setResult(els.saveResult, "saved to .env", "ok");
    } catch (e) {
      setResult(els.saveResult, e.message, "err");
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

  // ----- Wire-up & init ----------------------------------------------------
  function wire() {
    els.selectVertical.addEventListener("change", renderOverlays);
    els.inputApiKey.addEventListener("input", () => { store.apiKeyDirty = true; });
    els.inputAppKey.addEventListener("input", () => { store.appKeyDirty = true; });
    els.btnSave.addEventListener("click", onSave);
    els.btnTest.addEventListener("click", onTest);
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
      const [verticals, sites, env] = await Promise.all([
        getJSON("/api/verticals"),
        getJSON("/api/sites"),
        getJSON("/api/env"),
      ]);
      store.verticals = verticals;
      store.sites = sites;
      store.env = env;
    } catch (e) {
      els.health.textContent = `load error: ${e.message}`;
      els.health.dataset.state = "err";
      return;
    }

    renderSites();
    renderVerticals();
    renderEnvFields();
  }

  init();
})();
