// The work schedule — the one surface with no browser coverage at all until now.
//
// This matters more here than almost anywhere else in the suite. verify.sh can
// prove /roster answers 200 and can count cell markers in the HTML, but the
// grid is a thing you PRESS: the tap is a jsonrpc call, the answer repaints the
// cell in place without a reload, and none of that is visible to curl. Before
// this file existed, CLAUDE.md's browser gate came back green whether the page
// rendered twenty-one live cells, five dead cards, or a stack trace.
const { test, expect } = require('@playwright/test');
const { submitFormWith } = require('../lib/form.js');
const { requirePeople } = require('../lib/people.js');

const PASSWORD = process.env.MODRYN_DEMO_PASSWORD;
// dbfilter = ^%d$ takes the first hostname label as the database name.
const TENANT = new URL(process.env.BASE_URL || 'http://qa.localtest.me:8069').hostname.split('.')[0];
const PEOPLE = requirePeople(TENANT);

test.beforeAll(() => {
  if (!PASSWORD) throw new Error('MODRYN_DEMO_PASSWORD is unset — export the password the tenant was seeded with');
});

// Sign OUT first, every time. These specs change hands mid-test — a manager
// opens the window, then the worker presses a cell — and /staff/login redirects
// an already-authenticated visitor straight to her landing page. The username
// field then simply is not there, and the failure ("waiting for
// input[name=username]") reads as a broken login page rather than as a session
// that was never ended.
async function signIn(page, login) {
  await page.goto('/staff/logout');
  await page.goto('/staff/login');
  await page.fill('input[name="username"]', login);
  await page.fill('input[name="password"]', PASSWORD);
  await submitFormWith(page, 'username');
}

// The window controls live inside a <details>, collapsed by default — it is a
// setting a manager changes rarely, not something that should take up the top
// of the page every day. Playwright will not fill an input it cannot see, and
// the failure ("element is not visible") points at the input rather than at the
// closed fold above it.
async function openWindowPanel(page) {
  const panel = page.locator('details:has(#open_weekday)');
  if (!(await panel.getAttribute('open'))) {
    await panel.locator('summary').click();
  }
  await expect(page.locator('#open_weekday')).toBeVisible();
}

// The submission window is a GLOBAL setting for the tenant, and the specs below
// change it. Restoring it inline at the end of a test is not enough: a test that
// fails on its way there never reaches the restore, and the tenant is then left
// configured by a failure — which the next run measures as if it were the real
// setting. That is exactly how this tenant ended up on "Monday 09:00" with
// nothing to say it had happened.
//
// afterAll runs whatever happened, so the reset is unconditional.
const SHIPPED_RULE = { open_weekday: '3', open_time: '09:00',
                       close_weekday: '5', close_time: '21:00' };

test.afterAll(async ({ browser }) => {
  const page = await browser.newPage();
  try {
    await signIn(page, PEOPLE.manager);
    await page.goto('/roster?week=0');
    await openWindowPanel(page);
    await page.selectOption('#open_weekday', SHIPPED_RULE.open_weekday);
    await page.fill('#open_time', SHIPPED_RULE.open_time);
    await page.selectOption('#close_weekday', SHIPPED_RULE.close_weekday);
    await page.fill('#close_time', SHIPPED_RULE.close_time);
    await Promise.all([page.waitForURL(/\/roster/), submitFormWith(page, 'open_weekday')]);
  } finally {
    await page.close();
  }
});

// The week AFTER the one being planned. Deliberately not week=0: a tenant that
// has already published next week freezes it, and a frozen grid is disabled —
// which would look exactly like the feature being broken.
const WEEK = '/roster?week=1';

test('the week is seven days by three, always', async ({ page }) => {
  await signIn(page, PEOPLE.staff);
  await page.goto(WEEK);

  // Twenty-one, not "at least five". The whole point of the change is that a
  // cell exists because Friday evening exists, not because the boutique has
  // already invented a Friday evening shift — so a count that passes at 5 and
  // at 21 (which is what every pre-existing check does) tests nothing.
  await expect(page.locator('.modryn_avail_cell')).toHaveCount(21);
  await expect(page.locator('.modryn_avail_table thead th')).toHaveCount(8); // 7 days + the corner

  // Sunday first. In Hebrew and Arabic the page is RTL, so "first" means the
  // RIGHTMOST column — which is what the owner asked for and what falls out of
  // the markup by itself, with no direction set anywhere in the stylesheet.
  const firstDay = page.locator('.modryn_avail_table thead th').nth(1);
  await expect(firstDay).toContainText(/Sunday|ראשון|الأحد/);

  // Three rows, in the order the day happens.
  await expect(page.locator('.modryn_avail_table tbody tr')).toHaveCount(3);

  // The note and the Send button live UNDER the table — the order the work
  // actually happens in: press what you can work, then say anything else,
  // then send.
  const tableBottom = await page.locator('.modryn_avail_table').boundingBox();
  const sendBox = await page.locator('#modryn_send_week').boundingBox();
  expect(sendBox.y).toBeGreaterThan(tableBottom.y);
});

test('@writes a cell remembers being pressed, and un-pressed', async ({ page }) => {
  // The manager opens the window for this week first. Without it the cells are
  // legitimately disabled and the test below would be asserting on a deadline
  // rather than on the grid.
  await signIn(page, PEOPLE.manager);
  await page.goto(WEEK);
  await openWindowPanel(page);
  const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  const soon = new Date(Date.now() + 2 * 86400000).toISOString().slice(0, 10);
  await page.fill('input[name="opens_date"]', yesterday);
  await page.fill('input[name="opens_time"]', '00:00');
  await page.fill('input[name="closes_date"]', soon);
  await page.fill('input[name="closes_time"]', '23:00');
  await submitFormWith(page, 'opens_date');
  await expect(page.locator('.modryn_panel').first()).toContainText(/Open|פתוח|مفتوح/);

  await signIn(page, PEOPLE.staff);
  await page.goto(WEEK);

  // Friday evening: the corner of the week this boutique has never had a shift
  // in, which is precisely why it has to be pressable.
  const cell = page.locator('.modryn_avail_cell[data-type="night"]').nth(5);
  await expect(cell).toBeEnabled();
  await expect(cell).not.toHaveClass(/is_on/);

  await cell.click();
  // Repainted in place, with NO reload — the old page reloaded on every single
  // tap, which for twenty-one cells is twenty-one full round trips.
  await expect(cell).toHaveClass(/is_on/);
  await expect(cell).toHaveAttribute('aria-pressed', 'true');

  // It survives leaving the page, which is the only proof it was ever stored.
  await page.reload();
  const again = page.locator('.modryn_avail_cell[data-type="night"]').nth(5);
  await expect(again).toHaveClass(/is_on/);

  // And pressing it again takes it back.
  await again.click();
  await expect(again).not.toHaveClass(/is_on/);
  await page.reload();
  await expect(page.locator('.modryn_avail_cell[data-type="night"]').nth(5))
    .not.toHaveClass(/is_on/);

  // Put the tenant back on its usual window, and wait for it.
  await signIn(page, PEOPLE.manager);
  await page.goto(WEEK);
  await openWindowPanel(page);
  await Promise.all([
    page.waitForURL(/\/roster/),
    page.locator('button[name="clear"]').click(),
  ]);
});

test('@writes the manager can say when the team may answer', async ({ page }) => {
  await signIn(page, PEOPLE.manager);
  await page.goto(WEEK);
  await openWindowPanel(page);

  // The times shown must be the boutique's WALL CLOCK. They used to be printed
  // straight out of UTC, so the shipped Saturday 21:00 deadline read as 18:00 —
  // and a manager who types 21:00 and is shown 18:00 concludes it did not save.
  await expect(page.locator('#open_time')).toHaveValue('09:00');
  await expect(page.locator('#close_time')).toHaveValue('21:00');

  await page.selectOption('#open_weekday', '2');
  await page.fill('#open_time', '08:30');
  await submitFormWith(page, 'open_weekday');

  await page.goto(WEEK);
  await openWindowPanel(page);
  await expect(page.locator('#open_weekday')).toHaveValue('2');
  await expect(page.locator('#open_time')).toHaveValue('08:30');

  // Put it back — and WAIT for it to land. This restore used to fire and the
  // test then ended, tearing down the context with the POST still in flight, so
  // the tenant kept whatever the test had set. A spec that permanently
  // reconfigures the thing it is testing is worse than no spec: the next run
  // measures the last run's leftovers.
  await page.selectOption('#open_weekday', '3');
  await page.fill('#open_time', '09:00');
  await Promise.all([
    page.waitForURL(/\/roster/),
    submitFormWith(page, 'open_weekday'),
  ]);
  await page.goto(WEEK);
  await openWindowPanel(page);
  await expect(page.locator('#open_weekday')).toHaveValue('3');
  await expect(page.locator('#open_time')).toHaveValue('09:00');
});

// The deadline, from the chair. Everything below is about what she SEES when a
// week is not hers to change — reported as "it doesn't work", which is what a
// grid of twenty-one buttons that silently refuse every press looks like.
test('@writes the deadline passing locks the grid, and she can still read her own answer', async ({ page }) => {
  const day = (n) => new Date(Date.now() + n * 86400000).toISOString().slice(0, 10);

  const setWindow = async (fromDay, fromTime, toDay, toTime) => {
    await page.goto(WEEK);
    await openWindowPanel(page);
    await page.fill('input[name="opens_date"]', fromDay);
    await page.fill('input[name="opens_time"]', fromTime);
    await page.fill('input[name="closes_date"]', toDay);
    await page.fill('input[name="closes_time"]', toTime);
    await Promise.all([page.waitForURL(/\/roster/), submitFormWith(page, 'opens_date')]);
  };

  await signIn(page, PEOPLE.manager);
  await setWindow(day(-1), '00:00', day(2), '23:00');

  // She offers one shift while the window is open. The cell is only pressed if
  // it is not already on: this tenant is shared, and asserting on a COUNT of
  // gold cells across the week would fail the moment another act - or a hand
  // poking at the page - left one behind. What matters is that THIS cell is
  // hers and survives the lock.
  await signIn(page, PEOPLE.staff);
  await page.goto(WEEK);
  const grid = page.locator('.modryn_roster_grid');
  await expect(grid).toHaveAttribute('data-can-edit', '1');
  const cell = page.locator('.modryn_avail_cell[data-type="middle"]').nth(2);
  const wasAlreadyOn = (await cell.getAttribute('class')).includes('is_on');
  if (!wasAlreadyOn) {
    await cell.click();
  }
  await expect(cell).toHaveClass(/is_on/);

  // The manager moves the deadline into the past.
  await signIn(page, PEOPLE.manager);
  await setWindow(day(-3), '00:00', day(-1), '23:00');

  await signIn(page, PEOPLE.staff);
  await page.goto(WEEK);

  // Every cell is now dead, and the grid says so at the frame as well.
  await expect(grid).toHaveAttribute('data-can-edit', '0');
  await expect(grid).toHaveClass(/is_locked/);
  const cells = page.locator('.modryn_avail_cell');
  const total = await cells.count();
  expect(total).toBe(21);
  for (const c of await cells.all()) {
    await expect(c).toBeDisabled();
  }
  await expect(page.locator('#modryn_send_week')).toBeDisabled();
  await expect(page.locator('#modryn_week_note')).toBeDisabled();

  // Locked means she cannot CHANGE it — never that she cannot SEE it. Her
  // offer survives as a tick AND is written out in words, because a grey grid
  // is not something anyone reads back off a phone.
  await expect(page.locator('.modryn_avail_cell[data-type="middle"]').nth(2))
    .toHaveClass(/is_on/);
  await expect(page.locator('.modryn_panel', { hasText: /What you offered|מה שהצעת|ما عرضتِه/ })
    .first()).toBeVisible();

  // Put the week back the way the other specs expect to find it: the window
  // open, and her offer withdrawn only if this act was the one that made it.
  // A restore that toggles unconditionally is a restore that BREAKS the state
  // it was meant to protect on every second run.
  await signIn(page, PEOPLE.manager);
  await setWindow(day(-1), '00:00', day(2), '23:00');
  await signIn(page, PEOPLE.staff);
  await page.goto(WEEK);
  const again = page.locator('.modryn_avail_cell[data-type="middle"]').nth(2);
  if (!wasAlreadyOn) {
    await again.click();
    await expect(again).not.toHaveClass(/is_on/);
  }
});

test('@writes the week she is standing in is never hers to fill', async ({ page }) => {
  await signIn(page, PEOPLE.staff);
  // week=-1 is the CURRENT week — the rota she is working right now.
  await page.goto('/roster?week=-1');
  const grid = page.locator('.modryn_roster_grid');
  await expect(grid).toHaveAttribute('data-can-edit', '0');
  await expect(grid).toHaveClass(/is_locked/);
  for (const c of await page.locator('.modryn_avail_cell').all()) {
    await expect(c).toBeDisabled();
  }

  // And the server refuses it too, not just the disabled attribute — a button
  // is only a suggestion, and this route is reachable without one.
  const refused = await page.evaluate(async () => {
    const res = await fetch('/roster/available', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', method: 'call', params: {
        day: document.querySelector('.modryn_avail_cell').dataset.day,
        shift_type: 'morning', week: -1 } }),
    });
    return (await res.json()).result;
  });
  expect(refused.error).toBe('past_week');
  // The refusal carries a sentence, not only a code: a press that answers with
  // nothing to read is indistinguishable from a press that did nothing at all.
  expect(refused.message, 'the refusal came back with no sentence to show her').toBeTruthy();
});
