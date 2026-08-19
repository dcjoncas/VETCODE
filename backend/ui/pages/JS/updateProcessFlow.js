function processCandidateQueue() {
    const domain = sessionStorage.getItem("domain") || "dev";
    const ids = (sessionStorage.getItem(`shortlist:${domain}`) || sessionStorage.getItem("shortlist") || "")
        .split(",")
        .map((id) => id.trim())
        .filter(Boolean);
    let details = {};
    try {
        details = JSON.parse(sessionStorage.getItem(`shortlistDetails:${domain}`) || sessionStorage.getItem("shortlistDetails") || "{}") || {};
    } catch {}
    let activeId = sessionStorage.getItem(`activeCandidateId:${domain}`) || sessionStorage.getItem("activeCandidateId") || "";
    if (!ids.includes(String(activeId))) {
        const currentId = sessionStorage.getItem("candidateId") || "";
        activeId = ids.includes(String(currentId)) ? String(currentId) : (ids[0] || currentId);
        if (activeId && ids.includes(String(activeId))) {
            sessionStorage.setItem(`activeCandidateId:${domain}`, String(activeId));
            sessionStorage.setItem("activeCandidateId", String(activeId));
        }
    }
    let snapshot = {};
    try {
        snapshot = JSON.parse(sessionStorage.getItem(`activeCandidate:${domain}`) || sessionStorage.getItem("activeCandidate") || "{}") || {};
    } catch {}
    const candidate = details[String(activeId)] || snapshot || {};
    const index = ids.indexOf(String(activeId));
    return {
        domain,
        ids,
        details,
        activeId: String(activeId || ""),
        candidate,
        index,
        number: index >= 0 ? index + 1 : 0,
        total: ids.length,
        nextId: index >= 0 ? (ids[index + 1] || "") : "",
    };
}

function updateCandidate() {
    const target = document.getElementById("candidateSelected");
    if (!target) return;
    const queue = processCandidateQueue();
    const candidateName = queue.candidate.name || sessionStorage.getItem("activeCandidateName") || sessionStorage.getItem("candidateName");
    const profileType = queue.candidate.profileType || sessionStorage.getItem("candidateProfileType");
    const suffix = profileType ? ` (${profileType})` : "";
    const position = queue.total ? `${queue.number || 1} of ${queue.total}: ` : "";
    target.innerText = candidateName
        ? `Active Candidate ${position}${candidateName}${suffix}`
        : "No Active Candidate";
    target.classList.toggle("has-active-candidate", Boolean(candidateName));
    target.setAttribute("aria-live", "polite");

    const nextButton = document.getElementById("processNextCandidate");
    if (nextButton) {
        const next = queue.details[String(queue.nextId)] || {};
        nextButton.hidden = !queue.nextId;
        nextButton.disabled = !queue.nextId;
        nextButton.textContent = queue.nextId
            ? `Next: ${next.name || `Candidate ${queue.number + 1}`}`
            : "End of shortlist";
    }
}

function activateNextProcessCandidate() {
    if (window.DevReadyShortlist && typeof window.DevReadyShortlist.activateNext === "function") {
        const result = window.DevReadyShortlist.activateNext();
        updateCandidate();
        return result;
    }
    const queue = processCandidateQueue();
    if (!queue.nextId) return alert("This is the last candidate in the shortlist.");
    const next = queue.details[String(queue.nextId)] || { id: queue.nextId, name: `Candidate ${queue.number + 1}` };
    if (!confirm(`Move to candidate ${queue.number + 1} of ${queue.total}, ${next.name || "Candidate"}?`)) return null;
    sessionStorage.setItem(`activeCandidateId:${queue.domain}`, String(queue.nextId));
    sessionStorage.setItem("activeCandidateId", String(queue.nextId));
    sessionStorage.setItem(`activeCandidate:${queue.domain}`, JSON.stringify(next));
    sessionStorage.setItem("activeCandidate", JSON.stringify(next));
    sessionStorage.setItem("activeCandidateName", next.name || "Candidate");
    sessionStorage.setItem("activeCandidateEmail", next.email || "");
    sessionStorage.setItem("candidateId", String(queue.nextId));
    sessionStorage.setItem("selectedProfileId", String(queue.nextId));
    sessionStorage.setItem("candidateName", next.name || "Candidate");
    sessionStorage.setItem("candidateEmail", next.email || "");
    window.dispatchEvent(new CustomEvent("devready-active-candidate-changed", { detail: { candidate: next } }));
    updateCandidate();
    return next;
}

function updateJob() {
    const jobTitle = sessionStorage.getItem("jobTitle");
    const jobCompany = sessionStorage.getItem("jobCompany");

    if (jobTitle && jobCompany) {
        document.getElementById("jobSelected").innerText = `Selected Job: ${jobTitle} at ${jobCompany}`;
    } else if (jobTitle) {
        document.getElementById("jobSelected").innerText = `Selected Job: ${jobTitle}`;
    } else {
        document.getElementById("jobSelected").innerText = "No Job Selected";
    }
}

function updateAtlasClient() {
    const domain = sessionStorage.getItem("domain") || "dev";
    const target = document.getElementById("clientSelected");
    if (!target) return;
    try {
        const client = JSON.parse(sessionStorage.getItem(`atlasSourcingClient:${domain}`) || "null");
        target.innerText = client && client.name
            ? `Atlas Client: ${client.name}`
            : "No Atlas Client Attached";
    } catch {
        target.innerText = "No Atlas Client Attached";
    }
}
