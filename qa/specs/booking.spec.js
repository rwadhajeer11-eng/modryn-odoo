// Act 3 — the booking grid, from the browser's side of it.
//
// verify.sh can POST /book/submit and read the row back. What it cannot do is
// prove that THE GRID THE BROWSER WAS SHOWN is the grid the server accepts.
// That gap is the whole "slot offered, then refused" class the k6 harness had
// to build a four-way classifier for.
const { test, expect } = require('@playwright/test');
const { submitFormWith } = require('../lib/form.js');
const { qaPhone } = require('../lib/otp.js');

const SLOT_RE = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/;

test('act 3 — the grid offers real slots @writes', async ({ page }) => {
  await page.goto('/book');

  const slot = page.locator('select[name="slot"]');
  await expect(slot).toBeVisible();

  const values = (await slot.locator('option').evaluateAll((os) => os.map((o) => o.value)))
    .filter(Boolean);
  expect(values.length, '/book offered no slot at all — the availability engine returned an empty grid').toBeGreaterThan(0);
  for (const v of values.slice(0, 5)) expect(v).toMatch(SLOT_RE);
});

test('act 3b — terms are enforced server-side, and no row is created @writes', async ({ page }) => {
  await page.goto('/book');

  const slot = page.locator('select[name="slot"]');
  const values = (await slot.locator('option').evaluateAll((os) => os.map((o) => o.value))).filter(Boolean);
  test.skip(values.length === 0, 'no slot on offer');
  const chosen = values[0];

  await slot.selectOption(chosen);
  await page.fill('input[name="name"]', 'QA Bride');
  await page.fill('input[name="phone"]', qaPhone());
  // terms deliberately NOT ticked.
  await submitFormWith(page, 'slot');

  // Still on the form, not on a confirmation. Asserting on the URL rather than
  // on an error string: he_IL is the default language, so the English copy in
  // the source is not what ships, and keying on a sentence is how the k6
  // classifier broke.
  await expect(page).not.toHaveURL(/\/book\/confirmed\//);

  // And the slot is still on offer, which is the part that proves nothing was
  // written. A refusal that had quietly created a row would leave it gone.
  await page.goto('/book');
  const after = (await page.locator('select[name="slot"] option').evaluateAll((os) => os.map((o) => o.value))).filter(Boolean);
  expect(after, 'the slot disappeared after a REFUSED submit — a row was created anyway').toContain(chosen);
});

test('act 3c — a complete booking takes the slot off the grid @writes', async ({ page }) => {
  await page.goto('/book');

  const values = (await page.locator('select[name="slot"] option').evaluateAll((os) => os.map((o) => o.value))).filter(Boolean);
  test.skip(values.length === 0, 'no slot on offer');

  // NEVER TAKE THE LAST SEAT OF A DAY, and never simply the last slot on offer.
  //
  // This spec used to book values[values.length - 1] to stay clear of act 3b's
  // values[0]. The furthest day is also the emptiest, so on a fixture with one
  // slot left there it sold the day out — and a sold-out day correctly
  // disappears from /book, which made verify.sh §24 report
  //   "open days missing from the page: 26.08.2026"
  // on the very next run. The product was right; the check derives open days
  // from the rota rather than from remaining capacity, and the test had turned
  // a legitimate state into a red line in a suite that gates deploys.
  //
  // So: pick from a day that still has another slot after this one. The day
  // survives, §24 keeps its subject, and the assertion below is unchanged.
  const byDay = new Map();
  for (const v of values) {
    const day = v.slice(0, 10);
    byDay.set(day, [...(byDay.get(day) || []), v]);
  }
  const roomy = [...byDay.values()].find((slots) => slots.length >= 2);
  test.skip(!roomy, 'every open day has exactly one slot left — booking any of them would close a day');
  // Its last slot, so act 3b's values[0] is never the same hour.
  const chosen = roomy[roomy.length - 1];

  // HOW MANY SEATS THAT HOUR HAS IS THE BOUTIQUE'S DECISION, and it is made on
  // a screen built for making it. This act used to book once and assert the
  // hour was gone, which is only true where an hour seats ONE — so the day the
  // owner set a Sunday afternoon to five, a page doing correct arithmetic went
  // red. Booking until it is full asserts the same thing at any capacity: what
  // the grid offers and what the rows say agree.
  //
  // Bounded, because an unbounded "until it goes" is an infinite loop the first
  // time the counting genuinely breaks — which is the bug this act exists to
  // catch, and it should report it rather than hang.
  const SEATS_MAX = 12;
  let sold = 0;
  let after = [];
  while (sold < SEATS_MAX) {
    await page.locator('select[name="slot"]').selectOption(chosen);
    await page.fill('input[name="name"]', 'QA Bride');
    await page.fill('input[name="phone"]', qaPhone());
    // WHO IS COMING, when the boutique has written a list to choose from. The
    // list is per-boutique and may be empty, which is why this is conditional
    // rather than a fill: a shop that has not written one is not asked, and a
    // spec that always answered would be asserting a question that is not there.
    const who = page.locator('select[name="customer_kind"]');
    if (await who.count()) {
      const options = (await who.locator('option').evaluateAll(
        (os) => os.map((o) => o.value))).filter(Boolean);
      await who.selectOption(options[0]);
    }
    await page.locator('input[name="terms"]').check();
    await submitFormWith(page, 'slot');
    await expect(page).toHaveURL(/\/book\/confirmed\//);
    sold += 1;

    // The assertion curl cannot make cheaply: the grid the browser is shown
    // next has counted the seat that was just sold.
    await page.goto('/book');
    after = (await page.locator('select[name="slot"] option').evaluateAll((os) => os.map((o) => o.value))).filter(Boolean);
    if (!after.includes(chosen)) {
      break;
    }
  }
  expect(after, `the hour was still on offer after ${sold} bookings — the grid and the rows disagree`).not.toContain(chosen);
});
