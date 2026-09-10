const assert = require('node:assert/strict');
const { test } = require('node:test');
const widget = require('../ui/pages/JS/providerAccountUsage.js');

function fixture(overrides = {}) {
  return {
    provider: 'People Data Labs', source: 'head_response_headers', checkedAt: '2026-09-10T20:00:00Z', cached: false,
    products: {
      search: { label: 'Search', creditType: 'search', status: 'reported', accountRemainingCredits: 49989,
                purchasedRemainingCredits: 49990, overageRemainingCredits: 0, lifetimeCreditsUsed: 505,
                currentTermUsed: null, currentTermTotal: null, observedAt: '2026-09-10T20:00:00Z', httpStatus: 400 },
      enrich: { label: 'Enrichment', creditType: 'enrich', status: 'partial', accountRemainingCredits: 84,
                purchasedRemainingCredits: 85, overageRemainingCredits: null, lifetimeCreditsUsed: 20,
                currentTermUsed: null, currentTermTotal: null, observedAt: '2026-09-10T20:00:00Z', httpStatus: 400 },
    }, ...overrides,
  };
}

function host() {
  const events = new Map();
  return {
    innerHTML: '', events,
    addEventListener(name, listener) { events.set(name, listener); },
    removeEventListener(name, listener) { if (events.get(name) === listener) events.delete(name); },
    clickRefresh() { events.get('click')?.({ preventDefault() {}, target: { closest: () => true } }); },
  };
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

test('HEAD 400 metadata preserves independent search and enrichment balances', () => {
  const view = widget.describe(fixture());
  assert.equal(view.rows[0].remaining, 49989);
  assert.equal(view.rows[1].remaining, 84);
  assert.equal(view.rows[0].purchased, 49990);
  assert.equal(view.rows[1].purchased, 85);
  assert.match(view.notice, /may not reconcile; do not add them/);
  assert.match(view.notice, /Lifetime usage is not current billing-cycle usage/);
  assert.equal(view.rows[0].lifetimeUsed, 505);
  assert.equal(view.rows[1].lifetimeUsed, 20);
  assert.ok(view.checkedAt);
  assert.equal(view.cached, false);
});

test('zero is known while null, strings, negatives and nonfinite values are unavailable', () => {
  const data = fixture();
  for (const value of [null, undefined, '84', -1, NaN, Infinity, true, 0.5]) {
    data.products.enrich.accountRemainingCredits = value;
    assert.equal(widget.describe(data).rows[1].remaining, null);
  }
  data.products.enrich.accountRemainingCredits = 0;
  assert.equal(widget.describe(data).rows[1].remainingText, '0 credits');
});

test('missing product, wrong bucket or untrusted source never supplies balance', () => {
  assert.equal(widget.describe(fixture({ products: {} })).rows[0].remainingText, 'Unavailable');
  assert.equal(widget.describe(fixture({ source: 'saved_search' })).rows[0].remaining, null);
  const data = fixture();
  data.products.search.creditType = 'enrich';
  assert.equal(widget.describe(data).rows[0].remaining, null);
  data.products.search.creditType = 'search';
  data.products.search.status = 'unavailable';
  assert.equal(widget.describe(data).rows[0].remaining, null);
});

test('current cycle and rate limit fields do not become percentages or totals', () => {
  const data = fixture();
  data.products.search.currentTermUsed = 10;
  data.products.search.currentTermTotal = 100;
  data.products.search.rateLimitRemaining = 9999;
  const view = widget.describe(data);
  assert.equal(view.rows[0].remaining, 49989);
  assert.equal('currentTermUsed' in view.rows[0], false);
  assert.equal('percentage' in view.rows[0], false);
  assert.equal('total' in view.rows[0], false);
});

test('authentication, redirects and server failures cannot masquerade as reported balances', () => {
  for (const status of [100, 301, 302, 401, 403, 404, 500, 503]) {
    const data = fixture();
    data.products.search.httpStatus = status;
    assert.equal(widget.describe(data).rows[0].remaining, null, `HTTP ${status}`);
  }
});

test('exhausted and rate-limited snapshots keep confirmed headers with explicit warnings', async () => {
  for (const [httpStatus, remaining, warning] of [[402, 0, /Credits exhausted/], [429, 17, /rate limited/]]) {
    const data = fixture();
    Object.assign(data.products.search, { httpStatus, accountRemainingCredits: remaining, status: 'reported' });
    const view = widget.describe(data);
    assert.equal(view.rows[0].remaining, remaining);
    assert.equal(view.rows[0].remainingText, `${remaining} credits`);
    assert.match(view.rows[0].warning, warning);
    const element = host();
    const control = widget.mount(element, { token: () => 'a', request: async () => data });
    await control.refresh();
    assert.match(element.innerHTML, warning);
    assert.match(element.innerHTML, new RegExp(`<strong>${remaining} credits</strong>`));
    assert.equal(control.snapshot().state, 'ready');
  }
});

test('partial exhausted or rate-limited headers do not fabricate an available total', () => {
  for (const httpStatus of [402, 429]) {
    const data = fixture();
    Object.assign(data.products.enrich, { httpStatus, status: 'partial', accountRemainingCredits: null,
      purchasedRemainingCredits: 0, lifetimeCreditsUsed: 20 });
    const row = widget.describe(data).rows[1];
    assert.equal(row.remaining, null);
    assert.equal(row.remainingText, 'Unavailable');
    assert.equal(row.purchased, 0);
    assert.equal(row.lifetimeUsed, 20);
    assert.equal(row.status, 'partial');
    assert.ok(row.warning);
  }
});

test('error status and wrong product bucket still reject exhausted or rate-limited counts', () => {
  for (const httpStatus of [402, 429]) {
    const data = fixture();
    Object.assign(data.products.search, { httpStatus, status: 'reported', creditType: 'enrich' });
    assert.equal(widget.describe(data).rows[0].remaining, null);
    Object.assign(data.products.search, { status: 'unavailable', creditType: 'search' });
    assert.equal(widget.describe(data).rows[0].remaining, null);
  }
});

test('per-product cache and observation time survive a newer shared check', async () => {
  const data = fixture();
  data.products.search.cached = false;
  data.products.enrich.cached = true;
  data.products.enrich.observedAt = '2026-09-10T19:59:40Z';
  const view = widget.describe(data);
  assert.equal(view.cached, false);
  assert.equal(view.rows[0].cached, false);
  assert.equal(view.rows[1].cached, true);
  assert.notEqual(view.rows[0].observedAt, view.rows[1].observedAt);
  const element = host();
  const control = widget.mount(element, { token: () => 'a', request: async () => data });
  await control.refresh();
  assert.match(element.innerHTML, /cached up to 30 seconds/);
});

test('mount performs no automatic request and locked state links only to Admin', async () => {
  const element = host();
  let calls = 0;
  const control = widget.mount(element, { request: async () => { calls++; }, token: () => '', domain: () => 'law' });
  assert.match(element.innerHTML, /Unlock account usage/);
  assert.match(element.innerHTML, /admin.html\?domain=law/);
  assert.match(element.innerHTML, /Searches remain available/);
  await control.refresh();
  assert.equal(calls, 0);
});

test('refresh sends token in header only and renders concise rows plus disclosure', async () => {
  const element = host();
  const calls = [];
  const callbacks = [];
  const control = widget.mount(element, {
    request: async (...args) => { calls.push(args); return fixture({ cached: true }); },
    token: () => 'synthetic-private-token', onChange: (value) => callbacks.push(value),
  });
  assert.equal(calls.length, 0);
  await control.refresh();
  assert.equal(calls[0][0], '/api/azureJobs/external/provider-usage');
  assert.equal(calls[0][1].headers['X-DevReady-Admin-Token'], 'synthetic-private-token');
  assert.equal(calls[0][1].method, 'GET');
  assert.match(element.innerHTML, /Search remaining/);
  assert.match(element.innerHTML, /Enrichment remaining/);
  assert.match(element.innerHTML, /Lifetime credits used: 505/);
  assert.match(element.innerHTML, /cached for up to 30 seconds/);
  assert.doesNotMatch(element.innerHTML, /synthetic-private-token/);
  assert.doesNotMatch(JSON.stringify(callbacks), /synthetic-private-token/);
});

test('401 and 403 become unlock guidance without echoing response or error secrets', async () => {
  for (const status of [401, 403]) {
    const element = host();
    const control = widget.mount(element, { token: () => 'private-token', request: async () => {
      throw Object.assign(new Error('secret body <script>'), { status });
    } });
    await control.refresh();
    assert.equal(control.snapshot().state, 'locked');
    assert.match(element.innerHTML, /Unlock account usage/);
    assert.doesNotMatch(element.innerHTML, /private-token|secret body|<script>/);
  }
});

test('request failures clear prior figures and do not interfere with search', async () => {
  const element = host();
  let fail = false;
  const control = widget.mount(element, { token: () => 'a', request: async () => {
    if (fail) throw new Error('sensitive backend error');
    return fixture();
  } });
  await control.refresh();
  fail = true;
  await control.refresh();
  assert.equal(control.snapshot().rows[0].remaining, null);
  assert.equal(control.snapshot().state, 'error');
  assert.match(element.innerHTML, /Your search results are unchanged/);
  assert.doesNotMatch(element.innerHTML, /sensitive backend error/);
});

test('concurrent same-token refreshes coalesce into one request and promise', async () => {
  const wait = deferred();
  let calls = 0;
  const control = widget.mount(host(), { token: () => 'a', request: () => { calls++; return wait.promise; } });
  const first = control.refresh();
  const second = control.refresh();
  assert.equal(first, second);
  await Promise.resolve();
  assert.equal(calls, 1);
  wait.resolve(fixture());
  await first;
  assert.equal(control.snapshot().state, 'ready');
});

test('new-token request supersedes older response even when abort is ignored', async () => {
  const oldWait = deferred(), newWait = deferred();
  let currentToken = 'old';
  const control = widget.mount(host(), { token: () => currentToken, request: (_url, options) =>
    options.headers['X-DevReady-Admin-Token'] === 'old' ? oldWait.promise : newWait.promise });
  const first = control.refresh();
  await Promise.resolve();
  currentToken = 'new';
  const second = control.refresh();
  await Promise.resolve();
  const latest = fixture();
  latest.products.search.accountRemainingCredits = 7;
  newWait.resolve(latest);
  await second;
  oldWait.resolve(fixture());
  await first;
  assert.equal(control.snapshot().rows[0].remaining, 7);
});

test('token removed or changed without refresh invalidates late response', async () => {
  for (const replacement of ['', 'new']) {
    const wait = deferred();
    let currentToken = 'old';
    const element = host();
    const control = widget.mount(element, { token: () => currentToken, request: () => wait.promise });
    const first = control.refresh();
    await Promise.resolve();
    currentToken = replacement;
    wait.resolve(fixture());
    await first;
    assert.equal(control.snapshot().rows[0].remaining, null);
    assert.equal(control.snapshot().state, replacement ? 'idle' : 'locked');
  }
});

test('clear invalidates pending request, and subsequent refresh can succeed', async () => {
  const wait = deferred();
  let calls = 0;
  const control = widget.mount(host(), { token: () => 'a', request: () => ++calls === 1 ? wait.promise : Promise.resolve(fixture()) });
  const first = control.refresh();
  await Promise.resolve();
  control.invalidate();
  const next = control.refresh();
  await next;
  wait.resolve(fixture({ products: {} }));
  await first;
  assert.equal(control.snapshot().rows[0].remaining, 49989);
});

test('dispose removes handlers, blanks sensitive balances and rejects late updates', async () => {
  const wait = deferred();
  const element = host();
  const control = widget.mount(element, { token: () => 'a', request: () => wait.promise });
  const first = control.refresh();
  await Promise.resolve();
  control.dispose();
  assert.equal(element.events.size, 0);
  assert.equal(element.innerHTML, '');
  wait.resolve(fixture());
  await first;
  assert.equal(element.innerHTML, '');
  assert.equal(await control.refresh(), null);
});

test('remounting the same host disposes its earlier writer', async () => {
  const wait = deferred();
  const element = host();
  const old = widget.mount(element, { token: () => 'a', request: () => wait.promise });
  const first = old.refresh();
  await Promise.resolve();
  const next = widget.mount(element, { token: () => '', request: async () => fixture() });
  wait.resolve(fixture());
  await first;
  assert.equal(next.snapshot().state, 'locked');
  assert.match(element.innerHTML, /Unlock account usage/);
  assert.equal(element.events.size, 1);
});

test('untrusted label, error and domain content cannot execute markup', async () => {
  const element = host();
  const control = widget.mount(element, { token: () => '', domain: () => '\"><img src=x onerror=alert(1)>', request: async () => fixture() });
  assert.doesNotMatch(element.innerHTML, /<img/);
  const data = fixture();
  data.products.search.label = '<script>alert(1)</script>';
  data.products.search.errorCode = '<img src=x onerror=alert(1)>';
  assert.equal(widget.describe(data).rows[0].label, 'Search');
  control.dispose();
});

test('refresh button is wired and loading disables repeated user clicks', async () => {
  const element = host();
  const wait = deferred();
  let calls = 0;
  const control = widget.mount(element, { token: () => 'a', request: () => { calls++; return wait.promise; } });
  element.clickRefresh();
  assert.match(element.innerHTML, /disabled aria-disabled="true"/);
  element.clickRefresh();
  await Promise.resolve();
  assert.equal(calls, 1);
  wait.resolve(fixture());
  await control.refresh();
  assert.equal(control.snapshot().state, 'ready');
});

test('malformed data and failing callbacks do not throw or expose prior balances', async () => {
  const control = widget.mount(host(), { token: () => 'a', request: async () => 'not JSON data', onChange() { throw new Error('consumer'); } });
  await control.refresh();
  assert.equal(control.snapshot().state, 'error');
  assert.equal(control.snapshot().rows[0].remaining, null);
});

// Optional browser QA fixtures use real module/theme CSS but no provider,
// application storage or network API access. Set only for local visual checks.
if (process.env.PROVIDER_USAGE_PREVIEW_DIR) {
  const fs = require('node:fs');
  const path = require('node:path');
  const output = path.resolve(process.env.PROVIDER_USAGE_PREVIEW_DIR);
  const pages = path.resolve(__dirname, '../ui/pages');
  const assets = path.resolve(__dirname, '../ui/assets');
  const moduleSource = fs.readFileSync(path.join(pages, 'JS/providerAccountUsage.js'), 'utf8');
  const widgetCss = fs.readFileSync(path.join(assets, 'providerAccountUsage.css'), 'utf8');
  fs.mkdirSync(output, { recursive: true });
  for (const domain of ['dev', 'law', 'engineer', 'dental']) {
    const themeCss = fs.readFileSync(path.join(assets, `${domain}Styles.css`), 'utf8');
    for (const mobile of [false, true]) {
      const name = `synthetic-credit-${domain}${mobile ? '-mobile' : ''}.html`;
      const html = `<!doctype html><html lang="en" data-domain="${domain}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; frame-src 'self'; img-src data:">
<title>Synthetic PDL credits · ${domain}${mobile ? ' · 390px preview' : ''}</title><style>${themeCss}\n${widgetCss}
html,body{margin:0;min-width:0}body{padding:${mobile ? 12 : 24}px;box-sizing:border-box}main{max-width:820px;margin:auto;min-width:0}.fixture-controls{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.fixture-controls button{padding:5px 10px}.fixture-label{font-size:12px;color:var(--muted)}.fixture-frame{display:block;width:390px;max-width:100%;height:650px;border:1px solid var(--border);border-radius:8px;background:white}h1{font-size:22px}h2{font-size:16px;margin-top:24px}
</style></head><body><main><h1>PDL account usage</h1><p class="fixture-label">Synthetic local preview · ${domain} · no provider or application API requests</p>
<div class="fixture-controls" aria-label="Preview state"><button type="button" data-preview="reported">Reported</button><button type="button" data-preview="cached">Cached</button><button type="button" data-preview="partial">Partial</button><button type="button" data-preview="locked">Locked</button><button type="button" data-preview="error">Unavailable</button></div><div id="credit-preview"></div>
${mobile ? '' : `<h2>390-pixel viewport</h2><iframe class="fixture-frame" title="390-pixel mobile credit widget" src="synthetic-credit-${domain}-mobile.html"></iframe>`}
</main><script>${moduleSource}</script><script>
const synthetic = ${JSON.stringify(fixture())};
let previewState = 'cached';
const controller = DevReadyProviderAccountUsage.mount(document.getElementById('credit-preview'), {
  token: () => previewState === 'locked' ? '' : 'synthetic-preview-only', domain: () => '${domain}',
  request: async () => {
    if (previewState === 'error') throw Object.assign(new Error('Synthetic unavailable'), {status:503});
    const data = JSON.parse(JSON.stringify(synthetic));
    data.cached = previewState === 'cached';
    if (previewState === 'partial') {
      data.products.enrich = {status:'unavailable', httpStatus:400, accountRemainingCredits:null};
      data.products.search.status = 'partial';
      data.products.search.lifetimeCreditsUsed = null;
    }
    return data;
  }
});
document.querySelectorAll('[data-preview]').forEach(button => button.addEventListener('click', () => {
  previewState = button.dataset.preview;
  controller.invalidate();
  void controller.refresh();
}));
void controller.refresh();
</script></body></html>`;
      fs.writeFileSync(path.join(output, name), html, 'utf8');
    }
  }
}
