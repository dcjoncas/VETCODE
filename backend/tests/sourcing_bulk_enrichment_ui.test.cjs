const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const page = fs.readFileSync(path.join(__dirname, '../ui/pages/mine-candidate-external.html'), 'utf8');
const source = [...page.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map((match) => match[1]).find((script) => script.includes('function enrichAllLinkedProfiles'));
new vm.Script(source);

function candidate(id, extra = {}) {
  return { source: 'pdl', source_id: id, name: `Synthetic ${id}`, profile_url: `https://www.linkedin.com/in/synthetic-${id}/`, ...extra };
}

function completed(id, extra = {}) {
  return candidate(id, { professional_enrichment_complete: true,
    external_enrichment: { status: 'completed', profileVersion: 2, provider: 'synthetic' }, ...extra });
}

function fixture(rows, { approved = true, onEnrich = null, selected = ['pdl:A'] } = {}) {
  const nodes = new Map();
  const calls = { requests: [], confirmations: [], renders: [], persisted: [], schedules: [], credits: [] };
  const getNode = (id) => {
    if (!nodes.has(id)) nodes.set(id, { textContent: '', hidden: false, disabled: false, value: '', dataset: {},
      innerHTML: '', setAttribute() {}, removeAttribute() {}, classList: { add() {}, remove() {}, toggle() {} } });
    return nodes.get(id);
  };
  let fx;
  const context = vm.createContext({
    URL, URLSearchParams, JSON, Set, Map, Date,
    window: {}, document: { addEventListener() {}, getElementById: getNode, querySelectorAll: () => [] },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    console: { log() {}, warn() {}, error() {} },
    confirm(message) { calls.confirmations.push(message); return approved; },
    // The only transport is this deterministic synthetic response. It forbids
    // search/import routes; there is no fetch or real network implementation.
    api: async (url, options) => {
      assert.equal(url, '/api/azureJobs/external/enrich-result');
      assert.equal(options.method, 'POST');
      const payload = JSON.parse(options.body);
      calls.requests.push({ url, payload });
      if (onEnrich) await onEnrich(payload.candidate, fx);
      return { candidate: { ...payload.candidate, email: `${payload.candidate.source_id}@example.test` },
        enrichment: { status: 'completed', profileVersion: 2, provider: 'synthetic', enrichedAt: '2026-09-10T12:00:00Z' },
        archivePersistence: { status: 'saved' } };
    },
  });
  vm.runInContext(source, context);
  const run = (code) => vm.runInContext(code, context);
  const setRows = (values) => run(`latestExternalResults = ${JSON.stringify(values)};`);
  const getRows = () => JSON.parse(run('JSON.stringify(latestExternalResults)'));
  context.currentDomain = () => 'dev';
  context.selectedCandidateKeys = () => new Set(selected);
  context.selectedWorkflowJobId = () => 'synthetic-job';
  context.updateWorkflowGuidance = () => {};
  context.rankExternalResults = (values) => values;
  context.renderResults = (values, options = {}) => calls.renders.push({ ids: values.map((row) => row.source_id),
    selected: [...(options.preservedCandidateKeys || [])] });
  context.persistSourcingViewState = () => calls.persisted.push(getRows());
  context.refreshProviderCredits = (afterRequest) => calls.credits.push(afterRequest);
  context.scheduleAutomaticCandidateMatches = () => calls.schedules.push({ bulkRunning: run('externalBulkEnrichmentRunning'),
    persistedCount: calls.persisted.length, ids: getRows().map((row) => row.source_id) });
  setRows(rows);
  run("activeSavedSearch = { id: 'synthetic-archive', rootId: 'synthetic-archive' }; externalProviderStatus = {pdl:{ready:true},coresignal:{ready:true,collectionCreditsPerRequest:20}};");
  fx = { context, run, calls, node: getNode, setRows, getRows };
  return fx;
}

test('batch action is a visible section rather than a collapsed details disclosure', () => {
  assert.match(page, /<section\b[^>]*id="bulkLinkedProfileActions"/);
  assert.doesNotMatch(page, /<details\b[^>]*id="bulkLinkedProfileActions"/);
  assert.match(page, /id="bulkLinkedProfileStatus"[^>]*role="status"/);
});

test('five returned profiles show Enrich all 5 without starting a lookup', () => {
  const fx = fixture(['A', 'B', 'C', 'D', 'E'].map((id) => candidate(id)));
  fx.context.updateBulkLinkedProfileControls();
  assert.equal(fx.node('bulkLinkedProfileActions').hidden, false);
  assert.equal(fx.node('btnEnrichAllLinkedProfiles').hidden, false);
  assert.equal(fx.node('btnEnrichAllLinkedProfiles').disabled, false);
  assert.equal(fx.node('btnEnrichAllLinkedProfiles').textContent, 'Enrich all 5');
  assert.match(fx.node('bulkLinkedProfileStatus').textContent, /up to 5 PDL enrichment credits/);
  assert.equal(fx.calls.requests.length, 0);
  assert.equal(fx.calls.confirmations.length, 0);
  assert.equal(fx.calls.schedules.length, 0);
});

test('paid count excludes completed and TEMP-imported rows, unsupported sources and duplicate exact IDs', () => {
  const fx = fixture([candidate('A'), candidate('A'), candidate('a'), completed('B'),
    candidate('C', { devready_profile_complete: true }), candidate('D', { source: 'github' }),
    candidate('E', { professional_enrichment_complete: true, external_enrichment: { status: 'completed', profileVersion: 1 } }),
    candidate('F', { source_id: '', profile_url: '' }), candidate('B')]);
  assert.deepEqual([...fx.context.linkedProfileEnrichmentIndexes()], [0, 2, 6]);
  fx.context.updateBulkLinkedProfileControls();
  assert.equal(fx.node('btnEnrichAllLinkedProfiles').textContent, 'Enrich all 3');
});

test('all completed profiles show a ready summary with no paid button or duplicate lookup', async () => {
  const fx = fixture([completed('A'), completed('B')]);
  fx.context.updateBulkLinkedProfileControls();
  assert.equal(fx.node('bulkLinkedProfileActions').hidden, false);
  assert.equal(fx.node('btnEnrichAllLinkedProfiles').hidden, true);
  await fx.context.enrichAllLinkedProfiles();
  assert.equal(fx.calls.confirmations.length, 0);
  assert.equal(fx.calls.requests.length, 0);
});

test('a mixed-source confirmation states each provider estimate separately', async () => {
  const fx = fixture([candidate('A'), candidate('B', { source: 'coresignal' }), candidate('C', { source: 'coresignal' })], { approved: false });
  await fx.context.enrichAllLinkedProfiles();
  assert.match(fx.calls.confirmations[0], /up to 1 PDL enrichment credit/);
  assert.match(fx.calls.confirmations[0], /up to 40 Coresignal collection credits/);
  assert.equal(fx.calls.requests.length, 0);
});

test('without a selected JD the next step requests a JD rather than claiming a score refreshed', async () => {
  const fx = fixture([candidate('A')]);
  fx.context.selectedWorkflowJobId = () => '';
  fx.context.updateBulkLinkedProfileControls();
  assert.match(fx.node('bulkLinkedProfileStatus').textContent, /Choose a JD/);
  await fx.context.enrichAllLinkedProfiles();
  assert.match(fx.node('mineStatus').textContent, /Choose a JD/);
  assert.doesNotMatch(fx.node('mineStatus').textContent, /JD match is refreshing/);
});

test('every active archive/search/pagination/contact/import/batch operation blocks bulk execution', async () => {
  for (const flag of ['sourcingArchiveLoading', 'externalSearchRunning', 'paginationLoading',
    'sourcingContactOperations', 'externalImportRunning', 'externalBulkEnrichmentRunning']) {
    const fx = fixture([candidate('A')]);
    fx.run(`${flag} = ${flag === 'sourcingContactOperations' ? 1 : 'true'};`);
    fx.context.updateBulkLinkedProfileControls();
    assert.equal(fx.node('btnEnrichAllLinkedProfiles').disabled, true, flag);
    await fx.context.enrichAllLinkedProfiles();
    assert.equal(fx.calls.requests.length, 0, flag);
    assert.equal(fx.calls.confirmations.length, 0, flag);
  }
});

test('unavailable provider disables the paid action without requesting or prompting', async () => {
  const fx = fixture([candidate('A')]);
  fx.run('externalProviderStatus.pdl.ready = false;');
  fx.context.updateBulkLinkedProfileControls();
  assert.equal(fx.node('btnEnrichAllLinkedProfiles').disabled, true);
  await fx.context.enrichAllLinkedProfiles();
  assert.equal(fx.calls.requests.length, 0);
  assert.equal(fx.calls.confirmations.length, 0);
});

test('declining the clear credit confirmation has no mutations, requests, imports or automatic scoring', async () => {
  const fx = fixture([candidate('A'), candidate('B')], { approved: false });
  const before = fx.getRows();
  await fx.context.enrichAllLinkedProfiles();
  assert.equal(fx.calls.confirmations.length, 1);
  assert.match(fx.calls.confirmations[0], /up to 2 PDL enrichment credits/);
  assert.match(fx.calls.confirmations[0], /No TEMP profiles are created/);
  assert.match(fx.calls.confirmations[0], /does not scrape LinkedIn/);
  assert.equal(fx.calls.requests.length, 0);
  assert.equal(fx.calls.persisted.length, 0);
  assert.equal(fx.calls.schedules.length, 0);
  assert.deepEqual(fx.getRows(), before);
});

test('batch enriches each captured identity once even when asynchronous matching reranks and clones rows', async () => {
  const fx = fixture([candidate('A'), candidate('B'), candidate('C')], {
    selected: ['pdl:A', 'pdl:C'],
    onEnrich(row, state) {
      state.setRows(state.getRows().reverse().map((value) => ({ ...value, syntheticMatchUpdate: true })));
    },
  });
  await fx.context.enrichAllLinkedProfiles();
  assert.deepEqual(fx.calls.requests.map((call) => call.payload.candidate.source_id), ['A', 'B', 'C']);
  assert.ok(fx.getRows().every((row) => row.professional_enrichment_complete));
  assert.deepEqual(fx.calls.renders.at(-1).selected, ['pdl:A', 'pdl:C']);
  assert.equal(fx.calls.persisted.length, 1);
  assert.equal(fx.calls.schedules.length, 1);
  assert.equal(fx.calls.schedules[0].bulkRunning, false);
  assert.equal(fx.calls.schedules[0].persistedCount, 1);
  assert.equal(fx.node('btnEnrichAllLinkedProfiles').hidden, true);
  assert.match(fx.node('mineStatus').textContent, /No TEMP profiles were created/);
  assert.ok(fx.calls.requests.every((call) => call.payload.search_id === 'synthetic-archive'));
});

test('a reordered failing candidate receives its own error without replacing another row or current professional evidence', async () => {
  const fx = fixture([candidate('A'), candidate('B'), candidate('C')], { onEnrich(row, state) {
    if (row.source_id === 'A') {
      state.setRows(state.getRows().reverse().map((value) => ({ ...value, currentRevision: 9 })));
      throw new Error('Synthetic lookup unavailable');
    }
  } });
  await fx.context.enrichAllLinkedProfiles();
  const rows = Object.fromEntries(fx.getRows().map((row) => [row.source_id, row]));
  assert.match(rows.A.bulk_enrichment_error, /Synthetic lookup unavailable/);
  assert.equal(rows.A.currentRevision, 9);
  assert.equal(rows.B.bulk_enrichment_error, '');
  assert.equal(rows.C.bulk_enrichment_error, '');
  assert.equal(rows.B.professional_enrichment_complete, true);
  assert.equal(rows.C.professional_enrichment_complete, true);
  assert.deepEqual(fx.calls.requests.map((call) => call.payload.candidate.source_id), ['A', 'B', 'C']);
  assert.equal(fx.node('btnEnrichAllLinkedProfiles').textContent, 'Enrich all 1');
  assert.match(fx.node('mineStatus').textContent, /1 profile could not be enriched/);
  assert.equal(fx.calls.schedules.length, 1);
});

test('a profile completed by another response during the batch is reused without a second paid request', async () => {
  const fx = fixture([candidate('A'), candidate('B')], { onEnrich(row, state) {
    if (row.source_id === 'A') state.setRows(state.getRows().map((value) => value.source_id === 'B' ? completed('B') : value));
  } });
  await fx.context.enrichAllLinkedProfiles();
  assert.deepEqual(fx.calls.requests.map((call) => call.payload.candidate.source_id), ['A']);
  assert.match(fx.node('mineStatus').textContent, /2 profiles are enriched/);
});

test('repeating the batch action after completion never repeats paid lookups or confirmation', async () => {
  const fx = fixture([candidate('A'), candidate('B')]);
  await fx.context.enrichAllLinkedProfiles();
  await fx.context.enrichAllLinkedProfiles();
  assert.deepEqual(fx.calls.requests.map((call) => call.payload.candidate.source_id), ['A', 'B']);
  assert.equal(fx.calls.confirmations.length, 1);
  assert.equal(fx.calls.schedules.length, 1);
});

test('a candidate removed during reranking is skipped without charging or overwriting its replacement', async () => {
  const fx = fixture([candidate('A'), candidate('B'), candidate('C')], { onEnrich(row, state) {
    if (row.source_id === 'A') state.setRows(state.getRows().filter((value) => value.source_id !== 'B'));
  } });
  await fx.context.enrichAllLinkedProfiles();
  assert.deepEqual(fx.calls.requests.map((call) => call.payload.candidate.source_id), ['A', 'C']);
  assert.deepEqual(fx.getRows().map((row) => row.source_id), ['A', 'C']);
  assert.match(fx.node('mineStatus').textContent, /1 removed profile was skipped/);
});

test('an archive/domain change stops the remaining batch without altering the newer view or scheduling stale matches', async () => {
  for (const change of ['archive', 'domain']) {
    const fx = fixture([candidate('A'), candidate('B')], { onEnrich(row, state) {
      if (change === 'archive') state.run("activeSavedSearch = {rootId:'new-archive'};");
      else state.context.currentDomain = () => 'law';
      state.setRows([candidate('new-view-person')]);
      state.node('mineStatus').textContent = 'Newer view';
    } });
    await fx.context.enrichAllLinkedProfiles();
    assert.deepEqual(fx.calls.requests.map((call) => call.payload.candidate.source_id), ['A']);
    assert.equal(fx.getRows()[0].source_id, 'new-view-person');
    assert.equal(fx.node('mineStatus').textContent, 'Newer view');
    assert.equal(fx.calls.persisted.length, 0);
    assert.equal(fx.calls.schedules.length, 0);
    assert.equal(fx.run('externalBulkEnrichmentRunning'), false);
  }
});
