const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const page = fs.readFileSync(path.join(__dirname, '../ui/pages/mine-candidate-external.html'), 'utf8');
const scripts = [...page.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map((match) => match[1]);
scripts.forEach((script) => new vm.Script(script));
const source = scripts.find((script) => script.includes('function readSourcingViewState'));

// Run the production inline controller with synthetic browser state. No fetch or
// network implementation is installed; api below rejects every non-GET call.
function fixture({ domain = 'dev', navigationType = 'navigate', search = '', stored = {}, historyState = {},
  listError = false, archiveResponse = null, failArchiveWrite = false, apiHook = null } = {}) {
  const storage = new Map(Object.entries({ domain, ...stored }));
  const storageWriteFailures = new Set();
  const nodes = new Map();
  const calls = [];
  const rendered = { results: [], selected: [], criteria: {}, scroll: [] };
  const listeners = new Map();
  const windowListeners = new Map();
  const testLocation = { search, hash: '', pathname: '/ui/pages/mine-candidate-external.html',
    href: `https://example.test/ui/pages/mine-candidate-external.html${search}` };
  function updateLocation(url) {
    if (url === undefined) return;
    const next = new URL(url, testLocation.href);
    Object.assign(testLocation, { search: next.search, hash: next.hash, pathname: next.pathname, href: next.href });
  }
  const history = {
    state: historyState,
    replacements: [], pushes: [],
    replaceState(value, title, url) { this.state = value; this.replacements.push({ value, title, url }); updateLocation(url); },
    pushState(value, title, url) { this.state = value; this.pushes.push({ value, title, url }); updateLocation(url); },
  };
  function node(id) {
    if (!nodes.has(id)) {
      const attributes = new Map();
      const nodeListeners = new Map();
      nodes.set(id, {
        value: id === 'topK' ? '10' : '', textContent: '', innerHTML: '', dataset: {}, options: [{ value: '10' }, { value: '5' }],
        open: false, hidden: false, disabled: false, checked: false,
        listeners: nodeListeners,
        addEventListener(name, callback) { nodeListeners.set(name, callback); }, appendChild(child) { this.options.push(child); },
        setAttribute(key, value) { attributes.set(key, value); }, getAttribute(key) { return attributes.get(key); },
        removeAttribute(key) { attributes.delete(key); },
        classList: { add() {}, remove() {}, toggle() {} },
      });
    }
    return nodes.get(id);
  }
  const context = vm.createContext({
    URL, URLSearchParams, FormData, Set, Date, JSON, performance: { getEntriesByType: () => [{ type: navigationType }] }, history,
    location: testLocation,
    sessionStorage: { getItem: (key) => storage.get(key) ?? null,
      setItem(key, value) {
        if (failArchiveWrite && key.startsWith('externalSourcingArchive:')) throw new Error('Synthetic storage quota');
        if (storageWriteFailures.has(key)) throw new Error('Synthetic storage quota');
        storage.set(key, String(value));
      }, removeItem: (key) => storage.delete(key) },
    document: { addEventListener(name, callback) { listeners.set(name, callback); }, getElementById: node, querySelectorAll: () => [], querySelector: () => null, createElement: () => node(`generated-${nodes.size}`) },
    window: { location: testLocation, history,
      performance: { getEntriesByType: () => [{ type: navigationType }] }, scrollX: 0, scrollY: 350,
      addEventListener(name, callback) {
        windowListeners.set(name, [...new Set([...(windowListeners.get(name) || []), callback])]);
      }, removeEventListener(name, callback) {
        windowListeners.set(name, (windowListeners.get(name) || []).filter((registered) => registered !== callback));
      }, dispatchEvent() {}, setTimeout(callback) { callback(); },
      scrollTo(x, y) { rendered.scroll.push([x, y]); },
    },
    Option: function Option(text, value) { this.text = text; this.value = value; },
    Event: function Event(name) { this.type = name; }, requestAnimationFrame(callback) { callback(); },
    console: { log() {}, warn() {}, error() {} },
    api: async (url, options = {}) => {
      calls.push({ url, method: options.method || 'GET' });
      assert.equal(options.method || 'GET', 'GET', 'Initialization/archive navigation must never POST to a provider');
      if (apiHook) {
        const response = await apiHook(url, options);
        if (response !== undefined) return response;
      }
      if (url.includes('/external/search-history?')) {
        if (listError) throw new Error('Synthetic archive list unavailable');
        return { searches: [] };
      }
      if (url.includes('/external/search-history/')) return archiveResponse || {
        source: 'pdl', results: [{ source: 'pdl', source_id: 'archived-person', name: 'Synthetic Archived Candidate' }],
        sourceAudit: { queryExecuted: false, estimatedCreditsUsed: 0 }, savedSearch: { id: 'older-than-100', rootId: 'older-than-100', queryName: 'Archive fixture' },
        savedQuery: { source: 'pdl', queryMode: 'direct', directQuery: 'Python', domain }, searchUsesJobDescription: false,
      };
      if (url.includes('/getJob/')) return { jd_id: decodeURIComponent(url.split('/getJob/')[1].split('?')[0]), title: 'Synthetic JD', skills: ['Python'] };
      throw new Error(`Unexpected test request ${url}`);
    },
    escapeHtml: (value) => String(value ?? ''),
  });
  vm.runInContext(source, context);
  const run = (code) => vm.runInContext(code, context);
  const productionFunctions = { loadSearchCriteria: context.loadSearchCriteria };
  context.currentDomain = () => domain;
  context.rankExternalResults = (rows) => rows;
  context.renderResults = (rows, options = {}) => { rendered.results = rows; rendered.selected = [...(options.preservedCandidateKeys || [])]; };
  context.renderSourceAudit = (audit) => { rendered.audit = audit; };
  context.renderResultPager = () => {};
  context.updateWorkflowGuidance = () => {};
  context.updateSelectedEnrichmentCount = () => {};
  context.refreshProviderCredits = () => {};
  context.currentSearchCriteria = () => rendered.criteria;
  context.setSearchCriteria = (criteria) => { rendered.criteria = criteria; };
  context.setSource = (value) => run(`selectedSource = ${JSON.stringify(value)}`);
  context.selectedCandidateKeys = () => new Set(rendered.selected);
  context.criterionIgnored = (group) => (rendered.criteria.ignoredCriteria || []).includes(group);
  context.loadSearchCriteria = async (id) => { rendered.criteriaLoadedFor = id; };
  context.rememberJob = (id, job) => { rendered.rememberedJob = { id, job }; };
  run('externalJobPicker = { setSelection(id, job) { document.getElementById("jdSelect").value = id; }, clear() { document.getElementById("jdSelect").value = ""; } };');
  // These stubs only replace external component mounting/diagnostics. Actual
  // DOMContentLoaded navigation, archive, event and persistence handlers run.
  context.window.DevReadyJobPicker = { mount: () => run('externalJobPicker') };
  context.window.DevReadyProviderAccountUsage = { mount: () => ({ invalidate() {} }) };
  context.configureDomainPage = () => {};
  context.loadProviderStatus = async () => {};
  return { context, run, storage, storageWriteFailures, nodes, node, calls, rendered, history, listeners, windowListeners, productionFunctions };
}

function snapshot(domain = 'dev', extra = {}) {
  return { version: 1, domain, jobId: 'old-job', selectedSource: 'pdl', directSearch: 'Python', topK: '5',
    criteria: { ignoredCriteria: ['skills'], requiredSkills: [] }, criteriaPanelOpen: false,
    selectedCandidateKeys: ['pdl:person-1'], results: [{ source: 'pdl', source_id: 'person-1', name: 'Synthetic Candidate' }],
    activeExternalSearch: { endpoint: '/api/azureJobs/external/search', fields: [['jd_id', 'old-job']] },
    activeSavedSearch: { id: 'saved-old', rootId: 'saved-old', recordCount: 1 },
    nextExternalScrollToken: 'saved-next-page-token', cumulativeSourceAudit: { estimatedCreditsUsed: 5 },
    externalSearchCompleted: true, scroll: { x: 0, y: 470 }, statusText: 'One candidate returned.', jobSkillText: 'Python', ...extra };
}

test('stored sourcing view is accepted only for its exact domain and schema', () => {
  const wrong = fixture({ stored: { 'externalSourcingView:dev': JSON.stringify(snapshot('law')) } });
  assert.equal(wrong.context.readSourcingViewState(), null);
  const malformed = fixture({ stored: { 'externalSourcingView:dev': '{broken' } });
  assert.equal(malformed.context.readSourcingViewState(), null);
  const other = fixture({ domain: 'law', stored: { 'externalSourcingView:dev': JSON.stringify(snapshot()) } });
  assert.equal(other.context.readSourcingViewState(), null);
});

test('restoring a saved view preserves results, selections, criteria and scroll without requests', () => {
  const fx = fixture({ navigationType: 'back_forward' });
  const view = snapshot();
  assert.equal(fx.context.restoreSourcingViewState(view), true);
  assert.equal(fx.rendered.results[0].source_id, 'person-1');
  assert.deepEqual(fx.rendered.selected, ['pdl:person-1']);
  assert.deepEqual(fx.rendered.criteria.ignoredCriteria, ['skills']);
  assert.equal(fx.node('externalSearchInput').value, 'Python');
  assert.equal(fx.node('topK').value, '5');
  assert.ok(fx.rendered.scroll.some(([x, y]) => x === 0 && y === 470));
  assert.equal(fx.calls.length, 0);
});

test('navigation type distinguishes modern reload and Back/Forward with a safe legacy fallback', () => {
  for (const type of ['navigate', 'reload', 'back_forward']) {
    assert.equal(fixture({ navigationType: type }).context.sourcingNavigationType(), type);
  }
  const fx = fixture();
  for (const [type, expected] of [[0, 'navigate'], [1, 'reload'], [2, 'back_forward']]) {
    fx.context.window.performance = { navigation: { type } };
    assert.equal(fx.context.sourcingNavigationType(), expected);
  }
  fx.context.window.performance = undefined;
  assert.equal(fx.context.sourcingNavigationType(), 'navigate');
  fx.context.window.performance = { getEntriesByType() { throw new Error('Unsupported timing'); } };
  assert.equal(fx.context.sourcingNavigationType(), 'navigate');
});

test('Refresh archives the full current snapshot before clearing only current-domain view pointers', () => {
  const view = snapshot('dev', { results: [{ source: 'pdl', source_id: 'person-1',
    email: 'synthetic@example.test', external_enrichment: { enrichedAt: '2026-09-10T12:00:00Z' } }] });
  const otherView = JSON.stringify(snapshot('law'));
  const fx = fixture({ navigationType: 'reload', stored: {
    'externalSourcingView:dev': JSON.stringify(view), 'activeSourcingSearch:dev': 'saved-old',
    'workflowRestoreRequested:dev': 'true', 'externalSourcingView:law': otherView,
    'activeSourcingSearch:law': 'law-search', 'workflowRestoreRequested:law': 'true',
    selectedJdId: 'other-page-job', personId: 'synthetic-person', devreadyAdminUnlock: 'synthetic-token',
  } });
  const result = fx.context.prepareSourcingStartup();
  assert.equal(result.reload, true);
  assert.equal(result.savedView, null);
  assert.equal(result.requestedSearchId, '');
  assert.equal(result.preservationFailed, false);
  assert.deepEqual(JSON.parse(fx.storage.get('externalSourcingArchive:dev:saved-old')), view);
  for (const key of ['externalSourcingView:dev', 'activeSourcingSearch:dev', 'workflowRestoreRequested:dev']) {
    assert.equal(fx.storage.has(key), false, key);
  }
  assert.equal(fx.storage.get('externalSourcingView:law'), otherView);
  assert.equal(fx.storage.get('activeSourcingSearch:law'), 'law-search');
  assert.equal(fx.storage.get('workflowRestoreRequested:law'), 'true');
  assert.equal(fx.storage.get('selectedJdId'), 'other-page-job');
  assert.equal(fx.storage.get('personId'), 'synthetic-person');
  assert.equal(fx.storage.get('devreadyAdminUnlock'), 'synthetic-token');
  assert.equal(fx.calls.length, 0);
});

test('Refresh consumes an explicit archive URL without changing browser history state or pushing a new entry', () => {
  const historyState = { workflow: { previous: 'candidate-profile' }, entryKey: 'synthetic-key' };
  const fx = fixture({ navigationType: 'reload', search: '?domain=law&savedSearchId=older-than-100&mode=review', historyState });
  fx.context.window.location.hash = '#results';
  const startup = fx.context.prepareSourcingStartup();
  assert.equal(startup.requestedSearchId, '');
  assert.equal(startup.savedView, null);
  assert.equal(fx.history.state, historyState);
  assert.equal(fx.history.pushes.length, 0);
  assert.equal(fx.history.replacements.length, 1);
  const changedUrl = new URL(fx.history.replacements[0].url, 'https://example.test');
  assert.equal(changedUrl.searchParams.has('savedSearchId'), false);
  assert.equal(changedUrl.searchParams.get('domain'), 'law');
  assert.equal(changedUrl.searchParams.get('mode'), 'review');
  assert.equal(changedUrl.hash, '#results');
});

test('Refresh initialization does not hydrate old results, JD, selection, criteria or active provider search', async () => {
  const fx = fixture({ navigationType: 'reload', search: '?domain=dev&savedSearchId=saved-old', stored: {
    'externalSourcingView:dev': JSON.stringify(snapshot()), 'activeSourcingSearch:dev': 'saved-old',
    selectedJdId: 'global-stale-job',
  } });
  // A browser can restore form values before DOMContentLoaded on reload.
  fx.node('externalSearchInput').value = 'Browser restored query';
  fx.node('topK').value = '5';
  fx.node('jdSelect').value = 'browser-restored-job';
  fx.node('searchCriteriaPanel').open = true;
  await fx.context.initializeSourcingView();
  assert.equal(fx.node('externalSearchInput').value, '');
  assert.equal(fx.node('jdSelect').value, '');
  assert.equal(fx.node('topK').value, '10');
  assert.equal(fx.node('searchCriteriaPanel').open, false);
  assert.deepEqual(fx.rendered.results, []);
  assert.deepEqual(fx.rendered.selected, []);
  assert.deepEqual(fx.rendered.criteria, {});
  assert.equal(fx.rendered.criteriaLoadedFor, undefined);
  assert.equal(fx.run('activeExternalSearch'), null);
  assert.equal(fx.run('activeSavedSearch'), null);
  assert.equal(fx.run('nextExternalScrollToken'), '');
  assert.equal(fx.storage.get('selectedJdId'), 'global-stale-job');
  assert.ok(fx.rendered.scroll.some(([x, y]) => x === 0 && y === 0));
  assert.match(fx.node('mineStatus').textContent, /Ready for a new search/);
  assert.deepEqual(fx.calls, [{ url: '/api/azureJobs/external/search-history?domain=dev&limit=100', method: 'GET' }]);
});

test('Refresh fails safely when browser archive storage is full and retains paid contact evidence', async () => {
  const view = snapshot();
  const storedView = JSON.stringify(view);
  const fx = fixture({ navigationType: 'reload', failArchiveWrite: true, stored: {
    'externalSourcingView:dev': storedView, 'activeSourcingSearch:dev': 'saved-old',
    selectedJdId: 'unrelated-job',
  } });
  const startup = fx.context.prepareSourcingStartup();
  assert.equal(startup.preservationFailed, true);
  assert.deepEqual(startup.savedView, view);
  assert.equal(fx.storage.get('externalSourcingView:dev'), storedView);
  assert.equal(fx.storage.get('activeSourcingSearch:dev'), 'saved-old');
  await fx.context.initializeSourcingView();
  assert.equal(fx.rendered.results[0].source_id, 'person-1');
  assert.equal(fx.node('jdSelect').value, 'old-job');
  assert.match(fx.node('mineStatus').textContent, /results were kept/);
  assert.equal(fx.calls.filter((call) => call.url.includes('/external/search-history/')).length, 0);
  assert.ok(fx.calls.every((call) => call.method === 'GET'));
});

test('Refresh does not discard a local result that has no saved server archive ID', () => {
  const view = snapshot('dev', { activeSavedSearch: null });
  const fx = fixture({ navigationType: 'reload', stored: { 'externalSourcingView:dev': JSON.stringify(view) } });
  const startup = fx.context.prepareSourcingStartup();
  assert.equal(startup.preservationFailed, true);
  assert.deepEqual(startup.savedView, view);
  assert.equal(fx.storage.has('externalSourcingView:dev'), true);
});

test('Back/Forward restores the snapshot JD instead of another page global JD and keeps history state', async () => {
  const historyState = { workflow: { selectedCandidates: ['person-1'] }, entryKey: 'browser-entry' };
  const fx = fixture({ navigationType: 'back_forward', historyState, stored: {
    'externalSourcingView:dev': JSON.stringify(snapshot()), 'activeSourcingSearch:dev': 'saved-old',
    selectedJdId: 'changed-elsewhere-job',
  } });
  await fx.context.initializeSourcingView();
  assert.equal(fx.node('jdSelect').value, 'old-job');
  assert.equal(fx.rendered.rememberedJob.id, 'old-job');
  assert.equal(fx.rendered.criteriaLoadedFor, undefined, 'restored picker overrides must not be replaced from the JD');
  assert.equal(fx.node('externalSearchInput').value, 'Python');
  assert.deepEqual(fx.rendered.selected, ['pdl:person-1']);
  assert.deepEqual(fx.rendered.criteria.ignoredCriteria, ['skills']);
  assert.equal(fx.run('activeExternalSearch.fields[0][1]'), 'old-job');
  assert.equal(fx.run('nextExternalScrollToken'), 'saved-next-page-token');
  assert.equal(fx.run('cumulativeSourceAudit.estimatedCreditsUsed'), 5);
  assert.ok(fx.rendered.scroll.some(([x, y]) => x === 0 && y === 470));
  assert.equal(fx.history.state, historyState);
  assert.equal(fx.history.replacements.length, 1);
  assert.equal(new URL(fx.context.location.href).searchParams.get('savedSearchId'), 'saved-old');
  assert.equal(fx.history.pushes.length, 0);
  assert.deepEqual(fx.calls, [
    { url: '/api/azureJobs/getJob/old-job?domain=dev', method: 'GET' },
    { url: '/api/azureJobs/external/search-history?domain=dev&limit=100', method: 'GET' },
  ]);
});

test('Back/Forward for a direct search with explicitly empty JD never hydrates an unrelated global JD', async () => {
  const fx = fixture({ navigationType: 'back_forward', stored: {
    'externalSourcingView:dev': JSON.stringify(snapshot('dev', { jobId: '' })), selectedJdId: 'unrelated-job',
  } });
  await fx.context.initializeSourcingView();
  assert.equal(fx.node('jdSelect').value, '');
  assert.equal(fx.rendered.results[0].source_id, 'person-1');
  assert.ok(fx.calls.every((call) => !call.url.includes('/getJob/')));
});

test('saved view persistence includes the exact selected JD for later Back/Forward restoration', () => {
  const fx = fixture();
  fx.context.restoreSourcingViewState(snapshot());
  fx.node('jdSelect').value = 'job-in-this-view';
  fx.context.persistSourcingViewState();
  const saved = JSON.parse(fx.storage.get('externalSourcingView:dev'));
  assert.equal(saved.jobId, 'job-in-this-view');
  assert.equal(saved.results[0].source_id, 'person-1');
  assert.deepEqual(saved.selectedCandidateKeys, ['pdl:person-1']);
  assert.deepEqual(saved.criteria.ignoredCriteria, ['skills']);
});

for (const listError of [false, true]) {
  test(`explicit archive opens an ID older than latest100 even with list ${listError ? 'unavailable' : 'empty'}`, async () => {
    const fx = fixture({ search: '?domain=dev&savedSearchId=older-than-100', listError, stored: {
      'externalSourcingView:dev': JSON.stringify(snapshot()), 'activeSourcingSearch:dev': 'saved-old',
    } });
    await fx.context.initializeSourcingView();
    assert.equal(fx.rendered.results[0].source_id, 'archived-person');
    assert.equal(fx.run('activeSavedSearch.rootId'), 'older-than-100');
    assert.equal(fx.storage.get('activeSourcingSearch:dev'), 'older-than-100');
    assert.equal(fx.calls.filter((call) => call.url === '/api/azureJobs/external/search-history/older-than-100?domain=dev').length, 1);
    assert.ok(fx.calls.every((call) => call.method === 'GET'));
    assert.match(fx.node('mineStatus').textContent, /provider was not contacted and 0 search credits/);
  });
}

test('opening archive explicitly or from the selector sends only current-domain GETs with an encoded ID', async () => {
  for (const domain of ['dev', 'law', 'engineer', 'dental']) {
    const fx = fixture({ domain });
    fx.node('savedSearchSelect').value = 'synthetic/id?old=1';
    await fx.context.openSavedSearch();
    assert.equal(fx.rendered.results[0].source_id, 'archived-person');
    assert.deepEqual(fx.calls, [{ url: `/api/azureJobs/external/search-history/synthetic%2Fid%3Fold%3D1?domain=${domain}`, method: 'GET' }]);
    assert.equal(fx.storage.has(`externalSourcingView:${domain}`), true);
    assert.equal(fx.storage.has(`externalSourcingView:${domain === 'law' ? 'dev' : 'law'}`), false);
  }
});

test('a different-domain active search and view are never hydrated into the current domain', async () => {
  const fx = fixture({ domain: 'dental', navigationType: 'back_forward', stored: {
    'externalSourcingView:law': JSON.stringify(snapshot('law')), 'activeSourcingSearch:law': 'law-search',
  } });
  await fx.context.initializeSourcingView();
  assert.deepEqual(fx.rendered.results, []);
  assert.deepEqual(fx.calls, [{ url: '/api/azureJobs/external/search-history?domain=dental&limit=100', method: 'GET' }]);
  assert.equal(fx.storage.get('activeSourcingSearch:law'), 'law-search');
});

test('browser archive requires matching domain, version and root ID before legacy enrichment can be reused', () => {
  const archiveKey = 'externalSourcingArchive:dev:saved-old';
  for (const bad of [snapshot('law'), snapshot('dev', { version: 2 }), snapshot('dev', { activeSavedSearch: { rootId: 'other' } })]) {
    const fx = fixture({ stored: { [archiveKey]: JSON.stringify(bad) } });
    assert.equal(fx.context.readArchivedSourcingView('saved-old'), null);
  }
  const fx = fixture({ stored: { [archiveKey]: JSON.stringify(snapshot()) } });
  assert.equal(fx.context.readArchivedSourcingView('saved-old').results[0].source_id, 'person-1');
  assert.equal(fx.context.readArchivedSourcingView('missing'), null);
});

test('archive merge preserves newer matching browser enrichment without adding people or replacing newer server evidence', () => {
  const server = [
    { source: 'pdl', source_id: 'person-1', email: '', external_enrichment: { enrichedAt: '2026-09-10T10:00:00Z' } },
    { source: 'pdl', source_id: 'person-2', email: 'newer-server@example.test', external_enrichment: { enrichedAt: '2026-09-10T14:00:00Z' } },
  ];
  const local = snapshot('dev', { results: [
    { source: 'pdl', source_id: 'person-1', email: 'browser@example.test', external_enrichment: { enrichedAt: '2026-09-10T12:00:00Z' } },
    { source: 'pdl', source_id: 'person-2', email: 'older-browser@example.test', external_enrichment: { enrichedAt: '2026-09-10T12:00:00Z' } },
    { source: 'pdl', source_id: 'local-only', email: 'unrelated@example.test', external_enrichment: { enrichedAt: '2026-09-10T15:00:00Z' } },
  ] });
  const fx = fixture();
  const result = fx.context.mergeArchivedBrowserResults(server, local);
  assert.equal(result.length, 2);
  assert.equal(result[0].email, 'browser@example.test');
  assert.equal(result[1].email, 'newer-server@example.test');
  assert.equal(server[0].email, '', 'merge must not mutate the server response');
  assert.equal(fx.calls.length, 0);
});

test('archive merge never treats case-distinct provider IDs or different providers as the same person', () => {
  const fx = fixture();
  const incoming = { source: 'pdl', source_id: 'AbC', email: 'server@example.test' };
  for (const other of [
    { source: 'pdl', source_id: 'abc' },
    { source: 'coresignal', source_id: 'AbC' },
    { source: 'unknown', source_id: 'AbC' },
  ]) {
    const local = snapshot('dev', { results: [{ ...other, email: 'unrelated@example.test',
      external_enrichment: { enrichedAt: '2026-09-10T12:00:00Z' } }] });
    const merged = fx.context.mergeArchivedBrowserResults([incoming], local);
    assert.equal(merged[0], incoming);
    assert.equal(merged[0].email, 'server@example.test');
  }
});

test('archive merge cannot transfer contact details based only on a name or an untrusted profile URL', () => {
  const fx = fixture();
  for (const profileUrl of ['', 'https://example.test/in/same-person', 'https://linkedin.com.evil.test/in/same-person',
    'https://user:secret@linkedin.com/in/same-person', 'https://linkedin.com/company/same-person']) {
    const row = { source: 'pdl', name: 'Same Synthetic Name', profile_url: profileUrl, email: 'server@example.test' };
    const local = snapshot('dev', { results: [{ ...row, email: 'unrelated@example.test',
      external_enrichment: { enrichedAt: '2026-09-10T12:00:00Z' } }] });
    assert.equal(fx.context.mergeArchivedBrowserResults([row], local)[0], row);
  }
});

test('newer browser evidence overlays only allowed professional fields, preserves workflow, and invalidates old matches', () => {
  const fx = fixture();
  const server = { source: 'pdl', source_id: 'person-1', name: 'Server Name',
    devready_profile_id: 'current-profile', devready_profile_complete: true, status: 'interested',
    interest: { status: 'interested' }, score: 72, match: { score: 72 }, saved_match: { score: 72 },
    external_enrichment: { enrichedAt: '2026-09-10T10:00:00Z' } };
  const oldWorkflowWithNewerEvidence = { ...server, name: 'Stale Name', devready_profile_id: 'stale-profile',
    devready_profile_complete: false, status: 'not_interested', interest: { status: 'not_interested' },
    score: 100, match: { score: 100 }, saved_match: { score: 100 }, email: 'new-contact@example.test',
    professional_enrichment_complete: true, external_enrichment: { enrichedAt: '2026-09-10T12:00:00Z' } };
  const [merged] = fx.context.mergeArchivedBrowserResults([server], snapshot('dev', { results: [oldWorkflowWithNewerEvidence] }));
  assert.equal(merged.email, 'new-contact@example.test');
  assert.equal(merged.professional_enrichment_complete, true);
  assert.equal(merged.devready_profile_id, 'current-profile');
  assert.equal(merged.devready_profile_complete, true);
  assert.equal(merged.name, 'Server Name');
  assert.equal(merged.status, 'interested');
  assert.equal(merged.interest.status, 'interested');
  assert.equal(merged.score, null);
  assert.equal(merged.match, null);
  assert.equal(merged.saved_match, null);
  assert.equal(merged.match_pending, true);
});

async function bootPage(fx) {
  let startup;
  const initialize = fx.context.initializeSourcingView;
  fx.context.initializeSourcingView = () => { startup = initialize(); return startup; };
  fx.listeners.get('DOMContentLoaded')();
  assert.ok(startup, 'The real page-ready handler must start sourcing initialization');
  await startup;
}

test('changing the archive dropdown does not change the loaded archive or saved result identity', async () => {
  const fx = fixture({ search: '?domain=dev&savedSearchId=saved-old', stored: {
    'externalSourcingView:dev': JSON.stringify(snapshot()), 'activeSourcingSearch:dev': 'saved-old',
  } });
  await bootPage(fx);
  const select = fx.node('savedSearchSelect');
  select.value = 'archive-not-opened';
  select.listeners.get('change').call(select, { target: select });
  assert.equal(fx.node('btnOpenSavedSearch').disabled, false);
  assert.equal(fx.run('activeSavedSearch.rootId'), 'saved-old');
  assert.equal(fx.storage.get('activeSourcingSearch:dev'), 'saved-old');
  assert.equal(new URL(fx.context.location.href).searchParams.get('savedSearchId'), 'saved-old');
  for (const callback of fx.windowListeners.get('pagehide')) callback({ persisted: false });
  const stored = JSON.parse(fx.storage.get('externalSourcingView:dev'));
  assert.equal(stored.activeSavedSearch.rootId, 'saved-old');
  assert.equal(stored.results[0].source_id, 'person-1');
  assert.ok(fx.calls.every((call) => !call.url.includes('/external/search-history/')));
});

test('Open archive button ignores its MouseEvent and replaces URL A with loaded B without pushing history', async () => {
  const historyState = { entry: 'same-browser-history-entry' };
  const fx = fixture({ search: '?domain=dev&savedSearchId=saved-old', historyState, stored: {
    'externalSourcingView:dev': JSON.stringify(snapshot()),
  }, archiveResponse: {
    source: 'pdl', results: [{ source: 'pdl', source_id: 'candidate-b' }],
    savedSearch: { id: 'archive-b', rootId: 'archive-b' }, savedQuery: { queryMode: 'direct', directQuery: 'Python' },
  } });
  await bootPage(fx);
  fx.node('savedSearchSelect').value = 'archive-b';
  await fx.node('btnOpenSavedSearch').listeners.get('click')({ type: 'click', target: fx.node('btnOpenSavedSearch') });
  assert.equal(fx.rendered.results[0].source_id, 'candidate-b');
  assert.equal(fx.run('activeSavedSearch.rootId'), 'archive-b');
  assert.equal(new URL(fx.context.location.href).searchParams.get('savedSearchId'), 'archive-b');
  assert.equal(fx.history.state, historyState);
  assert.equal(fx.history.pushes.length, 0);
  assert.equal(fx.calls.filter((call) => call.url === '/api/azureJobs/external/search-history/archive-b?domain=dev').length, 1);
  assert.ok(fx.calls.every((call) => !call.url.includes('%5Bobject%20Object%5D')));
  const stored = JSON.parse(fx.storage.get('externalSourcingView:dev'));
  assert.equal(stored.activeSavedSearch.rootId, 'archive-b');
  assert.equal(fx.node('jdSelect').value, '', 'a direct/no-JD archive must not inherit the previously opened search JD');
  assert.equal(stored.jobId, '');
});

test('snapshot quota failure retains the last complete view and enables unload warning only until recovery', () => {
  const original = JSON.stringify(snapshot());
  const fx = fixture({ stored: { 'externalSourcingView:dev': original } });
  fx.context.restoreSourcingViewState(snapshot());
  fx.node('externalSearchInput').value = 'A newer unsaved query';
  fx.storageWriteFailures.add('externalSourcingView:dev');
  fx.context.persistSourcingViewState();
  assert.equal(fx.storage.get('externalSourcingView:dev'), original, 'atomic failure must not replace a complete snapshot with an empty one');
  assert.equal(fx.node('browserPersistenceStatus').hidden, false);
  assert.match(fx.node('browserPersistenceStatus').textContent, /last saved view and server archive were kept/);
  assert.equal(fx.windowListeners.get('beforeunload').length, 1);
  const warning = fx.windowListeners.get('beforeunload')[0];
  const event = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
  warning(event);
  assert.equal(event.defaultPrevented, true);
  assert.equal(event.returnValue, '');
  fx.context.persistSourcingViewState();
  assert.equal(fx.windowListeners.get('beforeunload').length, 1, 'repeat failure must not duplicate the guard');
  fx.storageWriteFailures.clear();
  fx.context.persistSourcingViewState();
  assert.equal(fx.node('browserPersistenceStatus').hidden, true);
  assert.equal(fx.windowListeners.get('beforeunload').length, 0, 'normal navigation must remain eligible for Back/Forward caching');
  assert.equal(JSON.parse(fx.storage.get('externalSourcingView:dev')).directSearch, 'A newer unsaved query');
  const recoveredEvent = { preventDefault() { assert.fail('Recovered storage must not block leaving'); } };
  warning(recoveredEvent);
  assert.equal(fx.calls.length, 0);
});

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => { resolve = resolvePromise; reject = rejectPromise; });
  return { promise, resolve, reject };
}

function archiveFixture(id) {
  return { source: 'pdl', results: [{ source: 'pdl', source_id: `candidate-${id}` }],
    savedSearch: { id, rootId: id }, savedQuery: { queryMode: 'direct', directQuery: `Query ${id}` } };
}

for (const oldOutcome of ['success', 'failure']) {
  test(`a slower archive ${oldOutcome} cannot overwrite a newer archive already opened`, async () => {
    const older = deferred();
    const newer = deferred();
    const fx = fixture({ apiHook: (url) => {
      if (url.includes('/external/search-history/archive-a?')) return older.promise;
      if (url.includes('/external/search-history/archive-b?')) return newer.promise;
    } });
    const openOlder = fx.context.openSavedSearch('archive-a');
    const openNewer = fx.context.openSavedSearch('archive-b');
    newer.resolve(archiveFixture('archive-b'));
    await openNewer;
    const successfulStatus = fx.node('mineStatus').textContent;
    if (oldOutcome === 'failure') older.reject(new Error('Synthetic obsolete response failure'));
    else older.resolve(archiveFixture('archive-a'));
    await openOlder;
    assert.equal(fx.rendered.results[0].source_id, 'candidate-archive-b');
    assert.equal(fx.run('activeSavedSearch.rootId'), 'archive-b');
    assert.equal(fx.node('mineStatus').textContent, successfulStatus);
    assert.equal(new URL(fx.context.location.href).searchParams.get('savedSearchId'), 'archive-b');
    assert.equal(JSON.parse(fx.storage.get('externalSourcingView:dev')).activeSavedSearch.rootId, 'archive-b');
    assert.ok(fx.calls.every((call) => call.method === 'GET'));
  });
}

test('opening a different archive is blocked during candidate work that belongs to the current results', async () => {
  for (const variable of ['sourcingContactOperations', 'paginationLoading', 'externalSearchRunning', 'externalBulkEnrichmentRunning', 'externalImportRunning']) {
    const fx = fixture();
    fx.context.restoreSourcingViewState(snapshot());
    fx.run(`${variable} = ${variable === 'sourcingContactOperations' ? 1 : 'true'};`);
    await fx.context.openSavedSearch('archive-b');
    assert.equal(fx.calls.length, 0, variable);
    assert.equal(fx.run('activeSavedSearch.rootId'), 'saved-old');
    assert.equal(fx.rendered.results[0].source_id, 'person-1');
    assert.match(fx.node('mineStatus').textContent, /Finish the current candidate action/);
  }
});

test('an archive response cannot render after the active domain changed', async () => {
  const pending = deferred();
  const fx = fixture({ apiHook: () => pending.promise });
  const operation = fx.context.openSavedSearch('archive-a');
  fx.context.currentDomain = () => 'law';
  pending.resolve(archiveFixture('archive-a'));
  await operation;
  assert.deepEqual(fx.rendered.results, []);
  assert.equal(fx.run('activeSavedSearch'), null);
  assert.equal(fx.storage.has('externalSourcingView:law'), false);
  assert.equal(fx.storage.has('externalSourcingView:dev'), false);
});

test('delayed startup JD hydration cannot replace an archive explicitly opened while startup was pending', async () => {
  const job = deferred();
  const fx = fixture({ navigationType: 'back_forward', stored: {
    'externalSourcingView:dev': JSON.stringify(snapshot()), 'activeSourcingSearch:dev': 'saved-old',
  }, apiHook: (url) => url.includes('/getJob/old-job?') ? job.promise : undefined,
  archiveResponse: archiveFixture('archive-b') });
  const startup = fx.context.initializeSourcingView();
  await fx.context.openSavedSearch('archive-b');
  assert.equal(fx.rendered.results[0].source_id, 'candidate-archive-b');
  job.resolve({ jd_id: 'old-job', title: 'Old Job', skills: ['Python'] });
  await startup;
  assert.equal(fx.rendered.results[0].source_id, 'candidate-archive-b');
  assert.equal(fx.run('activeSavedSearch.rootId'), 'archive-b');
  assert.equal(fx.node('jdSelect').value, '');
  assert.equal(new URL(fx.context.location.href).searchParams.get('savedSearchId'), 'archive-b');
});

test('delayed startup criteria cannot replace overrides from a newly opened archive', async () => {
  const criteria = deferred();
  const requested = deferred();
  const archiveB = archiveFixture('archive-b');
  archiveB.savedQuery.criteria = { ignoredCriteria: ['skills', 'licenses'], requiredSkills: ['Go'] };
  const fx = fixture({ stored: { selectedJdId: 'old-job' }, archiveResponse: archiveB, apiHook: (url) => {
    if (url.includes('/external/criteria/old-job?')) { requested.resolve(); return criteria.promise; }
  } });
  fx.context.loadSearchCriteria = fx.productionFunctions.loadSearchCriteria;
  const startup = fx.context.initializeSourcingView();
  await requested.promise;
  await fx.context.openSavedSearch('archive-b');
  const activeStatus = fx.node('mineStatus').textContent;
  criteria.resolve({ criteria: { ignoredCriteria: [], requiredSkills: ['Python'] } });
  await startup;
  assert.deepEqual(fx.rendered.criteria, archiveB.savedQuery.criteria);
  assert.equal(fx.node('mineStatus').textContent, activeStatus);
  assert.equal(fx.node('jdSelect').value, '');
  assert.equal(fx.rendered.results[0].source_id, 'candidate-archive-b');
});

for (const outcome of ['success', 'failure']) {
  test(`delayed startup archive-list ${outcome} cannot reopen an old archive or overwrite a newer archive list`, async () => {
    const list = deferred();
    const requested = deferred();
    const fx = fixture({ stored: { 'activeSourcingSearch:dev': 'archive-a' }, archiveResponse: archiveFixture('archive-b'),
      apiHook: (url) => {
        if (url.includes('/external/search-history?')) { requested.resolve(); return list.promise; }
      } });
    const startup = fx.context.initializeSourcingView();
    await requested.promise;
    await fx.context.openSavedSearch('archive-b');
    const activeLabel = fx.node('activeSavedSearchName').textContent;
    if (outcome === 'success') list.resolve({ searches: [{ id: 'archive-a', rootId: 'archive-a' }] });
    else list.reject(new Error('Synthetic stale list failure'));
    await startup;
    assert.equal(fx.run('savedExternalSearches.length'), 1);
    assert.equal(fx.run('savedExternalSearches[0].rootId'), 'archive-b');
    assert.equal(fx.node('activeSavedSearchName').textContent, activeLabel);
    assert.equal(fx.rendered.results[0].source_id, 'candidate-archive-b');
    assert.equal(fx.run('activeSavedSearch.rootId'), 'archive-b');
    assert.ok(fx.calls.every((call) => !call.url.includes('/external/search-history/archive-a?')));
  });
}

test('manual JD selection completes without an out-of-scope startup guard', async () => {
  const fx = fixture();
  fx.node('jdSelect').value = 'manually-selected-job';
  await fx.context.applySelectedExternalJob('manually-selected-job');
  assert.equal(fx.node('jdSelect').value, 'manually-selected-job');
  assert.equal(fx.rendered.criteriaLoadedFor, 'manually-selected-job');
  assert.match(fx.node('mineStatus').textContent, /JD loaded/);
});

for (const change of ['different-person', 'case-distinct-person', 'different-archive']) {
  test(`completed synthetic contact response is ignored when the view now has ${change}`, async () => {
    const response = deferred();
    const fx = fixture();
    fx.context.restoreSourcingViewState(snapshot());
    // This explicit mock accepts a single synthetic POST. It has no network
    // transport and must never call the generic navigation-only api fixture.
    fx.context.api = async (url, options) => {
      fx.calls.push({ url, method: options.method });
      assert.equal(url, '/api/azureJobs/external/enrich-result');
      assert.equal(options.method, 'POST');
      const payload = JSON.parse(options.body);
      assert.equal(payload.search_id, 'saved-old');
      assert.equal(payload.candidate.source_id, 'person-1');
      return response.promise;
    };
    const enrichment = fx.context.enrichExternalResultCandidate(0);
    assert.equal(fx.run('sourcingContactOperations'), 1);
    if (change === 'different-archive') {
      fx.context.setActiveSavedSearch({ id: 'archive-b', rootId: 'archive-b', recordCount: 1 });
    } else {
      const replacementId = change === 'case-distinct-person' ? 'PERSON-1' : 'person-2';
      fx.run(`latestExternalResults = [{source: 'pdl', source_id: '${replacementId}', email: 'keep@example.test'}];`);
    }
    const before = fx.run('JSON.stringify(latestExternalResults)');
    response.resolve({ candidate: { source: 'pdl', source_id: 'person-1', email: 'old-response@example.test' },
      enrichment: { status: 'completed', provider: 'synthetic-only', enrichedAt: '2026-09-10T12:00:00Z' } });
    const result = await enrichment;
    assert.equal(result.viewChanged, true);
    assert.equal(fx.run('JSON.stringify(latestExternalResults)'), before);
    assert.equal(fx.run('sourcingContactOperations'), 0);
    assert.equal(fx.calls.length, 1);
  });
}

test('Load more and new search respect the active pagination/archive/candidate-operation locks', async () => {
  for (const busyFlag of ['paginationLoading', 'sourcingArchiveLoading', 'sourcingContactOperations',
    'externalSearchRunning', 'externalBulkEnrichmentRunning', 'externalImportRunning']) {
    const fx = fixture();
    fx.context.restoreSourcingViewState(snapshot());
    fx.run(`${busyFlag} = ${busyFlag === 'sourcingContactOperations' ? 1 : 'true'};`);
    await fx.context.loadMoreExternalCandidates();
    assert.equal(fx.calls.length, 0, `Load more must not request while ${busyFlag}`);
    assert.equal(fx.run('latestExternalResults[0].source_id'), 'person-1');
    if (busyFlag === 'paginationLoading') {
      await fx.context.mineCandidates();
      assert.equal(fx.calls.length, 0, 'a new search must not replace the view while its next page is loading');
      assert.equal(fx.run('activeSavedSearch.rootId'), 'saved-old');
    }
  }
});

for (const outcome of ['success', 'failure']) {
  test(`a delayed next-page ${outcome} cannot append or change status after its search, revision, domain or archive changed`, async () => {
    for (const changed of ['search', 'revision', 'domain', 'archive']) {
      const pending = deferred();
      const fx = fixture();
      fx.context.restoreSourcingViewState(snapshot());
      // Synthetic-only provider-response mock: exactly one known request is
      // recorded; no fetch, sockets, DB access or provider calls are available.
      fx.context.api = async (url, options) => {
        fx.calls.push({ url, method: options.method });
        assert.equal(url, '/api/azureJobs/external/search');
        assert.equal(options.method, 'POST');
        assert.equal(options.body.get('scroll_token'), 'saved-next-page-token');
        return pending.promise;
      };
      const operation = fx.context.loadMoreExternalCandidates();
      assert.equal(fx.run('paginationLoading'), true);
      if (changed === 'search') fx.run('activeExternalSearch = { ...activeExternalSearch };');
      if (changed === 'revision') fx.run('sourcingArchiveRevision++;');
      if (changed === 'domain') fx.context.currentDomain = () => 'law';
      if (changed === 'archive') fx.context.setActiveSavedSearch({ id: 'archive-b', rootId: 'archive-b', recordCount: 1 });
      fx.run("latestExternalResults = [{source: 'pdl', source_id: 'new-view-person'}];");
      fx.context.renderResults(fx.run('latestExternalResults'));
      fx.node('mineStatus').textContent = 'The newer view stays current.';
      const before = fx.run('JSON.stringify({results:latestExternalResults,archive:activeSavedSearch,token:nextExternalScrollToken,audit:cumulativeSourceAudit})');
      if (outcome === 'success') pending.resolve({ results: [{ source: 'pdl', source_id: 'stale-page-person' }],
        savedSearch: { id: 'old-page-root', rootId: 'old-page-root' }, pagination: { nextScrollToken: 'stale-next-token' },
        sourceAudit: { recordsReturned: 100, estimatedCreditsUsed: 100 } });
      else pending.reject(new Error('Synthetic stale next-page failure'));
      await operation;
      assert.equal(fx.run('JSON.stringify({results:latestExternalResults,archive:activeSavedSearch,token:nextExternalScrollToken,audit:cumulativeSourceAudit})'), before, changed);
      assert.equal(fx.rendered.results[0].source_id, 'new-view-person', changed);
      assert.equal(fx.node('mineStatus').textContent, 'The newer view stays current.', changed);
      assert.equal(fx.calls.length, 1);
    }
  });
}
