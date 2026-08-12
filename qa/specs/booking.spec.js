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
  // The LAST offered slot, not the first: act 3b already probed the first, and
  // two specs competing for one hour would make this file race itself.
  const chosen = values[values.length - 1];

  await page.locator('select[name="slot"]').selectOption(chosen);
  await page.fill('input[name="name"]', 'QA Bride');
  await page.fill('input[name="phone"]', qaPhone());
  await page.locator('input[name="terms"]').check();
  await submitFormWith(page, 'slot');

  await expect(page).toHaveURL(/\/book\/confirmed\//);

  // The assertion curl cannot make cheaply: the grid the browser is shown next
  // no longer offers the hour that was just sold.
  await page.goto('/book');
  const after = (await page.locator('select[name="slot"] option').evaluateAll((os) => os.map((o) => o.value))).filter(Boolean);
  expect(after, 'the sold slot is still on offer — the grid and the row disagree').not.toContain(chosen);
});
