(function () {
  const agents = [
    {
      key: "talent",
      name: "Numa",
      page: "Talent",
      href: "find-candidate.html",
      color: "#2f7d4b",
      specialty: "Resume intake, profile discovery, domain context, and workflow reset.",
      canDo: ["Guide resume upload and known-profile search", "Find missing workflow context", "Route weak internal results to outside search"],
      prompt: "Owns candidate intake, active domain state, selected candidate setup, and the beginning of every search workflow.",
    },
    {
      key: "match",
      name: "Numa",
      page: "Find Candidates In",
      href: "match-role.html",
      color: "#1e7058",
      specialty: "Internal ranking, shortlist strength, skill gaps, and score explanations.",
      canDo: ["Explain match scores", "Spot missing JD or profile evidence", "Recommend shortlist next steps"],
      prompt: "Owns internal candidate comparison and explainable matching against the selected job description.",
    },
    {
      key: "external",
      name: "Numa",
      page: "Find Candidates Out",
      href: "mine-candidate-external.html",
      color: "#125b36",
      specialty: "Outside sourcing, temporary profiles, dedupe checks, and promotion decisions.",
      canDo: ["Shape search criteria", "Review temporary external profiles", "Help decide what becomes permanent"],
      prompt: "Owns external discovery and quality checks before adding outside candidates to the database.",
    },
    {
      key: "profile",
      name: "Numa",
      page: "Profiles",
      href: "profile-preview.html",
      color: "#3a6fb5",
      specialty: "Profile completeness, resume evidence, skills, badges, and public readiness.",
      canDo: ["Uncover missing profile sections", "Suggest profile edits without inventing facts", "Coordinate badges, tests, and public profile readiness"],
      prompt: "Owns candidate profile truth, completion state, edit readiness, and profile-to-client presentation.",
    },
    {
      key: "jobs",
      name: "Numa",
      page: "Job Descriptions",
      href: "job-descriptions.html",
      color: "#f28c28",
      specialty: "JD normalization, must-haves, preferred skills, and match-ready role structure.",
      canDo: ["Clean vague job requirements", "Separate required from preferred", "Improve matching quality"],
      prompt: "Owns role structure, job requirements, and the inputs that drive candidate matching.",
    },
    {
      key: "crm",
      name: "Numa",
      page: "CRM",
      href: "crm.html",
      color: "#7c3aed",
      specialty: "Client records, contacts, relationship notes, follow-ups, and meeting handoff.",
      canDo: ["Update client card context", "Read relationship signals", "Plan the next client touch"],
      prompt: "Owns client relationship memory and turns hiring activity into account context.",
    },
    {
      key: "meet",
      name: "Numa",
      page: "Meet",
      href: "meet.html",
      color: "#be2633",
      specialty: "Meeting recordings, summaries, action items, and CRM or profile handoff.",
      canDo: ["Summarize meeting notes", "Extract action items", "Push useful signals to CRM or profile work"],
      prompt: "Owns meeting intelligence and the handoff from conversation to usable workflow facts.",
    },
    {
      key: "schedule",
      name: "Numa",
      page: "Interviews",
      href: "schedule-interview.html?interview=ready",
      color: "#0ea5e9",
      specialty: "Candidate reviews, client interviews, invites, attendee context, and calendar readiness.",
      canDo: ["Prepare interview invite text", "Check selected candidate and job context", "Route follow-up to Meet or CRM"],
      prompt: "Owns interview logistics and makes sure the right candidate, job, and client context travel together.",
    },
    {
      key: "clientcomms",
      name: "Numa",
      page: "Client Communication",
      href: "client-comm.html",
      color: "#14b8a6",
      specialty: "Client-ready shortlist messages, profile links, outreach tone, and send readiness.",
      canDo: ["Draft client-facing candidate summaries", "Check profile links and shortlist context", "Keep outbound language accurate and professional"],
      prompt: "Owns client communication after shortlist review and before or after interview scheduling.",
    },
    {
      key: "time",
      name: "Numa",
      page: "Time",
      href: "time-admin.html",
      color: "#64748b",
      specialty: "Time-entry links, submitted hours, approvals, review flags, and HR processing.",
      canDo: ["Explain pending time status", "Guide approval or review", "Find staff time-entry context"],
      prompt: "Owns weekly time review and keeps approval states precise.",
    },
    {
      key: "challenge",
      name: "Numa",
      page: "Test Challenge",
      href: "test-challenge.html",
      color: "#334155",
      specialty: "Technical challenge links, result refresh, pass evidence, and profile attachment.",
      canDo: ["Send or refresh challenge status", "Attach result evidence", "Explain how the result affects profile strength"],
      prompt: "Owns challenge evidence as one part of candidate evaluation.",
    },
    {
      key: "cert",
      name: "Numa",
      page: "AI Certification",
      href: "ai-cert.html",
      color: "#d946ef",
      specialty: "AI cert links, badge status, exam level, and profile certification evidence.",
      canDo: ["Find the candidate for certification", "Prepare the cert link", "Record confirmed certification outcomes"],
      prompt: "Owns AI certification workflow and badge handoff to profiles.",
    },
    {
      key: "badges",
      name: "Numa",
      page: "Badge Catalog",
      href: "badge-catalog.html",
      color: "#eab308",
      specialty: "Badge paths, levels, role fit, and next credential selection.",
      canDo: ["Recommend the next badge", "Explain badge evidence needed", "Coordinate with profile and certification status"],
      prompt: "Owns badge taxonomy and practical credential progression.",
    },
    {
      key: "admin",
      name: "Numa",
      page: "Admin",
      href: "admin.html",
      color: "#111827",
      specialty: "Users, menu permissions, blocked access, candidate logins, and environment checks.",
      canDo: ["Diagnose access issues", "Guide permission updates", "Check domain and environment readiness"],
      prompt: "Owns operational access and warns before risky account changes.",
    },
  ];

  const CUSTOM_AGENTS_KEY = "devreadyCustomPageAgents";
  const DISABLED_AGENTS_KEY = "devreadyDisabledPageAgents";

  function readJson(key, fallback) {
    try {
      const value = JSON.parse(localStorage.getItem(key) || "null");
      return value === null ? fallback : value;
    } catch {
      return fallback;
    }
  }

  function writeJson(key, value) {
    localStorage.setItem(key, JSON.stringify(value));
  }

  function sanitizeAgent(value) {
    const agent = value || {};
    const key = String(agent.key || "").trim();
    if (!key) return null;
    return {
      key,
      name: String(agent.name || "Numa").trim() || "Numa",
      page: String(agent.page || "Custom").trim() || "Custom",
      href: String(agent.href || "agents.html").trim() || "agents.html",
      color: String(agent.color || "#2f7d4b").trim() || "#2f7d4b",
      specialty: String(agent.specialty || "Custom workflow guidance.").trim() || "Custom workflow guidance.",
      canDo: Array.isArray(agent.canDo)
        ? agent.canDo.map((item) => String(item || "").trim()).filter(Boolean).slice(0, 6)
        : [],
      prompt: String(agent.prompt || "Custom Numa agent prompt.").trim() || "Custom Numa agent prompt.",
      custom: Boolean(agent.custom),
    };
  }

  function loadCustomAgents() {
    return readJson(CUSTOM_AGENTS_KEY, [])
      .map(sanitizeAgent)
      .filter(Boolean);
  }

  function saveCustomAgents(customAgents) {
    writeJson(CUSTOM_AGENTS_KEY, customAgents.map(sanitizeAgent).filter(Boolean));
  }

  function allAgents() {
    const custom = loadCustomAgents();
    const seen = new Set();
    return [...agents, ...custom].filter((agent) => {
      if (seen.has(agent.key)) return false;
      seen.add(agent.key);
      return true;
    });
  }

  function disabledAgents() {
    const list = readJson(DISABLED_AGENTS_KEY, []);
    return new Set(Array.isArray(list) ? list.map(String) : []);
  }

  function isAgentEnabled(key) {
    return !disabledAgents().has(String(key || ""));
  }

  function setAgentEnabled(key, enabled) {
    const disabled = disabledAgents();
    const normalizedKey = String(key || "");
    if (enabled) disabled.delete(normalizedKey);
    else disabled.add(normalizedKey);
    writeJson(DISABLED_AGENTS_KEY, Array.from(disabled));
    window.dispatchEvent(new CustomEvent("devready-agent-activation-changed", { detail: { key: normalizedKey, enabled } }));
    renderWidgetAgent();
  }

  function createCustomAgent(agent) {
    const nowKey = `custom-${Date.now().toString(36)}`;
    const customAgent = sanitizeAgent({
      ...agent,
      key: String(agent?.key || nowKey).trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-") || nowKey,
      name: "Numa",
      custom: true,
    });
    const customAgents = loadCustomAgents().filter((item) => item.key !== customAgent.key);
    customAgents.push(customAgent);
    saveCustomAgents(customAgents);
    setAgentEnabled(customAgent.key, true);
    localStorage.setItem("devreadyActivePageAgent", customAgent.key);
    window.dispatchEvent(new CustomEvent("devready-agent-created", { detail: customAgent }));
    return customAgent;
  }

  function currentDomain() {
    return sessionStorage.getItem("domain") || document.documentElement.dataset.domain || "dev";
  }

  function currentUser() {
    try {
      return JSON.parse(sessionStorage.getItem("devreadyUser") || localStorage.getItem("devreadyUser") || "null") || {};
    } catch {
      return {};
    }
  }

  function adminUnlockToken() {
    try {
      return JSON.parse(sessionStorage.getItem("devreadyAdminUnlock") || "null")?.token || "";
    } catch {
      return "";
    }
  }

  function numaChangeMode() {
    return localStorage.getItem("devreadyNumaChangeMode") === "on" ? "on" : "off";
  }

  function domainColor() {
    const domain = currentDomain();
    if (domain === "engineer") return "#145db2";
    if (domain === "law") return "#754f2b";
    return "#2f7d4b";
  }

  const agentIcons = {
    talent: '<path d="M4 20v-1a4 4 0 0 1 4-4h3"></path><circle cx="9" cy="8" r="4"></circle><path d="M15 12h5"></path><path d="M17.5 9.5v5"></path>',
    match: '<path d="M8 7h12"></path><path d="M8 12h12"></path><path d="M8 17h12"></path><path d="m3 7 1 1 2-2"></path><path d="m3 12 1 1 2-2"></path><path d="m3 17 1 1 2-2"></path>',
    external: '<circle cx="11" cy="11" r="7"></circle><path d="m21 21-4.3-4.3"></path><path d="M11 8v6"></path><path d="M8 11h6"></path>',
    profile: '<rect x="5" y="3" width="14" height="18" rx="2"></rect><circle cx="12" cy="9" r="3"></circle><path d="M8 17a4 4 0 0 1 8 0"></path>',
    jobs: '<rect x="3" y="7" width="18" height="13" rx="2"></rect><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><path d="M8 12h8"></path>',
    crm: '<path d="M4 19V5"></path><path d="M4 19h16"></path><path d="M8 16v-5"></path><path d="M12 16V8"></path><path d="M16 16v-3"></path>',
    meet: '<rect x="3" y="5" width="13" height="14" rx="2"></rect><path d="m16 10 5-3v10l-5-3"></path>',
    schedule: '<rect x="3" y="4" width="18" height="17" rx="2"></rect><path d="M16 2v4"></path><path d="M8 2v4"></path><path d="M3 10h18"></path><path d="M8 15h4"></path>',
    clientcomms: '<path d="M4 5h16v12H7l-3 3V5Z"></path><path d="m7 8 5 4 5-4"></path>',
    time: '<circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3 2"></path>',
    challenge: '<path d="m8 9-4 3 4 3"></path><path d="m16 9 4 3-4 3"></path><path d="m14 5-4 14"></path>',
    cert: '<path d="M12 3 4 7v6c0 5 3.5 7.5 8 9 4.5-1.5 8-4 8-9V7l-8-4Z"></path><path d="m9 12 2 2 4-5"></path>',
    badges: '<circle cx="12" cy="8" r="5"></circle><path d="M8.5 12.5 7 21l5-3 5 3-1.5-8.5"></path>',
    admin: '<path d="M12 3 4 6v6c0 4.5 3.2 7.6 8 9 4.8-1.4 8-4.5 8-9V6l-8-3Z"></path><path d="M12 8v4"></path><path d="M12 16h.01"></path>',
  };

  function iconSvg(key) {
    const path = agentIcons[key] || agentIcons.talent;
    return `<svg viewBox="0 0 24 24" aria-hidden="true">${path}</svg>`;
  }

  function activeAgent() {
    const pageAgent = agentForCurrentPage();
    if (pageAgent) return pageAgent;
    const key = localStorage.getItem("devreadyActivePageAgent") || "";
    return allAgents().find((agent) => agent.key === key && isAgentEnabled(agent.key)) || allAgents().find((agent) => isAgentEnabled(agent.key)) || null;
  }

  function setActiveAgent(key) {
    const agent = allAgents().find((item) => item.key === key) || allAgents()[0];
    if (!agent) return null;
    setAgentEnabled(agent.key, true);
    localStorage.setItem("devreadyActivePageAgent", agent.key);
    window.dispatchEvent(new CustomEvent("devready-agent-activated", { detail: agent }));
    return agent;
  }

  function clippedText(value, limit = 700) {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    return text.length > limit ? `${text.slice(0, limit - 1)}...` : text;
  }

  function textOf(selector, limit = 700) {
    const element = document.querySelector(selector);
    return clippedText(element?.innerText || element?.textContent || "", limit);
  }

  function valueOf(selector, limit = 240) {
    const element = document.querySelector(selector);
    return clippedText(element?.value || element?.innerText || element?.textContent || "", limit);
  }

  function collectTexts(selector, limit = 12) {
    return Array.from(document.querySelectorAll(selector))
      .map((element) => clippedText(element.getAttribute("title") || element.innerText || element.textContent || "", 160))
      .filter(Boolean)
      .slice(0, limit);
  }

  function selectedCandidateName() {
    return clippedText(
      sessionStorage.getItem("candidateName") ||
        textOf("#profileHeader", 160) ||
        valueOf("#profileTimeName", 160) ||
        valueOf("#timePersonName", 160) ||
        valueOf('input[name="person_name"]', 160) ||
        "",
      180,
    );
  }

  function selectedCandidateEmail() {
    return clippedText(
      sessionStorage.getItem("candidateEmail") ||
        valueOf("#profileTimeEmail", 180) ||
        valueOf("#timePersonEmail", 180) ||
        valueOf('input[name="email"]', 180) ||
        "",
      220,
    );
  }

  function currentPageSnapshot() {
    const processText = textOf(".process-flow, #processFlow, .breadcrumb", 900);
    const snapshot = {
      pageFile: pageName(),
      visibleTitle: textOf("#pageTitle, .page-title, h1", 220),
      processFlow: processText,
    };

    if (pageName() === "profile-preview" || pageName() === "profile-preview-edit") {
      snapshot.profile = {
        name: textOf("#profileHeader", 180),
        profileId: textOf("#profileIdChip", 120),
        title: textOf("#candidateJobTitle", 180) || valueOf("#timeRoleTitle", 180),
        location: clippedText(
          [textOf("#candidateCity", 80), textOf("#candidateState", 80), textOf("#candidateCountry", 80)]
            .filter(Boolean)
            .join(" "),
          220,
        ),
        aboutHeading: textOf("#descriptionHeader", 180),
        about: textOf("#descriptionText", 1100),
        topMatches: collectTexts("#topMatches .pill, #featureSkillDiv .pill", 12),
        skills: collectTexts(".chartPill, .skill-pill, #skills .pill", 24),
        timePanel: {
          personName: valueOf('#profileTimeName, #profileTimePanel input[name="person_name"], #timePersonName, input[placeholder*="Person" i]', 180),
          email: valueOf('#profileTimeEmail, #profileTimePanel input[name="email"], #timePersonEmail, input[type="email"]', 180),
          roleTitle: valueOf('#profileTimeTitle, #profileTimePanel input[name="role_title"], #timeRoleTitle', 180),
        },
      };
    }

    if (window.DevReadyPageContext && typeof window.DevReadyPageContext === "object") {
      snapshot.pageData = window.DevReadyPageContext;
    }

    return snapshot;
  }

  function agentContext() {
    let shortlistCount = 0;
    const agent = activeAgent();
    const pageSnapshot = currentPageSnapshot();
    try {
      const shortlist = JSON.parse(sessionStorage.getItem("shortlistProfiles") || sessionStorage.getItem("shortlist") || "[]");
      shortlistCount = Array.isArray(shortlist) ? shortlist.length : 0;
    } catch {}
    return {
      domain: currentDomain(),
      page: document.title,
      activeUrl: window.location.href,
      pageAgentKey: agent?.key || "",
      activeAgent: agent
        ? {
            key: agent.key,
            name: agent.name,
            page: agent.page,
            specialty: agent.specialty,
            canDo: agent.canDo,
            prompt: agent.prompt,
            custom: Boolean(agent.custom),
          }
        : null,
      user: {
        id: currentUser().id || "",
        username: currentUser().username || "",
        email: currentUser().email || "",
      },
      candidateId: sessionStorage.getItem("candidateId") || sessionStorage.getItem("selectedProfileId") || "",
      candidateName: selectedCandidateName(),
      candidateEmail: selectedCandidateEmail(),
      jobId: sessionStorage.getItem("jobId") || sessionStorage.getItem("jobID") || sessionStorage.getItem("selectedJdId") || "",
      jobTitle: sessionStorage.getItem("jobTitle") || "",
      shortlistCount,
      pageSnapshot,
    };
  }

  function applyDomainToHref(href) {
    if (!href || href.startsWith("http")) return href;
    const url = new URL(href, window.location.href);
    url.searchParams.set("domain", currentDomain());
    return `${url.pathname.split("/").pop()}${url.search}${url.hash}`;
  }

  function pageName() {
    const path = window.location.pathname || "";
    const file = path.split("/").pop() || "";
    return file.replace(/\.html$/i, "").toLowerCase();
  }

  function agentKeyForPage(name = pageName()) {
    const pageMap = {
      "find-candidate": "talent",
      "match-role": "match",
      "mine-candidate-external": "external",
      "profile-preview": "profile",
      "profile-preview-edit": "profile",
      "profile-public": "profile",
      "profile-search-results": "profile",
      "job-descriptions": "jobs",
      crm: "crm",
      meet: "meet",
      "schedule-interview": "schedule",
      "client-comm": "clientcomms",
      "status-tracker": "schedule",
      "time-admin": "time",
      "time-entry": "time",
      "test-challenge": "challenge",
      "exam-answer-key": "challenge",
      "ai-cert": "cert",
      "badge-catalog": "badges",
      admin: "admin",
      onboarding: "admin",
      "candidate-chat": "profile",
      "external-chat": "profile",
    };
    return pageMap[name] || "";
  }

  function agentForCurrentPage() {
    const key = agentKeyForPage();
    if (!key || !isAgentEnabled(key)) return null;
    return allAgents().find((agent) => agent.key === key) || null;
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function ensureWidgetStyles() {
    if (document.getElementById("pageAgentWidgetStyles")) return;
    const style = document.createElement("style");
    style.id = "pageAgentWidgetStyles";
    style.textContent = `
      body .agent-orb-button{position:fixed;top:var(--agent-dock-top,16px);left:var(--agent-dock-left,50%);right:var(--agent-dock-right,auto);transform:var(--agent-dock-transform,translateX(-50%));z-index:9000;display:none;width:auto;min-width:0;min-height:50px;align-items:center;justify-content:flex-start;gap:10px;border:1px solid rgba(255,255,255,.62);border-radius:999px;padding:7px 12px 7px 7px;background:rgba(255,255,255,.92);color:#101827;box-shadow:0 18px 40px rgba(15,23,42,.18);cursor:pointer;font:inherit}
      body .agent-orb-button.visible{display:inline-flex}
      body .agent-orb{--agent-color:#2f7d4b;display:grid;place-items:center;width:50px;height:50px;border-radius:999px;background:radial-gradient(circle at 28% 24%,rgba(255,255,255,.96) 0 9%,rgba(255,255,255,.24) 10% 22%,transparent 23%),radial-gradient(circle at 68% 72%,rgba(0,0,0,.24),transparent 38%),radial-gradient(circle at 36% 34%,color-mix(in srgb,var(--agent-color),white 36%),var(--agent-color) 52%,color-mix(in srgb,var(--agent-color),black 35%) 100%);box-shadow:inset -10px -12px 18px rgba(0,0,0,.24),inset 9px 9px 15px rgba(255,255,255,.26),0 10px 22px color-mix(in srgb,var(--agent-color),transparent 64%)}
      body .agent-orb svg{width:46%;height:46%;fill:none;stroke:#fff;stroke-width:2.35;stroke-linecap:round;stroke-linejoin:round;filter:drop-shadow(0 1px 2px rgba(0,0,0,.28))}
      body .agent-orb-button span{display:inline-block;flex:0 0 auto;max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;font-weight:900}
      body .agent-chat-panel{position:fixed;top:var(--agent-panel-top,74px);left:var(--agent-panel-left,50%);right:var(--agent-panel-right,auto);transform:var(--agent-panel-transform,translateX(-50%));z-index:9001;display:none;width:min(390px,calc(100vw - 28px));border:1px solid var(--line,#dfe7e2);border-radius:14px;background:#fff;color:var(--text,#101827);box-shadow:0 22px 60px rgba(15,23,42,.22);overflow:hidden}
      .agent-chat-panel.open{display:block}
      .agent-chat-head{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:10px;align-items:center;padding:12px;border-bottom:1px solid var(--line,#dfe7e2);background:linear-gradient(90deg,rgba(47,125,75,.1),#fff)}
      .agent-chat-title strong{display:block;font-size:14px;line-height:1.15}.agent-chat-title span{display:block;margin-top:2px;color:var(--muted,#5b6b62);font-size:12px}
      .agent-chat-close{width:32px;height:32px;border:1px solid var(--line,#dfe7e2);border-radius:999px;background:#fff;cursor:pointer;font-weight:900}
      .agent-chat-log{display:grid;gap:8px;max-height:310px;overflow-y:auto;padding:12px;background:#f8faf9}
      .agent-msg{max-width:88%;border-radius:12px;padding:9px 10px;background:#fff;border:1px solid var(--line,#dfe7e2);font-size:13px;line-height:1.35;white-space:pre-wrap}.agent-msg.user{justify-self:end;color:#fff;background:var(--primary-2,#2f7d4b);border-color:transparent}
      .agent-chat-form{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;padding:10px;border-top:1px solid var(--line,#dfe7e2)}
      .agent-chat-form input{min-height:38px;border:1px solid var(--line,#dfe7e2);border-radius:999px;padding:8px 11px;font:inherit}
    `;
    document.head.appendChild(style);
  }

  function appendWidgetMessage(text, role, pending = false) {
    const log = document.getElementById("agentChatLog");
    if (!log) return;
    const bubble = document.createElement("div");
    bubble.className = `agent-msg${role === "user" ? " user" : ""}`;
    if (pending) bubble.dataset.pending = "1";
    bubble.textContent = text;
    log.appendChild(bubble);
    log.scrollTop = log.scrollHeight;
  }

  function replacePendingWidgetMessage(text) {
    const pending = document.querySelector('#agentChatLog [data-pending="1"]');
    if (pending) {
      pending.textContent = text;
      pending.removeAttribute("data-pending");
    } else {
      appendWidgetMessage(text, "assistant");
    }
  }

  function renderWidgetAgent() {
    const agent = activeAgent();
    const button = document.getElementById("agentOrbButton");
    if (!button) return;
    button.classList.toggle("visible", Boolean(agent));
    if (!agent) return;
    document.querySelectorAll("#agentOrbButton .agent-orb, #agentPanelOrb").forEach((orb) => {
      orb.style.setProperty("--agent-color", domainColor());
      orb.innerHTML = iconSvg(agent.key);
    });
    document.getElementById("agentOrbLabel").textContent = "Ask Numa";
    document.getElementById("agentPanelName").textContent = "Numa";
    document.getElementById("agentPanelPage").textContent = `${agent.page} specialist`;
    const log = document.getElementById("agentChatLog");
    if (log && log.dataset.agentKey !== agent.key) {
      log.dataset.agentKey = agent.key;
      log.innerHTML = `<div class="agent-msg">Ask Numa.</div>`;
    }
  }

  async function askWidgetAgent(event) {
    event.preventDefault();
    const agent = activeAgent();
    const input = document.getElementById("agentChatInput");
    const message = input?.value.trim();
    if (!agent || !message) return;
    input.value = "";
    appendWidgetMessage(message, "user");
    appendWidgetMessage("Thinking through the page context...", "assistant", true);
    const form = new FormData();
    form.append("agent_key", agent.key);
    form.append("message", message);
    form.append("domain", currentDomain());
    form.append("context_json", JSON.stringify(agentContext()));
    form.append("admin_token", adminUnlockToken());
    form.append("numa_change_mode", numaChangeMode());
    try {
      const response = await fetch("/api/agents/ask", { method: "POST", body: form });
      const data = await response.json();
      replacePendingWidgetMessage(data.answer || "I could not build an answer for that yet.");
    } catch {
      replacePendingWidgetMessage("The page agent is wired, but the backend agent endpoint is not reachable from this page yet.");
    }
  }

  function mountWidget() {
    if (!document.body || document.getElementById("agentOrbButton")) {
      renderWidgetAgent();
      return;
    }
    ensureWidgetStyles();
    const shell = document.createElement("div");
    shell.innerHTML = `
      <button id="agentOrbButton" class="agent-orb-button" type="button" aria-label="Ask Numa">
        <i class="agent-orb" aria-hidden="true"></i>
        <span id="agentOrbLabel">Ask Numa</span>
      </button>
      <section id="agentChatPanel" class="agent-chat-panel" aria-label="Ask Numa">
        <div class="agent-chat-head">
          <i id="agentPanelOrb" class="agent-orb" aria-hidden="true"></i>
          <div class="agent-chat-title">
            <strong id="agentPanelName">Numa</strong>
            <span id="agentPanelPage">Ready</span>
          </div>
          <button id="agentChatClose" class="agent-chat-close" type="button" aria-label="Close agent">x</button>
        </div>
        <div id="agentChatLog" class="agent-chat-log"></div>
        <form id="agentChatForm" class="agent-chat-form">
          <input id="agentChatInput" autocomplete="off" placeholder="Ask Numa..." />
          <button class="btn" type="submit">Ask</button>
        </form>
      </section>
    `;
    document.body.appendChild(shell);
    document.getElementById("agentOrbButton").addEventListener("click", () => {
      const panel = document.getElementById("agentChatPanel");
      panel.classList.toggle("open");
      if (panel.classList.contains("open")) document.getElementById("agentChatInput")?.focus();
    });
    document.getElementById("agentChatClose").addEventListener("click", () => {
      document.getElementById("agentChatPanel").classList.remove("open");
    });
    document.getElementById("agentChatForm").addEventListener("submit", askWidgetAgent);
    window.addEventListener("devready-agent-activated", renderWidgetAgent);
    window.addEventListener("devready-agent-activation-changed", renderWidgetAgent);
    window.addEventListener("devready-agent-created", renderWidgetAgent);
    renderWidgetAgent();
  }

  window.DevReadyPageAgents = {
    all: agents,
    allAgents,
    active: activeAgent,
    pageAgent: agentForCurrentPage,
    keyForPage: agentKeyForPage,
    activate: setActiveAgent,
    enabled: isAgentEnabled,
    setEnabled: setAgentEnabled,
    create: createCustomAgent,
    context: agentContext,
    mountWidget,
    domainColor,
    icon: iconSvg,
    withDomain: applyDomainToHref,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mountWidget, { once: true });
  } else {
    mountWidget();
  }
})();
