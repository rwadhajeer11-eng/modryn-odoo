// How a walk-in's visit ends, and what that does to the rail.
//
// Three things curl cannot prove, and all three are new.
//
// The board could always say a customer was "being served" and never say by
// WHOM without reading every card; the panel that groups them under the woman
// holding them is derived entirely in the browser from state the board already
// had, so it exists only once the component has painted.
//
// The dress is chosen by typing rather than by scrolling a <select> of every
// published variant. The matching is a prefix, per word and per field, done
// locally - none of which a status code can see.
//
// And the ending is confirmed before it lands, because "she took this one"
// takes a gown off the rail and there is no way back from the shop floor. The
// last act proves the count actually moved, by reading the same dress's
// remaining stock out of the picker before and after.
//
// @writes, so: the throwaway qa tenant only. It finishes walk-ins, which is
// permanent - they do not come back to the queue.
const { test, expect } = require('@playwright/test');
const { submitFormWith } = require('../lib/form.js');
const { requirePeople } = require('../lib/people.js');
const { startShift, endShift } = require('../lib/shift.js');

const PASSWORD = process.env.MODRYN_DEMO_PASSWORD;
const TENANT = new URL(process.env.BASE_URL || 'http://qa.localtest.me:8069').hostname.split('.')[0];
const PEOPLE = requirePeople(TENANT);

// The panel's two buttons are found by CLASS, following the rule floor_take
// already records: a spec that reads translated prose goes red the moment a
// translator improves the wording, and reads as a broken feature when it does.
// It matters more here than there - this board is rendered in whichever
// language the viewer's frontend_lang cookie asks for, so the labels are not
// stable even within one tenant and one signed-in user.
const BACK = '.modryn_team_back';
const FINISHED = '.modryn_team_finish';

// The dialog's own buttons carry no class, so these are matched on text in the
// three languages the product ships. NOT anchored with ^...$: hasText tests the
// rendered text, which carries the whitespace the template indents with.
const TOOK_IT = /She took this one|היא לקחה את זו|أخذت هاي/i;
const NO_BUY = /She left without buying|היא יצאה בלי לקנות|طلعت بدون شراء/i;
const CLOSE = /Close|סגירה|إغلاق/i;

// Each act signs in, walks through the shift door, waits for an OWL board to
// paint and then finishes two or three customers through a dialog. The 30s
// default expires in the middle of that and reports whichever wait happened to
// be running as the fault.
test.describe.configure({ timeout: 120000 });

test.beforeAll(() => {
  if (!PASSWORD) throw new Error('MODRYN_DEMO_PASSWORD is unset');
});

// submitFormWith CLICKS and does not wait. Navigating straight afterwards races
// the POST that sets the session cookie, and the next page then answers
// /web/login?redirect=/floor - a signed-out board, which reads as the floor
// being broken. Waiting for the login page to go is the cheap, honest fix.
async function signIn(page, login) {
  await page.goto('/staff/logout');
  await page.goto('/staff/login');
  await page.fill('input[name="username"]', login);
  await page.fill('input[name="password"]', PASSWORD);
  await submitFormWith(page, 'username');
  await page.waitForURL((url) => !/\/staff\/login/.test(url.pathname), { timeout: 20000 });
}

// A search term taken from the shop's own catalogue rather than hardcoded: the
// seeded dress names differ per tenant and change whenever the seeder does, and
// a spec that knows a name is a spec that goes red for a reason unrelated to
// what it is testing.
async function searchTermFromShop(page) {
  await page.goto('/shop');
  // The product tile's own link, read off its title rather than its text: the
  // first /shop/ link on the page is the CART, and the tile that follows wraps
  // a photograph and has no text at all.
  const link = page.locator('a[href^="/shop/"][title]').first();
  await expect(link, 'the shop lists no published dress - nothing to search for')
    .toBeAttached({ timeout: 20000 });
  const name = ((await link.getAttribute('title')) || '').trim();
  const word = name.split(/\s+/).find((w) => w.length >= 2);
  expect(word, `no word of at least two letters in the dress name "${name}"`).toBeTruthy();
  return word.slice(0, 2);
}

// The remaining count the picker shows beside one hit, as a number - so the
// before/after comparison is arithmetic and not string equality. A size with
// none left shows words instead of a figure, which parses to 0 and is true.
async function stockOnHit(hit) {
  const text = (await hit.locator('.modryn_badge').innerText()).trim();
  const n = parseInt(text, 10);
  return Number.isNaN(n) ? 0 : n;
}

async function openBoard(page) {
  // startShift navigates to /floor itself; a goto of its own here only races it.
  await startShift(page);
  // The board PAINTS before anything can be pressed, and the first load after a
  // restart recompiles the asset bundle. Kept well under the test timeout so a
  // slow paint reports as a slow paint rather than as the whole test expiring.
  await expect(page.locator('.modryn_panel').first()).toBeVisible({ timeout: 25000 });
}

test('@writes a customer is listed under the woman holding her, and goes back to the line', async ({ page }) => {
  await signIn(page, PEOPLE.manager);
  await openBoard(page);

  const panel = page.locator('.modryn_with_team');
  const clients = panel.locator('.modryn_team_client');

  // Counted BEFORE the take, and then watched to GROW. Counting after the click
  // races the board the click returns: the panel is already on screen from
  // whoever is being served, so waiting for it to be visible returns at once
  // and the count read is the old one. That made the spec fail by exactly the
  // customer it had just added.
  const before = await clients.count();

  // Take somebody. Asserted rather than skipped: an empty queue would let this
  // whole spec report success over nothing.
  const take = page.locator('.modryn_take_btn');
  await expect(take.first(), 'nobody is waiting - the queue is empty')
    .toBeVisible({ timeout: 20000 });
  await take.first().click();

  // She now appears under her stylist, in a panel built from board state the
  // page already had rather than from a second request.
  await expect(clients, 'the customer just taken is not listed under anybody')
    .toHaveCount(before + 1, { timeout: 20000 });
  const group = panel.locator('.modryn_team_group').first();
  await expect(group).toBeVisible();

  // Back to the line, pressed from the panel. She leaves it, and the board
  // offers to take her again - the same fact said from the queue's side.
  await group.locator(BACK).first().click();
  await expect(clients).toHaveCount(before, { timeout: 20000 });
  await expect(take.first()).toBeVisible({ timeout: 20000 });

  await endShift(page);
});

test('@writes the dress is found by typing, confirmed, and comes off the rail', async ({ page }) => {
  const term = await searchTermFromShop(page);

  await signIn(page, PEOPLE.manager);
  await openBoard(page);

  const take = page.locator('.modryn_take_btn');
  const panel = page.locator('.modryn_with_team');
  const modal = page.locator('.modryn_modal');
  const hits = page.locator('.modryn_dress_hit');

  async function finishNext() {
    await expect(take.first(), 'nobody is waiting - the queue is empty')
      .toBeVisible({ timeout: 20000 });
    await take.first().click();
    await expect(panel).toBeVisible({ timeout: 20000 });
    await panel.locator('.modryn_team_group').first()
      .locator(FINISHED).first().click();
    await expect(modal).toBeVisible({ timeout: 20000 });
  }

  await finishNext();

  // ---- the floor at two characters ---------------------------------------
  const box = modal.locator('input[type="search"]');
  await box.fill(term.slice(0, 1));
  await expect(hits, 'one character already searches - the floor is gone')
    .toHaveCount(0);

  await box.fill(term);
  await expect(hits.first(), 'nothing matched the first word of a published dress')
    .toBeVisible({ timeout: 15000 });
  // Capped: a kind can match hundreds, and a list that long inside a dialog
  // puts the dialog's own buttons out of reach.
  expect(await hits.count()).toBeLessThanOrEqual(12);

  // The FULLEST size, not the first. Every run of this spec takes one off the
  // rail, so a tenant run against often enough empties whichever size happens
  // to sort first - and the spec would then report the feature broken when what
  // is broken is the shop's stock.
  const counts = await hits.evaluateAll((els) =>
    els.map((el, i) => ({
      i: i,
      variant: el.dataset.variant,
      n: parseInt((el.querySelector('.modryn_badge') || {}).textContent || '0', 10) || 0,
    })));
  const best = counts.reduce((a, b) => (b.n > a.n ? b : a), counts[0]);
  expect(best.n, 'every size of that dress has gone - reseed the tenant')
    .toBeGreaterThan(0);
  const stockBefore = best.n;
  const takenVariant = best.variant;
  await hits.nth(best.i).click();
  await expect(modal.locator('.modryn_dress_picked')).toBeVisible();

  // ---- it asks before it takes -------------------------------------------
  await modal.locator('button').filter({ hasText: TOOK_IT }).click();
  const confirm = modal.locator('.modryn_finish_confirm');
  await expect(confirm, 'it took the dress without asking').toBeVisible();
  // The sentence names the count it is about to change - both numbers, because
  // "are you sure?" over a thousand gowns tells nobody which one.
  await expect(confirm).toContainText(String(stockBefore));
  await expect(confirm).toContainText(String(stockBefore - 1));

  await confirm.locator('button').first().click();
  await expect(modal.locator('.modryn_outcome_done'), 'nothing was recorded')
    .toBeVisible({ timeout: 20000 });
  // The dialog stays open: the workshop is a second question, and closing it
  // the moment she answers the first would lose it.
  await expect(modal.locator('input[type="date"]')).toBeVisible();
  await modal.locator('button').filter({ hasText: CLOSE }).click();
  await expect(modal).toBeHidden({ timeout: 15000 });

  // ---- and the count actually moved --------------------------------------
  // Read back through the picker itself rather than out of the database: this
  // is the number the next stylist sees, which is the one that matters.
  await finishNext();
  await modal.locator('input[type="search"]').fill(term);
  const same = hits.filter({ has: page.locator(`[data-variant="${takenVariant}"]`) })
    .or(page.locator(`.modryn_dress_hit[data-variant="${takenVariant}"]`));
  await expect(same.first()).toBeVisible({ timeout: 15000 });
  expect(await stockOnHit(same.first()), 'the dress did not come off the rail')
    .toBe(stockBefore - 1);

  // ---- and the other ending takes nothing --------------------------------
  await modal.locator('button').filter({ hasText: NO_BUY }).click();
  const lost = modal.locator('.modryn_finish_confirm');
  await expect(lost, 'it recorded a lost sale without asking').toBeVisible();
  await lost.locator('button').first().click();
  await expect(modal.locator('.modryn_outcome_done')).toBeVisible({ timeout: 20000 });
  await modal.locator('button').filter({ hasText: CLOSE }).click();
  await expect(modal).toBeHidden({ timeout: 15000 });

  await finishNext();
  await modal.locator('input[type="search"]').fill(term);
  const still = page.locator(`.modryn_dress_hit[data-variant="${takenVariant}"]`);
  await expect(still.first()).toBeVisible({ timeout: 15000 });
  expect(await stockOnHit(still.first()),
    'a customer who bought nothing still moved the count').toBe(stockBefore - 1);
  await modal.locator('button').filter({ hasText: CLOSE }).click();

  await endShift(page);

  // ---- and the rail is left as it was found ------------------------------
  // A spec that proves a decrement necessarily causes one. Without this the
  // suite eats its own tenant a dress at a time and eventually reports the
  // feature broken when what it broke is the shop. Put back through the
  // owner's own catalogue screen, which is the way a real boutique would.
  await signIn(page, PEOPLE.owner);
  await page.goto('/manage/dresses');
  const form = page.locator(`form:has(input[name="variant_id"][value="${takenVariant}"])`);
  await expect(form, 'the dress is not on the catalogue page').toHaveCount(1);
  const box2 = form.locator('input[name="stock"]');
  await box2.fill(String(stockBefore));
  await form.locator('[data-role="ask"]').click();
  await expect(form.locator('[data-role="confirm"]')).toBeVisible();
  await Promise.all([
    page.waitForURL(/\/manage\/dresses/),
    form.locator('[data-role="yes"]').click(),
  ]);
  await expect(
    page.locator(`form:has(input[name="variant_id"][value="${takenVariant}"]) input[name="stock"]`)
  ).toHaveValue(String(stockBefore));
});
