const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const pages = path.join(__dirname, '../ui/pages');
const page = fs.readFileSync(path.join(pages, 'mine-candidate-external.html'), 'utf8');
const scripts = [...page.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map((match) => match[1]);
scripts.forEach((script) => new vm.Script(script));
const source = scripts.find((script) => script.includes('function setCriteriaGroupIgnored'));
const groups = ['titles', 'skills', 'cities', 'experience', 'licenses', 'arrangements', 'workforce'];
const ids = ['Titles', 'Skills', 'Cities', 'Experience', 'Licenses', 'Arrangement', 'Workforce'];

function fixture(domain = 'dev') {
  let invalidations = 0;
  const nodes = new Map();
  const cards = new Map();
  const buttons = new Map();
  function element() {
    const classes = new Set();
    return {
      children: [], attributes: {}, dataset: {}, hidden: false, disabled: false,
      checked: false, open: true, value: '', textContent: '',
      set innerHTML(value) { this.children = []; this.html = value; },
      get innerHTML() { return this.html || ''; },
      append(...items) { this.children.push(...items); },
      appendChild(item) { this.children.push(item); },
      addEventListener() {},
      setAttribute(key, value) { this.attributes[key] = value; },
      classList: { toggle(name, on) { if (on) classes.add(name); else classes.delete(name); }, contains(name) { return classes.has(name); } },
    };
  }
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
  };
  const inputs = (group) => node(`criteria${ids[groups.indexOf(group)]}Options`).children.flatMap((label) => label.children).filter((child) => child.type === 'checkbox');
  groups.forEach((group, index) => {
    const card = element();
    card.picker = element();
    card.help = [element()];
    card.title = { textContent: group === 'titles' ? 'Current titles' : group };
    card.querySelector = (selector) => selector.startsWith('details') ? card.picker : selector === '.criteria-card-title' ? card.title : null;
    card.querySelectorAll = (selector) => selector === 'details input, details button' ? inputs(group) : card.help;
    cards.set(group, card);
    buttons.set(group, element());
  });
  node('criteriaStrictLocations').checked = true;
  const context = vm.createContext({
    document: {
      addEventListener() {}, createElement: element, getElementById: node,
      querySelector(selector) {
        const group = selector.match(/="([^"]+)"/)[1];
        return selector.includes('data-criteria-card') ? cards.get(group) : buttons.get(group);
      },
      querySelectorAll(selector) {
        if (selector === '#searchCriteriaPanel .criteria-picker') return [...cards.values()].map((card) => card.picker);
        const group = selector.match(/data-criteria-group="([^"]+)"/);
        return group ? inputs(group[1]).filter((input) => input.checked) : [];
      },
    },
    sessionStorage: { getItem: (key) => key === 'domain' ? domain : null },
    window: { invalidateDiscoveryMatches: () => { invalidations += 1; } },
  });
  vm.runInContext(source, context);
  context.window.invalidateDiscoveryMatches = () => { invalidations += 1; };
  context.refreshSearchCriteriaStatus = () => {};
  context.updateLawVerificationSource = () => {};
  return { context, cards, buttons, nodes, inputs, invalidations: () => invalidations, read: () => JSON.parse(JSON.stringify(context.currentSearchCriteria())) };
}

const criteria = {
  titles: ['Engineer'], mustHaveSkills: ['Python'], locations: ['Denver'], experienceRanges: ['3-5'],
  licensesOrCertifications: ['PMP'], workArrangements: ['remote'], workforceLocations: ['onshore'], strictLocations: true,
};

test('editor has three groups, seven accessible toggles, and initially closed pickers', () => {
  assert.match(page, /<details id="searchCriteriaPanel"[^>]*>/);
  assert.doesNotMatch(page.match(/<details id="searchCriteriaPanel"[^>]*>/)[0], /\sopen(?:\s|>)/);
  assert.equal((page.match(/<section class="criteria-section"/g) || []).length, 3);
  const pickers = [...page.matchAll(/<details class="criteria-picker"[^>]*>/g)].map((match) => match[0]);
  assert.equal(pickers.length, 7);
  pickers.forEach((picker) => assert.doesNotMatch(picker, /\sopen(?:\s|>)/));
  assert.equal((page.match(/data-criteria-ignore="[^"]+" aria-pressed="false" aria-label="Ignore [^"]+">Ignore<\/button>/g) || []).length, 7);
  assert.equal((page.match(/<summary aria-labelledby="criteria\w+Label criteria\w+Summary">/g) || []).length, 7);
  assert.doesNotMatch(page, /Ignore this criterion|Use this criterion|Choose current titles|Choose must-have skills/);
});

test('ignore hides the picker and help while retaining checked choices for Use', () => {
  const view = fixture();
  view.context.setSearchCriteria(criteria);
  groups.forEach((group) => assert.equal(view.cards.get(group).picker.open, false));
  view.cards.get('titles').picker.open = true;
  view.context.setCriteriaGroupIgnored('titles', true);
  assert.equal(view.cards.get('titles').picker.hidden, true);
  assert.equal(view.cards.get('titles').picker.open, false);
  assert.equal(view.cards.get('titles').help[0].hidden, true);
  assert.ok(view.inputs('titles').every((input) => input.disabled));
  assert.equal(view.buttons.get('titles').textContent, 'Use');
  assert.equal(view.buttons.get('titles').attributes['aria-label'], 'Use current titles');
  assert.deepEqual(view.read().titles, []);
  assert.deepEqual(view.read().selectedValues.titles, ['Engineer']);
  view.context.setCriteriaGroupIgnored('titles', false);
  assert.equal(view.cards.get('titles').picker.hidden, false);
  assert.ok(view.inputs('titles').every((input) => !input.disabled));
  assert.equal(view.buttons.get('titles').attributes['aria-pressed'], 'false');
  assert.deepEqual(view.read().titles, ['Engineer']);
});

test('Ignore all and Use all restore selections in each workspace', () => {
  for (const domain of ['dev', 'law', 'engineer', 'dental']) {
    const view = fixture(domain);
    view.context.setSearchCriteria(criteria);
    const original = view.read();
    view.context.setAllCriteriaIgnored(true);
    assert.deepEqual(view.read().ignoredCriteria, groups);
    groups.forEach((group) => {
      assert.equal(view.cards.get(group).picker.hidden, true);
      assert.equal(view.buttons.get(group).attributes['aria-pressed'], 'true');
    });
    view.context.setAllCriteriaIgnored(false);
    assert.deepEqual(view.read(), original);
  }
});

test('score invalidation runs for recruiter edits but not saved criteria initialization', () => {
  const view = fixture();
  view.context.setSearchCriteria(criteria);
  assert.equal(view.invalidations(), 0);
  view.context.setCriteriaGroupIgnored('titles', true);
  assert.equal(view.invalidations(), 1);
  view.context.setAllCriteriaIgnored(false);
  assert.equal(view.invalidations(), 2);
  view.context.handleCriteriaOptionChange('titles', view.inputs('titles')[0]);
  assert.equal(view.invalidations(), 3);
  view.context.setSearchCriteria(view.read());
  assert.equal(view.invalidations(), 3);
});

test('saved ignored choices survive restore but never leak into provider requirements', () => {
  const first = fixture();
  first.context.setSearchCriteria(criteria);
  first.context.setCriteriaGroupIgnored('cities', true);
  first.context.setCriteriaGroupIgnored('licenses', true);
  const saved = first.read();
  const second = fixture();
  second.context.setSearchCriteria(saved);
  const payload = new Map();
  second.context.appendSearchCriteria({ append: (key, value) => payload.set(key, value) });
  assert.equal(payload.get('locations'), '');
  assert.equal(payload.get('licenses_or_certifications'), '');
  assert.equal(payload.get('strict_locations'), 'false');
  assert.equal(payload.get('ignored_criteria'), 'cities,licenses');
  assert.equal(payload.get('required_skills'), 'Python');
  assert.equal(payload.has('selectedValues'), false);
  second.context.setAllCriteriaIgnored(false);
  assert.deepEqual(second.read().locations, ['Denver']);
  assert.deepEqual(second.read().licensesOrCertifications, ['PMP']);
  assert.equal(second.read().strictLocations, true);
});

test('workspace labels are readable while domain values and URL keys stay intact', () => {
  const flow = fs.readFileSync(path.join(pages, 'components/processFlow.html'), 'utf8');
  const options = [...flow.matchAll(/<option value="(dev|engineer|law|dental)">([^<]+)<\/option>/g)].map((match) => [match[1], match[2]]);
  assert.deepEqual(options, [['dev', 'Technology'], ['engineer', 'Engineering'], ['law', 'Law'], ['dental', 'Dental']]);
  for (const filename of ['saved-searches.html', 'temp-profiles.html']) {
    const html = fs.readFileSync(path.join(pages, filename), 'utf8');
    assert.doesNotMatch(html, /Current domain|current domain|\} domain\./);
    assert.match(html, /\?domain=/);
    assert.match(html, /sessionStorage\.getItem\("domain"\)/);
    [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].forEach((match) => new vm.Script(match[1]));
  }
});
