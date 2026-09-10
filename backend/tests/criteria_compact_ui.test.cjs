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
  const storage = new Map([['domain', domain]]);
  const nodes = new Map();
  const cards = new Map();
  const buttons = new Map();
  function element() {
    const classes = new Set();
    return {
      children: [], attributes: {}, dataset: {}, hidden: false, disabled: false, options: [{ value: '10' }],
      checked: false, open: true, value: '', textContent: '',
      set innerHTML(value) { this.children = []; this.html = value; },
      get innerHTML() { return this.html || ''; },
      append(...items) { this.children.push(...items); },
      appendChild(item) { this.children.push(item); },
      addEventListener() {},
      setAttribute(key, value) { this.attributes[key] = value; },
      getAttribute(key) { return this.attributes[key]; },
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
    card.picker.summary = element();
    card.picker.querySelector = (selector) => selector === 'summary' ? card.picker.summary : null;
    card.picker.closest = () => card;
    card.dataset.criteriaCard = group;
    card.help = [element()];
    const labelId = `criteria${ids[index]}Label`;
    card.title = { textContent: page.match(new RegExp(`<span id="${labelId}"[^>]*>([^<]+)</span>`))[1] };
    card.querySelector = (selector) => selector.startsWith('details') ? card.picker : selector === '.criteria-card-title' ? card.title : null;
    card.querySelectorAll = (selector) => {
      if (selector === ':scope > details input, :scope > details button') return inputs(group);
      // The whole criteria panel is itself <details>: an unscoped selector also
      // matches the sibling switch through that ancestor and disables recovery.
      if (selector === 'details input, details button') return [...inputs(group), buttons.get(group)];
      if (selector === '.criteria-toggle, .criteria-help') return card.help;
      return [];
    };
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
        if (selector === '#searchCriteriaPanel [data-criteria-card]') return [...cards.values()];
        const group = selector.match(/data-criteria-group="([^"]+)"/);
        return group ? inputs(group[1]).filter((input) => input.checked) : [];
      },
    },
    sessionStorage: { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) },
    window: { scrollX: 0, scrollY: 240, invalidateDiscoveryMatches: () => { invalidations += 1; } },
  });
  vm.runInContext(source, context);
  context.window.invalidateDiscoveryMatches = () => { invalidations += 1; };
  context.refreshSearchCriteriaStatus = () => {};
  context.updateLawVerificationSource = () => {};
  return { context, cards, buttons, nodes, inputs, storage, invalidations: () => invalidations, read: () => JSON.parse(JSON.stringify(context.currentSearchCriteria())) };
}

const criteria = {
  titles: ['Engineer'], mustHaveSkills: ['Python'], locations: ['Denver'], experienceRanges: ['3-5'],
  licensesOrCertifications: ['PMP'], workArrangements: ['remote'], workforceLocations: ['onshore'], strictLocations: true,
};

test('compact editor has seven labeled rows and accessible switches with exclusive closed editors', () => {
  assert.match(page, /<details id="searchCriteriaPanel"[^>]*>/);
  assert.doesNotMatch(page.match(/<details id="searchCriteriaPanel"[^>]*>/)[0], /\sopen(?:\s|>)/);
  assert.equal((page.match(/<div\b[^>]*data-criteria-card="/g) || []).length, 7);
  assert.doesNotMatch(page, /<section class="criteria-section"/);
  const pickers = [...page.matchAll(/<details class="criteria-picker"[^>]*>/g)].map((match) => match[0]);
  assert.equal(pickers.length, 7);
  pickers.forEach((picker) => {
    assert.doesNotMatch(picker, /\sopen(?:\s|>)/);
    assert.match(picker, /name="search-criteria-editor"/);
  });
  const switches = [...page.matchAll(/<button\b[^>]*data-criteria-ignore="([^"]+)"[^>]*>([\s\S]*?)<\/button>/g)];
  assert.deepEqual(switches.map((entry) => entry[1]).sort(), [...groups].sort());
  switches.forEach((entry) => {
    assert.match(entry[0], /role="switch"/);
    assert.match(entry[0], /aria-checked="true"/);
    assert.match(entry[0], /aria-label="Use [^"]+ criterion"/);
    assert.equal(entry[2], 'On');
    assert.doesNotMatch(entry[0], /aria-pressed|>Ignore</);
  });
  assert.equal((page.match(/<summary aria-labelledby="criteria\w+Label criteria\w+Summary">/g) || []).length, 7);
  assert.doesNotMatch(page, /Ignore this criterion|Use this criterion|Choose current titles|Choose must-have skills/);
  assert.match(source, /querySelectorAll\(":scope > details input, :scope > details button"\)/);
  assert.doesNotMatch(source, /querySelectorAll\("details input, details button"\)/);
});

test('turning off closes the editor but leaves selected values visible and the switch operable', () => {
  const view = fixture();
  view.context.setSearchCriteria(criteria);
  groups.forEach((group) => assert.equal(view.cards.get(group).picker.open, false));
  view.cards.get('titles').picker.open = true;
  view.context.setCriteriaGroupIgnored('titles', true);
  assert.equal(view.cards.get('titles').picker.hidden, false);
  assert.equal(view.cards.get('titles').picker.open, false);
  assert.equal(view.cards.get('titles').help[0].hidden, true);
  assert.ok(view.inputs('titles').every((input) => input.disabled));
  assert.equal(view.buttons.get('titles').textContent, 'Off');
  assert.equal(view.buttons.get('titles').attributes['aria-label'], 'Use title criterion');
  assert.equal(view.buttons.get('titles').attributes['aria-checked'], 'false');
  assert.equal(view.buttons.get('titles').disabled, false);
  assert.equal(view.cards.get('titles').picker.summary.attributes['aria-disabled'], 'true');
  assert.equal(view.cards.get('titles').picker.summary.tabIndex, -1);
  assert.equal(view.nodes.get('criteriaTitlesSummary').textContent, 'Engineer');
  assert.deepEqual(view.read().titles, []);
  assert.deepEqual(view.read().selectedValues.titles, ['Engineer']);
  view.context.setCriteriaGroupIgnored('titles', false);
  assert.equal(view.cards.get('titles').picker.hidden, false);
  assert.ok(view.inputs('titles').every((input) => !input.disabled));
  assert.equal(view.buttons.get('titles').attributes['aria-checked'], 'true');
  assert.equal(view.buttons.get('titles').textContent, 'On');
  assert.equal(view.cards.get('titles').picker.summary.attributes['aria-disabled'], 'false');
  assert.equal(view.cards.get('titles').picker.summary.tabIndex, 0);
  assert.deepEqual(view.read().titles, ['Engineer']);
});

test('Turn off all and Turn on all restore selections in each workspace', () => {
  for (const domain of ['dev', 'law', 'engineer', 'dental']) {
    const view = fixture(domain);
    view.context.setSearchCriteria(criteria);
    const original = view.read();
    view.context.setAllCriteriaIgnored(true);
    assert.deepEqual(view.read().ignoredCriteria, groups);
    groups.forEach((group) => {
      assert.equal(view.cards.get(group).picker.hidden, false);
      assert.equal(view.cards.get(group).picker.open, false);
      assert.equal(view.buttons.get(group).attributes['aria-checked'], 'false');
      assert.equal(view.buttons.get(group).disabled, false);
    });
    assert.equal(view.nodes.get('criteriaIgnoreAll').textContent, 'Turn on all');
    view.context.setAllCriteriaIgnored(false);
    assert.deepEqual(view.read(), original);
    assert.equal(view.nodes.get('criteriaIgnoreAll').textContent, 'Turn off all');
  }
});

test('exclusive editor fallback closes earlier editors and refuses disabled criteria', () => {
  for (const domain of ['dev', 'law', 'engineer', 'dental']) {
    const view = fixture(domain);
    view.context.setSearchCriteria(criteria);
    view.cards.get('titles').picker.open = true;
    view.context.handleCriteriaEditorToggle(view.cards.get('titles').picker);
    view.cards.get('skills').picker.open = true;
    view.context.handleCriteriaEditorToggle(view.cards.get('skills').picker);
    assert.equal(view.cards.get('titles').picker.open, false);
    assert.equal(view.cards.get('skills').picker.open, true);
    assert.equal([...view.cards.values()].filter((card) => card.picker.open).length, 1);
    view.context.setCriteriaGroupIgnored('licenses', true);
    view.cards.get('licenses').picker.open = true;
    view.context.handleCriteriaEditorToggle(view.cards.get('licenses').picker);
    assert.equal(view.cards.get('licenses').picker.open, false);
    assert.equal(view.cards.get('skills').picker.open, true);
  }
  assert.match(source, /if \(criterionIgnored\(group\)\) event\.preventDefault\(\)/);
});

test('row summaries show actual choices, abbreviate long selections, and expose the full list', () => {
  const view = fixture();
  view.context.setSearchCriteria({ ...criteria, titles: ['Lead', 'Manager', 'Engineer', 'Architect'] });
  const summary = view.nodes.get('criteriaTitlesSummary');
  assert.equal(summary.textContent, 'Lead, Manager, Engineer +1 more');
  assert.equal(summary.attributes.title, 'Lead, Manager, Engineer, Architect');
  view.context.setCriteriaGroupIgnored('titles', true);
  assert.equal(summary.textContent, 'Lead, Manager, Engineer +1 more');
  view.context.setSearchCriteria({ ...criteria, titles: [] });
  assert.equal(summary.textContent, 'Choose…');
  view.context.setCriteriaGroupIgnored('titles', true);
  assert.equal(summary.textContent, 'No restriction');
});

test('each criterion can be disabled and restored without changing other selections', () => {
  for (const domain of ['dev', 'law', 'engineer', 'dental']) {
    const view = fixture(domain);
    view.context.setSearchCriteria(criteria);
    const original = view.read();
    for (const group of groups) {
      view.context.setCriteriaGroupIgnored(group, true);
      assert.equal(view.buttons.get(group).disabled, false);
      assert.deepEqual(view.read().ignoredCriteria, [group]);
      assert.deepEqual(view.read().selectedValues, original.selectedValues);
      view.context.setCriteriaGroupIgnored(group, false);
      assert.deepEqual(view.read(), original);
    }
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
  for (const domain of ['dev', 'law', 'engineer', 'dental']) {
    const first = fixture(domain);
    first.context.setSearchCriteria(criteria);
    first.context.setCriteriaGroupIgnored('cities', true);
    first.context.setCriteriaGroupIgnored('licenses', true);
    const saved = first.read();
    const second = fixture(domain);
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
  }
});

test('browser history round-trip preserves choices, disabled criteria, and the active editor per workspace', () => {
  for (const domain of ['dev', 'law', 'engineer', 'dental']) {
    const first = fixture(domain);
    first.context.setSearchCriteria({ ...criteria, strictLocations: false });
    first.context.setCriteriaGroupIgnored('cities', true);
    first.context.setCriteriaGroupIgnored('licenses', true);
    first.nodes.get('searchCriteriaPanel').open = true;
    first.cards.get('skills').picker.open = true;
    vm.runInContext('latestExternalResults = [{ source: "pdl", source_id: "synthetic-1", name: "Example Candidate" }];', first.context);
    first.context.persistSourcingViewState();
    const saved = JSON.parse(first.storage.get(`externalSourcingView:${domain}`));
    assert.deepEqual(saved.openCriteriaGroups, ['skills']);
    assert.equal(saved.criteriaPanelOpen, true);
    assert.deepEqual(saved.criteria.ignoredCriteria, ['cities', 'licenses']);
    assert.deepEqual(saved.criteria.selectedValues.cities, ['Denver']);
    assert.equal(saved.criteria.strictLocationsSelection, false);

    const restored = fixture(domain);
    restored.storage.set(`externalSourcingView:${domain}`, JSON.stringify(saved));
    for (const helper of ['setSource', 'setActiveSavedSearch', 'renderSourceAudit', 'renderResults', 'renderResultPager', 'updateSelectedEnrichmentCount', 'restoreSourcingScroll']) {
      restored.context[helper] = () => {};
    }
    assert.equal(restored.context.restoreSourcingViewState(), true);
    assert.deepEqual(restored.read(), saved.criteria);
    assert.equal(restored.nodes.get('searchCriteriaPanel').open, true);
    assert.equal(restored.cards.get('skills').picker.open, true);
    assert.equal([...restored.cards.values()].filter((card) => card.picker.open).length, 1);
    assert.equal(restored.invalidations(), 0);
    assert.equal(restored.context.restoreSourcingViewState({ ...saved, openCriteriaGroups: ['cities', 'unknown', 'skills', 'titles'] }), true);
    assert.equal(restored.cards.get('cities').picker.open, false);
    assert.equal(restored.cards.get('skills').picker.open, true);
    assert.equal(restored.cards.get('titles').picker.open, false);
    assert.equal([...restored.cards.values()].filter((card) => card.picker.open).length, 1);
    restored.context.setAllCriteriaIgnored(false);
    assert.deepEqual(restored.read().locations, ['Denver']);
    assert.deepEqual(restored.read().licensesOrCertifications, ['PMP']);
    assert.equal(restored.read().strictLocations, false);
    restored.storage.set(`externalSourcingView:${domain}`, JSON.stringify({ ...saved, domain: 'unrelated' }));
    assert.equal(restored.context.readSourcingViewState(), null);
  }
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
