const assert = require('node:assert/strict');
const { test } = require('node:test');
const { create } = require('../ui/pages/JS/automaticCandidateMatch.js');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

async function tick() {
  // Drain only promise callbacks: no timers, provider APIs or live app startup.
  for (let i = 0; i < 12; i++) await Promise.resolve();
}

function fixture({ count = 1, concurrency, score, delay = 1000 } = {}) {
  let scope = { key: 'job-a|criteria-a', jobId: 'job-a' };
  let rows = Array.from({ length: count }, (_, i) => ({ id: `person-${i}`, evidence: `facts-${i}` }));
  let active = 0, maxActive = 0, changes = 0;
  const calls = [], applied = [];
  const queue = create({
    context: () => scope,
    candidates: () => rows,
    eligible: candidate => candidate.eligible !== false,
    current: (candidate, context) => candidate.match?.contextKey === context.key,
    evidenceKey: candidate => JSON.stringify([candidate.id, candidate.evidence]),
    score(candidate, context) {
      active++;
      maxActive = Math.max(maxActive, active);
      const wait = deferred();
      calls.push({ candidate, scope: context, wait });
      try {
        return Promise.resolve(score ? score(candidate, context) : wait.promise)
          .finally(() => { active--; });
      } catch (error) {
        active--;
        throw error;
      }
    },
    apply(candidate, response, context) {
      applied.push({ candidate, response, context });
      candidate.match = { contextKey: context.key, ...response };
    },
    changed() { changes++; },
    delay,
    ...(concurrency === undefined ? {} : { concurrency }),
  });
  return {
    queue, calls, applied,
    get rows() { return rows; }, set rows(value) { rows = value; },
    get scope() { return scope; }, set scope(value) { scope = value; },
    get maxActive() { return maxActive; }, get changes() { return changes; },
  };
}

test('current structured comparisons including unavailable/null and zero need no requests', async () => {
  const f = fixture({ count: 3 });
  f.rows[0].match = { contextKey: f.scope.key, status: 'calculated', score: 70 };
  f.rows[1].match = { contextKey: f.scope.key, status: 'unavailable', score: null };
  f.rows[2].match = { contextKey: f.scope.key, status: 'calculated', score: 0 };
  await f.queue.flush();
  assert.equal(f.calls.length, 0);
  assert.equal(f.applied.length, 0);
});

test('default and explicit two-worker queues never exceed two in-flight comparisons', async () => {
  for (const concurrency of [undefined, 2]) {
    const f = fixture({ count: 7, concurrency });
    const finished = f.queue.flush();
    await tick();
    assert.equal(f.calls.length, 2);
    for (let index = 0; index < 7; index++) {
      assert.ok(f.calls[index], `comparison ${index} should have been scheduled`);
      f.calls[index].wait.resolve({ score: 90 - index });
      await tick();
      assert.ok(f.maxActive <= 2);
    }
    await finished;
    assert.equal(f.applied.length, 7);
    f.rows.forEach(row => assert.equal(f.queue.state(row).status, 'done'));
    assert.ok(f.changes > 0);
  }
});

test('eligible filtering and missing-JD or paused contexts make no requests', async () => {
  const f = fixture({ count: 3 });
  f.scope = null; // The page returns null both without a JD and while paused.
  await f.queue.flush();
  assert.equal(f.calls.length, 0);
  f.scope = { key: 'job-a|criteria-a' };
  f.rows.forEach(row => { row.eligible = false; });
  await f.queue.flush();
  assert.equal(f.calls.length, 0);
  assert.equal(f.queue.state(f.rows[0]).status, 'idle');
});

test('failed comparisons settle across rerenders and only explicit retry runs again', async () => {
  const f = fixture();
  const finished = f.queue.flush();
  await tick();
  f.calls[0].wait.reject(new Error('synthetic local scoring failure'));
  await finished;
  assert.equal(f.queue.state(f.rows[0]).status, 'failed');
  for (let i = 0; i < 3; i++) { f.queue.refresh(); await f.queue.flush(); }
  assert.equal(f.calls.length, 1);
  f.queue.retry(f.rows[0]);
  const retried = f.queue.flush();
  await tick();
  assert.equal(f.calls.length, 2);
  f.calls[1].wait.resolve({ score: 55 });
  await retried;
  assert.equal(f.queue.state(f.rows[0]).status, 'done');
});

test('empty live responses settle as failed rather than permanent pending indicators', async () => {
  for (const response of [null, undefined]) {
    const f = fixture({ score: () => response });
    await f.queue.flush();
    assert.equal(f.queue.state(f.rows[0]).status, 'failed');
    await f.queue.flush();
    assert.equal(f.calls.length, 1);
    assert.equal(f.applied.length, 0);
  }
});

test('synchronous score errors settle and do not prevent remaining candidates', async () => {
  const f = fixture({ count: 3, score: candidate => {
    if (candidate.id === 'person-1') throw new Error('synthetic');
    return { score: 40 };
  } });
  await f.queue.flush();
  assert.equal(f.calls.length, 3);
  assert.equal(f.applied.length, 2);
  assert.equal(f.queue.state(f.rows[1]).status, 'failed');
});

test('context changed before score microtask prevents the obsolete request', async () => {
  const f = fixture({ score: () => ({ score: 60 }) });
  const finished = f.queue.flush();
  f.scope = { key: 'job-b|criteria-b', jobId: 'job-b' };
  await finished;
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].scope.jobId, 'job-b');
  assert.equal(f.applied[0].context.jobId, 'job-b');
});

test('new context during await ignores old results and automatically schedules new comparisons', async () => {
  const f = fixture({ count: 2 });
  const finished = f.queue.flush();
  await tick();
  assert.equal(f.calls.length, 2);
  f.scope = { key: 'job-b|criteria-b', jobId: 'job-b' };
  f.calls[0].wait.resolve({ score: 99 });
  await tick();
  assert.equal(f.applied.length, 0);
  assert.equal(f.calls.length, 3);
  assert.equal(f.calls[2].scope.jobId, 'job-b');
  f.calls[1].wait.reject(new Error('obsolete request'));
  await tick();
  assert.equal(f.calls.length, 4);
  assert.equal(f.calls[3].scope.jobId, 'job-b');
  f.calls[2].wait.resolve({ score: 30 });
  f.calls[3].wait.resolve({ score: 80 });
  await finished;
  assert.equal(f.applied.length, 2);
  assert.ok(f.applied.every(item => item.context.jobId === 'job-b'));
  assert.ok(f.maxActive <= 2);
});

test('paused context during await ignores the response and makes no follow-up request', async () => {
  const f = fixture({ count: 3 });
  const finished = f.queue.flush();
  await tick();
  f.scope = null;
  f.calls.forEach(call => call.wait.resolve({ score: 99 }));
  await finished;
  assert.equal(f.calls.length, 2);
  assert.equal(f.applied.length, 0);
  assert.equal(f.queue.state(f.rows[0]).status, 'idle');
});

test('resuming the same context retries abandoned work instead of leaving it pending forever', async () => {
  const f = fixture();
  const originalScope = f.scope;
  const first = f.queue.flush();
  await tick();
  f.scope = null;
  f.calls[0].wait.resolve({ score: 99 });
  await first;
  f.scope = originalScope;
  assert.equal(f.queue.state(f.rows[0]).status, 'idle');
  const resumed = f.queue.flush();
  await tick();
  assert.equal(f.calls.length, 2);
  f.calls[1].wait.resolve({ score: 21 });
  await resumed;
  assert.equal(f.applied.length, 1);
  assert.equal(f.rows[0].match.score, 21);
});

test('an abandoned old task does not erase a newer pending state for the same candidate', async () => {
  const f = fixture();
  const first = f.queue.flush();
  await tick();
  f.scope = { key: 'job-b|criteria-b', jobId: 'job-b' };
  const second = f.queue.flush();
  await tick();
  assert.equal(f.calls.length, 2);
  f.calls[0].wait.resolve({ score: 99 });
  await tick();
  assert.equal(f.queue.state(f.rows[0]).status, 'pending');
  assert.equal(f.calls.length, 2);
  f.calls[1].wait.resolve({ score: 11 });
  await Promise.all([first, second]);
  assert.equal(f.calls.length, 2);
  assert.equal(f.applied.length, 1);
  assert.equal(f.applied[0].context.jobId, 'job-b');
});

test('replacement object with the same ID rejects old response and compares the replacement', async () => {
  const f = fixture();
  const original = f.rows[0];
  const finished = f.queue.flush();
  await tick();
  const replacement = { ...original };
  f.rows = [replacement];
  f.calls[0].wait.resolve({ score: 99 });
  await tick();
  assert.equal(f.applied.length, 0);
  assert.equal(f.calls.length, 2);
  assert.equal(f.calls[1].candidate, replacement);
  f.calls[1].wait.resolve({ score: 44 });
  await finished;
  assert.equal(f.applied[0].candidate, replacement);
  assert.equal(original.match, undefined);
});

test('changed identity or professional evidence on the same object invalidates in-flight score', async () => {
  for (const field of ['id', 'evidence']) {
    const f = fixture();
    const finished = f.queue.flush();
    await tick();
    f.rows[0][field] = 'changed';
    assert.equal(f.queue.state(f.rows[0]).status, 'idle');
    f.calls[0].wait.resolve({ score: 99 });
    await tick();
    assert.equal(f.applied.length, 0);
    assert.equal(f.calls.length, 2);
    f.calls[1].wait.resolve({ score: 12 });
    await finished;
    assert.equal(f.applied.length, 1);
    assert.equal(f.applied[0].response.score, 12);
  }
});

test('removing a candidate ignores its response without reviving it', async () => {
  const f = fixture();
  const finished = f.queue.flush();
  await tick();
  f.rows = [];
  f.calls[0].wait.resolve({ score: 88 });
  await finished;
  assert.equal(f.calls.length, 1);
  assert.equal(f.applied.length, 0);
});

test('multiple refresh calls and concurrent flush waiters coalesce without duplicate work', async () => {
  const f = fixture();
  f.queue.refresh(); f.queue.refresh(); f.queue.refresh();
  const first = f.queue.flush();
  const second = f.queue.flush();
  await tick();
  assert.equal(f.calls.length, 1);
  f.calls[0].wait.resolve({ status: 'unavailable', score: null });
  await Promise.all([first, second]);
  assert.equal(f.queue.state(f.rows[0]).status, 'done');
  await f.queue.flush();
  assert.equal(f.calls.length, 1);
});
