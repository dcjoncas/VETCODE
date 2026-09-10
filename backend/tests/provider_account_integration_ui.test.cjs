const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const pages = path.join(__dirname, '../ui/pages');
for (const pageName of ['mine-candidate-external.html', 'temp-profiles.html']) {
  const page = fs.readFileSync(path.join(pages, pageName), 'utf8');
  const scripts = [...page.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(m => m[1]);
  scripts.forEach(script => new vm.Script(script));
  const source = scripts.find(script => script.includes('function refreshProviderCredits'));
  test(`${pageName} mounts the same protected credit widget and refreshes after a provider operation`, () => {
    assert.match(page, /providerAccountUsage\.css\?v=20260910-credits/);
    assert.match(page, /providerAccountUsage\.js\?v=20260910-credits/);
    assert.match(source, /request: api, token: providerCreditToken, domain: currentDomain/);
    assert.match(source, /refreshProviderCredits\(true\)/);
    assert.match(source, /window\.addEventListener\("focus", \(\) => refreshProviderCredits\(\)\)/);
    assert.match(source, /window\.addEventListener\("pagehide", \(\) => providerCreditWidget\?\.invalidate\(\)\)/);
    assert.doesNotMatch(source, /setInterval\([^)]*refreshProviderCredits/);
  });
  test(`${pageName} credit failures cannot break candidate actions and credentials stay request-only`, async () => {
    let calls = 0;
    let invalidations = 0;
    const context = vm.createContext({ sessionStorage: { getItem: () => '{"token":"synthetic-private-token"}' }, document: { addEventListener() {} }, window: {} });
    // Only evaluate the tiny account integration; never application startup/API calls.
    const start = source.indexOf('let providerCreditWidget = null;');
    const end = source.indexOf('let ', start + 'let providerCreditWidget = null;'.length);
    vm.runInContext(source.slice(start, end), context);
    assert.equal(context.providerCreditToken(), 'synthetic-private-token');
    context.widget = { invalidate() { invalidations++; }, refresh() { calls++; return Promise.reject(new Error('offline')); } };
    vm.runInContext('providerCreditWidget = widget;', context);
    assert.doesNotThrow(() => context.refreshProviderCredits(true));
    await Promise.resolve();
    assert.equal(invalidations, 1);
    assert.equal(calls, 1);
    context.widget.refresh = () => { throw new Error('widget unavailable'); };
    assert.doesNotThrow(() => context.refreshProviderCredits());
    context.sessionStorage.getItem = () => 'invalid json';
    assert.equal(context.providerCreditToken(), '');
  });
}

test('Find Out refreshes after search, next page, cached results and contact lookup without changing search audit totals', () => {
  const page = fs.readFileSync(path.join(pages, 'mine-candidate-external.html'), 'utf8');
  assert.match(page, /paginationLoading = false;\s*renderResultPager\(\);\s*refreshProviderCredits\(true\)/);
  assert.match(page, /externalSearchRunning = false;\s*updateBulkLinkedProfileControls\(\);\s*updateWorkflowGuidance\(\);\s*refreshProviderCredits\(true\)/);
  assert.match(page, /\.finally\(\(\) => \{\s*if \(!externalBulkEnrichmentRunning\) refreshProviderCredits\(true\)/);
  assert.match(page, /externalBulkEnrichmentRunning = false;\s*updateBulkLinkedProfileControls\(\);\s*updateWorkflowGuidance\(\);\s*refreshProviderCredits\(true\)/);
  assert.match(page, /Search balance at retrieval/);
  assert.match(page, /DevReadyProviderUsage\.describe\(audit\)/);
  assert.doesNotMatch(page, /audit\.providerUsage\s*=\s*.*providerCredit/);
});

test('legal profile validation uses reported charges and never mistakes missing billing headers for no matched profile', () => {
  const page = fs.readFileSync(path.join(pages, 'mine-candidate-external.html'), 'utf8');
  const source = page.slice(page.indexOf('function profileValidationHtml'), page.indexOf('async function validateCourtLeadProfile'));
  const context = vm.createContext({ escapeHtml: value => String(value) });
  vm.runInContext(source, context);
  const render = extra => context.profileValidationHtml({ profile_validation: { status: 'confirmed_profile_match', requestsUsed: 1, ...extra } });
  assert.match(render({ providerUsage: { creditsUsed: 0 } }), /0 provider-reported credits/);
  assert.match(render({ providerUsage: { creditsUsed: 3 } }), /3 provider-reported credits/);
  assert.match(render({ providerUsage: { creditsUsed: null }, estimatedCreditsUsed: 1 }), /1 estimated credit — charge not reported/);
  assert.match(render({ successfulEnrichmentCredits: 1 }), /Credit charge not reported/);
  assert.doesNotMatch(render({}), /no successful enrichment returned/);
});
