const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const matchUi = require('../ui/pages/JS/professionalMatch.js');
const pages = path.join(__dirname, '../ui/pages');
const sources = new Map();
const escapeSource = fs.readFileSync(path.join(pages, 'JS/apiScripts.js'), 'utf8').match(/function escapeHtml\(s\) \{[^\n]+/)[0];
for (const file of ['temp-profiles.html', 'determine-interest.html']) {
  const html = fs.readFileSync(path.join(pages, file), 'utf8');
  const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map((item) => item[1]);
  scripts.forEach((script) => new vm.Script(script));
  sources.set(file, scripts.find((script) => script.includes('function profileMatchesCurrentJob')));
}

const criteria = { titles: ['Engineer'], requiredSkills: ['Python'], ignoredCriteria: ['licenses'], strictLocations: false };
const match = {
  status: 'calculated', jobId: '123', evidenceType: 'structured_professional_profile',
  score: 50, coveragePercent: 50, reason: 'One requirement is supported; one needs confirmation.',
  criteriaSnapshot: criteria, matched: ['Python'],
  criteria: [{ label: 'Kubernetes', status: 'unknown', reason: 'Not stated', evidence: [{ text: '<unsafe>', field: 'summary' }] }],
  formula: 'Supported professional requirements / active requirements', limitations: [],
};

function fixture(file, profiles = [], domain = 'dev', jobId = '123') {
  const nodes = new Map();
  const calls = [];
  const alerts = [];
  const session = new Map([['domain', domain], ['jobID', jobId], ['jobTitle', 'Software Engineer']]);
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, {
      innerHTML: '', textContent: '', attributes: {}, hidden: false, disabled: false,
      setAttribute(key, value) { this.attributes[key] = value; },
      removeAttribute(key) { delete this.attributes[key]; },
      showModal() { this.open = true; }, close() { this.open = false; },
    });
    return nodes.get(id);
  };
  const context = vm.createContext({
    URL, URLSearchParams, API_BASE: '',
    document: { addEventListener() {}, getElementById: node, querySelectorAll: () => [] },
    window: { DevReadyProfessionalMatch: matchUi, location: { search: '' } },
    sessionStorage: { getItem: (key) => session.get(key) || null, setItem: (key, value) => session.set(key, value), removeItem: (key) => session.delete(key) },
    alert: (message) => alerts.push(message),
    confirm: () => { throw new Error('Fit calculation must not ask for paid enrichment.'); },
    api: async (url, options) => {
      calls.push({ url, payload: JSON.parse(options?.body || '{}') });
      return { match };
    },
  });
  vm.runInContext(escapeSource, context);
  vm.runInContext(sources.get(file), context);
  context.fixtureProfiles = profiles;
  vm.runInContext(`${file === 'temp-profiles.html' ? 'latestTempProfiles' : 'interestProfiles'} = fixtureProfiles`, context);
  context.loadTempProfiles = async () => {};
  context.loadInterestProfiles = async () => {};
  return { context, node, calls, alerts, session };
}

test('both pages reject flattened or legacy keyword scores as current fit', () => {
  const legacy = { personid: 42, matchCalculated: true, matchJobId: '123', matchScore: 99, match: { jobId: '123', score: 99 } };
  for (const file of sources.keys()) {
    const view = fixture(file, [legacy]);
    assert.equal(view.context.profileMatchesCurrentJob(legacy), false);
    if (file === 'temp-profiles.html') {
      view.context.renderTempProfiles([legacy]);
      assert.doesNotMatch(view.node('tempProfileList').innerHTML, /99%/);
    } else {
      assert.doesNotMatch(view.context.matchEvaluation(legacy), /99%/);
    }
  }
});

test('null assessment remains unavailable while genuine zero stays visible', () => {
  for (const file of sources.keys()) {
    const view = fixture(file);
    const unknown = { personid: 42, match: { ...match, status: 'unavailable', score: null, coveragePercent: null } };
    assert.equal(view.context.profileMatchesCurrentJob(unknown), true);
    if (file === 'temp-profiles.html') {
      view.context.renderTempProfiles([unknown]);
      assert.match(view.node('tempProfileList').innerHTML, /Fit not yet assessable/);
      assert.doesNotMatch(view.node('tempProfileList').innerHTML, /0%/);
      view.context.showMatchStats(unknown);
      assert.match(view.node('matchStatsBody').innerHTML, /Evidence coverage unavailable/);
    } else {
      const html = view.context.matchEvaluation(unknown);
      assert.match(html, /Not assessable/);
      assert.doesNotMatch(html, /0%/);
    }
    assert.equal(view.context.profileMatchView({ match: { ...match, score: 0 } }).score, 0);
  }
});

test('criterion details show unknowns, evidence coverage, and escaped evidence', () => {
  const profile = { personid: 42, name: 'Example', match };
  for (const file of sources.keys()) {
    const view = fixture(file, [profile]);
    let html;
    if (file === 'temp-profiles.html') {
      view.context.showMatchStats(profile);
      html = view.node('matchStatsBody').innerHTML;
    } else html = view.context.matchEvaluation(profile);
    assert.match(html, /50% evidence coverage/);
    assert.match(html, /Unknown/);
    assert.match(html, /not a hiring probability/);
    assert.match(html, /not a candidate-uploaded resume/);
    assert.match(html, /&lt;unsafe&gt;/);
    assert.doesNotMatch(html, /<unsafe>/);
  }
});

test('manual TEMP matching uses stored evidence without a paid enrichment prerequisite', async () => {
  const profile = { personid: 42, name: 'Example', enrichmentStatus: 'not_run', match };
  const view = fixture('temp-profiles.html', [profile]);
  await view.context.calculateTempMatch('42');
  assert.equal(view.alerts.length, 0);
  assert.equal(view.calls.length, 1);
  assert.match(view.calls[0].url, /\/42\/calculate-match$/);
  assert.deepEqual(view.calls[0].payload, { domain: 'dev', jd_id: '123', criteria });
});

test('interest matching preserves selected criteria and skips current unavailable assessments', async () => {
  const profile = { personid: 42, match };
  const view = fixture('determine-interest.html', [profile]);
  await view.context.calculateCandidateMatch('42');
  assert.equal(view.calls.length, 1);
  assert.deepEqual(view.calls[0].payload.criteria, criteria);
  const unavailable = { personid: 43, match: { ...match, status: 'unavailable', score: null } };
  const current = fixture('determine-interest.html', [unavailable]);
  assert.equal(await current.context.calculateMissingMatches(), false);
  assert.equal(current.calls.length, 0);
});

test('recalculating for a different job does not reuse previous job requirements', () => {
  for (const file of sources.keys()) {
    const view = fixture(file, [], 'law', '456');
    const payload = file === 'temp-profiles.html' ? view.context.tempMatchPayload({ match }) : view.context.interestMatchPayload({ match });
    assert.equal(payload.domain, 'law');
    assert.equal(payload.jd_id, '456');
    assert.equal('criteria' in payload, false);
    assert.equal(view.context.profileMatchesCurrentJob({ match }), false);
  }
});

test('TEMP enrichment selection starts empty and contact details remain available', () => {
  const profile = { personid: 42, name: 'Example', email: 'example@example.test', phone: '+1 202 555 0123', profileUrl: 'https://linkedin.com/in/example', match };
  const view = fixture('temp-profiles.html', [profile]);
  view.context.renderTempProfiles([profile]);
  const html = view.node('tempProfileList').innerHTML;
  const checkbox = html.match(/<input class="temp-profile-check"[^>]+>/)[0];
  assert.doesNotMatch(checkbox, /\schecked(?:\s|=|>)/);
  assert.equal(view.node('btnEnrichTempSelected').disabled, true);
  assert.match(html, /example@example.test/);
  assert.match(html, /202 555 0123/);
  assert.match(html, /Open LinkedIn/);
  assert.match(html, /Determine interest/);
});
