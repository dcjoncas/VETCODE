const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../ui/pages/components/processFlow.html'), 'utf8');
const script = [...source.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map((match) => match[1]).join('\n');
const markup = source.replace(/<(style|script)\b[^>]*>[\s\S]*?<\/\1>/g, '').replace(/<!--[\s\S]*?-->/g, '');
const styles = [...source.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((match) => match[1]).join('\n');

// Parse only this trusted static component. No dependency, browser network, or live data.
function parseComponent(baseUrl) {
  const nodes = [];
  const ids = new Map();
  function createNode(tag, attributes = {}, parent = null) {
    const classes = new Set((attributes.class || '').split(/\s+/).filter(Boolean));
    const node = {
      tag, attributes, parent, children: [], textContent: '', innerText: '', style: {}, dataset: {},
      classList: {
        add: (...values) => values.forEach((value) => classes.add(value)),
        remove: (...values) => values.forEach((value) => classes.delete(value)),
        contains: (value) => classes.has(value),
        toggle(value, force) {
          const on = force === undefined ? !classes.has(value) : force;
          if (on) classes.add(value); else classes.delete(value);
          return on;
        },
      },
      setAttribute(key, value) { attributes[key] = String(value); },
      removeAttribute(key) { delete attributes[key]; },
      getAttribute(key) { return attributes[key] ?? null; },
      addEventListener() {},
    };
    Object.defineProperties(node, {
      id: { get: () => attributes.id || '' },
      href: { get: () => attributes.href ? new URL(attributes.href, baseUrl).href : '', set: (value) => { attributes.href = value; } },
      hidden: { get: () => Object.hasOwn(attributes, 'hidden'), set: (value) => { if (value) attributes.hidden = ''; else delete attributes.hidden; } },
      open: { get: () => Object.hasOwn(attributes, 'open'), set: (value) => { if (value) attributes.open = ''; else delete attributes.open; } },
    });
    if (node.id) {
      assert.equal(ids.has(node.id), false, `Duplicate component ID: ${node.id}`);
      ids.set(node.id, node);
    }
    nodes.push(node);
    parent?.children.push(node);
    return node;
  }
  const root = createNode('div', { id: 'processFlow' });
  const stack = [root];
  const voidTags = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr']);
  for (const match of markup.matchAll(/<\/?[a-z][^>]*>|[^<]+/gi)) {
    const token = match[0];
    if (!token.startsWith('<')) { stack.at(-1).textContent += token; continue; }
    const tag = token.match(/^<\/?([a-z][\w-]*)/i)[1].toLowerCase();
    if (token.startsWith('</')) {
      assert.equal(stack.at(-1).tag, tag, `Unbalanced closing tag: ${token}`);
      stack.pop();
      continue;
    }
    const attributes = {};
    for (const attribute of token.slice(tag.length + 1, -1).matchAll(/([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) {
      attributes[attribute[1]] = attribute[2] ?? attribute[3] ?? attribute[4] ?? '';
    }
    const node = createNode(tag, attributes, stack.at(-1));
    if (!voidTags.has(tag) && !token.endsWith('/>')) stack.push(node);
  }
  assert.equal(stack.length, 1, 'Component markup must remain balanced');
  return { nodes, ids };
}

function harness({ page = 'mine-candidate-external.html', domain = 'dev', query = '', stored = {}, guidance = null, navigation = 'navigate' } = {}) {
  const url = new URL(`https://fixture.example/ui/pages/${page}?domain=${domain}${query ? `&${query}` : ''}`);
  const { nodes, ids } = parseComponent(url);
  const storage = new Map(Object.entries({ domain, ...stored }));
  const writes = [];
  const events = new Map();
  const history = { backCalls: 0, forwardCalls: 0, back() { this.backCalls++; }, forward() { this.forwardCalls++; } };
  const scrolls = [];
  const window = {
    location: url, history, scrollX: 5, scrollY: 820, DevReadyProfileGuidance: guidance,
    addEventListener(name, callback) { if (!events.has(name)) events.set(name, []); events.get(name).push(callback); },
    scrollTo: (x, y) => scrolls.push([x, y]),
  };
  const selectAll = (selector) => {
    if (selector === '#processMapOverlay .map-node') return nodes.filter((node) => node.classList.contains('map-node'));
    if (selector.startsWith('#processFlow [data-flow-step')) {
      const step = selector.match(/data-flow-step="([^"]+)"/)?.[1];
      const className = selector.match(/\]\.(\w+)$/)?.[1];
      return nodes.filter((node) => node.attributes['data-flow-step'] && (!step || node.attributes['data-flow-step'] === step) && (!className || node.classList.contains(className)));
    }
    throw new Error(`Unexpected selector: ${selector}`);
  };
  const context = vm.createContext({
    window, URL, URLSearchParams,
    document: { getElementById: (id) => ids.get(id) || null, querySelectorAll: selectAll, querySelector: (selector) => selectAll(selector)[0] || null,
      title: 'Synthetic workflow', documentElement: { dataset: { domain } } },
    sessionStorage: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => { writes.push(key); storage.set(key, String(value)); },
      removeItem: (key) => { writes.push(key); storage.delete(key); },
      key: (index) => [...storage.keys()][index] ?? null,
      get length() { return storage.size; },
    },
    performance: { getEntriesByType: () => [{ type: navigation }] },
    requestAnimationFrame: (callback) => callback(),
    syncDomainSelect: (value) => { ids.get('domainSelect').value = value; },
    console: { log() {}, warn() {}, error() {} },
    fetch: () => { throw new Error('Shared workflow mount must not make network requests'); },
  });
  vm.runInContext(script, context, { filename: 'processFlow.html.inline.js' });
  return { nodes, ids, storage, writes, window, history, scrolls,
    get: (id) => ids.get(id),
    event: (name) => events.get(name)?.forEach((callback) => callback()),
    step: (name) => nodes.find((node) => node.attributes['data-flow-step'] === name),
  };
}

function collapsedAncestor(node) {
  for (let parent = node.parent; parent; parent = parent.parent) if (parent.tag === 'details' && !parent.open) return parent;
  return null;
}

test('compact default keeps current, next, native history and domain outside the full-workflow disclosure', () => {
  const h = harness();
  const details = h.get('processWorkflowDetails');
  assert.equal(details.tag, 'details');
  assert.equal(details.open, false);
  assert.equal(details.children[0].tag, 'summary');
  assert.match(details.children[0].children[0].textContent, /Show full workflow/);
  for (const id of ['processStatusTitle', 'processNextStepLink', 'processNextStepNote', 'domainSelect', 'processStartOverButton', 'candidateSelected', 'jobSelected', 'clientSelected']) {
    assert.ok(h.get(id), `${id} retained`);
    assert.equal(collapsedAncestor(h.get(id)), null, `${id} always outside collapsed workflow`);
  }
  const historyButtons = h.nodes.filter((node) => node.classList.contains('workflow-history-button'));
  assert.equal(historyButtons.length, 2);
  assert.deepEqual(historyButtons.map((node) => node.attributes.onclick), ['processGoBack()', 'processGoForward()']);
  assert.ok(historyButtons.every((node) => !collapsedAncestor(node)));
  assert.equal(h.get('domainSelect').attributes.onchange, 'domainSwap()');
  assert.equal(h.get('domainSelect').attributes['aria-label'], 'Workspace domain');
  assert.deepEqual(h.get('domainSelect').children.map((node) => node.attributes.value), ['dev', 'engineer', 'law', 'dental']);
  assert.deepEqual(h.writes, [], 'Mounting compact chrome must not alter recruiter selection or search data');
});

test('all eleven step links, status details, progress, legend, map and hints remain accessible on expansion', () => {
  const h = harness();
  const details = h.get('processWorkflowDetails');
  const secondary = h.nodes.filter((node) => node.attributes['data-flow-step'] || node.classList.contains('workflow-status-legend') || node.classList.contains('flow-help') || node.classList.contains('process-map-button'));
  secondary.push(...['processProgressCopy', 'processProgressTrack', 'processProgressFill', 'processStatusDetail', 'hintToggle'].map(h.get));
  assert.equal(h.nodes.filter((node) => node.attributes['data-flow-step']).length, 11);
  assert.ok(secondary.every((node) => collapsedAncestor(node) === details));
  const before = [h.get('processStatusTitle').textContent, h.get('processNextStepLink').href];
  details.open = true; // Native details state; no JS replacement/toggle handler required.
  assert.ok(secondary.every((node) => !collapsedAncestor(node)));
  assert.deepEqual([h.get('processStatusTitle').textContent, h.get('processNextStepLink').href], before);
  assert.deepEqual(h.writes, []);
  assert.match(styles, /\.workflow-details\s*>\s*summary:focus-visible/);
  assert.match(styles, /\.workflow-details\[open\]\s+\.workflow-details-open/);
  assert.match(styles, /@media\s*\(max-width:\s*900px\)/);
});

for (const domain of ['dev', 'law', 'engineer', 'dental']) {
  test(`Find Out always presents a text-labelled current and next action in ${domain}`, () => {
    const h = harness({ domain });
    assert.match(h.get('processStatusTitle').textContent, /^Current: 3 - Find candidates \(external option\)$/);
    assert.match(h.get('processNextStepLink').textContent, /Next: 3A - Determine candidate interest/);
    assert.equal(new URL(h.get('processNextStepLink').href).pathname, '/ui/pages/determine-interest.html');
    assert.equal(new URL(h.get('processNextStepLink').href).searchParams.get('domain'), domain);
    assert.equal(h.get('domainSelect').value, domain);
    assert.equal(h.get('processNextStepLink').hidden, false);
    assert.equal(h.get('processNextStepNote').hidden, true);
    assert.equal(h.step('find-out').attributes['aria-current'], 'step');
    assert.equal(h.step('interest').classList.contains('next'), true);
  });
}

for (const [page, query, step, nextPage, label] of [
  ['find-candidate.html', '', 'talent', 'job-descriptions.html', '1 - Talent'],
  ['job-descriptions.html', '', 'job-descriptions', 'match-role.html', '2 - Job descriptions'],
  ['match-role.html', '', 'find-in', 'profile-preview.html', 'internal'],
  ['saved-searches.html', '', 'find-out', 'determine-interest.html', 'Saved Searches'],
  ['temp-profiles.html', '', 'find-out', 'determine-interest.html', 'TEMP Profiles'],
  ['determine-interest.html', '', 'interest', 'profile-preview.html', 'Determine Interest'],
  ['profile-preview.html', '', 'profile', 'candidate-chat.html', 'Build and review profile'],
  ['candidate-chat.html', '', 'candidate-chat', 'client-comm.html', 'Candidate AI chat'],
  ['client-comm.html', '', 'shortlist', 'schedule-interview.html', 'Shortlist'],
  ['schedule-interview.html', 'interview=ready', 'candidate-review', 'schedule-interview.html', 'DevReady candidate review'],
  ['schedule-interview.html', 'interview=client', 'client-interview', 'status-tracker.html', 'Client interview'],
]) {
  test(`${page} ${query} retains its stage-specific next action`, () => {
    const h = harness({ page, query, domain: 'law' });
    assert.ok(h.get('processStatusTitle').textContent.includes(label));
    assert.equal(h.step(step).attributes['aria-current'], 'step');
    assert.equal(new URL(h.get('processNextStepLink').href).pathname, `/ui/pages/${nextPage}`);
    assert.equal(new URL(h.get('processNextStepLink').href).searchParams.get('domain'), 'law');
    if (step === 'shortlist') assert.equal(new URL(h.get('processNextStepLink').href).searchParams.get('interview'), 'ready');
    if (step === 'candidate-review') assert.equal(new URL(h.get('processNextStepLink').href).searchParams.get('interview'), 'client');
    assert.equal(h.get('processNextStepLink').hidden, false);
    assert.equal(h.get('processNextStepNote').hidden, true);
  });
}

test('pending candidate decision keeps readable next guidance but cannot enable the client-interview link', () => {
  const h = harness({ page: 'status-tracker.html', stored: { 'latestScheduleTracking:dev': JSON.stringify({ interviewType: 'ready', candidateInterest: '' }) } });
  assert.equal(h.get('processNextStepLink').hidden, true);
  assert.equal(h.get('processNextStepLink').getAttribute('href'), null);
  assert.equal(h.get('processNextStepNote').hidden, false);
  assert.equal(h.get('processNextStepNote').textContent, 'Next: Record candidate interest on this page.');
  assert.equal(collapsedAncestor(h.get('processNextStepNote')), null);
  assert.equal(h.step('client-interview').classList.contains('next'), false);
  assert.match(styles, /#processFlow \.workflow-next-link\[hidden\],\s*#processFlow \.workflow-next-note\[hidden\]\s*\{\s*display:\s*none;/);
});

test('final outcome and unknown pages still explain the next action without a stale clickable URL', () => {
  for (const [page, expected] of [['status-tracker.html', 'Record the outcome or follow-up'], ['unrecognized.html', 'Open the full workflow']]) {
    const h = harness({ page });
    assert.equal(h.get('processNextStepLink').hidden, true);
    assert.equal(h.get('processNextStepLink').getAttribute('href'), null);
    assert.equal(h.get('processNextStepNote').hidden, false);
    assert.ok(h.get('processNextStepNote').textContent.includes(expected));
  }
});

test('profile guidance loading, interest and ready updates preserve stage gating in the compact view', () => {
  const h = harness({ page: 'profile-preview.html', guidance: { href: '', label: 'Checking candidate status', detail: 'Loading current stage.' } });
  assert.equal(h.get('processNextStepLink').hidden, true);
  assert.equal(h.get('processNextStepNote').hidden, false);
  assert.equal(h.get('processNextStepNote').textContent, 'Next: Checking candidate status');
  assert.equal(h.get('processNextStepLink').getAttribute('href'), null);
  h.window.DevReadyProfileGuidance = { step: 'interest', href: 'determine-interest.html?domain=dev&profileId=test-1', label: 'Next: Determine Interest', detail: 'Confirm interest first.' };
  h.event('profile-guidance-updated');
  assert.equal(h.get('processNextStepLink').hidden, false);
  assert.equal(h.get('processNextStepNote').hidden, true);
  assert.equal(h.step('interest').classList.contains('next'), true);
  assert.equal(h.step('interest').classList.contains('complete'), false);
  h.window.DevReadyProfileGuidance = { href: '', label: 'Next: Review stage unavailable', detail: 'Retry status before continuing.' };
  h.event('profile-guidance-updated');
  assert.equal(h.get('processNextStepLink').hidden, true);
  assert.equal(h.get('processNextStepLink').getAttribute('href'), null);
  assert.equal(h.get('processNextStepNote').textContent, 'Next: Review stage unavailable');
  assert.equal(h.get('processNextStepNote').hidden, false);
  assert.equal(h.step('interest').classList.contains('next'), false);
});

test('Back and Forward remain native history actions with domain-scoped position restoration', () => {
  const h = harness({ domain: 'dental', stored: { candidateId: 'synthetic-selected', jobId: 'synthetic-job' } });
  h.window.processGoBack();
  h.window.processGoForward();
  assert.equal(h.history.backCalls, 1);
  assert.equal(h.history.forwardCalls, 1);
  assert.equal(h.storage.get('candidateId'), 'synthetic-selected');
  assert.equal(h.storage.get('jobId'), 'synthetic-job');
  assert.equal(h.storage.get('workflowRestoreRequested:dental'), '1');
  const key = 'workflowPosition:dental:/ui/pages/mine-candidate-external.html?domain=dental';
  assert.deepEqual(JSON.parse(h.storage.get(key)), { x: 5, y: 820 });
  h.event('devready-page-content-restored');
  assert.deepEqual(h.scrolls, [[5, 820]]);
  assert.equal(h.storage.has('workflowRestoreRequested:dental'), false);
  assert.equal(h.storage.has('workflowRestoreRequested:dev'), false);
});

test('native browser Back hydration remains effective after shared component injection', () => {
  const key = 'workflowPosition:engineer:/ui/pages/profile-preview.html?domain=engineer';
  const h = harness({ page: 'profile-preview.html', domain: 'engineer', navigation: 'back_forward', stored: { [key]: JSON.stringify({ x: 0, y: 450 }) } });
  assert.deepEqual(h.scrolls, [[0, 450]]);
  assert.equal(h.get('processWorkflowDetails').open, false);
});
