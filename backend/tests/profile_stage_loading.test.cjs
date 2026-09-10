const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const stagePath = path.join(__dirname, '../ui/pages/JS/profileStages.js');
const stageSource = fs.readFileSync(stagePath, 'utf8');
const flowPage = fs.readFileSync(path.join(__dirname, '../ui/pages/components/processFlow.html'), 'utf8');
const flowPaintSource = flowPage.slice(flowPage.indexOf('  function paintProcessStep('), flowPage.indexOf('\n  document.querySelectorAll("#processFlow [data-flow-step]")'));
const flowRenderSource = flowPage.slice(flowPage.indexOf('  function renderWorkflowStatus('), flowPage.indexOf('\n  renderWorkflowStatus();'));

function stageHarness({ api, fetch } = {}) {
  const target = { innerHTML: '' };
  const context = vm.createContext({
    window: { api }, fetch,
    sessionStorage: { getItem: () => 'law' },
    console: { warn() {} },
    document: {
      getElementById: (id) => id === 'stage' ? target : id === 'devready-profile-stage-style' ? {} : null,
    },
  });
  vm.runInContext(stageSource, context);
  return { target, load: (id, options) => context.window.DevReadyProfileStages.load('stage', id, options) };
}

function stages(id, label) {
  return { ok: true, profile_id: id, stages: [{ key: 'onboarded', label, status: 'done', detail: label }] };
}

test('stage loader renders and forwards successful lifecycle data with domain context', async () => {
  const data = stages('p-1', 'Onboarded');
  const requests = [];
  const harness = stageHarness({ api: async (url) => { requests.push(url); return data; } });
  let loaded;
  await harness.load('p-1', { domain: 'dental', onLoaded: (value) => { loaded = value; } });
  assert.equal(requests[0], '/api/profile/p-1/process-stage?domain=dental');
  assert.equal(loaded, data);
  assert.match(harness.target.innerHTML, /profile-stage-item done/);
  assert.match(harness.target.innerHTML, /Onboarded/);
});

test('HTTP error in fetch fallback calls onError without treating an error body as loaded lifecycle data', async () => {
  let jsonRead = false;
  let loaded = false;
  let failure;
  const harness = stageHarness({ fetch: async () => ({
    ok: false, status: 503,
    json: async () => { jsonRead = true; return { detail: 'Temporarily unavailable' }; },
  }) });
  await harness.load('p-1', { onLoaded: () => { loaded = true; }, onError: (error) => { failure = error; } });
  assert.equal(loaded, false);
  assert.equal(jsonRead, false);
  assert.match(failure.message, /503/);
  assert.match(harness.target.innerHTML, /Stage unavailable/);
});

test('API errors reach the optional failure callback and existing callers can omit callbacks', async () => {
  const failure = new Error('Connection failed');
  const harness = stageHarness({ api: async () => { throw failure; } });
  let reported;
  await harness.load('p-1', { onError: (error) => { reported = error; } });
  assert.equal(reported, failure);
  await assert.doesNotReject(harness.load('p-1'));
});

test('late successful or failed stage loads cannot replace a newer profile or its guidance', async () => {
  const pending = [];
  const harness = stageHarness({ api: () => new Promise((resolve, reject) => pending.push({ resolve, reject })) });
  const callbacks = [];
  const first = harness.load('old', { onLoaded: () => callbacks.push('old'), onError: () => callbacks.push('old-error') });
  const second = harness.load('new', { onLoaded: () => callbacks.push('new') });
  pending[1].resolve(stages('new', 'Current worker'));
  await second;
  pending[0].resolve(stages('old', 'Old worker'));
  await first;
  assert.deepEqual(callbacks, ['new']);
  assert.match(harness.target.innerHTML, /Current worker/);
  assert.doesNotMatch(harness.target.innerHTML, /Old worker/);

  const third = harness.load('failing-old', { onError: () => callbacks.push('old-error') });
  const fourth = harness.load('latest', { onLoaded: () => callbacks.push('latest') });
  pending[3].resolve(stages('latest', 'Latest worker'));
  await fourth;
  pending[2].reject(new Error('Late error'));
  await third;
  assert.deepEqual(callbacks, ['new', 'latest']);
  assert.match(harness.target.innerHTML, /Latest worker/);
  assert.doesNotMatch(harness.target.innerHTML, /Stage unavailable/);
});

function flowHarness({ usesInterest = true } = {}) {
  const flowOrder = ['talent', 'job-descriptions', 'find-in', 'interest', 'profile', 'candidate-chat', 'shortlist', 'candidate-review', 'client-interview', 'status'];
  const elements = new Map();
  const steps = new Map(flowOrder.map((step) => [step, { classes: new Set(), attributes: {},
    setAttribute(key, value) { this.attributes[key] = value; },
  }]));
  for (const node of steps.values()) node.classList = {
    add: (...classes) => classes.forEach((name) => node.classes.add(name)),
    remove: (...classes) => classes.forEach((name) => node.classes.delete(name)),
  };
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, { textContent: '', href: '', hidden: false, style: {}, attributes: {},
      setAttribute(key, value) { this.attributes[key] = value; },
      removeAttribute(key) { delete this.attributes[key]; if (key === 'href') this.href = ''; },
    });
    return elements.get(id);
  };
  const context = vm.createContext({
    URL, window: { DevReadyProfileGuidance: null, location: { href: 'https://vetcode.example.test/ui/pages/profile-preview.html' } },
    currentFlowStep: 'profile', flowOrder,
    activeFlowOrder: usesInterest ? flowOrder : flowOrder.filter((step) => step !== 'interest'),
    waitingForCandidateDecision: false, nextFlowStep: 'candidate-chat', workflowPageContext: null,
    workflowPositionDomain: 'dev', flowLabels: { profile: '4 - Build and review profile' },
    flowDetails: { profile: 'Review profile' },
    document: {
      getElementById: element,
      querySelectorAll(selector) {
        const selected = selector.match(/data-flow-step="([^"]+)"/);
        if (selected) return steps.has(selected[1]) ? [steps.get(selected[1])] : [];
        const className = selector.endsWith('.next') ? 'next' : selector.endsWith('.complete') ? 'complete' : '';
        return [...steps.values()].filter((node) => !className || node.classes.has(className));
      },
    },
  });
  vm.runInContext(`${flowPaintSource}\n${flowRenderSource}`, context);
  steps.get('profile').classes.add('on');
  return {
    element, steps,
    render(guidance) { context.window.DevReadyProfileGuidance = guidance; context.renderWorkflowStatus(); },
  };
}

test('returning to Determine Interest removes false completion checkmarks and adjusts progress', () => {
  const harness = flowHarness();
  harness.steps.get('interest').classes.add('complete');
  harness.steps.get('candidate-chat').classes.add('next');
  harness.render({ step: 'interest', href: 'determine-interest.html?domain=dev&profileId=12', label: 'Next: Determine Interest', detail: 'Confirm interest first.' });
  assert.equal(harness.steps.get('interest').classes.has('complete'), false);
  assert.equal(harness.steps.get('interest').classes.has('next'), true);
  assert.equal(harness.steps.get('candidate-chat').classes.has('next'), false);
  assert.equal(harness.steps.get('profile').classes.has('on'), true);
  assert.equal(harness.element('processProgressCopy').textContent, '3 of 10 steps complete');
  assert.equal(harness.element('processProgressTrack').attributes['aria-valuenow'], '3');
  assert.equal(harness.element('processProgressFill').style.width, '30%');
  assert.equal(harness.element('processNextStepLink').href, 'determine-interest.html?domain=dev&profileId=12');
});

test('interest guidance stays accurate if shared flow mounted before TEMP profile data arrived', () => {
  const harness = flowHarness({ usesInterest: false });
  harness.render({ step: 'interest', href: 'determine-interest.html', label: 'Next: Determine Interest', detail: 'Confirm interest first.' });
  assert.equal(harness.element('processProgressCopy').textContent, '3 of 10 steps complete');
  assert.equal(harness.steps.get('interest').classes.has('complete'), false);
  assert.equal(harness.steps.get('interest').classes.has('next'), true);
});

test('guidance updates restore appropriate completion markers and hide unactionable loading Next', () => {
  const harness = flowHarness();
  harness.render({ step: 'interest', href: 'determine-interest.html', label: 'Next: Determine Interest', detail: 'Confirm interest.' });
  harness.render({ step: 'candidate-chat', href: 'candidate-chat.html', label: 'Next: Candidate Chat', detail: 'Complete responses.' });
  assert.equal(harness.steps.get('interest').classes.has('complete'), true);
  assert.equal(harness.steps.get('interest').classes.has('next'), false);
  assert.equal(harness.steps.get('candidate-chat').classes.has('next'), true);
  assert.equal(harness.element('processProgressCopy').textContent, '4 of 10 steps complete');
  harness.render({ href: '', label: 'Checking candidate status', detail: 'Loading current stage.' });
  assert.equal(harness.element('processNextStepLink').hidden, true);
  assert.equal(harness.element('processNextStepLink').href, '');
  assert.equal(harness.steps.get('candidate-chat').classes.has('next'), false);
});
