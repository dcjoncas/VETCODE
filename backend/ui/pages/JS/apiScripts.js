const API_BASE = (() => {
      const p = window.location.port;
      if (p === "5500") return "http://127.0.0.1:8000";
      return window.location.origin;
    })();

async function api(path, opts = {}) {
      const url = `${API_BASE}${path}`;
      console.log(`FETCH ${opts.method || "GET"} ${url}`);
      const r = await fetch(url, opts);
      const txt = await r.text();
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${txt}`);
      try { return JSON.parse(txt); } catch { return txt; }
    }

    // Backwards-compatible helper
    function apiFetch(path, optsOrPayload = null) {
      if (optsOrPayload && typeof optsOrPayload === 'object') {
        const looksLikeFetchOpts = ('method' in optsOrPayload) || ('headers' in optsOrPayload) || ('body' in optsOrPayload) || ('signal' in optsOrPayload);
        if (looksLikeFetchOpts) return api(path, optsOrPayload);
      }
      return api(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(optsOrPayload || {})
      });
    }

    // FastAPI Form endpoints -> x-www-form-urlencoded
    function apiPostForm(path, payload = {}) {
      const form = new URLSearchParams();
      Object.entries(payload || {}).forEach(([k, v]) => {
        if (v === undefined || v === null) return;
        form.append(k, String(v));
      });
      return api(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: form.toString()
      });
    }

    function escapeHtml(s) { return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }

    let selectedProfileId = null;
    let selectedJdId = null;
    let latestRank = [];
    let selectedRow = null;

    async function health() {
      try {
        const ctrl = new AbortController();
        const tmr = setTimeout(() => ctrl.abort(), 3500);
        const j = await api("/api/health", { signal: ctrl.signal });
        clearTimeout(tmr);
        setStatus(true, "ok");
        console.log(`HEALTH ok version=${j.version || "?"}`);
      } catch (e) {
        setStatus(false, "backend down");
        console.log(`HEALTH error: ${e.message || e}`);
      }
    }