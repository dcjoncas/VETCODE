const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const page = fs.readFileSync(path.join(__dirname, '../ui/pages/mine-candidate-external.html'), 'utf8');
const source = [...page.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map((match) => match[1]).find((script) => script.includes('function importSelectedExternalCandidates'));
new vm.Script(source);

function candidate(id, extra = {}) {
  return { source: 'pdl', source_id: id, name: `Synthetic ${id}`,
    profile_url: `https://www.linkedin.com/in/synthetic-${id}/`, ...extra };
}

function fixture(rows = [candidate('A'), candidate('B')], options = {}) {
  const store = new Map(Object.entries({ domain: 'dev', jobTitle: 'Synthetic role', jobCompany: 'Synthetic company', ...options.store }));
  const nodes = new Map();
  const calls = { requests: [], confirmations: [], alerts: [], renders: [], persisted: 0, focus: [] };
  const makeNode = () => ({ textContent: '', value: '', dataset: {}, children: [],
    append(child) { this.children.push(child); }, scrollIntoView() {}, classList: { add() {}, remove() {} } });
  const node = (id) => { if (!nodes.has(id)) nodes.set(id, makeNode()); return nodes.get(id); };
  let job = 'synthetic-job';
  let selected = options.selected ?? rows.map((_, index) => index);
  let fx;
  const context = vm.createContext({
    URL, URLSearchParams, JSON, Set, Map, Date,
    document: { addEventListener() {}, getElementById: node, querySelectorAll: () => [], createElement: makeNode },
    window: { location: { href: 'https://vetcode.test/ui/pages/mine-candidate-external.html?domain=dev', origin: 'https://vetcode.test' } },
    sessionStorage: {
      getItem: (key) => store.get(key) ?? null,
      setItem(key, value) { if (options.failStore?.(key, value)) throw new Error('Synthetic browser store full'); store.set(key, value); },
      removeItem: (key) => store.delete(key),
    },
    confirm(message) { calls.confirmations.push(message); return options.approved !== false; },
    alert(message) { calls.alerts.push(message); },
    console: { log() {}, warn() {}, error() {} },
    api: async (url, request) => {
      // Only TEMP import is allowed. No discovery/enrichment/send endpoint is
      // present and this fixture has no real network implementation.
      assert.equal(url, '/api/azureJobs/external/import');
      assert.equal(request.method, 'POST');
      const payload = JSON.parse(request.body);
      assert.equal(payload.enrich_contacts, false);
      calls.requests.push(payload);
      if (options.onImport) await options.onImport(payload, fx);
      if ((options.failIds || []).includes(payload.candidate.source_id)) throw new Error('Synthetic TEMP service failure');
      return { personid: `temp-${payload.candidate.source_id}`, name: payload.candidate.name, enrichment: {}, match: {} };
    },
  });
  vm.runInContext(source, context);
  const run = (code) => vm.runInContext(code, context);
  context.selectedCandidateIndexes = () => selected;
  context.selectedCandidateKeys = () => new Set(selected.map((index) => rows[index]?.source_id));
  context.selectedWorkflowJobId = () => job;
  context.currentLawCriteria = () => ({ titles: ['Synthetic role'] });
  context.candidateResultKey = (row) => row?.source_id;
  context.updateWorkflowGuidance = () => {};
  context.updateSelectedEnrichmentCount = () => {};
  context.rankExternalResults = (values) => values;
  context.renderResults = (values) => calls.renders.push(values.map((row) => row.source_id));
  context.persistSourcingViewState = () => { calls.persisted++; };
  context.renderLatestTempProfile = () => {};
  context.markCandidateDevReadyProfile = (row, response) => ({ ...row, devready_profile_complete: true, devready_profile_id: response.personid });
  context.focusWorkflowPrerequisite = (target) => calls.focus.push(target);
  run(`latestExternalResults = ${JSON.stringify(rows)};`);
  node('jdSelect').value = job;
  fx = { context, run, store, calls, node, setJob(value) { job = value; node('jdSelect').value = value; },
    setSelected(value) { selected = value; }, initialUrl: context.window.location.href };
  return fx;
}

function click(fx, extra = {}, linkExtra = {}) {
  const link = { href: 'https://vetcode.test/ui/pages/determine-interest.html?domain=dev', target: '', dataset: {},
    hasAttribute: () => false, ...linkExtra };
  const event = { button: 0, defaultPrevented: false, prevented: false, stopped: false,
    target: { closest: () => link }, preventDefault() { this.prevented = true; },
    stopImmediatePropagation() { this.stopped = true; }, ...extra };
  return { event, link, result: fx.context.handleDetermineInterestNavigation(event) };
}

test('capture delegation covers dynamically loaded stage links', () => {
  assert.match(page, /document\.addEventListener\("click", handleDetermineInterestNavigation, true\)/);
  assert.match(page, /if \(target === "determine-interest"\) \{\s+openDetermineInterestForSelection\(\)/);
});

test('normal stage link prepares all checked candidates with job context before navigation', async () => {
  const fx = fixture();
  const clicked = click(fx);
  await clicked.result;
  assert.equal(clicked.event.prevented, true);
  assert.equal(clicked.event.stopped, true);
  assert.deepEqual(fx.calls.requests.map((value) => value.candidate.source_id), ['A', 'B']);
  assert.deepEqual(JSON.parse(fx.store.get('determineInterestBatch:dev')), ['temp-A', 'temp-B']);
  assert.deepEqual(JSON.parse(fx.store.get('determineInterestBatchContext:dev')),
    { jobId: 'synthetic-job', jobTitle: 'Synthetic role', jobCompany: 'Synthetic company' });
  assert.equal(fx.context.window.location.href, 'determine-interest.html?domain=dev');
  assert.equal(fx.calls.persisted, 1);
  assert.match(fx.calls.confirmations[0], /No paid contact lookup will run/);
});

test('unchecked results without a prepared batch stay visible and explain the next action', async () => {
  const fx = fixture(undefined, { selected: [] });
  await click(fx).result;
  assert.equal(fx.context.window.location.href, fx.initialUrl);
  assert.equal(fx.calls.requests.length, 0);
  assert.match(fx.node('mineStatus').textContent, /Select the candidates you want/);
  assert.match(fx.node('mineStatus').textContent, /Search archive/);
  assert.deepEqual(fx.calls.focus, ['candidate-results']);
});

test('no checked candidates reopens only a compatible prepared workspace, including legacy batches', async () => {
  for (const includeContext of [true, false]) {
    const fx = fixture(undefined, { selected: [], store: { 'determineInterestBatch:dev': '["temp-old"]',
      ...(includeContext ? { 'determineInterestBatchContext:dev': '{"jobId":"synthetic-job"}' } : {}) } });
    await click(fx).result;
    assert.equal(fx.context.window.location.href, 'determine-interest.html?domain=dev');
    assert.equal(fx.calls.requests.length, 0);
  }
  const otherJob = fixture(undefined, { selected: [], store: { 'determineInterestBatch:dev': '["temp-old"]',
    'determineInterestBatchContext:dev': '{"jobId":"other-job"}' } });
  await click(otherJob).result;
  assert.equal(otherJob.context.window.location.href, otherJob.initialUrl);
  assert.match(otherJob.node('mineStatus').textContent, /No interest batch is prepared for this job/);
});

test('new-tab gestures, explicit profile links, other domains and external links keep native behavior', async () => {
  const cases = [
    [{ ctrlKey: true }], [{ metaKey: true }], [{ shiftKey: true }], [{ altKey: true }],
    [{ button: 1 }], [{ defaultPrevented: true }], [{}, { target: '_blank' }],
    [{}, { hasAttribute: () => true }],
    [{}, { href: 'https://outside.test/ui/pages/determine-interest.html' }],
    [{}, { href: 'https://vetcode.test/ui/pages/determine-interest.html?domain=law' }],
    [{}, { href: 'https://vetcode.test/ui/pages/determine-interest.html?profileId=temp-one' }],
    [{}, { href: 'https://vetcode.test/ui/pages/interested-candidates.html' }],
  ];
  for (const [event, link] of cases) {
    const fx = fixture();
    const clicked = click(fx, event, link);
    await clicked.result;
    assert.equal(clicked.event.prevented, false);
    assert.equal(fx.calls.requests.length, 0);
    assert.equal(fx.context.window.location.href, fx.initialUrl);
  }
});

test('a second navigation click while preparing does not duplicate TEMP imports', async () => {
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  const fx = fixture([candidate('A')], { onImport: () => held });
  const first = click(fx);
  await click(fx).result;
  assert.equal(fx.calls.requests.length, 1);
  release();
  await first.result;
  assert.equal(fx.calls.requests.length, 1);
});

test('partial import failure preserves ready IDs and selections with explicit open-ready link', async () => {
  const fx = fixture(undefined, { failIds: ['B'] });
  await click(fx).result;
  assert.deepEqual(JSON.parse(fx.store.get('determineInterestBatch:dev')), ['temp-A']);
  assert.equal(fx.context.window.location.href, fx.initialUrl);
  assert.match(fx.node('mineStatus').textContent, /1 selected candidate is ready; 1 could not be prepared/);
  assert.match(fx.node('mineStatus').textContent, /Synthetic TEMP service failure/);
  const ready = fx.node('mineStatus').children[0];
  assert.equal(ready.textContent, 'Open 1 ready candidate');
  const opened = click(fx, {}, ready);
  await opened.result;
  assert.equal(opened.event.prevented, false);
  assert.equal(fx.calls.requests.length, 2);
  fx.setSelected([0]);
  await click(fx).result;
  assert.equal(fx.calls.requests.length, 2, 'saved profile is reused rather than imported again');
});

test('batch storage failure keeps paired prior context, reports saved profiles, and does not navigate', async () => {
  const fx = fixture([candidate('A')], { store: { 'determineInterestBatch:dev': '["temp-old"]',
    'determineInterestBatchContext:dev': '{"jobId":"old-job"}' },
    failStore: (key, value) => key === 'determineInterestBatch:dev' && value.includes('temp-A') });
  await click(fx).result;
  assert.equal(fx.context.window.location.href, fx.initialUrl);
  assert.equal(fx.store.get('determineInterestBatch:dev'), '["temp-old"]');
  assert.equal(fx.store.get('determineInterestBatchContext:dev'), '{"jobId":"old-job"}');
  assert.match(fx.node('mineStatus').textContent, /saved on the server/);
  assert.match(fx.node('mineStatus').textContent, /could not be saved in this browser/);
  assert.equal(fx.run('externalImportRunning'), false);
});

test('failed optional local TEMP cache does not lose a successfully saved server profile', async () => {
  const fx = fixture([candidate('A')], { failStore: (key) => key === 'temporaryProfiles' });
  await click(fx).result;
  assert.deepEqual(JSON.parse(fx.store.get('determineInterestBatch:dev')), ['temp-A']);
  assert.equal(fx.context.window.location.href, 'determine-interest.html?domain=dev');
  assert.equal(fx.calls.requests.length, 1);
});

test('job changes during preparation retain the original batch context without navigating or importing later people', async () => {
  const fx = fixture(undefined, { onImport: (_payload, state) => state.setJob('changed-job') });
  await click(fx).result;
  assert.equal(fx.calls.requests.length, 1);
  assert.equal(fx.calls.requests[0].jd_id, 'synthetic-job');
  assert.equal(JSON.parse(fx.store.get('determineInterestBatchContext:dev')).jobId, 'synthetic-job');
  assert.equal(fx.context.window.location.href, fx.initialUrl);
  assert.match(fx.node('mineStatus').textContent, /job or search changed/);
});

test('an asynchronous rank change attaches imported TEMP identities to the correct candidate', async () => {
  const fx = fixture(undefined, { onImport: (payload, state) => {
    if (payload.candidate.source_id === 'A') state.run('latestExternalResults = [...latestExternalResults].reverse();');
  } });
  await click(fx).result;
  assert.deepEqual(fx.calls.requests.map((payload) => payload.candidate.source_id), ['A', 'B']);
  assert.deepEqual(JSON.parse(fx.run('JSON.stringify(latestExternalResults.map(row => [row.source_id, row.devready_profile_id]))')),
    [['B', 'temp-B'], ['A', 'temp-A']]);
});

test('conflicting work, invalid contacts, over-ten selections and declining confirmation do not import', async () => {
  for (const flag of ['externalSearchRunning', 'externalBulkEnrichmentRunning', 'sourcingArchiveLoading',
    'sourcingJobLoading', 'paginationLoading', 'sourcingContactOperations']) {
    const fx = fixture();
    fx.run(`${flag} = ${flag === 'sourcingContactOperations' ? 1 : 'true'};`);
    await click(fx).result;
    assert.equal(fx.calls.requests.length, 0, flag);
    assert.match(fx.node('mineStatus').textContent, /Wait for the current/);
  }
  for (const fx of [fixture([candidate('A', { profile_url: '' })]),
    fixture(Array.from({ length: 11 }, (_, index) => candidate(String(index)))), fixture(undefined, { approved: false })]) {
    await click(fx).result;
    assert.equal(fx.calls.requests.length, 0);
    assert.equal(fx.context.window.location.href, fx.initialUrl);
  }
});
