(function () {
  function ensureProfileCompletionStyles() {
    if (document.getElementById("devready-profile-completion-style")) return;
    const style = document.createElement("style");
    style.id = "devready-profile-completion-style";
    style.textContent = `
      .profile-completion-panel {
        display: grid !important;
        grid-template-columns: minmax(0, 1fr) auto;
        align-items: center;
        gap: 14px;
        border: 1px solid rgba(var(--primary-2-rgb, 47, 125, 75), 0.24) !important;
        border-radius: 12px !important;
        padding: 12px !important;
        background: #f8fbf9 !important;
        color: var(--text, #102018) !important;
      }
      .profile-completion-heading {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
      }
      .profile-completion-kicker {
        color: var(--muted, #5b6b62);
        font-size: 10px;
        font-weight: 900;
        letter-spacing: .08em;
        text-transform: uppercase;
      }
      .profile-completion-count {
        border-radius: 999px;
        padding: 3px 7px;
        background: rgba(var(--primary-rgb, 127, 191, 63), .13);
        color: var(--primary-2, #2f7d4b);
        font-size: 10px;
        font-weight: 900;
      }
      .profile-completion-title {
        display: block;
        margin-top: 2px;
        font-size: 14px;
      }
      .profile-completion-checks {
        display: flex;
        gap: 6px;
        flex-wrap: wrap;
        margin-top: 8px;
      }
      .profile-completion-check {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        border: 1px solid var(--line, #dfe7e2);
        border-radius: 999px;
        padding: 4px 8px;
        background: #fff;
        color: var(--muted, #5b6b62);
        font-size: 10px;
        font-weight: 850;
      }
      .profile-completion-check.done {
        border-color: rgba(var(--primary-2-rgb, 47, 125, 75), .3);
        color: var(--primary-2, #2f7d4b);
      }
      .profile-completion-check.missing::before { content: "○"; }
      .profile-completion-check.done::before { content: "✓"; }
      .profile-completion-next {
        margin-top: 8px;
        color: var(--muted, #5b6b62);
        font-size: 11px;
        line-height: 1.4;
      }
      .profile-completion-inline-actions {
        display: flex;
        gap: 7px;
        flex-wrap: wrap;
        margin-top: 9px;
      }
      .profile-completion-link-ready {
        color: var(--primary-2, #2f7d4b);
        font-size: 10px;
        font-weight: 900;
      }
      @media (max-width: 760px) {
        .profile-completion-panel { grid-template-columns: 1fr; }
      }
    `;
    document.head.appendChild(style);
  }

  ensureProfileCompletionStyles();

  function currentDomain() {
    return sessionStorage.getItem("domain") || "dev";
  }

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function candidateNameFromProfile(profileData, fallback) {
    const profile = profileData && profileData.profile ? profileData.profile : profileData || {};
    const name = [profile.firstName, profile.lastName].filter(Boolean).join(" ");
    return name || profile.name || fallback || "Candidate";
  }

  function candidateEmailFromProfile(profileData) {
    const profile = profileData && profileData.profile ? profileData.profile : profileData || {};
    return profile.email || profileData?.email || sessionStorage.getItem("candidateEmail") || "";
  }

  function candidateAiProfileMessage(name, link) {
    return `Hi ${name || "there"},\n\nPlease kindly complete your DevReady AI profile and personality chat using this secure link:\n\n${link}\n\nYour answers help us complete your profile and represent you accurately for the right opportunities.\n\nThank you,\nDevReady Team`;
  }

  async function copyProfileCompletionText(value, successMessage) {
    try {
      await navigator.clipboard.writeText(String(value || ""));
      showProfileCompletionNotice(successMessage || "Copied.");
      return true;
    } catch (error) {
      showProfileCompletionNotice("Could not copy automatically. Open the link and copy it from the address bar.", true);
      return false;
    }
  }

  function isProfileCompletionDone(profileData) {
    return getProfileCompletionState(profileData) === "complete";
  }

  function getProfileCompletionPieces(profileData) {
    const profile = profileData && profileData.profile ? profileData.profile : profileData || {};
    const skills = profileData && Array.isArray(profileData.skills) ? profileData.skills : [];
    const technicalSkills =
      profileData && Array.isArray(profileData.technicalSkills) ? profileData.technicalSkills : [];
    const portfolioExperience =
      profileData && Array.isArray(profileData.portfolioExperience)
        ? profileData.portfolioExperience
        : [];

    const regularComplete =
      typeof profileData?.hasRegularProfile === "boolean"
        ? profileData.hasRegularProfile
        : Boolean(profile.title) &&
          Boolean(
            profile.description ||
              skills.length ||
              technicalSkills.length ||
              portfolioExperience.some((item) => item && (item.description || item.mainrole)),
          );
    const personalityComplete =
      typeof profileData?.hasPersonality === "boolean"
        ? profileData.hasPersonality
        : Array.isArray(profileData && profileData.personality) &&
          profileData.personality.some((item) => item && item.title && item.score);
    const cultureComplete =
      typeof profileData?.hasCulture === "boolean"
        ? profileData.hasCulture
        : Array.isArray(profileData && profileData.culturalExperience) &&
          profileData.culturalExperience.some(
            (item) => item && item.title && Number(item.level) > 0,
          );

    return {
      regular: regularComplete,
      personality: personalityComplete,
      culture: cultureComplete,
    };
  }

  function missingProfileCompletionPieces(profileData) {
    if (profileData && Array.isArray(profileData.missing) && profileData.missing.length) {
      return profileData.missing;
    }
    const pieces = getProfileCompletionPieces(profileData);
    const missing = [];
    if (!pieces.regular) missing.push("regular profile");
    if (!pieces.personality) missing.push("personality survey");
    if (!pieces.culture) missing.push("culture profile");
    return missing;
  }

  function getProfileCompletionState(profileData) {
    if (profileData && profileData.state) return profileData.state;

    const pieces = getProfileCompletionPieces(profileData);
    const values = Object.values(pieces);
    if (values.every(Boolean)) return "complete";
    if (values.some(Boolean)) return "partial";
    return "missing";
  }

  function profileUpdateLink(profileId, section) {
    const params = new URLSearchParams({
      candidateId: String(profileId || ""),
      domain: currentDomain(),
      complete: section,
    });
    return `${window.location.origin}/ui/pages/profile-preview-edit.html?${params.toString()}`;
  }

  function linkForMissingPiece(profileId, piece) {
    if (piece === "regular profile") {
      return {
        key: "regular",
        label: "Regular profile",
        description: "Title, bio, skills, and core profile details.",
        url: profileUpdateLink(profileId, "regular"),
      };
    }
    if (piece === "culture profile") {
      return {
        key: "culture",
        label: "Culture profile",
        description: "Culture and work-style fields for this domain.",
        url: profileUpdateLink(profileId, "culture"),
      };
    }
    return {
      key: "personality",
      label: "Personality AI chat",
      description: "Candidate-safe AI chat link for personality answers.",
      url: "",
      async: true,
    };
  }

  async function profileCompletionLink(profileId) {
    let token = "";
    try {
      token = await api(`/api/chat/getUrlCode/${profileId}`, {
        method: "GET",
      });
    } catch (error) {
      token = "";
    }

    if (!token || String(token).toLowerCase().includes("legacy survey")) {
      const fd = new FormData();
      fd.append("profileid", profileId);
      fd.append("domain", currentDomain());
      token = await api("/api/chat/scheduleChat", {
        method: "POST",
        body: fd,
      });
    }

    return `${window.location.origin}/ui/pages/external-chat.html?candidate=${encodeURIComponent(token)}&domain=${encodeURIComponent(currentDomain())}`;
  }

  async function sendProfileCompletionChat(profileId, profileData) {
    if (!profileId) {
      alert("No profile selected.");
      return "";
    }

    showProfileCompletionNotice("Creating finish-profile chat link...");
    let link = "";
    try {
      link = await profileCompletionLink(profileId);
    } catch (error) {
      const message = `Could not create finish-profile chat link: ${error.message || error}`;
      showProfileCompletionNotice(message, true);
      alert(message);
      return "";
    }
    const email = candidateEmailFromProfile(profileData);
    const name = candidateNameFromProfile(profileData);
    const subject = encodeURIComponent("Please complete your DevReady AI profile and personality chat");
    const body = encodeURIComponent(candidateAiProfileMessage(name, link));

    try {
      await navigator.clipboard.writeText(link);
    } catch (error) {
      console.warn("Could not copy profile completion link.", error);
    }

    showProfileCompletionNotice(
      `Finish-profile chat link ready${email ? ` for ${email}` : ""}: ${link}`,
    );

    if (email) {
      window.location.href = `mailto:${encodeURIComponent(email)}?subject=${subject}&body=${body}`;
    } else {
      alert(`Profile completion link copied:\n${link}`);
    }

    return link;
  }

  async function buildProfileCompletionLinks(profileId, profileData) {
    const missing = missingProfileCompletionPieces(profileData);
    const needsAiProfileChat = missing.some((piece) =>
      ["personality survey", "culture profile"].includes(piece),
    );
    const links = [];
    if (needsAiProfileChat) {
      links.push({
        key: "ai-profile-personality",
        label: "AI profile & personality chat",
        description: "Secure candidate-facing chat for personality and culture answers.",
        url: await profileCompletionLink(profileId),
        candidateSafe: true,
      });
    }
    missing
      .filter((piece) => !["personality survey", "culture profile"].includes(piece))
      .forEach((piece) => links.push({ ...linkForMissingPiece(profileId, piece), candidateSafe: false }));
    return links;
  }

  async function showProfileCompletionLinks(profileId, profileData, context = {}) {
    if (!profileId) {
      alert("No profile selected.");
      return [];
    }

    const stored =
      profileData ||
      (window.profileCompletionProfiles &&
        window.profileCompletionProfiles[String(profileId)]) ||
      {};
    showProfileCompletionNotice("Preparing profile completion links...");

    let links = [];
    try {
      links = await buildProfileCompletionLinks(profileId, stored);
    } catch (error) {
      const message = `Could not create completion links: ${error.message || error}`;
      showProfileCompletionNotice(message, true);
      alert(message);
      return [];
    }

    if (!links.length) {
      showProfileCompletionNotice("Profile is complete. No missing links needed.");
      return [];
    }

    const email = candidateEmailFromProfile(stored);
    const name = candidateNameFromProfile(stored);
    showProfileCompletionLinksPanel(links, email, name, context);
    return links;
  }

  function showProfileCompletionLinksPanel(links, email, name, context = {}) {
    let panel = document.getElementById("profileCompletionLinksPanel");
    if (!panel) {
      panel = document.createElement("div");
      panel.id = "profileCompletionLinksPanel";
      panel.className = "notice";
      panel.style.position = "fixed";
      panel.style.right = "18px";
      panel.style.bottom = "18px";
      panel.style.zIndex = "9999";
      panel.style.maxWidth = "620px";
      panel.style.maxHeight = "calc(100vh - 36px)";
      panel.style.overflowY = "auto";
      panel.style.boxShadow = "0 12px 26px rgba(0,0,0,0.16)";
      document.body.appendChild(panel);
    }

    const candidateLink = links.find((item) => item.candidateSafe);
    const candidateMessage = candidateLink
      ? candidateAiProfileMessage(name, candidateLink.url)
      : "";
    const subject = encodeURIComponent("Please complete your DevReady AI profile and personality chat");
    const body = encodeURIComponent(candidateMessage);
    const panelTitle = context.title || "Profile completion actions";
    const panelMessage = context.message || "";

    panel.innerHTML = `
      <div style="display:flex; justify-content:space-between; gap:12px; align-items:flex-start;">
        <div>
          <strong>${escapeHtml(panelTitle)}</strong>
          <div style="margin-top:4px; color:var(--muted);">The AI chat is candidate-safe. Profile editor links remain internal to DevReady.</div>
        </div>
        <button class="btn secondary" type="button" style="padding:6px 10px;" onclick="document.getElementById('profileCompletionLinksPanel')?.remove()">Close</button>
      </div>
      ${panelMessage ? `<div style="border:1px solid rgba(198,40,50,0.24); border-radius:12px; padding:10px; margin-top:12px; background:#fff4f5; color:#7f1d2d;">${escapeHtml(panelMessage)}</div>` : ""}
      <div style="display:grid; gap:8px; margin-top:12px;">
        ${links
          .map(
            (item, index) => `
              <div style="border:1px solid var(--line); border-radius:12px; padding:10px; background:#fff;">
                <div style="font-weight:900;">${escapeHtml(item.label)}</div>
                <div style="font-size:13px; color:var(--muted);">${escapeHtml(item.description)}</div>
                <div style="font-size:11px; font-weight:900; margin-top:6px; color:${item.candidateSafe ? "#176b3a" : "var(--muted)"};">${item.candidateSafe ? "CANDIDATE-SAFE LINK" : "DEVREADY INTERNAL LINK"}</div>
                <a href="${escapeHtml(item.url)}" target="_blank" rel="noopener" style="display:block; margin-top:6px; overflow-wrap:anywhere;">${escapeHtml(item.url)}</a>
                <div class="row-actions" style="margin-top:8px;">
                  <a class="btn secondary" href="${escapeHtml(item.url)}" target="_blank" rel="noopener" style="text-decoration:none;">${item.candidateSafe ? "Open AI profile chat" : "Open profile editor"}</a>
                  <button class="btn secondary" type="button" data-copy-completion-link="${index}">Copy link</button>
                </div>
              </div>
            `,
          )
          .join("")}
      </div>
      ${candidateLink ? `
        <div style="border:1px solid rgba(39,143,81,0.3); border-radius:12px; padding:10px; margin-top:12px; background:#f4fbf7;">
          <strong>Message to candidate</strong>
          <div style="white-space:pre-wrap; margin-top:6px; font-size:13px;">${escapeHtml(candidateMessage)}</div>
          <div class="row-actions" style="margin-top:10px;">
            <button class="btn secondary" type="button" data-copy-candidate-message>Copy message</button>
            ${email
              ? `<a class="btn" style="text-decoration:none;" href="mailto:${encodeURIComponent(email)}?subject=${subject}&body=${body}">Send link to candidate</a>`
              : `<span style="color:var(--muted);">No candidate email found. Copy the message and send it through your preferred channel.</span>`}
          </div>
        </div>
      ` : `<div style="margin-top:12px; color:var(--muted);">The AI profile/personality section is already complete. Internal DevReady profile actions are shown above.</div>`}
    `;

    panel.querySelectorAll("[data-copy-completion-link]").forEach((button) => {
      button.addEventListener("click", () => {
        const item = links[Number(button.dataset.copyCompletionLink)];
        if (item?.url) copyProfileCompletionText(item.url, `${item.label} link copied.`);
      });
    });
    panel.querySelector("[data-copy-candidate-message]")?.addEventListener("click", () => {
      copyProfileCompletionText(candidateMessage, "Candidate message copied.");
    });
  }

  async function renderInlineAiProfileActions(container, profileId, profileData) {
    const host = container.querySelector("[data-ai-profile-actions]");
    if (!host) return;
    try {
      const link = await profileCompletionLink(profileId);
      const email = candidateEmailFromProfile(profileData);
      const name = candidateNameFromProfile(profileData);
      const message = candidateAiProfileMessage(name, link);
      const subject = encodeURIComponent("Please complete your DevReady AI profile and personality chat");
      host.innerHTML = `
        <span class="profile-completion-link-ready">Secure candidate link ready</span>
        <div class="profile-completion-inline-actions">
          <a class="btn secondary" href="${escapeHtml(link)}" target="_blank" rel="noopener" style="text-decoration:none;">Open AI profile chat</a>
          <button class="btn secondary" type="button" data-copy-inline-ai-link>Copy link</button>
          <button class="btn secondary" type="button" data-copy-inline-ai-message>Copy message</button>
          ${email ? `<a class="btn" href="mailto:${encodeURIComponent(email)}?subject=${subject}&body=${encodeURIComponent(message)}" style="text-decoration:none;">Send link to candidate</a>` : ""}
        </div>
      `;
      host.querySelector("[data-copy-inline-ai-link]")?.addEventListener("click", () => {
        copyProfileCompletionText(link, "AI profile chat link copied.");
      });
      host.querySelector("[data-copy-inline-ai-message]")?.addEventListener("click", () => {
        copyProfileCompletionText(message, "Candidate message copied.");
      });
    } catch (error) {
      host.textContent = `Could not prepare the AI profile chat link: ${error.message || error}`;
    }
  }

  function showProfileCompletionNotice(message, isError = false) {
    let notice = document.getElementById("profileCompletionNotice");
    if (!notice) {
      notice = document.createElement("div");
      notice.id = "profileCompletionNotice";
      notice.className = "notice";
      notice.style.position = "fixed";
      notice.style.right = "18px";
      notice.style.bottom = "18px";
      notice.style.zIndex = "9999";
      notice.style.maxWidth = "520px";
      notice.style.boxShadow = "0 12px 26px rgba(0,0,0,0.16)";
      document.body.appendChild(notice);
    }
    notice.style.borderColor = isError ? "rgba(198, 40, 50, 0.35)" : "";
    notice.style.background = isError ? "#fff4f5" : "";
    notice.textContent = message;
    clearTimeout(notice._hideTimer);
    notice._hideTimer = setTimeout(() => {
      notice.remove();
    }, isError ? 12000 : 9000);
  }

  function completionButton(profileId, profileData, label) {
    const safeId = escapeHtml(profileId);
    const state = getProfileCompletionState(profileData);
    const buttonClass =
      state === "partial"
        ? "profile-completion-warning"
        : "profile-completion-danger";
    window.profileCompletionProfiles = window.profileCompletionProfiles || {};
    window.profileCompletionProfiles[String(profileId)] = profileData || {};
    return `<button class="btn ${buttonClass}" type="button" data-hint="Create sendable links for any missing regular profile, culture, or personality chat pieces." onclick="event.stopPropagation(); window.showProfileCompletionLinks('${safeId}', window.profileCompletionProfiles['${safeId}'])">${escapeHtml(label || "Completion links")}</button>`;
  }

  function normalizeProfileForCompletion(profile, status) {
    const firstName = profile.firstName || "";
    const lastName = profile.lastName || "";
    const fullName = profile.name || profile.full_name || status?.name || [firstName, lastName].filter(Boolean).join(" ");
    return {
      ...(status || {}),
      ...(profile || {}),
      profile: {
        id: profile.id || profile.profile_id || profile.personid || status?.profileId,
        firstName,
        lastName,
        name: fullName,
        email: profile.email || status?.email || "",
        title: profile.title || status?.title || "",
      },
    };
  }

  async function renderCompletionActionIfNeeded(profile, targetId, label) {
    const profileId = profile && (profile.id || profile.profile_id || profile.personid || profile.profileId);
    if (!profileId) return;

    const target = typeof targetId === "string" ? document.getElementById(targetId) : targetId;
    if (!target) return;

    try {
      const status = await profileNeedsCompletion(profileId);
      if (!status) {
        target.innerHTML = "";
        return;
      }
      target.innerHTML = completionButton(
        profileId,
        normalizeProfileForCompletion(profile, status),
        label || "Complete profile",
      );
    } catch (error) {
      console.warn("Could not check profile completion.", error);
    }
  }

  function renderProfileCompletionPanel(target, profileId, profileData) {
    const el = typeof target === "string" ? document.getElementById(target) : target;
    if (!el || !profileId) return;

    if (isProfileCompletionDone(profileData)) {
      el.innerHTML = "";
      el.style.display = "none";
      return;
    }

    const state = getProfileCompletionState(profileData);
    const isPartial = state === "partial";
    const pieces = getProfileCompletionPieces(profileData);
    const completedCount = Object.values(pieces).filter(Boolean).length;
    el.style.display = "block";
    const missing = missingProfileCompletionPieces(profileData);
    const checkMarkup = [
      ["regular", "Core profile"],
      ["personality", "Personality"],
      ["culture", "Culture"],
    ].map(([key, label]) => `<span class="profile-completion-check ${pieces[key] ? "done" : "missing"}">${escapeHtml(label)}</span>`).join("");
    const needsCandidateChat = missing.some((piece) => ["personality survey", "culture profile"].includes(piece));
    el.innerHTML = `
      <div class="profile-completion-panel ${isPartial ? "partial" : "missing"}">
        <div>
          <div class="profile-completion-heading">
            <span class="profile-completion-kicker">Profile completion</span>
            <span class="profile-completion-count">${completedCount} of 3 complete</span>
          </div>
          <strong class="profile-completion-title">${isPartial ? "Finish the remaining profile sections" : "Complete this profile before client sharing"}</strong>
          <div class="profile-completion-checks">${checkMarkup}</div>
          <div class="profile-completion-next">${needsCandidateChat
            ? "Next: send the secure AI chat so the candidate can complete the missing personality or culture sections."
            : "Next: open the internal profile editor to add the missing core profile details."}</div>
          ${needsCandidateChat
            ? `<div data-ai-profile-actions style="margin-top:8px;"><span>Preparing secure AI profile/personality link...</span></div>`
            : ""}
        </div>
        ${completionButton(profileId, profileData, needsCandidateChat ? "Completion options" : "Open completion options")}
      </div>
    `;
    if (needsCandidateChat) {
      renderInlineAiProfileActions(el, profileId, profileData);
    }
  }

  async function profileNeedsCompletion(profileId) {
    const status = await api(
      `/api/azure/profile/completionStatus/${encodeURIComponent(profileId)}?domain=${encodeURIComponent(currentDomain())}`,
    );
    return status && status.complete === false ? status : null;
  }

  window.profileCompletion = {
    currentDomain,
    escapeHtml,
    getProfileCompletionState,
    getProfileCompletionPieces,
    missingProfileCompletionPieces,
    isProfileCompletionDone,
    sendProfileCompletionChat,
    showProfileCompletionLinks,
    buildProfileCompletionLinks,
    completionButton,
    renderCompletionActionIfNeeded,
    renderProfileCompletionPanel,
    profileNeedsCompletion,
    showProfileCompletionNotice,
    candidateAiProfileMessage,
    copyProfileCompletionText,
  };
  window.escapeHtml = window.escapeHtml || escapeHtml;
  window.sendProfileCompletionChat = sendProfileCompletionChat;
  window.showProfileCompletionLinks = showProfileCompletionLinks;
})();
