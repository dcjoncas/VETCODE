const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const professional = require('../ui/pages/JS/professionalMatch.js');
const usage = require('../ui/pages/JS/providerUsage.js');
const interested = require('../ui/pages/JS/interestedCandidates.js');
const page = fs.readFileSync(path.join(__dirname, '../ui/pages/mine-candidate-external.html'), 'utf8');
const reportPage = fs.readFileSync(path.join(__dirname, '../ui/pages/interested-candidates.html'), 'utf8');
const source = [...page.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map((entry) => entry[1]).find((script) => script.includes('async function calculateCandidateMatch'));
const criteria = { requiredSkills: ['Python'], titles: ['Engineer'] };
const match = { status: 'calculated', jobId: 'jd1', score: 50, coveragePercent: 50,
  evidenceType: 'structured_professional_profile', criteriaSnapshot: criteria, reason: 'One reviewed requirement is supported; one needs confirmation.',
  criteria: [{ label: 'Python', status: 'matched', required: true, scored: true, evidence: [{ field: 'skills[0]', text: 'Python' }] },
    { label: 'Engineer', status: 'unknown', required: true, scored: true, reason: 'Role alignment needs review.', evidence: [] }],
  mustHaveUnknown: ['Engineer'], formula: 'Supported reviewed requirements / active reviewed requirements', limitations: ['Not a complete semantic JD assessment.'] };
const person = { name: 'Alex Morgan', source: 'pdl', source_id: 'sample-123', title: 'Software Engineer',
  company: 'Example Company', location: 'Denver, Colorado', skills: ['Python'], profile_url: 'https://www.linkedin.com/in/alex-example',
  email: 'alex@example.test', phone: '+1 202 555 0100', score: 50, match };

function fixture({ candidate = person, confirm = true, job = 'jd1', domain = 'dev', batch = [], api = async () => ({ match }) } = {}) {
  const nodes = new Map(), calls = [], confirmations = [], alerts = [];
  const node = (id) => {
    const classes = new Set();
    if (!nodes.has(id)) nodes.set(id, { innerHTML: '', textContent: '', hidden: false, open: false, value: '', dataset: {}, selectedOptions: [{ textContent: 'Python Engineer' }],
      addEventListener() {}, showModal() { this.open = true; }, close() { this.open = false; },
      classList: { toggle() {}, add: (name) => classes.add(name), remove: (name) => classes.delete(name), contains: (name) => classes.has(name) },
      setAttribute() {}, querySelectorAll: () => [], scrollIntoView() {} });
    return nodes.get(id);
  };
  node('jdSelect').value = job;
  const context = vm.createContext({
    URL, URLSearchParams, console,
    escapeHtml: (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character])),
    document: { addEventListener() {}, querySelectorAll: () => [], getElementById: node },
    window: { DevReadyProfessionalMatch: professional, DevReadyProviderUsage: usage },
    sessionStorage: { getItem: (key) => key === 'domain' ? domain : key === `determineInterestBatch:${domain}` ? JSON.stringify(batch) : null, setItem() {} },
    confirm: (message) => { confirmations.push(message); return confirm; }, alert: (message) => alerts.push(message),
    api: async (url, options) => { calls.push({ url, body: JSON.parse(options?.body || '{}') }); return api(url, options); },
    fixturePerson: JSON.parse(JSON.stringify(candidate)), fixtureCriteria: criteria,
  });
  vm.runInContext(source, context);
  vm.runInContext('latestExternalResults = [fixturePerson]; externalProviderStatus = { pdl: { ready: true } };', context);
  const updateBulkControls = context.updateBulkLinkedProfileControls;
  context.currentLawCriteria = () => criteria;
  context.candidateInterestControls = () => '';
  context.updateSelectedEnrichmentCount = () => {};
  context.updateBulkLinkedProfileControls = () => {};
  context.persistSourcingViewState = () => {};
  context.setSearchCriteria = () => {};
  context.setSource = () => {};
  context.updateLawVerificationSource = () => {};
  return { context, node, calls, confirmations, alerts,
    updateBulkControls,
    candidate: () => JSON.parse(vm.runInContext('JSON.stringify(latestExternalResults[0])', context)),
    render: () => { vm.runInContext('renderResults(latestExternalResults)', context); return node('results').innerHTML; } };
}

test('unsaved discovery results show support and actual contacts before a paid lookup', () => {
  const harness = fixture();
  const html = harness.render();
  assert.match(html, /50% requirement support/);
  assert.match(html, /Evidence coverage: 50%/);
  assert.match(html, /Email: alex@example.test/);
  assert.match(html, /Call: \+1 202 555 0100/);
  assert.doesNotMatch(html, /Get contact details/);
  assert.doesNotMatch(html, /Enrich and create TEMP/);
  assert.equal(harness.calls.length, 0);
});

test('optional contact lookup appears only when direct contact information is missing', () => {
  for (const candidate of [{...person, email: ''}, {...person, phone: ''}, {...person, email: 'Unknown'}]) {
    const harness = fixture({candidate});
    assert.match(harness.render(), /Get contact details/);
    assert.equal(harness.calls.length, 0);
  }
});

test('Calculate works on discovery evidence without a TEMP id or enrichment', async () => {
  const harness = fixture();
  await harness.context.calculateCandidateMatch(0);
  assert.equal(harness.calls.length, 1);
  assert.equal(harness.calls[0].url, '/api/azureJobs/external/calculate-match');
  assert.equal(harness.calls[0].body.candidate.source_id, 'sample-123');
  assert.equal(harness.node('candidateMatchDialog').open, true);
  assert.match(harness.node('candidateMatchBody').innerHTML, /not a candidate-uploaded resume/);
});

test('no JD refuses scoring locally and a changed criterion hides an old percentage', async () => {
  const harness = fixture({ job: '' });
  await harness.context.calculateCandidateMatch(0);
  assert.equal(harness.calls.length, 0);
  const changed = fixture();
  changed.context.currentLawCriteria = () => ({ requiredSkills: ['Java'] });
  assert.doesNotMatch(changed.render(), /50% requirement support/);
  assert.match(changed.render(), /Calculate current JD fit/);
});

test('cancelled contact lookup has no call; accepted lookup names its credit impact', async () => {
  const cancelled = fixture({ confirm: false });
  await cancelled.context.requestCandidateContacts(0);
  assert.equal(cancelled.calls.length, 0);
  assert.match(cancelled.confirmations[0], /1 enrichment credit/);
  const accepted = fixture({ api: async () => ({ candidate: person, enrichment: { status: 'completed', provider: 'People Data Labs Person Enrichment' } }) });
  await accepted.context.requestCandidateContacts(0);
  assert.equal(accepted.calls[0].url, '/api/azureJobs/external/enrich-result');
  assert.equal(accepted.candidate().match, null);
});

test('existing TEMP contact lookup uses the persisted endpoint and updates shown values', async () => {
  const harness = fixture({ candidate: { ...person, devready_profile_id: '42', email: '' }, api: async () => ({
    personid: '42', email: 'returned@example.test', phone: '+1 202 555 0110', contact: { primaryEmail: 'returned@example.test' },
    profileUrl: person.profile_url, enrichment: { status: 'completed', provider: 'People Data Labs Person Enrichment' },
  }) });
  await harness.context.requestCandidateContacts(0);
  assert.equal(harness.calls[0].url, '/api/azureJobs/external/temp/42/enrich-professional');
  assert.match(harness.render(), /returned@example.test/);
  assert.equal(harness.candidate().saved_match, null);
});

test('source audit labels estimates and missing balance honestly through the actual renderer', () => {
  const harness = fixture();
  harness.context.renderSourceAudit({ provider: 'People Data Labs', estimatedCreditsUsed: 5, totalMatches: 754299581, recordsReturned: 5, recordsReviewed: 5 });
  assert.equal(harness.node('auditCost').textContent, 'Not reported');
  assert.equal(harness.node('auditBalance').textContent, 'Unavailable');
  assert.match(harness.node('sourceAuditNote').textContent, /estimate, not a provider-confirmed charge/);
});

test('Review All Interested Candidates keeps only workspace scope, independent of JD and current batch', () => {
  assert.match(page, /id="reviewAllInterestedCandidates"[^>]*>Review All Interested Candidates<\/a>/);
  for (const domain of ['dev', 'law', 'engineer', 'dental']) {
    for (const state of [{ job: '', batch: [] }, { job: 'another-jd', batch: ['temp-42', 'temp-43'] }]) {
      const harness = fixture({ domain, ...state });
      harness.context.configureDomainPage();
      const href = harness.node('reviewAllInterestedCandidates').href;
      const url = new URL(href, 'https://example.test/ui/pages/');
      assert.equal(url.pathname, '/ui/pages/interested-candidates.html');
      assert.deepEqual([...url.searchParams], [['domain', domain]]);
      assert.equal(harness.calls.length, 0);
    }
  }
});

test('bulk enrichment stays optional and collapsed while next guidance leads to reviewing results', () => {
  const bulk = extractMarkup(page, /<details\b[^>]*id="bulkLinkedProfileActions"[^>]*>/);
  assert.doesNotMatch(bulk.match(/^<details[^>]*>/)[0], /\sopen(?:[\s=>])/);
  assert.match(bulk, /<summary>Optional bulk enrichment — uses provider credits<\/summary>/);
  assert.match(bulk, /id="btnEnrichAllLinkedProfiles"/);
  assert.doesNotMatch(bulk, /id="(?:enrichmentSelectionStatus|results|shortlistSection)"/);
  for (const domain of ['dev', 'law', 'engineer', 'dental']) {
    const harness = fixture({ domain });
    harness.updateBulkControls();
    assert.equal(harness.node('bulkLinkedProfileActions').hidden, false);
    assert.equal(harness.node('bulkLinkedProfileActions').open, false);
    harness.context.updateWorkflowGuidance();
    assert.equal(harness.node('workflowGuidanceAction').dataset.target, 'candidate-results');
    assert.equal(harness.node('workflowGuidanceAction').textContent, 'Review candidate results');
    assert.match(harness.node('workflowGuidanceMessage').textContent, /Contact lookup is optional/);
    assert.equal(harness.node('btnEnrichAllLinkedProfiles').classList.contains('workflow-current-action'), false);
    assert.equal(harness.node('results').classList.contains('workflow-current-action'), true);
    assert.equal(harness.calls.length, 0);
  }
});

// Preserve nested details/divs in fixtures instead of truncating at the first close tag.
function extractMarkup(html, openingPattern) {
  const opening = openingPattern.exec(html);
  assert.ok(opening, `Fixture markup not found: ${openingPattern}`);
  const tag = opening[0].match(/^<([a-z][a-z0-9-]*)\b/i)[1];
  const tags = new RegExp(`<\\/?${tag}\\b[^>]*>`, 'gi');
  tags.lastIndex = opening.index;
  let depth = 0, token;
  while ((token = tags.exec(html))) {
    depth += token[0].startsWith('</') ? -1 : 1;
    if (depth === 0) return html.slice(opening.index, tags.lastIndex);
  }
  throw new Error(`Unclosed fixture element: ${tag}`);
}

if (process.env.FIND_OUT_PREVIEW_DIR) {
  const directory = process.env.FIND_OUT_PREVIEW_DIR;
  fs.mkdirSync(directory, { recursive: true });
  const styles = page.match(/<style>([\s\S]*?)<\/style>/)[1];
  const reportStyles = reportPage.match(/<style>([\s\S]*?)<\/style>/)[1];
  const harness = fixture();
  const cards = harness.render();
  const sampleProfiles = [
    { personid: 'fixture-1', name: 'Alex Morgan', title: 'Software Engineer', location: 'Denver, Colorado',
      emails: ['alex@example.test'], phones: ['+1 202 555 0100'], linkedinUrl: person.profile_url,
      summary: 'Python engineering and application delivery experience. Confirm scope and current availability with the candidate.',
      interestJobId: 'fixture-jd', interestJobTitle: 'Senior Engineer — Example Company', interestConfirmedAt: '2026-09-10', temporary: true, source: 'People Data Labs' },
    { personid: 'fixture-2', name: 'Taylor Brooks', title: 'Operations specialist', location: 'Phoenix, Arizona',
      emails: ['taylor@example.test'], phones: [], linkedinUrl: 'https://www.linkedin.com/in/taylor-example',
      summary: 'Saved qualifications brief for a promoted profile. Missing contact fields are shown honestly.',
      interestJobTitle: 'Operations Lead — Sample Organization', temporary: false, source: 'Saved record' },
    { personid: 'fixture-3', name: 'Jordan Casey', title: 'Title not saved', emails: [], phones: [], temporary: true },
  ];
  const reportCard = extractMarkup(reportPage, /<section class="card">/);
  const unlockPanel = extractMarkup(reportCard, /<div id="unlockPanel">/);
  const criteriaPanel = extractMarkup(page, /<details\b[^>]*id="searchCriteriaPanel"[^>]*>/);
  // Reuse the real function declarations and real criteria event wiring, omitting app
  // startup, provider loading and job selection. CSP below disallows all connections.
  const startupIndex = source.indexOf('document.addEventListener("DOMContentLoaded"');
  const criteriaEventsStart = source.indexOf('document.querySelectorAll("[data-criteria-add]")', startupIndex);
  const criteriaEventsEnd = source.indexOf('document.getElementById("btnMine").addEventListener', criteriaEventsStart);
  assert.ok(startupIndex > 0 && criteriaEventsStart > startupIndex && criteriaEventsEnd > criteriaEventsStart);
  const criteriaFunctions = source.slice(0, startupIndex);
  const criteriaEvents = source.slice(criteriaEventsStart, criteriaEventsEnd);
  const previewNote = '<p class="notice">Synthetic layout check only — example data, no provider requests, no messages sent.</p>';
  for (const domain of ['dev', 'law', 'engineer', 'dental']) {
    const theme = fs.readFileSync(path.join(__dirname, `../ui/assets/${domain}Styles.css`), 'utf8');
    const wrap = (body, css = styles, script = '') => `<!doctype html><html lang="en" data-domain="${domain}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${theme}\n${css}</style></head><body><main class="main">${previewNote}${body}</main>${script}</body></html>`;
    const html = wrap(`<section class="card"><h2>Find Candidates (Out)</h2>${cards}</section>
      <dialog id="candidateMatchDialog" class="match-dialog" aria-labelledby="candidateMatchTitle"><div class="match-dialog-head"><h3 id="candidateMatchTitle">Alex Morgan — reviewed JD criteria</h3><button class="btn secondary" onclick="this.closest('dialog').close()">Close</button></div><div class="match-dialog-body">${professional.details(match)}</div><div class="match-dialog-footer"><span class="muted">No provider credits used.</span><button class="btn primary" onclick="this.closest('dialog').close()">Done</button></div></dialog>`, styles,
      '<script>function showCandidateMatchStats(){ document.getElementById("candidateMatchDialog").showModal(); }</script>');
    fs.writeFileSync(path.join(directory, `${domain}.html`), html);
    const report = reportCard.replace(unlockPanel, '')
      .replace('<div id="reportPanel" hidden>', '<div id="reportPanel">')
      .replace('id="findOutLink" class="btn secondary" href="mine-candidate-external.html"', `id="findOutLink" class="btn secondary" href="${domain}.html"`)
      .replace('<strong id="scopeLabel">Current workspace</strong>', `<strong id="scopeLabel">${interested.scopeNames[domain]} workspace · All saved interested candidates · All jobs</strong>`)
      .replace('<strong id="reportCount">No report loaded</strong>', '<strong id="reportCount">3 interested candidates loaded · Complete report</strong>')
      .replace('<div id="candidateRows"></div>', `<div id="candidateRows">${interested.renderRows(sampleProfiles, domain)}</div>`);
    fs.writeFileSync(path.join(directory, `report-${domain}.html`), wrap(report, reportStyles));
    const criteriaScript = `${criteriaFunctions}
      function currentDomain() { return ${JSON.stringify(domain)}; }
      function api() { throw new Error("Provider calls are disabled in this fixture."); }
      window.invalidateDiscoveryMatches = () => {};
      const fixtureChoices = DOMAIN_CRITERIA_CHOICES[currentDomain()];
      setSearchCriteria({ titles: fixtureChoices.titles.slice(0, 2), mustHaveSkills: fixtureChoices.skills.slice(0, 4),
        locations: ["Denver"], experienceRanges: ["6-9"], licensesOrCertifications: fixtureChoices.licenses.slice(0, 1),
        workArrangements: ["remote"], workforceLocations: ["onshore"], strictLocations: true });
      ${criteriaEvents}`;
    new vm.Script(criteriaScript, { filename: `criteria-${domain}.html` });
    const criteriaHtml = wrap(`<section class="card"><h2>Find Candidates (Out) — criteria layout</h2><p class="muted">Interactive example: switches, choices and exclusive row editors use the real page logic. This fixture cannot contact providers or save data.</p>${criteriaPanel}</section>`, styles, `<script>${criteriaScript}</script>`)
      .replace('<head>', '<head><meta http-equiv="Content-Security-Policy" content="connect-src \'none\'; form-action \'none\'">');
    fs.writeFileSync(path.join(directory, `criteria-${domain}.html`), criteriaHtml);
    if (domain === 'dev') {
      fs.writeFileSync(path.join(directory, 'criteria-dev-mobile.html'), criteriaHtml.replace('<details id="searchCriteriaPanel"', '<details open id="searchCriteriaPanel"'));
    }
  }
  fs.writeFileSync(path.join(directory, 'criteria-mobile.html'), `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Criteria mobile viewport preview</title><style>body{margin:16px;font:14px system-ui,sans-serif;background:#eef1f3;color:#17212b}iframe{display:block;width:390px;height:850px;border:0;background:white;box-shadow:0 2px 14px #0002}</style></head><body><p>Synthetic mobile preview — 390 × 850 pixel viewport</p><iframe src="criteria-dev-mobile.html" width="390" height="850" title="Interactive criteria at mobile width"></iframe></body></html>`);
}
