(function (root) {
  "use strict";

  const mounts = new WeakMap();
  const endpoint = "/api/azureJobs/external/provider-usage";
  const products = [{ key: "search", label: "Search", creditType: "search" }, { key: "enrich", label: "Enrichment", creditType: "enrich" }];
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const count = (value) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
  const format = (value) => value === null ? "Not reported" : value.toLocaleString();
  const object = (value) => value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const timestamp = (value) => {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T/.test(value)) return "";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? "" : parsed.toLocaleString();
  };

  function describe(payload = null) {
    const data = object(payload);
    const knownSource = data.source === "head_response_headers";
    const rows = products.map(({ key, label, creditType }) => {
      const product = object(object(data.products)[key]);
      const valid = knownSource && ["reported", "partial"].includes(product.status)
        // PDL HEAD can return credit headers with a missing-query, exhausted or
        // rate-limited response. Keep those reported values AND their warning.
        && Number.isInteger(product.httpStatus) && ((product.httpStatus >= 200 && product.httpStatus < 300) || [400, 402, 429].includes(product.httpStatus))
        && (!product.creditType || product.creditType === creditType);
      const remaining = valid ? count(product.accountRemainingCredits) : null;
      return {
        key, label, remaining,
        remainingText: remaining === null ? "Unavailable" : `${format(remaining)} credits`,
        status: valid ? product.status : "unavailable",
        warning: valid && product.httpStatus === 402 ? "Credits exhausted."
          : valid && product.httpStatus === 429 ? "Provider rate limited this check; retry later." : "",
        purchased: valid ? count(product.purchasedRemainingCredits) : null,
        overage: valid ? count(product.overageRemainingCredits) : null,
        lifetimeUsed: valid ? count(product.lifetimeCreditsUsed) : null,
        observedAt: valid ? timestamp(product.observedAt) : "",
        cached: valid && (product.cached === true || (product.cached === undefined && data.cached === true)),
      };
    });
    return {
      rows, checkedAt: knownSource ? timestamp(data.checkedAt) : "", cached: knownSource && data.cached === true,
      notice: "Available, purchased and overage figures are shown independently as reported by PDL and may not reconcile; do not add them. Lifetime usage is not current billing-cycle usage. No cycle total or usage percentage is reported here.",
    };
  }

  function mount(host, options = {}) {
    if (!host || typeof host.addEventListener !== "function") throw new TypeError("An account-usage host element is required.");
    if (typeof options.request !== "function") throw new TypeError("An account-usage request function is required.");
    mounts.get(host)?.dispose();
    let generation = 0;
    let disposed = false;
    let pending = null;
    let data = null;
    let state = "idle";

    function token() {
      try {
        const value = typeof options.token === "function" ? options.token() : options.token;
        return typeof value === "string" ? value.trim() : "";
      } catch { return ""; }
    }

    function domain() {
      try {
        const value = typeof options.domain === "function" ? options.domain() : options.domain;
        return typeof value === "string" && value ? value : "dev";
      } catch { return "dev"; }
    }

    function snapshot() { return { state, ...describe(data) }; }

    function render() {
      if (disposed) return;
      const view = snapshot();
      const locked = state === "locked";
      const loading = state === "loading";
      let status = locked ? "Unlock to check account credits. Searches remain available."
        : loading ? "Checking account credit headers…"
        : state === "error" ? "Account usage is unavailable. Your search results are unchanged."
        : view.checkedAt ? `Checked ${view.checkedAt}${view.cached ? " · cached for up to 30 seconds" : ""}`
        : "Account credits have not been checked.";
      const warnings = view.rows.filter((row) => row.warning).map((row) => `${row.label}: ${row.warning}`);
      if (warnings.length) status += ` · ${warnings.join(" ")}`;
      const rows = view.rows.map((row) => `<div class="provider-account-row"><span>${row.label} remaining</span><strong>${loading ? "Checking…" : escape(row.remainingText)}</strong></div>`).join("");
      const details = view.rows.map((row) => `<div class="provider-account-detail"><strong>${row.label}</strong><span>Purchased remaining: ${escape(format(row.purchased))}</span><span>Overage remaining: ${escape(format(row.overage))}</span><span>Lifetime credits used: ${escape(format(row.lifetimeUsed))}</span>${row.observedAt || row.cached ? `<span>${row.observedAt ? `Reported ${escape(row.observedAt)}` : "Reported time unavailable"}${row.cached ? " · cached up to 30 seconds" : ""}</span>` : ""}</div>`).join("");
      const action = locked
        ? `<a class="provider-account-unlock" href="admin.html?domain=${escape(encodeURIComponent(domain()))}">Unlock account usage</a>`
        : `<button type="button" class="btn secondary provider-account-refresh" data-account-usage-refresh${loading ? ' disabled aria-disabled="true"' : ""}>Refresh</button>`;
      host.innerHTML = `<section class="provider-account-usage" aria-label="People Data Labs account credits"><div class="provider-account-heading"><strong>PDL account credits</strong>${action}</div>${rows}<p class="provider-account-status" role="status" aria-live="polite">${escape(status)}</p><details class="provider-account-disclosure"><summary>Credit details</summary>${details}<p>${escape(view.notice)}</p></details></section>`;
      if (typeof options.onChange === "function") {
        try { options.onChange(view); } catch { /* A consumer callback must not break account isolation. */ }
      }
    }

    function cancel() {
      generation += 1;
      pending?.controller?.abort();
      pending = null;
    }

    function current(request) {
      if (disposed || request.id !== generation) return false;
      if (token() !== request.token) {
        cancel();
        data = null;
        state = token() ? "idle" : "locked";
        render();
        return false;
      }
      return true;
    }

    function clear() {
      if (disposed) return;
      cancel();
      data = null;
      state = token() ? "idle" : "locked";
      render();
    }

    function refresh() {
      if (disposed) return Promise.resolve(null);
      const currentToken = token();
      if (!currentToken) {
        clear();
        return Promise.resolve(null);
      }
      if (pending?.token === currentToken) return pending.promise;
      cancel();
      data = null;
      state = "loading";
      const request = { id: generation, token: currentToken, controller: typeof AbortController === "function" ? new AbortController() : null, promise: null };
      pending = request;
      request.promise = Promise.resolve().then(() => {
        if (!current(request)) return null;
        return options.request(endpoint, {
          method: "GET", cache: "no-store",
          headers: { "X-DevReady-Admin-Token": currentToken },
          ...(request.controller ? { signal: request.controller.signal } : {}),
        });
      }).then((response) => {
        if (!current(request)) return null;
        pending = null;
        data = object(response);
        state = data.source === "head_response_headers" ? "ready" : "error";
        render();
        return snapshot();
      }).catch((error) => {
        if (!current(request)) return null;
        pending = null;
        data = null;
        state = [401, 403].includes(Number(error?.status)) ? "locked" : "error";
        // Never render provider error text, request headers or authentication data.
        render();
        return snapshot();
      });
      render();
      return request.promise;
    }

    function handleClick(event) {
      if (event.target?.closest?.("[data-account-usage-refresh]")) {
        event.preventDefault();
        void refresh();
      }
    }

    function dispose() {
      if (disposed) return;
      cancel();
      disposed = true;
      data = null;
      host.removeEventListener("click", handleClick);
      host.innerHTML = "";
      if (mounts.get(host) === controller) mounts.delete(host);
    }

    const controller = { refresh, clear, invalidate: clear, dispose, snapshot };
    mounts.set(host, controller);
    host.addEventListener("click", handleClick);
    state = token() ? "idle" : "locked";
    render();
    return controller;
  }

  root.DevReadyProviderAccountUsage = { describe, mount };
  if (typeof module !== "undefined" && module.exports) module.exports = root.DevReadyProviderAccountUsage;
})(typeof window === "undefined" ? globalThis : window);
