const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const page = fs.readFileSync(path.join(__dirname, '../ui/pages/determine-interest.html'), 'utf8');
const source = [...page.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map(m => m[1]).find(s => s.includes('function loadInterestProfiles'));

function fixture({ domain = 'dev', ids = ['701', '702'], job = 'job-a', context, request, search = '' } = {}) {
  const stored = new Map([['domain', domain], ['jobID', job], [`determineInterestBatch:${domain}`, JSON.stringify(ids)]]);
  if (context) stored.set(`determineInterestBatchContext:${domain}`, JSON.stringify(context));
  const nodes = new Map();
  const calls = [];
  const shortlist = [];
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, { textContent: '', innerHTML: '', hidden: false, disabled: false, href: '', addEventListener() {} });
    return nodes.get(id);
  };
  const sandbox = vm.createContext({
    URL, URLSearchParams, Map, Set, console,
    sessionStorage: { getItem: k => stored.get(k) ?? null, setItem: (k, v) => stored.set(k, String(v)) },
    document: { getElementById: node, addEventListener() {}, querySelector: () => node('backLink') },
    window: { location: { search }, DevReadyShortlist: {
      add: (...args) => shortlist.push({ action: 'add', args }),
      remove: (...args) => shortlist.push({ action: 'remove', args }),
    }, DevReadyProfessionalMatch: {
      view: p => ({ score: p.score ?? null, current: p.current !== false, match: { matched: [], reason: 'Saved professional evidence', coveragePercent: 70 }, label: 'JD match', detail: 'Pending' }),
      details: () => '<p>Evidence</p>',
    } },
    api: async (url, options = {}) => {
      calls.push({ url, options });
      return request ? request(url, options) : { profiles: [person('701', 75), person('702', 95)] };
    },
    escapeHtml: v => String(v ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;'),
  });
  vm.runInContext(source, sandbox);
  return { sandbox, node, calls, stored, shortlist, run: code => vm.runInContext(code, sandbox) };
}
function person(id, score, extra = {}) {
  return { personid: id, name: `Synthetic ${id}`, title: 'Engineer', email: `${id}@example.test`, score, ...extra };
}
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

for (const domain of ['dev', 'law', 'engineer', 'dental']) {
  test(`${domain}: direct navigation with no batch gives clear action and no data/provider calls`, async () => {
    const fx = fixture({ domain, ids: [] });
    await fx.sandbox.loadInterestProfiles();
    assert.equal(fx.calls.length, 0);
    assert.equal(fx.node('interestEmpty').hidden, false);
    assert.match(fx.node('interestEmptyMessage').textContent, /check the people/);
    assert.equal(fx.node('interestSummary').hidden, true);
    assert.equal(fx.node('btnRefreshInterest').disabled, false);
  });
}

test('explicit batch loads exact older IDs without 500-record scan and ranks best first', async () => {
  const fx = fixture();
  await fx.sandbox.loadInterestProfiles();
  assert.match(fx.calls[0].url, /person_ids=701%2C702/);
  assert.doesNotMatch(fx.calls[0].url, /limit=500/);
  const html = fx.node('interestList').innerHTML;
  assert.ok(html.indexOf('Synthetic 702') < html.indexOf('Synthetic 701'));
  assert.match(html, /95%/);
  assert.match(html, /mailto:/);
  assert.equal(fx.node('interestEmpty').hidden, true);
  assert.equal(fx.node('interestNextStep').hidden, false);
});

test('zero match remains numeric and unknowns sort last', async () => {
  const fx = fixture({ ids: ['1', '2', '3'], request: async () => ({ profiles: [person('1', null), person('2', 0), person('3', 95)] }) });
  await fx.sandbox.loadInterestProfiles();
  assert.deepEqual(JSON.parse(fx.run('JSON.stringify(interestProfiles.map(p => p.personid))')), ['3', '2', '1']);
});

test('different job context cannot silently reuse an old batch', async () => {
  const fx = fixture({ context: { jobId: 'other-job' } });
  await fx.sandbox.loadInterestProfiles();
  assert.equal(fx.calls.length, 0);
  assert.match(fx.node('interestEmptyTitle').textContent, /current job/);
  assert.match(fx.node('interestEmptyMessage').textContent, /different job/);
});

test('explicit profile link is independent of an unrelated old batch context', async () => {
  const fx = fixture({ context: { jobId: 'other-job' }, search: '?profileId=701' });
  await fx.sandbox.loadInterestProfiles();
  assert.match(fx.calls[0].url, /person_ids=701$/);
  assert.equal(fx.node('interestSelectedCount').textContent, 1);
});

test('contactless saved selections stay visible with a warning; missing IDs are reported', async () => {
  const fx = fixture({ request: async () => ({ profiles: [person('701', 80, { email: '' })], missingPersonIds: ['702'] }) });
  await fx.sandbox.loadInterestProfiles();
  assert.match(fx.node('interestList').innerHTML, /Synthetic 701/);
  assert.match(fx.node('interestStatus').textContent, /1 selected profile\(s\) could not be loaded/);
  assert.match(fx.node('interestStatus').textContent, /no saved contact channel/);
});

test('names and contacts render while scoring is still pending', async () => {
  let finishScore;
  let scoreStarted;
  const started = new Promise(resolve => { scoreStarted = resolve; });
  let scored = false;
  const fx = fixture({ ids: ['701'], request: async (url, options) => {
    if (options.method === 'POST') {
      assert.match(url, /calculate-match$/);
      scoreStarted();
      await new Promise(resolve => { finishScore = resolve; });
      scored = true;
      return {};
    }
    return { profiles: [person('701', scored ? 90 : null, { current: scored })] };
  } });
  const pending = fx.sandbox.loadInterestProfiles();
  await started;
  assert.match(fx.node('interestList').innerHTML, /Synthetic 701/);
  assert.match(fx.node('interestList').innerHTML, /mailto:/);
  finishScore();
  await pending;
  assert.match(fx.node('interestList').innerHTML, /90%/);
  assert.equal(fx.calls.some(c => /enrich|external\/search/.test(c.url)), false);
});

test('late responses cannot overwrite a newer load', async () => {
  let finishOld;
  let count = 0;
  const fx = fixture({ request: async () => ++count === 1 ? new Promise(resolve => { finishOld = resolve; }) : { profiles: [person('702', 95)] } });
  const first = fx.sandbox.loadInterestProfiles();
  await fx.sandbox.loadInterestProfiles();
  finishOld({ profiles: [person('701', 10)] });
  await first;
  assert.match(fx.node('interestList').innerHTML, /Synthetic 702/);
  assert.doesNotMatch(fx.node('interestList').innerHTML, /Synthetic 701/);
});

test('marking Interested during automatic scoring wins over its late completion', async () => {
  const score = deferred();
  const scoreStarted = deferred();
  const save = deferred();
  const saveStarted = deferred();
  const fx = fixture({ ids: ['701'], request: async (url, options) => {
    if (/calculate-match$/.test(url)) { scoreStarted.resolve(); return score.promise; }
    if (/\/interest$/.test(url)) { saveStarted.resolve(); return save.promise; }
    return { profiles: [person('701', null, { current: false, interestStatus: 'contacting' })] };
  } });
  const loading = fx.sandbox.loadInterestProfiles();
  await scoreStarted.promise;
  const saving = fx.sandbox.updateInterest('701', 'interested');
  await saveStarted.promise;
  score.resolve({});
  await loading;
  assert.equal(fx.node('btnRefreshInterest').disabled, true);
  save.resolve({});
  await saving;
  assert.equal(fx.run('interestProfiles[0].interestStatus'), 'interested');
  assert.match(fx.node('interestList').innerHTML, /Continue to Profile Build/);
  assert.match(fx.node('interestStatus').textContent, /marked Interested/);
  assert.equal(fx.calls.filter(call => !call.options.method).length, 1, 'cancelled scoring must not reload an old batch');
  assert.equal(fx.shortlist.filter(call => call.action === 'add').length, 1);
  assert.equal(fx.node('btnRefreshInterest').disabled, false);
});

test('already-issued post-score GET cannot undo a newly recorded response', async () => {
  const savedSnapshot = deferred();
  const snapshotStarted = deferred();
  let reads = 0;
  const fx = fixture({ ids: ['701'], request: async (url, options) => {
    if (options.method === 'POST') return {};
    if (++reads === 1) return { profiles: [person('701', null, { current: false, interestStatus: 'contacting' })] };
    snapshotStarted.resolve();
    return savedSnapshot.promise;
  } });
  const loading = fx.sandbox.loadInterestProfiles();
  await snapshotStarted.promise;
  await fx.sandbox.updateInterest('701', 'interested');
  savedSnapshot.resolve({ profiles: [person('701', 95, { interestStatus: 'contacting' })] });
  await loading;
  assert.equal(fx.run('interestProfiles[0].interestStatus'), 'interested');
  assert.match(fx.node('interestList').innerHTML, /Continue to Profile Build/);
  assert.match(fx.node('interestStatus').textContent, /marked Interested/);
});

test('saving one response blocks double-submit, manual scoring, and reload until it settles', async () => {
  const save = deferred();
  const saveStarted = deferred();
  const fx = fixture({ ids: ['701'], request: async (url, options) => {
    if (options.method === 'POST') { saveStarted.resolve(); return save.promise; }
    return { profiles: [person('701', 95)] };
  } });
  await fx.sandbox.loadInterestProfiles();
  const saving = fx.sandbox.updateInterest('701', 'interested');
  await saveStarted.promise;
  await fx.sandbox.updateInterest('701', 'not_interested');
  await fx.sandbox.loadInterestProfiles();
  await fx.sandbox.calculateCandidateMatch('701');
  assert.equal(fx.calls.length, 2, 'only the original load and one response write are allowed');
  assert.equal(fx.node('btnRefreshInterest').disabled, true);
  save.resolve({});
  await saving;
  assert.equal(fx.node('btnRefreshInterest').disabled, false);
  assert.equal(fx.run('interestResponsePending'), false);
});

test('late response from another job does not add a candidate to the new active context', async () => {
  const save = deferred();
  const fx = fixture({ ids: ['701'], request: async (url, options) => options.method === 'POST'
    ? save.promise : { profiles: [person('701', 95)] } });
  await fx.sandbox.loadInterestProfiles();
  const saving = fx.sandbox.updateInterest('701', 'interested');
  fx.stored.set('jobID', 'other-job');
  save.resolve({});
  await saving;
  assert.equal(JSON.parse(fx.calls[1].options.body).job_id, 'job-a');
  assert.equal(fx.shortlist.length, 0);
  assert.equal(fx.run('interestProfiles[0].interestStatus'), undefined);
  assert.equal(fx.run('interestResponsePending'), false);
});

test('load failures give retry instructions, not a false empty success', async () => {
  const fx = fixture({ request: async () => { throw new Error('Saved data unavailable'); } });
  await fx.sandbox.loadInterestProfiles();
  assert.match(fx.node('interestStatus').textContent, /Could not load.*Reload saved candidates/);
  assert.equal(fx.node('btnRefreshInterest').disabled, false);
});

test('unrelated job from another domain is not used for scores', () => {
  const fx = fixture();
  fx.stored.set('jobDomain', 'law');
  assert.equal(fx.sandbox.currentJobId(), '');
});

test('candidate report comes before collapsed secondary Atlas tools', () => {
  assert.ok(page.indexOf('id="interestList"') < page.indexOf('id="atlasClientContext"'));
  assert.match(page, /<details class="interest-secondary">/);
  assert.match(page, /id="btnChooseInterestCandidates"/);
  assert.match(page, /Search archive/);
});
