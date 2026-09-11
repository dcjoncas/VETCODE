const assert = require('node:assert/strict');
const { test } = require('node:test');
const ui = require('../ui/pages/JS/professionalMatch.js');
const match = { jobId: '123', evidenceType: 'structured_professional_profile', score: 50, coveragePercent: 50, criteriaSnapshot: { requiredSkills: ['Python'] }, criteria: [] };

test('old algorithm comparisons are refreshed and availability never guesses a negative', () => {
  assert.equal(ui.view({ match: { ...match, version: 'professional-evidence-v1' } }, '123').current, false);
  assert.equal(ui.view({ match: { ...match, version: 'professional-evidence-v2' } }, '123').current, true);
  assert.match(ui.availability(null), /Open to Work: unknown/);
  const html = ui.availability({ status: 'signal', evidence: '<script>x</script>' });
  assert.match(html, /profile text/);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(ui.details({ criteria: [{ label: 'Airflow', required: false, status: 'unknown', jdEvidence: 'Bonus: Airflow' }] }), /bonus/);
});

test('broad discovery filters cannot suppress the saved JD comparison', () => {
  const fs = require('node:fs');
  const vm = require('node:vm');
  const page = fs.readFileSync(require('node:path').join(__dirname, '../ui/pages/mine-candidate-external.html'), 'utf8');
  const definition = page.match(/function currentMatchCriteria\(\) \{[\s\S]*?\n      \}/)[0];
  const context = vm.createContext({ currentSearchCriteria: () => ({ ignoredCriteria: ['skills', 'titles', 'experience', 'licenses'] }) });
  const criteria = vm.runInContext(`${definition}; currentMatchCriteria()`, context);
  assert.equal(JSON.stringify(criteria), '{}');
  assert.equal(ui.view({ match: { ...match, criteriaSnapshot: { ignoredCriteria: ['skills', 'titles'] }, score: null } }, '123', criteria).current, false);
  assert.equal(ui.view({ match: { ...match, criteriaSnapshot: {} } }, '123', criteria).score, 50);
});

test('summary distinguishes missing evidence from conflicts and escapes requirement labels', () => {
  const html = ui.summary({ criteria: [
    { label: '<Python>', status: 'matched' }, { label: 'Java', status: 'unknown' },
    { label: 'License', status: 'gap' }, { label: 'Location', status: 'unknown', scored: false },
  ] });
  assert.match(html, /Supported \(1\)/);
  assert.match(html, /Not evidenced \(1\)/);
  assert.match(html, /Conflicting evidence \(1\)/);
  assert.match(html, /&lt;Python&gt;/);
  assert.doesNotMatch(html, /Location|<Python>/);
  assert.match(ui.details({}), /No structured job requirements are available/);
});

test('legacy keyword score and absent JD never appear as a current fit percentage', () => {
  assert.equal(ui.view({ score: 99, saved_match: { jobId: '123', score: 99 } }, '123').score, null);
  assert.equal(ui.view({ match }, '').score, null);
});
test('only matching job and criteria show the percentage, including true zero', () => {
  assert.equal(ui.view({ match }, '123', { mustHaveSkills: ['python'] }).score, 50);
  assert.equal(ui.view({ match }, '456').score, null);
  assert.equal(ui.view({ match }, '123', { requiredSkills: ['Java'] }).score, null);
  assert.equal(ui.view({ match: { ...match, score: 0 } }, '123').score, 0);
  assert.equal(ui.view({ match: { ...match, score: null } }, '123').score, null);
});
test('ignored values and location strictness changes invalidate an old comparison', () => {
  assert.equal(ui.view({ match }, '123', { requiredSkills: ['Python'], ignoredCriteria: ['skills'] }).current, false);
  assert.notEqual(ui.criteriaKey({ strictLocations: true }), ui.criteriaKey({ strictLocations: false }));
});
test('details distinguish unknown evidence, escaped quotations, and non-scored logistics', () => {
  const html = ui.details({ ...match, criteria: [{ label: 'Python', status: 'unknown', reason: 'Not stated', evidence: [{ field: 'summary', text: '<script>unsafe</script>' }], scored: false }] });
  assert.match(html, /Unknown/);
  assert.match(html, /outside score/);
  assert.match(html, /not a candidate-uploaded resume/);
  assert.match(html, /50% evidence coverage/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;/);
});
