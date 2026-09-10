const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { renderRows, createLoader, contacts } = require('../ui/pages/JS/interestedCandidates.js');

test('saved report loads all jobs without the active batch or contact-ready filter', async () => {
  const paths = [];
  const loader = createLoader(async (url) => { paths.push(url); return { domain: 'law', profiles: [{ personid: 1, contactAvailable: false }], hasMore: false }; }, 'law');
  const result = await loader.load(true);
  assert.equal(result.profiles.length, 1);
  assert.match(paths[0], /domain=law/);
  assert.doesNotMatch(paths[0], /jd_id|batch|enrich|calculate-match/);
});

test('pagination keeps every saved person and deduplicates overlap', async () => {
  const paths = [];
  const loader = createLoader(async (url) => { paths.push(url); return paths.length === 1
    ? { domain: 'dev', profiles: [{ personid: 1 }], hasMore: true, nextCursor: '700' }
    : { domain: 'dev', profiles: [{ personid: 1 }, { personid: 801 }], hasMore: false }; }, 'dev');
  await loader.load(true);
  const result = await loader.load();
  assert.deepEqual(result.profiles.map(p => p.personid), [1, 801]);
  assert.match(paths[1], /after=700/);
});

test('empty scan pages with a cursor do not pretend the full report is empty', async () => {
  const loader = createLoader(async () => ({ domain: 'dental', profiles: [], hasMore: true, nextCursor: '5000' }), 'dental');
  assert.equal((await loader.load()).hasMore, true);
});

test('workspace mismatches are rejected and stale refresh responses ignored', async () => {
  const bad = createLoader(async () => ({ domain: 'law', profiles: [] }), 'dev');
  await assert.rejects(bad.load(), /different workspace/);
  let resolve;
  const loader = createLoader(() => new Promise(r => { resolve = r; }), 'dev');
  const pending = loader.load();
  loader.clear();
  resolve({ domain: 'dev', profiles: [{ personid: 10 }], hasMore: false });
  assert.equal(await pending, null);
});

test('saved contact links and profiles include safe scope and missing labels', () => {
  const html = renderRows([{ personid: 42, name: '<img onerror=bad>', emails: ['person@example.com'], phones: ['+1 303 555 0100'], linkedinUrl: 'https://linkedin.com/in/person', interestJobId: '', summary: '<script>bad</script>' }], 'dental');
  assert.match(html, /domain=dental&amp;profileId=42/);
  assert.match(html, /mailto:/);
  assert.match(html, /tel:/);
  assert.match(html, /Open LinkedIn profile/);
  assert.match(html, /No job recorded/);
  assert.doesNotMatch(html, /<img|<script>/);
  assert.match(contacts({}), /Not saved/);
  assert.doesNotMatch(contacts({ linkedinUrl: 'https://linkedin.com.evil.test/in/person', email: '<script>', phone: 'javascript:bad' }), /href=/);
});

test('contact report does not render stale scores supplied by a stored response', () => {
  const html = renderRows([{ personid: 42, name: 'Saved candidate', matchScore: 97, interestJobId: '5' }], 'dev');
  assert.doesNotMatch(html, /97|requirement support|Fit not assessed/);
});

test('page defaults locked, verifies server token, and uses no lookup or outreach APIs', () => {
  const page = fs.readFileSync(path.join(__dirname, '../ui/pages/interested-candidates.html'), 'utf8');
  assert.match(page, /id="reportPanel" hidden/);
  assert.match(page, /X-DevReady-Admin-Token/);
  assert.match(page, /\/api\/access\/admin-check/);
  assert.match(page, /\/api\/access\/admin-login/);
  assert.match(page, /type="password"/);
  assert.match(page, /form\.elements\.password\.value = ""/);
  assert.match(page, /Regular recruiter report access is not enabled yet/);
  assert.doesNotMatch(page, /enrich-result|calculate-match|external\/import|candidateInterest|DevReadyShortlist/);
});
