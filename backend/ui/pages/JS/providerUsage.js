(function (root) {
  "use strict";
  const known = (value) => typeof value === "number" && Number.isFinite(value) && value >= 0;
  const number = (value) => value.toLocaleString();

  function describe(audit = {}) {
    const usage = audit.providerUsage || {};
    const notes = [];
    let used = "Not reported";
    if (usage.source === "local_cache") {
      used = "0 — saved results";
      notes.push("Opening saved results did not call the provider.");
    } else if (known(usage.creditsUsed)) {
      used = `${number(usage.creditsUsed)} credits`;
      if (usage.creditType) notes.push(`Credit bucket: ${usage.creditType}.`);
    } else if (known(usage.reportedCreditsUsed) && usage.reportedRequests > 0) {
      used = `Partially reported: ${number(usage.reportedCreditsUsed)} credits`;
      notes.push(`Credit headers were returned for ${usage.reportedRequests} of ${usage.requests} calls; the full charge is unavailable.`);
    }
    if (!known(usage.creditsUsed) && known(audit.estimatedCreditsUsed)) {
      notes.push(`Estimated search usage: ${number(audit.estimatedCreditsUsed)} ${audit.costLabel || "credits"}. This is an estimate, not a provider-confirmed charge or account balance.`);
    }
    const balanceUsage = usage.source === "local_cache" ? (audit.originalProviderUsage || {}) : usage;
    const historical = usage.source === "local_cache" || balanceUsage.balanceStatus === "historical";
    let remaining = "Unavailable";
    if (known(balanceUsage.accountRemainingCredits)) {
      remaining = `${number(balanceUsage.accountRemainingCredits)}${historical ? " — saved snapshot" : " — last reported"}`;
      const date = new Date(balanceUsage.observedAt || "");
      notes.push(`PDL account credits${balanceUsage.creditType ? ` (${balanceUsage.creditType})` : ""} include purchased credits and available overage${Number.isNaN(date.getTime()) ? "" : `; reported ${date.toLocaleString()}`}. This is not a live balance check.`);
    } else {
      notes.push("The provider did not report an account balance for this request. Total matches is a result count, not credits remaining.");
    }
    return { used, remaining, notes };
  }

  function merge(previousAudit, currentAudit) {
    const callsFor = (audit) => {
      if (!audit) return [];
      if (Array.isArray(audit.providerUsage?.calls)) return audit.providerUsage.calls;
      return audit.queryExecuted ? [{ creditsUsed: null }] : [];
    };
    const calls = [...callsFor(previousAudit), ...callsFor(currentAudit)];
    const reported = calls.filter((call) => known(call.creditsUsed));
    const last = calls[calls.length - 1] || {};
    const total = reported.reduce((sum, call) => sum + call.creditsUsed, 0);
    return {
      status: !calls.length ? "not_called" : reported.length === calls.length ? "reported" : reported.length ? "partial" : "unavailable",
      creditsUsed: reported.length === calls.length ? total : null,
      reportedCreditsUsed: total,
      requests: calls.length,
      reportedRequests: reported.length,
      creditType: last.creditType || null,
      accountRemainingCredits: known(last.accountRemainingCredits) ? last.accountRemainingCredits : null,
      purchasedRemainingCredits: known(last.purchasedRemainingCredits) ? last.purchasedRemainingCredits : null,
      overageRemainingCredits: known(last.overageRemainingCredits) ? last.overageRemainingCredits : null,
      observedAt: last.observedAt || null,
      balanceStatus: known(last.accountRemainingCredits) ? "reported" : "unavailable",
      source: calls.length ? "response_headers" : "local_cache",
      calls,
    };
  }

  root.DevReadyProviderUsage = { describe, merge };
  if (typeof module !== "undefined" && module.exports) module.exports = root.DevReadyProviderUsage;
})(typeof window === "undefined" ? globalThis : window);
