function updateCandidate() {
    const candidateName = sessionStorage.getItem("candidateName");
    const profileType = sessionStorage.getItem("candidateProfileType");
    const suffix = profileType ? ` (${profileType})` : "";

    if (candidateName) {
        document.getElementById("candidateSelected").innerText = `Selected Candidate: ${candidateName}${suffix}`;
    } else {
        document.getElementById("candidateSelected").innerText = "No Candidate Selected";
    }
    
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
