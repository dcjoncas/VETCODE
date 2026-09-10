const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const pagePath = path.join(__dirname, '../ui/pages/onboarding-admin.html');
const page = fs.readFileSync(pagePath, 'utf8');
const scripts = [...page.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map((match) => match[1]);
scripts.forEach((script) => new vm.Script(script, { filename: pagePath }));
const fullSource = scripts.find((script) => script.includes('function renderPacket'));
const source = fullSource.slice(0, fullSource.indexOf('      document.getElementById("onboardingForm").addEventListener'));

function onboardingPage(people = [], domain = 'dev') {
  const nodes = new Map();
  const copied = [];
  const calls = [];
  const values = new Map([['domain', domain]]);
  const location = new URL(`https://vetcode.example.test/ui/pages/onboarding-admin.html?domain=${domain}`);
  function element(id) {
    if (!nodes.has(id)) nodes.set(id, {
      value: '', innerHTML: '', textContent: '', href: '', hidden: false, disabled: false,
      classList: { add() {}, remove() {} },
      removeAttribute(name) { if (name === 'href') this.href = ''; },
    });
    return nodes.get(id);
  }
  const context = vm.createContext({
    URL, URLSearchParams, console,
    document: { getElementById: element },
    window: { location },
    sessionStorage: { getItem: (key) => values.get(key), setItem: (key, value) => values.set(key, value) },
    navigator: { clipboard: { writeText: async (value) => copied.push(value) } },
    api: async (url, options) => { calls.push({ url, options }); return { people }; },
  });
  vm.runInContext(source, context);
  context.setInitialState();
  return { context, element, copied, calls, location };
}

function person(status, name = 'Worker') {
  return { status, candidate_name: name, email: `${name}@example.test`, domain: 'dev', token: name,
    title: 'Engineer', start_day: '2026-09-14',
    onboarding_link: `/ui/pages/onboarding.html?token=${name}`,
    time_entry_link: `/ui/pages/time-entry.html?token=${name}`,
  };
}

test('pending onboarding email keeps paperwork, start details and note but omits weekly time URL', () => {
  const { context } = onboardingPage();
  const pending = person('hire_started');
  const body = context.emailBodyFor(pending, pending.onboarding_link, pending.time_entry_link, 'Bring your laptop.');
  assert.match(body, /Please complete your onboarding paperwork/);
  assert.match(body, /onboarding\.html\?token=Worker/);
  assert.match(body, /Start day: 2026-09-14/);
  assert.match(body, /Bring your laptop/);
  assert.match(body, /after onboarding is complete/);
  assert.doesNotMatch(body, /time-entry\.html/);
});

test('completed statuses allow the weekly link while pending and unknown statuses do not', () => {
  const { context } = onboardingPage();
  for (const status of ['paperwork_submitted', 'completed', 'complete', 'onboarded', 'active', ' ACTIVE ']) {
    const record = person(status);
    assert.match(context.personLinks(record).time, /time-entry\.html\?token=Worker$/);
    assert.match(context.emailBodyFor(record, record.onboarding_link, record.time_entry_link), /time-entry\.html/);
  }
  for (const status of ['hire_started', 'pending', 'in_progress', 'unknown', '', undefined]) {
    const record = person(status);
    assert.equal(context.personLinks(record).time, '');
    assert.doesNotMatch(context.emailBodyFor(record, record.onboarding_link, record.time_entry_link), /time-entry\.html/);
  }
  assert.equal(context.personLinks({ status: 'completed' }).time, '');
});

test('switching from completed to pending packet clears old time link but keeps onboarding available', () => {
  const harness = onboardingPage();
  harness.context.renderPacket({ record: person('completed', 'CompletedWorker') });
  assert.equal(harness.element('timeEntryLinkBox').hidden, false);
  assert.match(harness.element('timeEntryLink').href, /CompletedWorker/);
  harness.context.renderPacket({ record: person('hire_started', 'PendingWorker') });
  assert.equal(harness.element('timeEntryLinkBox').hidden, true);
  assert.equal(harness.element('timeEntryLink').href, '');
  assert.equal(harness.element('timeEntryLink').textContent, '');
  assert.match(harness.element('onboardingLink').href, /onboarding\.html\?token=PendingWorker$/);
  assert.match(harness.element('openOnboardingLink').href, /PendingWorker$/);
  assert.match(harness.element('packetNextStep').textContent, /send the onboarding form/);
  assert.doesNotMatch(harness.element('emailBody').value, /CompletedWorker|time-entry\.html/);

  // Check the gated container actually surrounds the weekly link, leaving the onboarding link visible.
  const wrapper = page.match(/<div id="timeEntryLinkBox"[^>]*>([\s\S]*?)<\/div>/)[1];
  assert.match(wrapper, /id="timeEntryLink"/);
  assert.doesNotMatch(wrapper, /id="onboardingLink"/);
  assert.match(page, /#timeEntryLinkBox\[hidden\]\s*\{\s*display: none;/);
});

test('existing record cards show time controls only for completed workers', async () => {
  const harness = onboardingPage([person('hire_started', 'PendingWorker'), person('paperwork_submitted', 'ReadyWorker')], 'dental');
  await harness.context.loadPeople();
  const cards = [...harness.element('peopleList').innerHTML.matchAll(/<article class="person-card">([\s\S]*?)<\/article>/g)].map((match) => match[1]);
  assert.equal(cards.length, 2);
  assert.match(cards[0], /PendingWorker/);
  assert.match(cards[0], /Copy onboarding|Email packet/);
  assert.match(cards[0], /Next: complete onboarding paperwork/);
  assert.doesNotMatch(cards[0], /time-entry\.html|Copy time link|>Open time</);
  assert.match(cards[1], /ReadyWorker/);
  assert.match(cards[1], /Copy time link/);
  assert.match(cards[1], />Open time</);
  assert.match(cards[1], /time-entry\.html\?token=ReadyWorker/);
  assert.equal(harness.calls[0].url, '/api/onboarding/admin?domain=dental');
  assert.equal(harness.calls[0].options, undefined);
  assert.match(harness.element('currentDomainPill').textContent, /Dental/);
});

test('copy helper blocks pending time links while normal onboarding copy and draft email still work', async () => {
  const harness = onboardingPage([person('hire_started', 'PendingWorker'), person('completed', 'ReadyWorker')]);
  await harness.context.loadPeople();
  await harness.context.copyPersonTime(0);
  assert.deepEqual(harness.copied, []);
  await harness.context.copyPersonOnboarding(0);
  assert.match(harness.copied[0], /onboarding\.html\?token=PendingWorker$/);
  await harness.context.copyPersonTime(1);
  assert.match(harness.copied[1], /time-entry\.html\?token=ReadyWorker$/);
  harness.context.emailPersonOnboarding(0);
  assert.match(harness.location.href, /^mailto:PendingWorker%40example.test/);
  const pendingBody = new URL(harness.location.href).searchParams.get('body');
  assert.match(pendingBody, /onboarding\.html\?token=PendingWorker/);
  assert.doesNotMatch(pendingBody, /time-entry\.html/);
  assert.equal(harness.calls.length, 1);
});

test('new packet email action uses the gated draft and never auto-sends a message', () => {
  const harness = onboardingPage();
  harness.context.renderPacket({ record: person('hire_started', 'NewWorker') });
  assert.match(harness.location.href, /^https:/);
  harness.context.emailOnboardingPacket();
  assert.match(harness.location.href, /^mailto:NewWorker%40example.test/);
  const draft = new URL(harness.location.href).searchParams.get('body');
  assert.match(draft, /onboarding\.html\?token=NewWorker/);
  assert.doesNotMatch(draft, /time-entry\.html/);
  assert.equal(harness.calls.length, 0);
});
