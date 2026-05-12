(function () {
  const globalKey = "shortlist";

  function domain() {
    return sessionStorage.getItem("domain") || "dev";
  }

  function detailsKey() {
    return `shortlistDetails:${domain()}`;
  }

  function readIds() {
    return (sessionStorage.getItem(globalKey) || "")
      .split(",")
      .map((id) => id.trim())
      .filter(Boolean);
  }

  function writeIds(ids) {
    const clean = [...new Set((ids || []).map((id) => String(id)).filter(Boolean))];
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
      source: candidate.source_label || candidate.source || extra.source || "DevReady",
      score: score === "" || score === null || Number.isNaN(Number(score)) ? "" : Math.round(Number(score)),
      skills: candidate.skills || candidate.skillMatches || candidate.top_matches || [],
      topMatches: candidate.top_matches || candidate.skillMatches || candidate.skills || [],
      stage: extra.stage || candidate.stage || "1 - Talent",
      selectedAt: candidate.selectedAt || new Date().toISOString(),
      archived: false,
      ...extra,
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
    writeIds(readIds().filter((row) => row !== targetId));
    const details = readDetails();
    delete details[targetId];
    writeDetails(details);
    render();
  }

  function archive(id) {
    const targetId = String(id);
    const details = readDetails();
    if (details[targetId]) {
      details[targetId].archived = true;
      details[targetId].status = "Closed / archived";
      details[targetId].archivedAt = new Date().toISOString();
      writeDetails(details);
    }
    writeIds(readIds().filter((row) => row !== targetId));
    render();
  }

  function list(includeArchived = false) {
    const ids = readIds();
    const details = readDetails();
    return ids
      .map((id) => details[id] || { id, name: `Candidate ${id}` })
      .filter((item) => includeArchived || !item.archived);
  }

  function setActive(candidate) {
    if (!candidate || !candidate.id) return;
    sessionStorage.setItem("candidateId", String(candidate.id));
    sessionStorage.setItem("candidateName", candidate.name || "Candidate");
    sessionStorage.setItem("candidateEmail", candidate.email || "");
    sessionStorage.setItem("candidateStatus", candidate.status || candidate.stage || "");
    if (candidate.profileType) sessionStorage.setItem("candidateProfileType", candidate.profileType);
    if (candidate.source) sessionStorage.setItem("externalCandidateSource", candidate.source);
    if (candidate.topMatches && candidate.topMatches.length) {
      sessionStorage.setItem("candidateTopMatches", candidate.topMatches.join(", "));
    }
    if (typeof window.updateCandidate === "function") window.updateCandidate();
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
    target.innerHTML = `
      <div class="shortlist-tray-head">
        <div>
          <h2>Selected candidate shortlist</h2>
          <p>These candidates now move together through profile review, chat, client comms, interviews, and status.</p>
        </div>
        <span class="pill">${rows.length} selected</span>
      </div>
      <div class="shortlist-sections">
        ${rows
          .map(
            (item, index) => `
              <div class="shortlist-section">
                <div class="shortlist-section-title">Section ${index + 1}</div>
                <strong>${escapeHtml(item.name)}</strong>
                <div class="muted">${escapeHtml(item.email || item.title || "No email listed")}</div>
                <div class="pills">
                  ${item.score !== "" && item.score !== undefined ? `<span class="pill">${escapeHtml(item.score)}% match</span>` : ""}
                  <span class="pill">${escapeHtml(item.status || item.stage || "Selected")}</span>
                  ${item.profileType ? `<span class="pill">${escapeHtml(item.profileType)}</span>` : ""}
                </div>
                <div class="row-actions" style="margin-top:8px">
                  <button class="btn secondary" type="button" onclick="DevReadyShortlist.activate('${escapeHtml(item.id)}')">Use</button>
                  <button class="btn secondary" type="button" onclick="DevReadyShortlist.remove('${escapeHtml(item.id)}')">Remove</button>
                </div>
              </div>
            `,
          )
          .join("")}
      </div>
    `;
  }

  function activate(id) {
    const details = readDetails();
    const item = details[String(id)];
    if (item) setActive(item);
  }

  window.DevReadyShortlist = {
    add,
    addMany,
    archive,
    remove,
    list,
    readDetails,
    writeDetails,
    setActive,
    activate,
    render,
    normalize,
  };

  document.addEventListener("DOMContentLoaded", () => render());
})();
