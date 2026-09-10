const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const pagePath = path.join(__dirname, '../ui/pages/time-admin.html');
const page = fs.readFileSync(pagePath, 'utf8');
const scripts = [...page.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map((match) => match[1]);
scripts.forEach((script) => new vm.Script(script, { filename: pagePath }));
const source = scripts.find((script) => script.includes('function loadOnboardedPeople'));

function timePage({ domain = 'dev', query = '', api = async () => ({ people: [] }) } = {}) {
  const nodes = new Map();
  const values = new Map([
    ['domain', domain], ['candidateId', 'selected-123'], ['candidateName', 'Selected Candidate'],
  ]);
  const calls = [];
  const copied = [];
  const location = new URL(`https://vetcode.example.test/ui/pages/time-admin.html?domain=${domain}${query}`);
  function element(id) {
    if (!nodes.has(id)) nodes.set(id, { innerHTML: '', textContent: '', value: '', hidden: false, href: '', addEventListener() {} });
    return nodes.get(id);
  }
  const context = vm.createContext({
    URL, URLSearchParams, console,
    document: { getElementById: element, addEventListener() {} },
    sessionStorage: { getItem: (key) => values.get(key) || null, setItem: (key, value) => values.set(key, value) },
    window: { location, history: { replaceState: (_state, _title, url) => { location.href = url; } } },
    navigator: { clipboard: { writeText: async (text) => copied.push(text) } },
    api: async (url, options) => { calls.push({ url, options }); return api(url, options); },
    setTimeout() {},
  });
  vm.runInContext(source, context);
  context.initializeTimeContext();
  return { context, element, values, location, calls, copied };
}

function person(status, name = status) {
  return {
    status, candidate_name: name, profile_id: `profile-${name}`, domain: 'dev',
    email: `${name}@example.test`, onboarding_link: `/ui/pages/onboarding.html?token=${name}`,
    time_entry_link: `/ui/pages/time-entry.html?token=${name}`,
  };
}

test('mixed API records expose time actions only for completed onboarding', async () => {
  const harness = timePage({ domain: 'dental', api: async () => ({ people: [
    person('hire_started', 'PendingWorker'), person('paperwork_submitted', 'ReadyWorker'),
    person('completed', 'CompletedWorker'), person('', 'UnknownWorker'),
  ] }) });
  await harness.context.loadOnboardedPeople();
  const rendered = harness.element('onboardedPeopleList').innerHTML;
  assert.match(rendered, /ReadyWorker/);
  assert.match(rendered, /CompletedWorker/);
  assert.doesNotMatch(rendered, /PendingWorker|UnknownWorker/);
  assert.equal((rendered.match(/>Open time link</g) || []).length, 2);
  assert.equal(harness.element('pendingOnboardingNotice').hidden, false);
  assert.match(harness.element('pendingOnboardingCount').textContent, /^2 people are awaiting/);
  assert.equal(harness.element('pendingOnboardingLink').href, 'onboarding-admin.html?domain=dental');
  assert.match(harness.element('onboardedStatus').textContent, /2 onboarded.*Dental/);
  assert.equal(harness.calls[0].url, '/api/onboarding/admin?domain=dental');
  assert.equal(harness.calls[0].options, undefined);
});

test('time-link helper rejects pending and unknown states even with a valid link', () => {
  const { context } = timePage();
  for (const status of ['hire_started', 'pending', 'rejected', '', 'in_progress']) {
    assert.equal(context.timeEntryLinkFor(person(status)), '');
  }
  for (const status of ['paperwork_submitted', 'completed', 'complete', 'onboarded', 'active', ' ACTIVE ']) {
    assert.match(context.timeEntryLinkFor(person(status, 'worker')), /time-entry\.html\?token=worker$/);
  }
  assert.equal(context.timeEntryLinkFor(null), '');
  assert.equal(context.timeEntryLinkFor({ status: 'completed' }), '');
});

test('pending-only and empty batches show no time actions and update the next step', async () => {
  let people = [person('hire_started', 'PendingWorker')];
  const harness = timePage({ api: async () => ({ people }) });
  await harness.context.loadOnboardedPeople();
  assert.equal(harness.element('onboardedPeopleList').innerHTML, '');
  assert.match(harness.element('pendingOnboardingCount').textContent, /^1 person is awaiting/);
  assert.match(harness.element('onboardedStatus').textContent, /^No onboarded people/);
  people = [];
  await harness.context.loadOnboardedPeople();
  assert.equal(harness.element('pendingOnboardingNotice').hidden, true);
  assert.equal(harness.element('onboardedPeopleList').innerHTML, '');
});

test('completed worker without a link gets a clear onboarding action instead of blank links', async () => {
  const ready = person('completed', 'NoLinkWorker');
  delete ready.time_entry_link;
  delete ready.onboarding_link;
  const harness = timePage({ domain: 'law', api: async () => ({ people: [ready] }) });
  await harness.context.loadOnboardedPeople();
  const rendered = harness.element('onboardedPeopleList').innerHTML;
  assert.match(rendered, /Time link unavailable/);
  assert.match(rendered, /href="onboarding-admin\.html\?domain=law"/);
  assert.doesNotMatch(rendered, /Copy time link|Email time link|Open time link|href=""/);
});

test('copy and email target the filtered worker, not a pending row from the original result', async () => {
  const harness = timePage({ api: async () => ({ people: [
    person('hire_started', 'PendingWorker'), person('onboarded', 'ReadyWorker'),
  ] }) });
  await harness.context.loadOnboardedPeople();
  await harness.context.copyPersonTimeLink(0);
  assert.match(harness.copied[0], /token=ReadyWorker$/);
  harness.context.emailPersonTimeLink(0);
  assert.match(harness.location.href, /^mailto:ReadyWorker%40example.test/);
  assert.doesNotMatch(harness.location.href, /PendingWorker/);
  assert.equal(harness.calls.length, 1);
});

test('domain and week context survive initialization and existing time reporting still loads', async () => {
  const harness = timePage({ domain: 'dental', query: '&week_start=2026-09-07', api: async () => ({
    groups: [], total_hours: 11, staff_count: 1, processed_hours: 5,
    entries: [{ status: 'approved_for_payment', hours: 4 }, { status: 'needs_review', hours: 2 }],
  }) });
  assert.equal(harness.element('domainSelect').value, 'dental');
  assert.equal(harness.element('weekFilter').value, '2026-09-07');
  assert.equal(harness.element('openOnboardingAdmin').href, 'onboarding-admin.html?domain=dental');
  assert.equal(harness.element('openInvoices').href, 'invoices.html?domain=dental');
  assert.equal(harness.element('openAccounting').href, 'accounting.html?domain=dental');
  assert.equal(harness.values.get('candidateId'), 'selected-123');
  assert.equal(harness.values.get('candidateName'), 'Selected Candidate');
  await harness.context.loadTime();
  assert.equal(harness.calls[0].url, '/api/time-entry/admin?domain=dental&status=all&week_start=2026-09-07');
  assert.equal(harness.element('totalHours').textContent, 11);
  assert.equal(harness.element('approvedHours').textContent, 4);
  assert.equal(harness.element('openHours').textContent, 2);
  assert.equal(harness.element('domainName').textContent, 'Dental');
});

test('late response from another domain cannot show its workers after a switch', async () => {
  const responses = [];
  const harness = timePage({ api: () => new Promise((resolve) => responses.push(resolve)) });
  const devRequest = harness.context.loadOnboardedPeople();
  harness.element('domainSelect').value = 'law';
  const lawRequest = harness.context.loadOnboardedPeople();
  responses[1]({ people: [person('completed', 'LawWorker')] });
  await lawRequest;
  responses[0]({ people: [person('completed', 'OldDevWorker')] });
  await devRequest;
  assert.match(harness.element('onboardedPeopleList').innerHTML, /LawWorker/);
  assert.doesNotMatch(harness.element('onboardedPeopleList').innerHTML, /OldDevWorker/);
});

test('failed reload clears earlier workers and their time actions', async () => {
  let fail = false;
  const harness = timePage({ api: async () => {
    if (fail) throw new Error('Could not refresh onboarding');
    return { people: [person('completed', 'EarlierWorker')] };
  } });
  await harness.context.loadOnboardedPeople();
  fail = true;
  await harness.context.loadOnboardedPeople();
  assert.equal(harness.element('onboardedPeopleList').innerHTML, '');
  assert.match(harness.element('onboardedStatus').textContent, /Could not refresh onboarding/);
  await harness.context.copyPersonTimeLink(0);
  assert.equal(harness.copied.length, 0);
});
