(function () {
  "use strict";

  const instances = new Map();
  const domainAliases = {
    technology: "dev",
    tech: "dev",
    devready: "dev",
    engineering: "engineer",
    build: "engineer",
    buildready: "engineer",
    legal: "law",
    legalready: "law",
    dentalready: "dental",
    "dental ready": "dental",
  };
  const domainLabels = {
    dev: "Technology",
    engineer: "Engineering",
    law: "Law",
    dental: "Dental",
  };

  function normalizeDomain(value) {
    const clean = String(value || "dev").trim().toLowerCase();
    const normalized = domainAliases[clean] || clean;
    return domainLabels[normalized] ? normalized : "dev";
  }

  function activeDomain(explicit) {
    const supplied = typeof explicit === "function" ? explicit() : explicit;
    const urlDomain = new URLSearchParams(window.location.search).get("domain");
    return normalizeDomain(
      supplied ||
        urlDomain ||
        document.documentElement.dataset.domain ||
        sessionStorage.getItem("domain") ||
        "dev",
    );
  }

  function domainLabel(value) {
    return domainLabels[normalizeDomain(value)];
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function clientName(jd) {
    return String(jd?.company || "").trim() || "Client to be confirmed";
  }

  function skillsFor(jd) {
    const value = jd?.skills || jd?.jd_skills || [];
    if (Array.isArray(value)) return value.filter(Boolean);
    return Object.values(value || {}).flatMap((items) =>
      Array.isArray(items) ? items.filter(Boolean) : [],
    );
  }

  function ensureStyles() {
    if (document.getElementById("devreadyJobPickerStyles")) return;
    const style = document.createElement("style");
    style.id = "devreadyJobPickerStyles";
    style.textContent = `
      body.jd-picker-open { overflow: hidden; }
      .jd-picker-native { display: none !important; }
      .jd-picker-trigger {
        width: 100%; min-height: 50px; padding: 9px 12px; border: 1px solid var(--line, #ccd7d0);
        border-radius: 10px; background: #fff; color: var(--text, #17251d); cursor: pointer;
        display: flex; align-items: center; justify-content: space-between; gap: 12px; text-align: left;
        box-shadow: 0 2px 7px rgba(21, 55, 37, 0.07); transition: border-color .16s ease, box-shadow .16s ease;
      }
      .jd-picker-trigger:hover, .jd-picker-trigger:focus-visible {
        border-color: var(--primary, #2f7d4b); box-shadow: 0 0 0 3px rgba(var(--primary-rgb, 47, 125, 75), .13);
        outline: none;
      }
      .jd-picker-trigger-copy { min-width: 0; display: grid; gap: 2px; }
      .jd-picker-trigger-copy strong, .jd-picker-trigger-copy small { overflow-wrap: anywhere; }
      .jd-picker-trigger-copy strong { font-size: 13px; }
      .jd-picker-trigger-copy small { color: var(--muted, #65756c); font-size: 11px; }
      .jd-picker-trigger-icon { flex: 0 0 auto; color: var(--primary-2, #1d673a); font-size: 18px; font-weight: 900; }
      .jd-picker-backdrop[hidden] { display: none !important; }
      .jd-picker-backdrop {
        position: fixed; inset: 0; z-index: 7000; padding: 24px; background: rgba(12, 27, 19, .48);
        display: grid; place-items: center;
      }
      .jd-picker-dialog {
        width: min(1040px, 100%); max-height: min(84vh, 780px); overflow: hidden; border: 1px solid rgba(var(--primary-rgb, 47, 125, 75), .28);
        border-radius: 14px; background: #fff; box-shadow: 0 24px 70px rgba(7, 28, 17, .28); display: grid;
        grid-template-rows: auto auto auto minmax(0, 1fr);
      }
      .jd-picker-head { display: flex; justify-content: space-between; gap: 16px; padding: 16px 18px 12px; border-bottom: 1px solid var(--line, #d9e1dc); }
      .jd-picker-head h2 { margin: 0; font-size: 18px; }
      .jd-picker-head p { margin: 4px 0 0; color: var(--muted, #65756c); font-size: 12px; }
      .jd-picker-close {
        width: 34px; height: 34px; flex: 0 0 auto; border: 1px solid var(--line, #d9e1dc); border-radius: 999px;
        background: #fff; color: var(--text, #17251d); cursor: pointer; font-size: 20px; line-height: 1;
      }
      .jd-picker-summary { display: flex; gap: 7px; flex-wrap: wrap; padding: 11px 18px 0; }
      .jd-picker-kpi {
        border: 1px solid var(--line, #d9e1dc); border-radius: 999px; padding: 6px 10px; background: rgba(var(--primary-rgb, 47, 125, 75), .055);
        display: inline-flex; align-items: baseline; gap: 6px; font-size: 11px; color: var(--muted, #65756c); font-weight: 800;
      }
      .jd-picker-kpi strong { color: var(--primary-2, #1d673a); font-size: 14px; }
      .jd-picker-tools { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; padding: 11px 18px; }
      .jd-picker-search { width: 100%; min-height: 38px; border: 1px solid var(--line, #d9e1dc); border-radius: 9px; padding: 8px 10px; }
      .jd-picker-manage { display: inline-flex; align-items: center; justify-content: center; text-decoration: none; white-space: nowrap; }
      .jd-picker-scroll { min-height: 180px; overflow-y: auto; padding: 0 18px 18px; scrollbar-gutter: stable; }
      .jd-picker-status { margin: 2px 0 10px; padding: 9px 10px; border: 1px dashed rgba(var(--primary-rgb, 47, 125, 75), .3); border-radius: 8px; color: var(--muted, #65756c); font-size: 12px; }
      .jd-picker-recent { margin-bottom: 10px; padding: 9px 10px; border: 1px solid rgba(var(--primary-rgb, 47, 125, 75), .22); border-radius: 10px; background: rgba(var(--primary-rgb, 47, 125, 75), .045); }
      .jd-picker-recent h3, .jd-picker-library-head h3 { margin: 0; font-size: 13px; }
      .jd-picker-recent-list { display: flex; gap: 6px; margin-top: 7px; overflow-x: auto; padding-bottom: 2px; }
      .jd-picker-recent-chip {
        max-width: 300px; flex: 0 0 auto; border: 1px solid rgba(var(--primary-rgb, 47, 125, 75), .2); border-radius: 999px;
        padding: 6px 10px; background: #fff; color: var(--text, #17251d); cursor: pointer; overflow: hidden; text-overflow: ellipsis;
        white-space: nowrap; text-align: left; font-size: 11px; font-weight: 800;
      }
      .jd-picker-recent-chip:hover, .jd-picker-recent-chip:focus-visible { border-color: var(--primary, #2f7d4b); outline: none; }
      .jd-picker-library-head { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; margin: 0 0 7px; }
      .jd-picker-library-head span { color: var(--muted, #65756c); font-size: 11px; }
      .jd-picker-tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 225px), 1fr)); gap: 6px; align-items: stretch; }
      .jd-picker-tile {
        min-width: 0; min-height: 74px; border: 1px solid var(--line, #d9e1dc); border-radius: 9px; padding: 8px 9px; background: #fff; color: var(--text, #17251d);
        cursor: pointer; text-align: left; display: grid; align-content: start; gap: 5px; transition: transform .14s ease, border-color .14s ease, box-shadow .14s ease;
      }
      .jd-picker-tile:hover, .jd-picker-tile:focus-visible { transform: translateY(-1px); border-color: var(--primary, #2f7d4b); box-shadow: 0 5px 14px rgba(24, 69, 42, .11); outline: none; }
      .jd-picker-tile.selected { border-color: var(--primary, #2f7d4b); box-shadow: 0 0 0 2px rgba(var(--primary-rgb, 47, 125, 75), .12); }
      .jd-picker-tile-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
      .jd-picker-tile-title { min-width: 0; font-size: 12px; font-weight: 900; overflow-wrap: anywhere; }
      .jd-picker-tile-id { flex: 0 0 auto; border-radius: 999px; padding: 2px 6px; background: rgba(var(--primary-rgb, 47, 125, 75), .09); color: var(--primary-2, #1d673a); font-size: 9px; font-weight: 900; }
      .jd-picker-tile-meta { color: var(--muted, #65756c); font-size: 10px; overflow-wrap: anywhere; }
      .jd-picker-empty { padding: 18px; border: 1px dashed var(--line, #d9e1dc); border-radius: 10px; color: var(--muted, #65756c); text-align: center; }
      @media (max-width: 640px) {
        .jd-picker-backdrop { padding: 8px; }
        .jd-picker-dialog { max-height: 92vh; }
        .jd-picker-tools { grid-template-columns: 1fr; }
        .jd-picker-manage { width: 100%; }
      }
    `;
    document.head.appendChild(style);
  }

  class JobDescriptionPicker {
    constructor(options) {
      this.options = options || {};
      this.select = document.getElementById(this.options.selectId || "jdSelect");
      if (!this.select) throw new Error("Job description picker select was not found.");
      this.domain = () => activeDomain(this.options.domain);
      this.jobs = [];
      this.recentAsks = [];
      this.lastFocus = null;
      this.build();
    }

    build() {
      ensureStyles();
      this.select.classList.add("jd-picker-native");
      this.select.setAttribute("aria-hidden", "true");
      this.select.tabIndex = -1;

      this.trigger = document.createElement("button");
      this.trigger.type = "button";
      this.trigger.className = "jd-picker-trigger";
      this.trigger.setAttribute("aria-haspopup", "dialog");
      this.trigger.innerHTML = `
        <span class="jd-picker-trigger-copy">
          <strong>Choose a saved job description</strong>
          <small>Open the compact job library</small>
        </span>
        <span class="jd-picker-trigger-icon" aria-hidden="true">&#8599;</span>`;
      this.select.insertAdjacentElement("afterend", this.trigger);

      const dialogId = `${this.select.id}JobPickerTitle`;
      this.backdrop = document.createElement("div");
      this.backdrop.className = "jd-picker-backdrop";
      this.backdrop.hidden = true;
      this.backdrop.innerHTML = `
        <section class="jd-picker-dialog" role="dialog" aria-modal="true" aria-labelledby="${dialogId}">
          <header class="jd-picker-head">
            <div><h2 id="${dialogId}">Choose a saved job description</h2><p>Compact, gap-free tiles from the current domain's Job Descriptions library.</p></div>
            <button class="jd-picker-close" type="button" aria-label="Close job description picker">&times;</button>
          </header>
          <div class="jd-picker-summary"></div>
          <div class="jd-picker-tools">
            <input class="jd-picker-search" type="search" placeholder="Search client, role, or JD number" aria-label="Search saved job descriptions" />
            <a class="btn secondary jd-picker-manage" href="job-descriptions.html">Manage job descriptions</a>
          </div>
          <div class="jd-picker-scroll"><div class="jd-picker-status">Loading saved job descriptions...</div><div class="jd-picker-results"></div></div>
        </section>`;
      document.body.appendChild(this.backdrop);
      this.search = this.backdrop.querySelector(".jd-picker-search");
      this.summary = this.backdrop.querySelector(".jd-picker-summary");
      this.status = this.backdrop.querySelector(".jd-picker-status");
      this.results = this.backdrop.querySelector(".jd-picker-results");
      this.manage = this.backdrop.querySelector(".jd-picker-manage");

      this.trigger.addEventListener("click", () => this.open());
      this.backdrop.querySelector(".jd-picker-close").addEventListener("click", () => this.close());
      this.backdrop.addEventListener("click", (event) => {
        if (event.target === this.backdrop) this.close();
        const tile = event.target.closest("[data-jd-picker-id]");
        if (!tile) return;
        const jd = this.jobs.find((item) => String(item.jd_id) === String(tile.dataset.jdPickerId));
        if (jd) {
          this.setSelection(jd.jd_id, jd);
          this.close();
        }
      });
      this.search.addEventListener("input", () => this.render());
      document.addEventListener("keydown", (event) => {
        if (!this.backdrop.hidden && event.key === "Escape") this.close();
      });
      this.syncFromSelect();
    }

    async open() {
      this.lastFocus = document.activeElement;
      this.backdrop.hidden = false;
      document.body.classList.add("jd-picker-open");
      this.search.value = "";
      this.manage.href = `job-descriptions.html?domain=${encodeURIComponent(this.domain())}`;
      this.backdrop.querySelector(".jd-picker-close").focus();
      await this.load();
      this.search.focus();
    }

    close() {
      this.backdrop.hidden = true;
      document.body.classList.remove("jd-picker-open");
      if (this.lastFocus && typeof this.lastFocus.focus === "function") this.lastFocus.focus();
    }

    async load() {
      const domain = this.domain();
      this.status.hidden = false;
      this.status.textContent = `Loading ${domainLabel(domain)} job descriptions...`;
      this.results.innerHTML = "";
      try {
        const [jobs, callAskData] = await Promise.all([
          window.api(`/api/azureJobs/list/${encodeURIComponent(domain)}/200`),
          window.api(`/api/call-intake/asks?domain=${encodeURIComponent(domain)}&limit=8`).catch(() => ({ asks: [] })),
        ]);
        this.jobs = Array.isArray(jobs) ? jobs : [];
        const jobIds = new Set(this.jobs.map((job) => String(job.jd_id)));
        this.recentAsks = (callAskData?.asks || []).filter((ask) => ask?.jd_id && jobIds.has(String(ask.jd_id))).slice(0, 6);
        this.status.hidden = true;
        this.render();
      } catch (error) {
        this.jobs = [];
        this.recentAsks = [];
        this.summary.innerHTML = "";
        this.status.hidden = false;
        this.status.textContent = `Could not load job descriptions: ${error.message || error}`;
      }
    }

    filteredJobs() {
      const query = String(this.search?.value || "").trim().toLowerCase();
      if (!query) return this.jobs;
      return this.jobs.filter((jd) =>
        [jd.jd_id, jd.title, jd.company, ...(skillsFor(jd).slice(0, 10))]
          .join(" ")
          .toLowerCase()
          .includes(query),
      );
    }

    render() {
      const domain = this.domain();
      const filtered = this.filteredJobs().slice().sort((left, right) =>
        clientName(left).localeCompare(clientName(right))
          || String(left.title || "").localeCompare(String(right.title || "")),
      );
      this.summary.innerHTML = `
        <span class="jd-picker-kpi"><strong>${this.jobs.length}</strong> Total JDs</span>
        <span class="jd-picker-kpi"><strong>${new Set(this.jobs.map(clientName)).size}</strong> Clients</span>
        <span class="jd-picker-kpi"><strong>${escapeHtml(domainLabel(domain))}</strong> Current domain</span>`;

      if (!filtered.length) {
        this.results.innerHTML = `<div class="jd-picker-empty">${this.jobs.length ? "No saved roles match this search." : `No job descriptions are saved for ${escapeHtml(domainLabel(domain))}.`}</div>`;
        return;
      }

      const visibleIds = new Set(filtered.map((jd) => String(jd.jd_id)));
      const recent = this.recentAsks
        .map((ask) => this.jobs.find((jd) => String(jd.jd_id) === String(ask.jd_id)))
        .filter((jd) => jd && visibleIds.has(String(jd.jd_id)));
      const searching = Boolean(String(this.search?.value || "").trim());
      const recentIds = new Set(recent.map((jd) => String(jd.jd_id)));
      const libraryJobs = searching ? filtered : filtered.filter((jd) => !recentIds.has(String(jd.jd_id)));
      const recentHtml = recent.length
        ? `<section class="jd-picker-recent"><h3>Recent Call Ask JDs</h3><div class="jd-picker-recent-list">${recent.map((jd) => this.recentChip(jd)).join("")}</div></section>`
        : "";
      this.results.innerHTML =
        recentHtml +
        `<section class="jd-picker-library"><header class="jd-picker-library-head"><h3>${searching ? "Matching roles" : "All saved roles"}</h3><span>${libraryJobs.length} role${libraryJobs.length === 1 ? "" : "s"}</span></header><div class="jd-picker-tiles">${libraryJobs.map((jd) => this.tile(jd)).join("")}</div></section>`;
    }

    recentChip(jd) {
      const id = String(jd.jd_id || "");
      return `<button class="jd-picker-recent-chip" type="button" data-jd-picker-id="${escapeHtml(id)}" title="${escapeHtml(`${clientName(jd)} - ${jd.title || `JD ${id}`}`)}">${escapeHtml(jd.title || `JD ${id}`)} &middot; ${escapeHtml(clientName(jd))}</button>`;
    }

    tile(jd) {
      const id = String(jd.jd_id || "");
      const selected = String(this.select.value || "") === id;
      return `
        <button class="jd-picker-tile${selected ? " selected" : ""}" type="button" data-jd-picker-id="${escapeHtml(id)}">
          <span class="jd-picker-tile-head"><span class="jd-picker-tile-title">${escapeHtml(jd.title || "Role title to be confirmed")}</span><span class="jd-picker-tile-id">JD ${escapeHtml(id)}</span></span>
          <span class="jd-picker-tile-meta">${escapeHtml(clientName(jd))} &bull; ${escapeHtml(domainLabel(this.domain()))}</span>
        </button>`;
    }

    setSelection(jdId, jd = {}, options = {}) {
      const id = String(jdId || "");
      if (!id) return;
      const label = `${clientName(jd)} - ${jd.title || id}`;
      let option = [...this.select.options].find((item) => String(item.value) === id);
      if (!option) {
        option = new Option(label, id);
        this.select.appendChild(option);
      }
      option.textContent = label;
      option.dataset.company = jd.company || "";
      option.dataset.title = jd.title || "";
      this.select.value = id;
      this.syncFromSelect(jd);
      if (options.dispatch !== false) {
        this.select.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }

    clear(options = {}) {
      this.select.value = "";
      this.syncFromSelect();
      if (options.dispatch) this.select.dispatchEvent(new Event("change", { bubbles: true }));
    }

    syncFromSelect(jd = {}) {
      const selected = this.select.options[this.select.selectedIndex];
      const title = jd.title || selected?.dataset?.title || "";
      const company = jd.company || selected?.dataset?.company || "";
      const hasValue = Boolean(this.select.value);
      this.trigger.querySelector("strong").textContent = hasValue
        ? `${company ? company + " - " : ""}${title || selected?.textContent || this.select.value}`
        : "Choose a saved job description";
      this.trigger.querySelector("small").textContent = hasValue
        ? `${domainLabel(this.domain())} job selected - open library to change`
        : `Open ${domainLabel(this.domain())} compact job library`;
      this.trigger.classList.toggle("has-selection", hasValue);
    }
  }

  window.DevReadyJobPicker = {
    mount(options = {}) {
      const selectId = options.selectId || "jdSelect";
      if (instances.has(selectId)) return instances.get(selectId);
      const instance = new JobDescriptionPicker({ ...options, selectId });
      instances.set(selectId, instance);
      return instance;
    },
    get(selectId = "jdSelect") {
      return instances.get(selectId) || null;
    },
    normalizeDomain,
    domainLabel,
  };
})();
