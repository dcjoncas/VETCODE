const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const page = fs.readFileSync(path.join(__dirname, '../ui/pages/profile-preview.html'), 'utf8');
const flow = fs.readFileSync(path.join(__dirname, '../ui/pages/components/processFlow.html'), 'utf8');
const scripts = [...page.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map((match) => match[1]);
scripts.forEach((script) => new vm.Script(script));
const source = scripts.find((script) => script.includes('function profileNextAction'));
const flowSource = flow.slice(flow.indexOf('  function renderWorkflowStatus()'), flow.indexOf('\n  renderWorkflowStatus();'));
const candidate = { profile: { name: 'Alex Morgan' } };
const completion = { complete: false, hasRegularProfile: true, hasPersonality: false, hasCulture: false };
const stage = (status = '') => ({ signals: { onboarding: { status }, completion } });

function profile({ domain = 'dev', profileId = 'sample-123' } = {}) {
  const nodes = new Map();
  const events = [];
  const painted = [];
  const removed = [];
  const values = new Map([['domain', domain], ['candidateDomain', domain], ['candidateId', profileId]]);
  function element(id) {
    if (!nodes.has(id)) nodes.set(id, { textContent: '', innerHTML: '', href: '', style: {}, hidden: false, setAttribute() {}, removeAttribute(key) { this[key] = ''; } });
    return nodes.get(id);
  }
  const context = vm.createContext({
    URL, URLSearchParams, Event,
    document: { getElementById: element, addEventListener() {}, querySelectorAll: () => [] },
    sessionStorage: { getItem: (key) => values.get(key) || null },
    window: { dispatchEvent: (event) => events.push(event.type) },
  });
  vm.runInContext(source, context);
  Object.assign(context, {
    currentFlowStep: 'profile', activeFlowOrder: ['talent', 'jd', 'find-out', 'interest', 'profile', 'candidate-chat'],
    flowOrder: ['talent', 'jd', 'find-out', 'interest', 'profile', 'candidate-chat', 'shortlist'],
    flowLabels: { profile: 'Profile Build' }, flowDetails: {}, workflowPageContext: null,
    waitingForCandidateDecision: false, nextFlowStep: 'candidate-chat', workflowPositionDomain: domain,
    paintProcessStep: (key, status) => painted.push([key, status]),
  });
  vm.runInContext(flowSource, context);
  return {
    element, events, painted, removed,
    next: (data, savedStage = stage()) => JSON.parse(JSON.stringify(context.profileNextAction(data, savedStage, { domain, profileId }))),
    render: (data, savedStage = stage()) => {
      context.renderProfileNextStep(data, savedStage);
      context.document.querySelectorAll = () => [{ classList: { remove: (value) => removed.push(value) } }];
      context.renderWorkflowStatus();
      return JSON.parse(JSON.stringify(context.window.DevReadyProfileGuidance));
    },
  };
}

test('no candidate means select a candidate, never time or vetting', () => {
  const result = profile({ profileId: '' }).next({});
  assert.equal(result.href, 'find-candidate.html?domain=dev');
  assert.equal(result.label, 'Select a candidate');
});

test('TEMP candidates confirm interest before vetting, for each workspace', () => {
  for (const domain of ['dev', 'law', 'engineer', 'dental']) {
    const harness = profile({ domain });
    const temp = { profile: { description: 'Temporary external profile from PDL' }, externalProfile: { interestWorkflow: { status: 'contacted' } } };
    assert.equal(harness.next(temp).href, `determine-interest.html?domain=${domain}&profileId=sample-123`);
    temp.externalProfile.interestWorkflow.status = 'interested';
    assert.equal(harness.next(temp).href, `candidate-chat.html?domain=${domain}&profileId=sample-123`);
  }
});

test('unfinished onboarding points to paperwork, never time', () => {
  for (const status of ['hire_started', 'pending', 'in_progress', 'rejected', 'unknown']) {
    const result = profile({ domain: 'law' }).next(candidate, stage(status));
    assert.equal(result.href, 'onboarding-admin.html?domain=law&profileId=sample-123');
    assert.match(result.detail, /Weekly time links appear in Time after completion/);
  }
});

test('time is next only for server-defined completed onboarding statuses', () => {
  for (const status of ['paperwork_submitted', 'completed', 'complete', 'onboarded', 'active', ' ACTIVE ']) {
    const result = profile().next(candidate, stage(status));
    assert.equal(result.href, 'time-admin.html?domain=dev&profileId=sample-123');
    assert.equal(result.label, 'Next: Time and approvals');
  }
  assert.equal(profile().next(candidate).href, 'candidate-chat.html?domain=dev&profileId=sample-123');
});

test('profile and shared workflow guide agree when stage data arrives and when candidate changes', () => {
  const harness = profile({ domain: 'dental' });
  for (const savedStage of [stage(), stage('hire_started'), stage('paperwork_submitted'), stage()]) {
    const result = harness.render(candidate, savedStage);
    for (const id of ['profileNextStep', 'processNextStepLink']) {
      assert.equal(harness.element(id).href, result.href);
      assert.equal(harness.element(id).textContent, `${result.label} →`);
    }
    assert.equal(harness.element('processStatusDetail').textContent, harness.element('profileStepHint').textContent);
    assert.equal(harness.element('processLocationLabel').textContent, 'You are here: Profile Build');
  }
  assert.equal(harness.events.length, 4);
  assert.ok(harness.events.every((name) => name === 'profile-guidance-updated'));
  assert.ok(harness.removed.length >= 4);
});

test('loading and failed stage reads never guess an advance to Candidate Chat or Time', () => {
  const harness = profile();
  assert.equal(harness.next(candidate, null).href, '');
  assert.equal(harness.next(candidate, {}).label, 'Retry workflow status');
  assert.equal(harness.next(candidate, { unavailable: true }).href, 'profile-preview.html?domain=dev&profileId=sample-123');
  harness.render(candidate, null);
  assert.equal(harness.element('profileNextStep').hidden, true);
  assert.equal(harness.element('processNextStepLink').hidden, true);
});

test('completion guidance agrees with the existing missing-section panel', () => {
  const harness = profile();
  const saved = { signals: { onboarding: {}, completion: {
    complete: false, hasRegularProfile: false, hasPersonality: true, hasCulture: true,
  } } };
  assert.equal(harness.next(candidate, saved).href, 'profile-preview-edit.html?domain=dev&profileId=sample-123');
  saved.signals.completion.hasCulture = false;
  assert.match(harness.next(candidate, saved).href, /^candidate-chat.html/);
  saved.signals.completion = { complete: true, hasRegularProfile: true, hasPersonality: true, hasCulture: true };
  assert.match(harness.next(candidate, saved).href, /^client-comm.html/);
});

test('compensation is collapsed by default and time-link creation is not on the profile', () => {
  assert.match(page, /<details class="profile-comp-panel">\s*<summary>Internal Compensation/);
  assert.doesNotMatch(page, /profileTimePanel|Create time link|createProfileTimeLink/);
});

// Browser-only visual fixtures use actual profile markup and synthetic contacts.
if (process.env.PROFILE_GUIDANCE_PREVIEW_DIR) {
  const target = process.env.PROFILE_GUIDANCE_PREVIEW_DIR;
  fs.mkdirSync(target, { recursive: true });
  const styles = page.match(/<style>([\s\S]*?)<\/style>/)[1];
  const identityStart = page.indexOf('<div class="profile-title-row">');
  const identityEnd = page.lastIndexOf('<div', page.indexOf('id="profileSubHeader"', identityStart));
  const identity = page.slice(identityStart, identityEnd).replace('No Candidate Loaded', 'Alex Morgan');
  const actions = page.match(/<aside class="profile-action-panel"[\s\S]*?<\/aside>/)[0];
  const compensation = page.match(/<details class="profile-comp-panel">[\s\S]*?<\/details>/)[0];
  for (const domain of ['dev', 'law', 'engineer', 'dental']) {
    const theme = fs.readFileSync(path.join(__dirname, `../ui/assets/${domain}Styles.css`), 'utf8');
    const result = profile({ domain }).next(candidate, stage('hire_started'));
    const actionMarkup = actions.replace(/(<p id="profileStepHint"[^>]*>)[\s\S]*?<\/p>/, `$1${result.detail}</p>`)
      .replace(/<a id="profileNextStep"[^>]*>[\s\S]*?<\/a>/, `<a id="profileNextStep" class="profile-next-step" href="${result.href}">${result.label} →</a>`);
    const html = `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>${theme}\n${styles}</style></head><body><main class="main"><p>Layout check — synthetic candidate, onboarding in progress</p><section class="card"><div class="profile-overview"><div class="imageBox">AM</div><div class="profile-identity">${identity}<p>Software engineer</p></div>${actionMarkup}</div><div class="profile-admin-grid">${compensation}</div></section></main></body></html>`;
    fs.writeFileSync(path.join(target, `${domain}.html`), html);
  }
}
