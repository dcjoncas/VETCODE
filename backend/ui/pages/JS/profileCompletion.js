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

  function getProfileCompletionState(profileData) {
    if (profileData && profileData.state) return profileData.state;

    const personalityComplete =
      Array.isArray(profileData && profileData.personality) &&
      profileData.personality.some((item) => item && item.title && item.score);
    const culturalExperienceComplete =
      Array.isArray(profileData && profileData.culturalExperience) &&
      profileData.culturalExperience.some(
        (item) => item && item.title && Number(item.level) > 0,
      );
    if (personalityComplete && culturalExperienceComplete) return "complete";
    if (personalityComplete || culturalExperienceComplete) return "partial";
    return "missing";
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

    const link = await profileCompletionLink(profileId);
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

    if (email) {
      window.location.href = `mailto:${encodeURIComponent(email)}?subject=${subject}&body=${body}`;
    } else {
      alert(`Profile completion link copied:\n${link}`);
    }

    return link;
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
    return `<button class="btn ${buttonClass}" type="button" data-hint="Send this candidate a secure chat link to complete their profile for this domain." onclick="event.stopPropagation(); window.sendProfileCompletionChat('${safeId}', window.profileCompletionProfiles['${safeId}'])">${escapeHtml(label || "Finish profile chat")}</button>`;
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
    el.innerHTML = `
      <div class="profile-completion-panel ${isPartial ? "partial" : "missing"}">
        <div>
          <strong>${isPartial ? "Profile partially complete" : "Profile needs completion"}</strong>
          <div>${isPartial ? "This profile has either culture or personality saved, but still needs the remaining profile answers for this domain." : "This profile is missing culture/personality answers for the current domain."}</div>
        </div>
        ${completionButton(profileId, profileData, "Send finish-profile chat")}
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
    isProfileCompletionDone,
    sendProfileCompletionChat,
    completionButton,
    renderCompletionActionIfNeeded,
    renderProfileCompletionPanel,
    profileNeedsCompletion,
  };
  window.escapeHtml = window.escapeHtml || escapeHtml;
  window.sendProfileCompletionChat = sendProfileCompletionChat;
})();
