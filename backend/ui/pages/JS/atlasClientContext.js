(function () {
  "use strict";

  const state = {
    recordsByDomain: new Map(),
    pickerOpen: new Set(),
  };

  function currentDomain() {
    const raw = sessionStorage.getItem("domain") || "dev";
    return raw === "technology" ? "dev" : (["dev", "engineer", "law", "dental"].includes(raw) ? raw : "dev");
  }

  function domainLabel(domain = currentDomain()) {
    return {
      dev: "DevReady Technology",
      engineer: "EngineerReady",
      law: "LegalReady",
      dental: "DentalReady",
    }[domain] || "DevReady";
  }

  function storageKey(domain = currentDomain()) {
    return `atlasSourcingClient:${domain}`;
  }

  function localCrmStorageKey(domain = currentDomain()) {
    return `devreadyCrmRecords:${domain}`;
  }

  function clean(value, limit = 1200) {
    return String(value == null ? "" : value).trim().slice(0, limit);
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function parseContactLine(line, record = {}) {
    const raw = clean(line, 2400);
    if (!raw) return null;
    const parts = raw.includes("|")
      ? raw.split("|").map((part) => clean(part, 500))
      : raw.split(",").map((part) => clean(part, 500));
    const emailIndex = parts.findIndex((part) => part.includes("@"));
    const phoneIndex = parts.findIndex((part) => !part.includes("@") && /\d{3}[^\d]*\d{3}/.test(part));
    return {
      id: "",
      name: parts[0] || clean(record.contact, 240),
      relationshipRole: parts[1] || "Primary Contact",
      email: emailIndex >= 0 ? parts[emailIndex] : clean(record.email, 240),
      phone: phoneIndex >= 0 ? parts[phoneIndex] : clean(record.phone, 120),
      title: parts[4] || "",
      description: parts[5] || "",
      lastConversation: parts[6] || clean(record.lastTouched || record.when, 120),
      linkedinUrl: parts[7] || "",
    };
  }

  function recordContacts(record = {}) {
    const rows = [];
    const members = Array.isArray(record.teamMembers)
      ? record.teamMembers
      : (Array.isArray(record.players) ? record.players : []);
    members.forEach((member) => {
      if (!member || typeof member !== "object") return;
      rows.push({
        id: clean(member.id, 180),
        name: clean(member.name || member.contact, 240),
        relationshipRole: clean(member.relationshipRole || member.role, 160),
        email: clean(member.email, 240),
        phone: clean(member.phone, 120),
        title: clean(member.jobTitle || member.title, 240),
        description: clean(member.description, 800),
        lastConversation: clean(member.lastConversation, 240),
        linkedinUrl: clean(member.linkedinUrl || member.linkedin, 500),
      });
    });
    if (typeof record.contacts === "string") {
      record.contacts.split(/\r?\n/).forEach((line) => {
        const parsed = parseContactLine(line, record);
        if (parsed) rows.push(parsed);
      });
    }
    if (!rows.length && (record.contact || record.email || record.phone)) {
      rows.push({
        id: "",
        name: clean(record.contact, 240),
        relationshipRole: "Primary Contact",
        email: clean(record.email, 240),
        phone: clean(record.phone, 120),
        title: "",
        description: "",
        lastConversation: clean(record.lastTouched || record.when, 240),
        linkedinUrl: "",
      });
    }
    const seen = new Set();
    return rows.filter((row) => {
      const key = `${row.email || ""}|${row.name || ""}`.toLowerCase();
      if (!key.replace("|", "") || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function primaryContact(record = {}, contacts = recordContacts(record)) {
    const named = clean(record.contact, 240).toLowerCase();
    return contacts.find((row) => named && row.name.toLowerCase() === named)
      || contacts.find((row) => /primary|decision maker|champion|hiring/i.test(row.relationshipRole || "") && row.email)
      || contacts.find((row) => row.email)
      || contacts[0]
      || {};
  }

  function normalizeRecord(record = {}, domain = currentDomain()) {
    const contacts = recordContacts(record);
    const contact = primaryContact(record, contacts);
    const deal = Array.isArray(record.deals) && record.deals.length ? record.deals[0] : {};
    const id = clean(record.id || record.crm_customer_id || record.customer, 180);
    const name = clean(record.customer || record.name || record.company, 240);
    return {
      id,
      name,
      domain,
      contactId: clean(contact.id, 180),
      contactName: clean(contact.name || record.contact, 240),
      contactEmail: clean(contact.email || record.email, 240),
      contactPhone: clean(contact.phone || record.phone, 120),
      contactTitle: clean(contact.title, 240),
      contactRole: clean(contact.relationshipRole, 160),
      contactLinkedIn: clean(contact.linkedinUrl, 500),
      owner: clean(record.owner || deal.owner, 180),
      dealTitle: clean(record.dealTitle || deal.title, 300),
      dealStage: clean(record.dealStage || record.contractStatus || deal.stage, 180),
      address: clean(record.billing_address || record.address, 1200),
      website: clean(record.website || record.companyWebsite, 500),
      industry: clean(record.industry, 240),
      currentNeed: clean(record.what, 1200),
      whyItMatters: clean(record.why, 1200),
      nextStep: clean(record.nextStep, 800),
      meetingUrl: clean(record.meetingUrl, 500),
      lastTouched: clean(record.lastTouched || record.when, 120),
      contacts: contacts.slice(0, 20),
    };
  }

  function getAttachedClient(domain = currentDomain()) {
    try {
      const parsed = JSON.parse(sessionStorage.getItem(storageKey(domain)) || "null");
      if (!parsed || parsed.domain !== domain || !parsed.id || !parsed.name) return null;
      return parsed;
    } catch {
      return null;
    }
  }

  function atlasUrl(client = getAttachedClient(), domain = currentDomain()) {
    const url = new URL("crm.html", window.location.href);
    url.searchParams.set("domain", domain);
    if (client && client.id) url.searchParams.set("atlasClientId", client.id);
    if (client && client.name) url.searchParams.set("client", client.name);
    return url.href;
  }

  function notify(client, action) {
    if (typeof window.updateAtlasClient === "function") window.updateAtlasClient();
    window.dispatchEvent(new CustomEvent("devready-atlas-client-changed", {
      detail: { client, action, domain: currentDomain() },
    }));
  }

  function attachClient(recordOrClient, options = {}) {
    const domain = currentDomain();
    const client = recordOrClient && recordOrClient.contactEmail !== undefined
      ? { ...recordOrClient, domain }
      : normalizeRecord(recordOrClient || {}, domain);
    if (!client.id || !client.name) throw new Error("Choose a valid Atlas client record first.");
    client.attachedAt = new Date().toISOString();
    client.attachedFrom = clean(options.sourcePage || window.location.pathname.split("/").pop(), 160);
    client.jobId = clean(sessionStorage.getItem("jobID") || sessionStorage.getItem("jobId"), 180);
    client.jobTitle = clean(sessionStorage.getItem("jobTitle"), 240);
    sessionStorage.setItem(storageKey(domain), JSON.stringify(client));
    state.pickerOpen.delete(domain);
    renderAll();
    notify(client, "attached");
    return client;
  }

  function clearAttachedClient(options = {}) {
    const domain = options.domain || currentDomain();
    sessionStorage.removeItem(storageKey(domain));
    state.pickerOpen.delete(domain);
    renderAll();
    notify(null, "removed");
  }

  function readLocalAtlasRecords(domain) {
    try {
      const parsed = JSON.parse(localStorage.getItem(localCrmStorageKey(domain)) || "[]");
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  function mergeRecords(serverRecords, localRecords, domain) {
    const merged = new Map();
    [...serverRecords, ...localRecords].forEach((record) => {
      if (!record || typeof record !== "object" || record.archived === true) return;
      const recordDomain = clean(record.domain || domain, 40);
      if (recordDomain !== domain) return;
      const normalized = normalizeRecord(record, domain);
      if (!normalized.id || !normalized.name) return;
      merged.set(normalized.id, normalized);
    });
    return [...merged.values()].sort((a, b) => a.name.localeCompare(b.name));
  }

  async function loadRecords(force = false) {
    const domain = currentDomain();
    if (!force && state.recordsByDomain.has(domain)) return state.recordsByDomain.get(domain);
    let serverRecords = [];
    try {
      const response = await fetch(`/api/crm/records?domain=${encodeURIComponent(domain)}&limit=1000`, { cache: "no-store" });
      if (!response.ok) throw new Error(await response.text());
      const data = await response.json();
      serverRecords = Array.isArray(data.records) ? data.records : [];
    } catch (error) {
      console.warn("Atlas clients could not be loaded from the server.", error);
    }
    const records = mergeRecords(serverRecords, readLocalAtlasRecords(domain), domain);
    state.recordsByDomain.set(domain, records);
    return records;
  }

  function clientMeta(client) {
    return [
      client.contactName,
      client.contactTitle,
      client.contactEmail,
      client.contactPhone,
    ].filter(Boolean).join(" - ");
  }

  function mismatchMessage(client) {
    const jobCompany = clean(sessionStorage.getItem("jobCompany"), 240);
    if (!jobCompany || !client || !client.name) return "";
    const a = jobCompany.toLowerCase();
    const b = client.name.toLowerCase();
    if (a === b || a.includes(b) || b.includes(a)) return "";
    return `The loaded JD company is ${jobCompany}, while this process is attached to ${client.name}. Confirm that this is intentional.`;
  }

  function injectStyles() {
    if (document.getElementById("atlasClientContextStyles")) return;
    const style = document.createElement("style");
    style.id = "atlasClientContextStyles";
    style.textContent = `
      .atlas-client-card { margin: 0 0 18px; border-left: 4px solid #d6a93f; }
      .atlas-client-head, .atlas-client-summary, .atlas-client-actions, .atlas-client-picker-row { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
      .atlas-client-head { justify-content:space-between; align-items:flex-start; }
      .atlas-client-head h2 { margin:0 0 4px; }
      .atlas-client-head p { margin:0; color:var(--muted); }
      .atlas-client-summary { margin-top:12px; padding:12px; border:1px solid var(--line); border-radius:10px; background:#fbfcfb; justify-content:space-between; }
      .atlas-client-summary strong, .atlas-client-summary span { display:block; }
      .atlas-client-summary span { margin-top:3px; color:var(--muted); font-size:12px; }
      .atlas-client-actions { justify-content:flex-end; }
      .atlas-client-picker { margin-top:12px; padding-top:12px; border-top:1px solid var(--line); }
      .atlas-client-picker-row input { flex:1 1 220px; min-width:180px; }
      .atlas-client-picker-row select { flex:2 1 320px; min-width:220px; }
      .atlas-client-warning { margin-top:10px; color:#8a4d12; background:#fff7e8; border:1px solid #ecc98a; border-radius:9px; padding:9px 10px; font-size:12px; }
      .atlas-client-empty { margin-top:10px; color:var(--muted); font-size:13px; }
      .atlas-client-check { color:#217a43; font-weight:800; }
      @media (max-width:700px) { .atlas-client-actions, .atlas-client-picker-row { align-items:stretch; flex-direction:column; } .atlas-client-actions .btn, .atlas-client-picker-row .btn { width:100%; text-align:center; } }
    `;
    document.head.appendChild(style);
  }

  function pickerHtml(records, query = "") {
    const normalizedQuery = clean(query, 240).toLowerCase();
    const filtered = records.filter((client) => [
      client.name,
      client.contactName,
      client.contactEmail,
      client.owner,
      client.dealStage,
    ].join(" ").toLowerCase().includes(normalizedQuery));
    const options = filtered.slice(0, 300).map((client) => {
      const detail = [client.contactName, client.dealStage].filter(Boolean).join(" - ");
      return `<option value="${escapeHtml(client.id)}">${escapeHtml(client.name)}${detail ? ` - ${escapeHtml(detail)}` : ""}</option>`;
    }).join("");
    return {
      count: filtered.length,
      html: options || '<option value="">No matching Atlas client</option>',
    };
  }

  function renderHost(host, records = state.recordsByDomain.get(currentDomain()) || []) {
    const domain = currentDomain();
    const client = getAttachedClient(domain);
    const pickerOpen = !client || state.pickerOpen.has(domain);
    const warning = mismatchMessage(client);
    const picker = pickerHtml(records);
    host.innerHTML = `
      <section class="card atlas-client-card" aria-label="Atlas client attached to sourcing process">
        <div class="atlas-client-head">
          <div>
            <h2>${client ? '<span class="atlas-client-check">&#10003;</span> ' : ""}Client for this sourcing process</h2>
            <p>Attach one ${escapeHtml(domainLabel(domain))} Atlas record so client, contact, and relationship context follow Find Talent, shortlist communication, and meeting invites.</p>
          </div>
          <a class="btn secondary" href="${escapeHtml(atlasUrl(client, domain))}">${client ? "Open Atlas record" : "Open Atlas"}</a>
        </div>
        ${client ? `
          <div class="atlas-client-summary">
            <div>
              <strong>${escapeHtml(client.name)}</strong>
              <span>${escapeHtml(clientMeta(client) || "No primary contact details captured in Atlas")}</span>
              <span>${escapeHtml([client.dealStage, client.owner ? `Owner: ${client.owner}` : ""].filter(Boolean).join(" - ") || "Atlas client attached")}</span>
            </div>
            <div class="atlas-client-actions">
              <button class="btn secondary" type="button" data-atlas-change>${pickerOpen ? "Cancel change" : "Change client"}</button>
              <button class="btn secondary" type="button" data-atlas-remove>Remove link</button>
            </div>
          </div>
          ${warning ? `<div class="atlas-client-warning"><strong>Check client/JD alignment:</strong> ${escapeHtml(warning)}</div>` : ""}
        ` : '<div class="atlas-client-empty">No Atlas client is attached yet.</div>'}
        <div class="atlas-client-picker" ${pickerOpen ? "" : "hidden"}>
          <div class="atlas-client-picker-row">
            <input type="search" data-atlas-search placeholder="Search Atlas by client, contact, email, owner, or stage" aria-label="Search Atlas clients" />
            <select data-atlas-select aria-label="Choose Atlas client">${picker.html}</select>
            <button class="btn primary" type="button" data-atlas-attach ${records.length ? "" : "disabled"}>Attach Atlas client</button>
          </div>
          <div class="atlas-client-empty" data-atlas-count>${records.length ? `${picker.count} Atlas client record${picker.count === 1 ? "" : "s"} available in this domain.` : "No Atlas client records were found in this domain. Add the client in Atlas first."}</div>
        </div>
      </section>
    `;

    host.querySelector("[data-atlas-change]")?.addEventListener("click", () => {
      if (state.pickerOpen.has(domain)) state.pickerOpen.delete(domain);
      else state.pickerOpen.add(domain);
      renderAll();
    });
    host.querySelector("[data-atlas-remove]")?.addEventListener("click", () => {
      if (window.confirm("Remove the Atlas client from this sourcing process?")) clearAttachedClient();
    });
    const search = host.querySelector("[data-atlas-search]");
    const select = host.querySelector("[data-atlas-select]");
    const count = host.querySelector("[data-atlas-count]");
    search?.addEventListener("input", () => {
      const filtered = pickerHtml(records, search.value);
      select.innerHTML = filtered.html;
      count.textContent = `${filtered.count} matching Atlas client record${filtered.count === 1 ? "" : "s"}.`;
    });
    host.querySelector("[data-atlas-attach]")?.addEventListener("click", () => {
      const selected = records.find((row) => row.id === select.value);
      if (!selected) return window.alert("Choose an Atlas client record first.");
      attachClient(selected);
    });
  }

  function renderAll() {
    injectStyles();
    document.querySelectorAll("[data-atlas-client-context]").forEach((host) => renderHost(host));
  }

  async function mount() {
    injectStyles();
    renderAll();
    await loadRecords();
    renderAll();
  }

  window.DevReadyAtlasClient = {
    atlasUrl,
    attachClient,
    clear: clearAttachedClient,
    currentDomain,
    getAttachedClient,
    loadRecords,
    normalizeRecord,
    refresh: renderAll,
    mount,
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
