(function (root) {
  "use strict";
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const scopeNames = { dev: "Technology", engineer: "Engineering", law: "Legal", dental: "Dental" };
  function contacts(profile) {
    const emails = (Array.isArray(profile.emails) ? profile.emails : [profile.email]).filter((v) => typeof v === "string" && /^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$/.test(v));
    const phones = (Array.isArray(profile.phones) ? profile.phones : [profile.phone]).filter((v) => typeof v === "string" && /^[\d\s+().xX-]+$/.test(v) && v.replace(/\D/g, "").length >= 7);
    let linkedin = "";
    try {
      const url = new URL(profile.linkedinUrl || "");
      if (/^https?:$/.test(url.protocol) && !url.username && !url.password && /(^|\.)linkedin\.com$/i.test(url.hostname) && /^\/(in|pub)\/[^/]+/.test(url.pathname)) linkedin = url.href;
    } catch (_) { /* Unknown saved link stays unavailable. */ }
    return `<div class="contact-lines"><div><strong>Email:</strong> ${emails.length ? emails.map((v) => `<a href="mailto:${escape(encodeURIComponent(v))}">${escape(v)}</a>`).join(" · ") : "Not saved"}</div>
      <div><strong>Phone:</strong> ${phones.length ? phones.map((v) => `<a href="tel:${escape(v.replace(/[^\d+]/g, ""))}">${escape(v)}</a>`).join(" · ") : "Not saved"}</div>
      <div><strong>LinkedIn:</strong> ${linkedin ? `<a href="${escape(linkedin)}" target="_blank" rel="noopener noreferrer">Open LinkedIn profile</a>` : "Not saved"}</div></div>`;
  }
  function renderRows(profiles, domain) {
    return profiles.map((profile) => {
      const jobId = String(profile.interestJobId || "");
      const job = profile.interestJobTitle || (jobId ? `JD ${jobId}` : "No job recorded");
      const profileHref = `profile-preview.html?domain=${encodeURIComponent(domain)}&profileId=${encodeURIComponent(profile.personid)}`;
      return `<article class="interested-row"><div class="candidate-details"><h3><a href="${escape(profileHref)}">${escape(profile.name)}</a></h3>
        <p class="muted">${escape(profile.title || "Title not saved")}${profile.location ? ` · ${escape(profile.location)}` : ""}</p>${contacts(profile)}
        ${profile.summary ? `<details><summary>Saved qualifications brief</summary><p>${escape(profile.summary)}</p></details>` : ""}</div>
        <div class="interest-details"><span class="pill">Interest confirmed</span><p><strong>${escape(job)}</strong></p>
        <p class="muted">${profile.interestConfirmedAt ? `Recorded: ${escape(profile.interestConfirmedAt)}` : "Confirmation date not saved"}</p>
        <p class="muted">${profile.temporary ? "TEMP profile" : "Saved profile"} · ${escape(profile.source || "Saved record")}</p>
        <a class="btn primary" href="${escape(profileHref)}">Review candidate profile</a></div></article>`;
    }).join("");
  }
  function createLoader(request, domain) {
    if (!scopeNames[domain]) throw new Error("Select a supported workspace.");
    let version = 0, profiles = [], cursor = null, more = true;
    return {
      clear() { version += 1; profiles = []; cursor = null; more = true; },
      async load(reset = false) {
        if (reset) this.clear();
        const activeVersion = version;
        const params = new URLSearchParams({ domain, limit: "50" });
        if (cursor !== null) params.set("after", cursor);
        const data = await request(`/api/azureJobs/external/interested?${params}`);
        if (version !== activeVersion) return null;
        if (data.domain !== domain || !Array.isArray(data.profiles)) throw new Error("Report returned a different workspace. Refresh to retry.");
        const seen = new Set(profiles.map((p) => String(p.personid)));
        data.profiles.forEach((p) => { if (!seen.has(String(p.personid))) { profiles.push(p); seen.add(String(p.personid)); } });
        more = data.hasMore === true;
        if (more && (data.nextCursor == null || String(data.nextCursor) === String(cursor))) throw new Error("Report did not advance. Refresh to retry.");
        cursor = more ? String(data.nextCursor) : null;
        return { profiles: profiles.slice(), hasMore: more };
      },
    };
  }
  const exported = { contacts, renderRows, createLoader, scopeNames };
  root.DevReadyInterestedCandidates = exported;
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
})(typeof window === "undefined" ? globalThis : window);
