const assert = require('node:assert/strict');
const { test } = require('node:test');
const usage = require('../ui/pages/JS/providerUsage.js');

test('legacy estimates never become confirmed credit usage or balance', () => {
  const display = usage.describe({ totalMatches: 754299581, estimatedCreditsUsed: 5, costLabel: 'record credits' });
  assert.equal(display.used, 'Not reported');
  assert.equal(display.remaining, 'Unavailable');
  assert.match(display.notes.join(' '), /Estimated search usage: 5/);
});

test('provider-reported zero is not missing and account balance is timestamped', () => {
  const display = usage.describe({ providerUsage: { creditsUsed: 0, creditType: 'search', accountRemainingCredits: 0, observedAt: '2026-09-10T12:00:00Z' } });
  assert.equal(display.used, '0 credits');
  assert.equal(display.remaining, '0 — last reported');
  assert.match(display.notes.join(' '), /available overage/);
});

test('saved searches cost zero to reopen but their balance stays historical', () => {
  const display = usage.describe({ providerUsage: { source: 'local_cache', creditsUsed: 0 }, originalProviderUsage: {
    creditsUsed: 5, accountRemainingCredits: 100, balanceStatus: 'historical', observedAt: '2026-09-09T12:00:00Z',
  } });
  assert.equal(display.used, '0 — saved results');
  assert.equal(display.remaining, '100 — saved snapshot');
});

test('pagination sums reported usage, but never sums remaining balances', () => {
  const previous = { providerUsage: { calls: [{ creditsUsed: 5, accountRemainingCredits: 95 }] } };
  const current = { providerUsage: { calls: [{ creditsUsed: 3, accountRemainingCredits: 92 }] } };
  assert.equal(usage.merge(previous, current).creditsUsed, 8);
  assert.equal(usage.merge(previous, current).accountRemainingCredits, 92);
});

test('missing latest header invalidates balance and partial charges stay partial', () => {
  const previous = { providerUsage: { calls: [{ creditsUsed: 5, accountRemainingCredits: 95 }] } };
  const merged = usage.merge(previous, { providerUsage: { calls: [{ creditsUsed: null }] } });
  assert.equal(merged.creditsUsed, null);
  assert.equal(merged.reportedCreditsUsed, 5);
  assert.equal(merged.accountRemainingCredits, null);
  assert.match(usage.describe({ providerUsage: merged }).used, /Partially reported/);
});

test('unknown legacy page charge cannot disappear when a newer page has headers', () => {
  const merged = usage.merge({ queryExecuted: true, estimatedCreditsUsed: 5 }, { providerUsage: { calls: [{ creditsUsed: 2 }] } });
  assert.equal(merged.creditsUsed, null);
  assert.equal(merged.requests, 2);
});
