const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const pagePath = path.join(__dirname, '../ui/pages/profile-preview.html');
const page = fs.readFileSync(pagePath, 'utf8');
const scripts = [...page.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map((match) => match[1]);
scripts.forEach((script) => new vm.Script(script, { filename: pagePath }));
const source = scripts.find((script) => script.includes('function renderProfileHeaderContacts'));

function header() {
  const strip = { innerHTML: '' };
  const context = vm.createContext({
    URL,
    document: {
      querySelectorAll: () => [],
      addEventListener: () => {},
      getElementById: (id) => id === 'profileContactStrip' ? strip : null,
    },
  });
  vm.runInContext(source, context);
  return {
    resolve: (data) => JSON.parse(JSON.stringify(context.profileHeaderContacts(data))),
    render: (data) => { context.renderProfileHeaderContacts(data); return strip.innerHTML; },
  };
}

test('internal profile uses saved phone, email and a scheme-less LinkedIn URL', () => {
  assert.deepEqual(header().resolve({ profile: {
    email: 'alex@example.test', phone: '+1 (202) 555-0123', linkedinUrl: 'www.linkedin.com/in/alex-example',
  } }), { email: 'alex@example.test', phone: '+1 (202) 555-0123', linkedin: 'https://www.linkedin.com/in/alex-example' });
});

test('TEMP profile contact fields work without canonical profile fields', () => {
  const result = header().render({ externalProfile: { contact: {
    primaryEmail: 'temp@example.test', primaryPhone: '+1 202 555 0198',
  }, profileUrl: 'https://linkedin.com/in/temp-example' } });
  assert.match(result, /href="mailto:temp%40example.test"/);
  assert.match(result, /href="tel:\+12025550198"/);
  assert.match(result, /href="https:\/\/linkedin.com\/in\/temp-example" target="_blank"/);
  assert.match(result, /> linkedin.com\/in\/temp-example<\/a>/);
});

test('invalid primary values do not mask valid object and string alternatives', () => {
  assert.deepEqual(header().resolve({ profile: { email: 'N/A', phone: 'Unknown' }, externalProfile: { contact: {
    primaryEmail: 'invalid', professionalEmails: [{ email: 'array@example.test' }],
    primaryPhone: '123', phoneNumbers: [{ number: '(202) 555-0134 ext. 42' }],
  } } }), { email: 'array@example.test', phone: '(202) 555-0134 ext. 42', linkedin: '' });
  assert.match(header().render({ profile: { phone: '(202) 555-0134 ext. 42' } }), /href="tel:2025550134;ext=42"/);
});

test('canonical candidate contacts take priority over provider contacts', () => {
  assert.equal(header().resolve({ profile: { email: 'saved@example.test' }, externalProfile: {
    contact: { primaryEmail: 'provider@example.test' },
  } }).email, 'saved@example.test');
});

test('switching candidates clears previous contacts and keeps all three labels', () => {
  const renderer = header();
  renderer.render({ profile: { email: 'previous@example.test', phone: '202-555-0123' } });
  const result = renderer.render({ profile: {} });
  assert.doesNotMatch(result, /previous|202-555|href=/);
  assert.equal((result.match(/Not available/g) || []).length, 3);
  for (const label of ['Phone', 'Email', 'LinkedIn']) assert.ok(result.includes(`>${label}</span>`));
});

test('unsafe, unrelated and company URLs do not become candidate LinkedIn links', () => {
  for (const linkedinUrl of ['javascript:alert(1)', 'https://linkedin.com.attacker.test/in/x',
    'https://linkedin.com@attacker.test/in/x', 'https://user:password@linkedin.com/in/x',
    'https://linkedin.com/company/company-example']) {
    assert.equal(header().resolve({ profile: { linkedinUrl } }).linkedin, '');
  }
});

// Optional generated visual fixture: uses the actual page CSS, identity markup,
// and renderer with synthetic contacts. No candidate or account data is copied.
if (process.env.PROFILE_CONTACT_PREVIEW_DIR) {
  const target = process.env.PROFILE_CONTACT_PREVIEW_DIR;
  fs.mkdirSync(target, { recursive: true });
  const styles = page.match(/<style>([\s\S]*?)<\/style>/)[1];
  const start = page.indexOf('<div class="profile-title-row">');
  const end = page.indexOf('id="profileSubHeader"', start);
  const rawHeader = page.slice(start, page.lastIndexOf('<div', end));
  const contacts = header().render({ profile: {
    email: 'alex.morgan@example.test', phone: '+1 (202) 555-0123',
    linkedinUrl: 'www.linkedin.com/in/alex-morgan-example',
  } });
  const markup = rawHeader.replace('No Candidate Loaded', 'Alex Morgan')
    .replace(/(<div id="profileContactStrip"[^>]*>)[\s\S]*?<\/div>/, `$1${contacts}</div>`);
  for (const domain of ['dev', 'law', 'engineer', 'dental']) {
    const theme = fs.readFileSync(path.join(__dirname, `../ui/assets/${domain}Styles.css`), 'utf8');
    fs.writeFileSync(path.join(target, `${domain}.html`), `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>${theme}\n${styles}</style></head><body><main class="main"><p>Visual test - synthetic candidate</p><section class="card"><div class="profile-overview"><div class="imageBox">AM</div><div class="profile-identity">${markup}<p>Software engineer</p></div><aside class="profile-action-panel">Profile actions</aside></div></section></main></body></html>`);
  }
}
