(function () {
  const styleId = "devready-profile-stage-style";

  function ensureStyles() {
    if (document.getElementById(styleId)) return;
    const style = document.createElement("style");
    style.id = styleId;
    style.textContent = `
      .profile-stage-strip {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 8px;
        margin-top: 10px;
      }
      .profile-stage-strip.compact {
        gap: 5px;
        margin-top: 0;
      }
      .profile-stage-item {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        color: #65716a;
        font-size: 13px;
        font-weight: 900;
        line-height: 1;
        white-space: nowrap;
      }
      .profile-stage-strip.compact .profile-stage-item {
        font-size: 11px;
      }
      .profile-stage-icon {
        width: 26px;
        height: 26px;
        display: inline-grid;
        place-items: center;
        border-radius: 999px;
        color: #fff;
        background: #a9b0ad;
        box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.32);
      }
      .profile-stage-strip.compact .profile-stage-icon {
        width: 22px;
        height: 22px;
      }
      .profile-stage-icon svg {
        width: 15px;
        height: 15px;
        stroke: currentColor;
        stroke-width: 2.25;
        fill: none;
        stroke-linecap: round;
        stroke-linejoin: round;
      }
      .profile-stage-connector {
        width: 22px;
        height: 2px;
        border-radius: 999px;
        background: #ccd5d0;
      }
      .profile-stage-strip.compact .profile-stage-connector {
        width: 12px;
      }
      .profile-stage-item.done {
        color: #17683e;
      }
      .profile-stage-item.done .profile-stage-icon,
      .profile-stage-item.done + .profile-stage-connector {
        background: #2f7d4b;
      }
      .profile-stage-item.current {
        color: #1b62b8;
      }
      .profile-stage-item.current .profile-stage-icon {
        background: #3b82f6;
      }
      .profile-stage-item.attention {
        color: #b91c1c;
      }
      .profile-stage-item.attention .profile-stage-icon {
        background: #dc2626;
      }
      .profile-stage-item.pending .profile-stage-icon {
        background: #9ca3af;
      }
      .profile-stage-label {
        max-width: 120px;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .profile-stage-strip.compact .profile-stage-label {
        display: none;
      }
      .profile-stage-empty {
        color: #68736d;
        font-size: 12px;
        font-weight: 800;
      }
    `;
    document.head.appendChild(style);
  }

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function iconSvg(icon) {
    const icons = {
      fingerprint: `<path d="M12 11v2.5"></path><path d="M8.5 14.5c.2-3 1.2-5 3.5-5s3.3 2 3.5 5"></path><path d="M7 11.5a5 5 0 0 1 10 0"></path><path d="M5.5 14.5a7 7 0 0 1 13 0"></path>`,
      clipboard: `<path d="M9 5h6"></path><path d="M9 9h6"></path><path d="M9 13h3"></path><rect x="6" y="3.5" width="12" height="15" rx="2"></rect>`,
      shield: `<path d="M12 3.5 18 6v4.5c0 3.8-2.4 6.5-6 8-3.6-1.5-6-4.2-6-8V6l6-2.5Z"></path><path d="m9.2 11.4 1.9 1.9 3.8-4"></path>`,
      briefcase: `<path d="M8 7V5.8c0-.9.7-1.5 1.5-1.5h5c.8 0 1.5.6 1.5 1.5V7"></path><rect x="4" y="7" width="16" height="11" rx="2"></rect><path d="M4 11h16"></path>`,
      award: `<circle cx="12" cy="8" r="4"></circle><path d="m9.5 12-1.2 6 3.7-2 3.7 2-1.2-6"></path>`,
    };
    return `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[icon] || icons.fingerprint}</svg>`;
  }

  function render(target, stages, options = {}) {
    ensureStyles();
    const element = typeof target === "string" ? document.getElementById(target) : target;
    if (!element) return;
    const rows = Array.isArray(stages) ? stages : [];
    if (!rows.length) {
      element.innerHTML = `<div class="profile-stage-empty">No profile stage data found.</div>`;
      return;
    }
    const compact = Boolean(options.compact);
    element.innerHTML = `<div class="profile-stage-strip${compact ? " compact" : ""}" aria-label="Profile process stage">
      ${rows
        .map((stage, index) => {
          const status = ["done", "current", "attention", "pending"].includes(stage.status) ? stage.status : "pending";
          const connector = index < rows.length - 1 ? `<span class="profile-stage-connector" aria-hidden="true"></span>` : "";
          const label = escapeHtml(stage.label || stage.key || "Stage");
          const detail = escapeHtml(stage.detail || label);
          return `<span class="profile-stage-item ${status}" title="${detail}" aria-label="${label}: ${detail}">
            <span class="profile-stage-icon">${iconSvg(stage.icon)}</span>
            <span class="profile-stage-label">${label}</span>
          </span>${connector}`;
        })
        .join("")}
    </div>`;
  }

  async function load(target, profileId, options = {}) {
    const element = typeof target === "string" ? document.getElementById(target) : target;
    if (!element || !profileId) return;
    ensureStyles();
    element.innerHTML = `<div class="profile-stage-empty">Loading profile stage...</div>`;
    try {
      const domain = options.domain || sessionStorage.getItem("domain") || sessionStorage.getItem("candidateDomain") || "dev";
      const url = `/api/profile/${encodeURIComponent(profileId)}/process-stage?domain=${encodeURIComponent(domain)}`;
      const data = window.api ? await window.api(url) : await fetch(url).then((response) => response.json());
      render(element, data.stages || [], options);
    } catch (error) {
      console.warn("Unable to load profile process stage", error);
      element.innerHTML = `<div class="profile-stage-empty">Stage unavailable.</div>`;
    }
  }

  window.DevReadyProfileStages = {
    render,
    load,
  };
})();
