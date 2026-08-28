// A saleswoman taking the customer standing in front of her.
//
// This is the one flow curl cannot prove. Taking a customer is a jsonrpc call
// that repaints the board in place, the button only exists for somebody the
// server agrees is on the card, and the whole thing is gated on a grant the
// owner makes in a different screen. Each of those can be right on its own and
// wrong together.
//
// The spec grants the board through the OWNER'S OWN SCREEN and takes the grant
// back at the end, rather than reaching into the database. Two reasons: it is
// the flow the owner actually performs, and act 5c in staff.spec.js asserts
// that the floor is NOT a staff page by default - a grant left behind would
// turn that spec red and read as a permission regression.
const { test, expect } = require('@playwright/test');
const { submitFormWith } = require('../lib/form.js');
const { requirePeople } = require('../lib/people.js');
const { startShift, endShift } = require('../lib/shift.js');

const PASSWORD = process.env.MODRYN_DEMO_PASSWORD;
const TENANT = new URL(process.env.BASE_URL || 'http://qa.localtest.me:8069').hostname.split('.')[0];
const PEOPLE = requirePeople(TENANT);

test.beforeAll(() => {
  if (!PASSWORD) throw new Error('MODRYN_DEMO_PASSWORD is unset');
});

async function signIn(page, login) {
  await page.goto('/staff/logout');
  await page.goto('/staff/login');
  await page.fill('input[name="username"]', login);
  await page.fill('input[name="password"]', PASSWORD);
  await submitFormWith(page, 'username');
}

test('@writes a worker can be given the board, and take a customer on it', async ({ page }) => {
  // ---- the owner grants the floor board to the seamstress role -------------
  await signIn(page, PEOPLE.owner);
  await page.goto('/manage/roles');

  // The grants are ONE form holding a role-by-page checkbox matrix, each box
  // named "pages" and valued "<roleId>:<pageKey>". So the row is the role and
  // the value suffix is the page.
  // Wait for the matrix itself before asking about a row in it. The first load
  // after a server restart recompiles the asset bundle, and the page can arrive
  // slower than the default 5s locator timeout - which shows up as "no role
  // carries the seeded atelier grant" and reads as broken seed data.
  await expect(page.locator('input[name="pages"]').first()).toBeAttached({ timeout: 30000 });

  const row = page.locator('tr:has(input[value$=":atelier"]:checked)').first();
  await expect(row, 'no role carries the seeded atelier grant').toHaveCount(1);
  const floorBox = row.locator('input[value$=":floor"]');
  await expect(floorBox).toHaveCount(1);

  const wasChecked = await floorBox.isChecked();
  if (!wasChecked) {
    await floorBox.check();
    await Promise.all([
      page.waitForURL(/\/manage\/roles/),
      page.getByRole('button', { name: /save page access|שמירת|حفظ/i }).click(),
    ]);
    await expect(
      page.locator('tr:has(input[value$=":atelier"]:checked) input[value$=":floor"]')
    ).toBeChecked();
  }

  try {
    // ---- the worker can now open the board ------------------------------
    await signIn(page, PEOPLE.staff);
    const res = await page.goto('/floor');
    expect(res.status()).toBe(200);
    // Through the door first. /floor answers 200 either way - the entry card is
    // a page, not a refusal - so the status alone no longer says the board is
    // there, which is exactly why the assertion below follows the press.
    await startShift(page);
    await expect(page.locator('owl-component[name="modryn_staff.floor_board"]')).toHaveCount(1);

    // The board PAINTS before anything can be pressed. modryn_panel is the
    // column wrapper the component renders whether or not the queue holds
    // anybody, so it is the honest "it rendered" signal - a card class would
    // only appear when somebody happens to be waiting.
    await expect(page.locator('.modryn_panel').first()).toBeVisible({ timeout: 20000 });

    // ---- and the taking buttons are hers, while the manager-only ones are not
    // Asserted, never skipped. An `if (count)` here would let the whole point
    // of this spec pass silently on a day the queue happened to be empty - and
    // it is exactly the shape of check that reports success over nothing.
    // Matched on a CLASS, not on the label. The label is translated, and this
    // spec failed the moment its Hebrew shipped: it looked for לקחת while the
    // button says אני לוקחת אותה. A test that reads translated prose breaks
    // every time a translator improves the wording, and reads as a broken
    // feature when it does.
    const takeButtons = page.locator('.modryn_take_btn');
    await expect(takeButtons.first()).toBeVisible({ timeout: 10000 });
    {
      await takeButtons.first().click();
      // The card repaints in place: the same card now offers the way back.
      await expect(
        page.locator('.modryn_release_btn').first()
      ).toBeVisible({ timeout: 10000 });

      // Put her back, so the tenant is left as it was found.
      await page.locator('.modryn_release_btn').first().click();
      await expect(takeButtons.first()).toBeVisible({ timeout: 10000 });
    }

    // Whatever the queue held, the manager-only actions must NOT be offered to
    // her - their routes refuse her, so a visible button would simply error.
    await expect(page.getByRole('button', { name: /^Invite to book$/i })).toHaveCount(0);
    await endShift(page);
  } finally {
    // ---- hand the grant back, whatever happened above -------------------
    // act 5c asserts the floor is not a staff page by default. Leaving this
    // ticked would turn that spec red and read as a permission regression
    // rather than as this test's litter.
    if (!wasChecked) {
      await signIn(page, PEOPLE.owner);
      await page.goto('/manage/roles');
      const box = page.locator(
        'tr:has(input[value$=":atelier"]:checked) input[value$=":floor"]');
      if (await box.isChecked()) {
        await box.uncheck();
        await Promise.all([
          page.waitForURL(/\/manage\/roles/),
          page.getByRole('button', { name: /save page access|שמירת|حفظ/i }).click(),
        ]);
      }
    }
  }
});
