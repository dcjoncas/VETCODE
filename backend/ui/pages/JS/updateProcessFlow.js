function updateCandidate() {
    const candidateName = sessionStorage.getItem("candidateName");

    if (candidateName) {
        document.getElementById("candidateSelected").innerText = `Selected Candidate: ${candidateName}`;
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