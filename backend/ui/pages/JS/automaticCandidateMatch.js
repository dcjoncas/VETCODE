(function (root) {
  "use strict";

  // This queue only requests local evidence comparisons. Provider enrichment
  // remains a separate, explicitly confirmed recruiter action.
  function create(options) {
    const states = new WeakMap();
    const limit = Math.max(1, Math.min(4, options.concurrency || 2));
    let active = 0;
    let timer = null;
    let waiters = [];
    const context = () => options.context();
    const key = (candidate, scope) => scope.key + "\n" + options.evidenceKey(candidate);
    const live = (candidate, scope, taskKey) => {
      const now = context();
      return now && now.key === scope.key && options.candidates().includes(candidate)
        && key(candidate, now) === taskKey;
    };
    function next(scope) {
      return options.candidates().find((candidate) => {
        if (!options.eligible(candidate) || options.current(candidate, scope)) return false;
        const state = states.get(candidate);
        return !state || state.key !== key(candidate, scope);
      });
    }
    function state(candidate) {
      const scope = context();
      const value = states.get(candidate);
      return scope && value?.key === key(candidate, scope) ? value : { status: "idle" };
    }
    function pump() {
      let started = false;
      let scope = context();
      while (scope && active < limit) {
        const candidate = next(scope);
        if (!candidate) break;
        const taskScope = scope;
        const taskKey = key(candidate, taskScope);
        states.set(candidate, { key: taskKey, status: "pending" });
        active++;
        started = true;
        let changed = false;
        Promise.resolve().then(() => {
          if (!live(candidate, taskScope, taskKey)) return null;
          return options.score(candidate, taskScope);
        }).then((response) => {
          if (!live(candidate, taskScope, taskKey)) return;
          if (!response) throw new Error("No comparison was returned.");
          options.apply(candidate, response, taskScope);
          states.set(candidate, { key: taskKey, status: "done" });
          changed = true;
        }).catch(() => {
          if (!live(candidate, taskScope, taskKey)) return;
          // Failure is settled until an explicit retry or changed context. A
          // rerender must not become an endless request loop.
          states.set(candidate, { key: taskKey, status: "failed" });
          changed = true;
        }).finally(() => {
          active--;
          const abandoned = states.get(candidate);
          if (!changed && abandoned?.key === taskKey && abandoned.status === "pending") states.delete(candidate);
          if (changed) options.changed();
          pump();
        });
        scope = context();
      }
      if (started) options.changed();
      if (!active) {
        const done = waiters;
        waiters = [];
        done.forEach((resolve) => resolve());
      }
    }
    function refresh() {
      if (timer !== null) clearTimeout(timer);
      timer = setTimeout(() => { timer = null; pump(); }, options.delay ?? 200);
    }
    function flush() {
      if (timer !== null) clearTimeout(timer);
      timer = null;
      return new Promise((resolve) => { waiters.push(resolve); pump(); });
    }
    function retry(candidate) {
      states.delete(candidate);
      refresh();
    }
    return { refresh, flush, state, retry };
  }

  root.DevReadyAutomaticCandidateMatch = { create };
  if (typeof module !== "undefined" && module.exports) module.exports = root.DevReadyAutomaticCandidateMatch;
})(typeof window === "undefined" ? globalThis : window);
