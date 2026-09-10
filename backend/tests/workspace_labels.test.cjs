const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const pages = path.join(__dirname, '../ui/pages');
const expected = [['dev', 'Technology'], ['engineer', 'Engineer'], ['law', 'Law'], ['dental', 'Dental']];

for (const [page, handler] of [
  ['accounting', 'switchAccountingDomain()'], ['admin', 'changeAdminDomain(this.value)'],
  ['invoices', 'switchDomain()'], ['onboarding-admin', 'switchDomain()'],
  ['reports', 'switchReportsDomain()'], ['sales-crm', 'switchDomain()'],
  ['time-admin', 'switchTimeDomain()'],
]) {
  test(`${page} labels omit Domain without changing option values or switching handlers`, () => {
    const html = fs.readFileSync(path.join(pages, `${page}.html`), 'utf8');
    const id = page === 'admin' ? 'adminDomainSelect' : 'domainSelect';
    const select = html.match(new RegExp(`<select\\b[^>]*id="${id}"[^>]*>[\\s\\S]*?</select>`))[0];
    const options = [...select.matchAll(/<option value="([^"]+)"[^>]*>([^<]+)<\/option>/g)].map((m) => [m[1], m[2]]);
    assert.deepEqual(options, page === 'reports' ? [...expected, ['all', 'All workspaces']] : expected);
    assert.ok(select.includes(`onchange="${handler}"`));
  });
}

test('shared environment label omits the suffix while keeping stored domain keys', () => {
  const sidebar = fs.readFileSync(path.join(pages, 'components/sidebar.html'), 'utf8');
  assert.doesNotMatch(sidebar, /(?:Technology|Engineer|Law|Dental) Domain/);
  assert.ok(sidebar.includes('{ label: "Workspace", value: domainName }'));
  assert.ok(sidebar.includes('sessionStorage.getItem("domain")'));
});
