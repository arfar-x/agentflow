// Browser half of the end-to-end run: `node ui.mjs <phase> [arg]`.
//
// Each phase drives the real console in Chromium the way an operator would and
// exits non-zero on the first thing that is not as expected. Everything it sees
// from /api is appended to $E2E_OUT/responses.jsonl so run.py can prove no
// response ever carried a credential. State between phases lives in the stack
// itself (database, files), never in this script.
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';

const require = createRequire(path.resolve(process.env.E2E_PLAYWRIGHT_FROM ?? '.') + '/');
const { chromium } = require('playwright');

const URL = process.env.E2E_URL ?? 'http://127.0.0.1:8324';
const TOKEN = process.env.E2E_TOKEN;
const OUT = process.env.E2E_OUT ?? '.';
const CHROMIUM = process.env.E2E_CHROMIUM ?? '/opt/pw-browsers/chromium';
const [phase, arg] = process.argv.slice(2);

const browser = await chromium.launch({ executablePath: CHROMIUM, args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1360, height: 1100 } });
const problems = [];
page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
page.on('console', (m) => m.type() === 'error' && !/status of (401|403|409|404|400|429)/.test(m.text()) && problems.push(`console: ${m.text()}`));
page.on('response', async (response) => {
  if (!response.url().includes('/api/')) return;
  try {
    fs.appendFileSync(path.join(OUT, 'responses.jsonl'), JSON.stringify({ url: response.url(), status: response.status(), body: await response.text() }) + '\n');
  } catch { /* a navigation raced the read */ }
});

const shot = (name) => page.screenshot({ path: path.join(OUT, `${name}.png`) });
const say = (...args) => console.log(`[${phase}]`, ...args);
const expect = (condition, message) => {
  if (!condition) throw new Error(`expected: ${message}`);
  say('ok -', message);
};

async function signIn() {
  await page.goto(URL + '/');
  await page.fill('#token', TOKEN);
  await page.click('button[type=submit]');
  await page.waitForSelector('nav');
}
const go = (name) => page.click(`nav >> text=${name}`);

async function approve(finalButton = 'Write the change') {
  await page.waitForSelector('text=Review before anything is written');
  const write = page.getByRole('button', { name: finalButton });
  expect(await write.isDisabled(), 'writing is blocked until the warnings are acknowledged');
  await page.check('text=I have read what this does');
  await write.click();
}

const phases = {
  async login() {
    await page.goto(URL + '/');
    await page.fill('#token', 'x'.repeat(40));
    await page.click('button[type=submit]');
    await page.waitForSelector('[role=alert]');
    expect((await page.textContent('[role=alert]')).includes('not the admin token'), 'a wrong token is refused');
    await page.fill('#token', TOKEN);
    await page.click('button[type=submit]');
    await page.waitForSelector('nav');
    await page.waitForSelector('text=Scheduler reported', { timeout: 20000 });
    expect(true, 'the right token signs in and the scheduler is seen alive');
    await shot('01-overview');
  },

  async add_source() {
    await signIn();
    await go('Sources');
    await page.click('text=Add a source');
    await page.selectOption('#src-kind', 'confluence');
    await page.fill('#src-id', 'eng-wiki');
    await page.fill('#spaces', 'ENG');
    // new sources start disabled: nothing is read until an operator says so
    await page.click('text=Review the change');
    await approve();
    await page.waitForSelector('article >> text=eng-wiki');
    await shot('02-source-added');
    expect(true, 'the source card appears after the file is written');
  },

  async enable_store() {
    await signIn();
    await go('Sources');
    await page.click('[aria-label="Edit eng-wiki"]');
    await page.click('[role=switch][aria-label="Enabled"]');
    await page.click('[role=switch][aria-label="Keep each document\'s text"]');
    await page.click('text=Review the change');
    await page.waitForSelector('text=every user of the catalog');
    await shot('03-store-warning');
    expect(true, 'keeping text warns that every catalog user can read it');
    await approve();
    await page.waitForSelector('text=Written to kb-sources.yaml');
  },

  async trigger() {
    // arg: "dry" | "full" | "incremental"
    await signIn();
    await page.click('button:has-text("Trigger sync")');
    await page.selectOption('#sync-source', 'eng-wiki');
    if (arg !== 'incremental') await page.locator('label', { hasText: 'Looks at everything' }).click();
    if (arg === 'dry') await page.getByLabel(/Dry run/).check();
    await page.getByRole('button', { name: arg === 'dry' ? /queue a dry run/i : /queue the sync/i }).click();
    await page.waitForSelector('text=Finished.', { timeout: 60000 });
    await shot(`04-sync-${arg}`);
    say('result:', (await page.textContent('[role=dialog] ul')).replace(/\s+/g, ' ').slice(0, 160));
  },

  async reports() {
    await signIn();
    await go('Reports');
    await page.waitForSelector('tr:has-text("eng-wiki")');
    const rows = await page.locator('tr:has-text("eng-wiki")').count();
    expect(rows >= 2, `the dry run and the full sync are both listed (${rows} rows for eng-wiki)`);
    await shot('05-reports');
  },

  async issues() {
    // arg: text that must be on the Issues screen
    await signIn();
    await go('Issues');
    await page.waitForSelector(`text=${arg}`, { timeout: 20000 });
    await shot('issues');
    expect(true, `Issues shows "${arg}"`);
  },

  async forms_create() {
    await signIn();
    await go('Issues');
    await page.click('li:has-text("release process") >> text=Write a record');
    await page.waitForSelector('#form-title');
    expect((await page.inputValue('#form-title')) === 'release process', 'an unanswered search seeds the title');
    await page.fill('#form-title', 'Release process');
    await page.fill('[aria-label=Text]', '# Release process\n\nReleases go out on **Tuesdays** after the freeze.\n');
    await page.click('button:has-text("Generate summary")');
    await page.waitForSelector('text=Drafted by the model');
    await page.click('[role=tab]:has-text("fa")');
    expect((await page.getAttribute('#form-title', 'dir')) === 'rtl', 'Persian is laid out right to left');
    expect((await page.inputValue('#form-summary')).includes('انتشار'), 'the draft carries a Persian summary');
    await shot('07-form-editor');
    await page.click('button:has-text("Save and publish")');
    await page.waitForSelector('tr:has-text("release-process"):has-text("published")', { timeout: 60000 });
    await shot('07-form-published');
    expect(true, 'the record is published by the scheduler');
    await page.click('[aria-label="Read release-process as an agent"]');
    await page.waitForSelector('pre');
    expect((await page.textContent('pre')).includes('Tuesdays'), 'the inspector shows what kb_get returns');
  },

  async forms_edit_summary() {
    await signIn();
    await go('Form records');
    await page.click('[aria-label="Edit release-process"]');
    await page.fill('#form-summary', 'Edited by hand: we ship on Tuesdays.');
    await page.click('button:has-text("Save changes")');
    await page.waitForSelector('tr:has-text("release-process"):has-text("rev 2"):has-text("published")', { timeout: 60000 });
    expect(true, 'a summary-only edit is published as revision 2');
  },

  async forms_delete() {
    await signIn();
    await go('Form records');
    await page.click('[aria-label="Delete release-process"]');
    await page.click('button:has-text("Delete the record")');
    await page.waitForSelector('text=No manual records yet');
    expect(true, 'the record is gone from the list');
  },

  async approved_off() {
    await signIn();
    await go('Sources');
    await page.click('button:has-text("Syncing on")');
    await approve();
    await page.waitForSelector('button:has-text("Syncing off")');
    await shot('08-syncing-off');
    expect(true, 'syncing is switched off from the console');
  },

  async delete_source() {
    await signIn();
    await go('Sources');
    await page.click('[aria-label="Delete eng-wiki"]');
    await page.waitForSelector('text=Also remove its');
    expect(await page.isChecked('input[type=checkbox] >> nth=0'), 'removing the entries is offered, and ticked');
    await approve();
    await page.waitForSelector('article:has-text("eng-wiki")', { state: 'detached', timeout: 20000 });
    await shot('09-source-deleted');
    expect(true, 'the source card is gone');
  },

  async history() {
    await signIn();
    await go('Sources');
    await page.click('button:has-text("History")');
    await page.waitForSelector('text=edited by hand');
    await shot('10-history');
    expect((await page.textContent('[role=dialog]')).includes('edited by hand'), 'a hand edit shows in the history');
  },
};

let failed = false;
try {
  if (!phases[phase]) throw new Error(`unknown phase ${phase}`);
  await phases[phase]();
  if (problems.length) throw new Error(`the browser reported errors:\n${problems.join('\n')}`);
} catch (error) {
  failed = true;
  console.error(`[${phase}] FAILED: ${error.message}`);
  await shot(`FAIL-${phase}`).catch(() => {});
}
await browser.close();
process.exit(failed ? 1 : 0);
