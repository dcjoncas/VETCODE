(function () {
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
    const profile = profileData && profileData.profile ? profileData.profile : profileData || {};
    const email = profile.email || profileData?.email || sessionStorage.getItem("candidateEmail") || "";
    const name = candidateNameFromProfile(profileData);
    const subject = encodeURIComponent("Please finish your DevReady profile");
    const body = encodeURIComponent(
      `Hi ${name},\n\nPlease complete your DevReady profile using this secure chat link:\n\n${link}\n\nThis helps us finish your profile for the right domain and next steps.\n\nBest,\nDevReady Team`,
    );

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
    const links = missing.map((piece) => linkForMissingPiece(profileId, piece));
    const personality = links.find((item) => item.async);
    if (personality) {
      personality.url = await profileCompletionLink(profileId);
      delete personality.async;
    }
    return links;
  }

  async function showProfileCompletionLinks(profileId, profileData) {
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

    const profile = stored && stored.profile ? stored.profile : stored || {};
    const email = profile.email || stored?.email || sessionStorage.getItem("candidateEmail") || "";
    const name = candidateNameFromProfile(stored);
    const linkText = links.map((item) => `${item.label}: ${item.url}`).join("\n");
    try {
      await navigator.clipboard.writeText(linkText);
    } catch (error) {
      console.warn("Could not copy completion links.", error);
    }

    showProfileCompletionLinksPanel(links, email, name);
    return links;
  }

  function showProfileCompletionLinksPanel(links, email, name) {
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
      panel.style.boxShadow = "0 12px 26px rgba(0,0,0,0.16)";
      document.body.appendChild(panel);
    }

    const subject = encodeURIComponent("Please complete your DevReady profile");
    const body = encodeURIComponent(
      `Hi ${name || "Candidate"},\n\nPlease complete the missing DevReady profile sections below:\n\n${links
        .map((item) => `${item.label}: ${item.url}`)
        .join("\n")}\n\nBest,\nDevReady Team`,
    );

    panel.innerHTML = `
      <div style="display:flex; justify-content:space-between; gap:12px; align-items:flex-start;">
        <div>
          <strong>Profile completion links copied</strong>
          <div style="margin-top:4px; color:var(--muted);">Send only the missing sections for this candidate.</div>
        </div>
        <button class="btn secondary" type="button" style="padding:6px 10px;" onclick="document.getElementById('profileCompletionLinksPanel')?.remove()">Close</button>
      </div>
      <div style="display:grid; gap:8px; margin-top:12px;">
        ${links
          .map(
            (item) => `
              <div style="border:1px solid var(--line); border-radius:12px; padding:10px; background:#fff;">
                <div style="font-weight:900;">${escapeHtml(item.label)}</div>
                <div style="font-size:13px; color:var(--muted);">${escapeHtml(item.description)}</div>
                <a href="${escapeHtml(item.url)}" target="_blank" rel="noopener" style="display:block; margin-top:6px; overflow-wrap:anywhere;">${escapeHtml(item.url)}</a>
              </div>
            `,
          )
          .join("")}
      </div>
      ${
        email
          ? `<a class="btn" style="display:inline-flex; margin-top:12px; text-decoration:none;" href="mailto:${encodeURIComponent(email)}?subject=${subject}&body=${body}">Email ${escapeHtml(email)}</a>`
          : `<div style="margin-top:12px; color:var(--muted);">No email found. Links are copied so you can paste them where needed.</div>`
      }
    `;
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
    el.style.display = "block";
    const missing = missingProfileCompletionPieces(profileData);
    el.innerHTML = `
      <div class="profile-completion-panel ${isPartial ? "partial" : "missing"}">
        <div>
          <strong>${isPartial ? "Profile partially complete" : "Profile needs completion"}</strong>
          <div>Missing: ${escapeHtml(missing.join(", "))}. Create sendable links for only the pieces this person still needs.</div>
        </div>
        ${completionButton(profileId, profileData, "Create completion links")}
      </div>
    `;
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
  };
  window.escapeHtml = window.escapeHtml || escapeHtml;
  window.sendProfileCompletionChat = sendProfileCompletionChat;
  window.showProfileCompletionLinks = showProfileCompletionLinks;
})();
