(function () {
  const globalKey = "shortlist";

  function domain() {
    return sessionStorage.getItem("domain") || "dev";
  }

  function detailsKey() {
    return `shortlistDetails:${domain()}`;
  }

  function idsKey() {
    return `shortlist:${domain()}`;
  }

  function activeKey() {
    return `activeCandidateId:${domain()}`;
  }

  function activeSnapshotKey() {
    return `activeCandidate:${domain()}`;
  }

  function readIds() {
    return (sessionStorage.getItem(idsKey()) || sessionStorage.getItem(globalKey) || "")
      .split(",")
      .map((id) => id.trim())
      .filter(Boolean);
  }

  function writeIds(ids) {
    const clean = [...new Set((ids || []).map((id) => String(id)).filter(Boolean))];
    sessionStorage.setItem(idsKey(), clean.join(","));
    sessionStorage.setItem(globalKey, clean.join(","));
    return clean;
  }

  function readDetails() {
    try {
      return JSON.parse(sessionStorage.getItem(detailsKey()) || sessionStorage.getItem("shortlistDetails") || "{}");
    } catch {
      return {};
    }
  }

  function writeDetails(details) {
    sessionStorage.setItem(detailsKey(), JSON.stringify(details || {}));
    sessionStorage.setItem("shortlistDetails", JSON.stringify(details || {}));
  }

  function writeActiveId(id) {
    const cleanId = String(id || "");
    if (cleanId) {
      sessionStorage.setItem(activeKey(), cleanId);
      sessionStorage.setItem("activeCandidateId", cleanId);
    } else {
      sessionStorage.removeItem(activeKey());
      sessionStorage.removeItem("activeCandidateId");
      sessionStorage.removeItem(activeSnapshotKey());
      sessionStorage.removeItem("activeCandidate");
    }
    return cleanId;
  }

  function readActiveId() {
    const ids = readIds();
    if (!ids.length) return writeActiveId("");
    const stored = sessionStorage.getItem(activeKey()) || sessionStorage.getItem("activeCandidateId") || "";
    if (ids.includes(String(stored))) return String(stored);
    const currentCandidate = String(sessionStorage.getItem("candidateId") || "");
    return writeActiveId(ids.includes(currentCandidate) ? currentCandidate : ids[0]);
  }

  function fullName(candidate) {
    return (
      candidate.name ||
      candidate.fullName ||
      [candidate.firstName, candidate.lastName].filter(Boolean).join(" ") ||
      "Candidate"
    ).trim();
  }

  function candidateId(candidate) {
    return candidate.id || candidate.profile_id || candidate.personid || candidate.personId || "";
  }

  function normalize(candidate, extra = {}) {
    const { makeActive: _makeActive, ...metadata } = extra;
    const id = String(candidateId(candidate));
    const score = candidate.matchPercentage ?? candidate.match_percent ?? candidate.score ?? candidate._score100 ?? "";
    return {
      id,
      name: fullName(candidate),
      firstName: candidate.firstName || "",
      lastName: candidate.lastName || "",
      email: candidate.email || "",
      title: candidate.title || candidate.primaryStack || "",
      status: candidate.status || candidate.step || candidate.candidateStatus || "",
      profileType: candidate.profileType || candidate.candidateProfileType || "",
      source: candidate.source_label || candidate.source || metadata.source || "DevReady",
      score: score === "" || score === null || Number.isNaN(Number(score)) ? "" : Math.round(Number(score)),
      skills: candidate.skills || candidate.skillMatches || candidate.top_matches || [],
      topMatches: candidate.top_matches || candidate.skillMatches || candidate.skills || [],
      stage: metadata.stage || candidate.stage || "1 - Talent",
      selectedAt: candidate.selectedAt || new Date().toISOString(),
      archived: false,
      ...metadata,
    };
  }

  function add(candidate, extra = {}) {
    const item = normalize(candidate, extra);
    if (!item.id) return null;
    const ids = writeIds([...readIds(), item.id]);
    const details = readDetails();
    details[item.id] = { ...(details[item.id] || {}), ...item };
    writeDetails(details);
    if (extra.makeActive !== false) setActive(details[item.id]);
    render();
    return details[item.id];
  }

  function addMany(candidates, extra = {}) {
    const added = [];
    (candidates || []).forEach((candidate, index) => {
      const item = add(candidate, { ...extra, makeActive: index === 0 && extra.makeActive !== false });
      if (item) added.push(item);
    });
    render();
    return added;
  }

  function remove(id) {
    const targetId = String(id);
    const ids = readIds();
    const targetIndex = ids.indexOf(targetId);
    const wasActive = readActiveId() === targetId;
    const remainingIds = writeIds(ids.filter((row) => row !== targetId));
    const details = readDetails();
    delete details[targetId];
    writeDetails(details);
    if (wasActive) {
      const replacementId = remainingIds[Math.min(Math.max(targetIndex, 0), remainingIds.length - 1)] || "";
      if (replacementId && details[replacementId]) setActive(details[replacementId]);
      else clearActiveCandidate();
    }
    render();
  }

  function archive(id) {
    const targetId = String(id);
    const ids = readIds();
    const targetIndex = ids.indexOf(targetId);
    const wasActive = readActiveId() === targetId;
    const details = readDetails();
    if (details[targetId]) {
      details[targetId].archived = true;
      details[targetId].status = "Closed / archived";
      details[targetId].archivedAt = new Date().toISOString();
      writeDetails(details);
    }
    const remainingIds = writeIds(ids.filter((row) => row !== targetId));
    if (wasActive) {
      const replacementId = remainingIds[Math.min(Math.max(targetIndex, 0), remainingIds.length - 1)] || "";
      if (replacementId && details[replacementId]) setActive(details[replacementId]);
      else clearActiveCandidate();
    }
    render();
  }

  function list(includeArchived = false) {
    const ids = readIds();
    const details = readDetails();
    return ids
      .map((id) => details[id] || { id, name: `Candidate ${id}` })
      .filter((item) => includeArchived || !item.archived);
  }

  function active() {
    const id = readActiveId();
    if (!id) return null;
    const details = readDetails();
    return details[id] || { id, name: sessionStorage.getItem("candidateName") || `Candidate ${id}` };
  }

  function position() {
    const rows = list();
    const id = readActiveId();
    const index = rows.findIndex((item) => String(item.id) === String(id));
    return {
      id,
      index,
      number: index >= 0 ? index + 1 : 0,
      total: rows.length,
      candidate: index >= 0 ? rows[index] : null,
      next: index >= 0 && index + 1 < rows.length ? rows[index + 1] : null,
    };
  }

  function clearActiveCandidate() {
    writeActiveId("");
    sessionStorage.removeItem("activeCandidateName");
    sessionStorage.removeItem("activeCandidateEmail");
    [
      "candidateId",
      "selectedProfileId",
      "candidateName",
      "candidateEmail",
      "candidateStatus",
      "candidateProfileType",
      "candidateTopMatches",
      "externalCandidateSource",
    ].forEach((key) => sessionStorage.removeItem(key));
    window.dispatchEvent(new CustomEvent("devready-active-candidate-changed", { detail: { candidate: null, position: position() } }));
  }

  function setActive(candidate) {
    if (!candidate || !candidate.id) return;
    const id = writeActiveId(candidate.id);
    const candidateName = candidate.name || fullName(candidate) || "Candidate";
    const snapshot = { ...candidate, id, name: candidateName };
    sessionStorage.setItem(activeSnapshotKey(), JSON.stringify(snapshot));
    sessionStorage.setItem("activeCandidate", JSON.stringify(snapshot));
    sessionStorage.setItem("activeCandidateName", candidateName);
    sessionStorage.setItem("activeCandidateEmail", candidate.email || "");
    sessionStorage.setItem("candidateId", id);
    sessionStorage.setItem("selectedProfileId", id);
    sessionStorage.setItem("candidateName", candidateName);
    sessionStorage.setItem("candidateEmail", candidate.email || "");
    sessionStorage.setItem("candidateDomain", domain());
    const status = candidate.status || candidate.stage || "";
    if (status) sessionStorage.setItem("candidateStatus", status);
    else sessionStorage.removeItem("candidateStatus");
    if (candidate.profileType) sessionStorage.setItem("candidateProfileType", candidate.profileType);
    else sessionStorage.removeItem("candidateProfileType");
    if (candidate.source) sessionStorage.setItem("externalCandidateSource", candidate.source);
    else sessionStorage.removeItem("externalCandidateSource");
    if (candidate.topMatches && candidate.topMatches.length) {
      sessionStorage.setItem("candidateTopMatches", candidate.topMatches.join(", "));
    } else sessionStorage.removeItem("candidateTopMatches");
    if (typeof window.updateCandidate === "function") window.updateCandidate();
    window.dispatchEvent(new CustomEvent("devready-active-candidate-changed", { detail: { candidate: snapshot, position: position() } }));
    return snapshot;
  }

  function restoreActive() {
    const candidate = active();
    if (candidate) setActive(candidate);
    return candidate;
  }

  function update(id, patch = {}) {
    const targetId = String(id || "");
    if (!targetId) return null;
    const details = readDetails();
    const existing = details[targetId] || { id: targetId, name: `Candidate ${targetId}` };
    details[targetId] = { ...existing, ...patch, id: targetId };
    writeDetails(details);
    if (readActiveId() === targetId) setActive(details[targetId]);
    render();
    return details[targetId];
  }

  function advance() {
    const state = position();
    if (!state.next) return null;
    setActive(state.next);
    render();
    return { candidate: state.next, position: position() };
  }

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function render(targetId = "shortlistTray") {
    const target = document.getElementById(targetId);
    if (!target) return;
    const rows = list();
    if (!rows.length) {
      target.innerHTML = `<div class="notice">No candidates selected yet. Check 2-3 matches, then add them to the shortlist.</div>`;
      return;
    }
    const activeState = position();
    const activeId = activeState.id;
    target.innerHTML = `
      <div class="shortlist-tray-head">
        <div>
          <h2>Selected candidate shortlist</h2>
          <p>These candidates stay in selection order. One active candidate drives profile review, scheduling, interviews, communications, and status records until you move to the next person.</p>
        </div>
        <div class="row-actions">
          <span class="pill">${activeState.number || 1} of ${rows.length} active</span>
          ${activeState.next ? `<button class="btn secondary" type="button" onclick="DevReadyShortlist.activateNext()">Next candidate</button>` : ""}
        </div>
      </div>
      <div class="shortlist-sections">
        ${rows
          .map(
            (item, index) => {
              const isActive = String(item.id) === activeId;
              const statusLabel = item.status || item.stage || "Selected";
              const profileTypeLabel =
                item.profileType && String(item.profileType).toLowerCase() !== String(statusLabel).toLowerCase()
                  ? item.profileType
                  : "";
              return `
              <div class="shortlist-section">
                <div class="shortlist-section-title">Candidate ${index + 1}</div>
                <strong>${escapeHtml(item.name)}</strong>
                <div class="muted">${escapeHtml(item.email || item.title || "No email listed")}</div>
                <div class="pills">
                  ${item.score !== "" && item.score !== undefined ? `<span class="pill">${escapeHtml(item.score)}% match</span>` : ""}
                  <span class="pill">${escapeHtml(statusLabel)}</span>
                  ${profileTypeLabel ? `<span class="pill">${escapeHtml(profileTypeLabel)}</span>` : ""}
                  ${item.workflowStatus ? `<span class="pill">${escapeHtml(item.workflowStatus)}</span>` : ""}
                  ${isActive ? `<span class="pill">Active candidate ${index + 1} of ${rows.length}</span>` : ""}
                </div>
                <div class="row-actions" style="margin-top:8px">
                  <button class="btn secondary" type="button" onclick="DevReadyShortlist.activate('${escapeHtml(item.id)}')" ${isActive ? "disabled" : ""} aria-label="${isActive ? "Active candidate" : `Select ${escapeHtml(item.name)} as active candidate`}">${isActive ? "Active" : "Set active"}</button>
                  <button class="btn secondary" type="button" onclick="DevReadyShortlist.remove('${escapeHtml(item.id)}')">Remove</button>
                </div>
              </div>
            `;
            },
          )
          .join("")}
      </div>
    `;
  }

  function activate(id) {
    const details = readDetails();
    const item = details[String(id)];
    if (!item) return;
    const approved = confirm(
      `Set ${item.name || "this candidate"} as the active candidate? This updates the workspace header and makes this profile the candidate used by the next review, chat, and workflow actions.`,
    );
    if (!approved) return;
    setActive(item);
    render();
    alert(`${item.name || "Candidate"} is now active.`);
  }

  function activateNext() {
    const state = position();
    if (!state.next) return alert("This is the last candidate in the shortlist.");
    const approved = confirm(
      `Move from ${state.candidate?.name || "the active candidate"} to candidate ${state.number + 1} of ${state.total}, ${state.next.name || "Candidate"}?`,
    );
    if (!approved) return null;
    const result = advance();
    if (result) alert(`${result.candidate.name || "Candidate"} is now active (${result.position.number} of ${result.position.total}).`);
    return result;
  }

  window.DevReadyShortlist = {
    add,
    addMany,
    archive,
    remove,
    list,
    active,
    activeId: readActiveId,
    position,
    readDetails,
    writeDetails,
    setActive,
    restoreActive,
    update,
    advance,
    activateNext,
    activate,
    render,
    normalize,
  };

  document.addEventListener("DOMContentLoaded", () => {
    restoreActive();
    render();
  });
})();
