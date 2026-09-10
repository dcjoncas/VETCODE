const assert = require('node:assert/strict');
const { test } = require('node:test');
const ui = require('../ui/pages/JS/professionalMatch.js');
const match = { jobId: '123', evidenceType: 'structured_professional_profile', score: 50, coveragePercent: 50, criteriaSnapshot: { requiredSkills: ['Python'] }, criteria: [] };

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
