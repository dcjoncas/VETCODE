(function (root) {
  "use strict";
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
  const list = (value) => (Array.isArray(value) ? value : value ? [value] : []).map((item) => String(item).trim().toLowerCase()).filter(Boolean).sort();
  const validScore = (value) => typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 100;
  function criteriaKey(value = {}) {
    return JSON.stringify({
      titles: list(value.titles), skills: list(value.mustHaveSkills || value.requiredSkills),
      locations: list(value.locations), region: String(value.region || "").toLowerCase(),
      minYears: Number(value.minYears) || 0, experience: list(value.experienceRanges),
      credentials: list(value.licensesOrCertifications || value.licenseOrCertification),
      arrangements: list(value.workArrangements || value.workArrangement),
      workforce: list(value.workforceLocations || value.workforceLocation),
      ignored: list(value.ignoredCriteria), ignoreAll: value.ignoreAll === true,
      strictLocations: value.strictLocations === true,
    });
  }
  function view(candidate = {}, jobId = "", criteria) {
    const match = candidate.match || candidate.saved_match || {};
    const current = Boolean(jobId) && String(match.jobId || "") === String(jobId)
      && match.evidenceType === "structured_professional_profile"
      && (!criteria || criteriaKey(match.criteriaSnapshot) === criteriaKey(criteria));
    return {
      match, current,
      score: current && validScore(match.score) ? match.score : null,
      label: current && validScore(match.score) ? `${Math.round(match.score)}% JD match` : current ? "JD match unavailable" : jobId ? "Calculate current JD fit" : "Choose a JD to assess fit",
      detail: current ? (match.reason || "Review the supporting evidence and unknowns.") : "Calculate from the returned professional evidence. No contact lookup or provider credits are needed.",
    };
  }
  function summary(match = {}) {
    const rows = Array.isArray(match.criteria) ? match.criteria : [];
    const groups = [['matched', 'Supported'], ['unknown', 'Not evidenced'], ['gap', 'Conflicting evidence']];
    return groups.map(([status, label]) => {
      const items = rows.filter((row) => row.scored !== false && row.status === status);
      const text = items.slice(0, 5).map((row) => escape(row.label)).join(', ');
      return `<div class="meta"><strong>${label} (${items.length}):</strong> ${text || 'None reported'}${items.length > 5 ? `; +${items.length - 5} more in the breakdown` : ''}</div>`;
    }).join('');
  }
  function details(match = {}) {
    const rows = Array.isArray(match.criteria) ? match.criteria : [];
    const statusLabels = { matched: 'Supported', gap: 'Conflicting evidence', unknown: 'Unknown', ignored: 'Ignored' };
    return `<div class="match-score-summary"><div class="match-score-number">${validScore(match.score) ? `${Math.round(match.score)}%` : "—"}</div><div><strong>Support for reviewed job requirements</strong><p>${escape(match.reason)}</p><div>${validScore(match.coveragePercent) ? `${Math.round(match.coveragePercent)}% evidence coverage` : "Evidence coverage unavailable"} · not a hiring probability</div></div></div>
      ${summary(match)}
      <div class="match-stat-grid">${rows.length ? rows.map((row) => `<section class="match-stat-card"><h4>${escape(row.label)}</h4><strong>${escape(statusLabels[row.status] || 'Unknown')}</strong>${row.required ? ' · required' : ''}${row.scored === false ? ' · outside score' : ''}<p>${escape(row.reason)}</p>${(row.evidence || []).length ? row.evidence.map((item) => `<blockquote>${escape(item.text)}<footer>${escape(item.field)}</footer></blockquote>`).join('') : '<p>No supporting profile evidence returned for this requirement.</p>'}</section>`).join('') : '<p>No structured job requirements are available. Review the selected job and its saved skills before assessing candidates.</p>'}</div>
      <section class="match-stat-card"><h4>How to read this</h4><p>${escape(match.formula)}</p><p>Compared with structured professional profile data, not a candidate-uploaded resume. Missing evidence is unknown; verify qualifications, credential validity and availability with the candidate.</p><ul>${(match.limitations || []).map((item) => `<li>${escape(item)}</li>`).join('')}</ul><p>No provider credits used for this calculation.</p></section>`;
  }
  root.DevReadyProfessionalMatch = { criteriaKey, view, details, summary };
  if (typeof module !== "undefined" && module.exports) module.exports = root.DevReadyProfessionalMatch;
})(typeof window === "undefined" ? globalThis : window);
