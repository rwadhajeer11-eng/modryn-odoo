// Acts 5 and 6 — the staff surfaces, and the one that arrives over a socket.
const { test, expect } = require('@playwright/test');
const { submitFormWith } = require('../lib/form.js');

const { requirePeople } = require('../lib/people.js');
const { qaPhone } = require('../lib/otp.js');

const PASSWORD = process.env.MODRYN_DEMO_PASSWORD;
// dbfilter = ^%d$ takes the first hostname label as the database name.
const TENANT = new URL(process.env.BASE_URL || 'http://bella.localtest.me:8069').hostname.split('.')[0];
const PEOPLE = requirePeople(TENANT);

test.beforeAll(() => {
  // Fail here rather than at the first 303. An unset password produces a
  // session-less jar and every authenticated page then redirects, which reads
  // as four broken routes instead of one missing environment variable — the
  // same trap main.js documents for the k6 harness.
  if (!PASSWORD) throw new Error('MODRYN_DEMO_PASSWORD is unset — export the password the tenant was seeded with');
});

async function signIn(page, login) {
  await page.goto('/staff/login');
  await page.fill('input[name="username"]', login);
  await page.fill('input[name="password"]', PASSWORD);
  await submitFormWith(page, 'username');
}

test('act 5 — a manager lands on the floor board, and it paints', async ({ page }) => {
  await signIn(page, PEOPLE.manager);

  // Landing on /floor rather than the back office is a product decision, not an
  // accident of Odoo's default redirect.
  await expect(page).toHaveURL(/\/floor/);

  // THE ASSERTION verify.sh §10a CANNOT MAKE. It proves /floor answers 200.
  // A 200 carrying a JS exception that leaves an empty div is exactly the shape
  // of the bug §10a was written after, and §10a still cannot see it.
  await expect(page.locator('body')).toBeVisible();
  const painted = await page.evaluate(() => document.body.innerText.trim().length);
  expect(painted, '/floor returned 200 and rendered nothing — a JS exception left the board empty').toBeGreaterThan(120);
});

test('act 5b — staff-level access stops where it should', async ({ page }) => {
  await signIn(page, PEOPLE.staff);   // staff-level, not a manager
  const res = await page.goto('/manage/staff');
  const status = res ? res.status() : 0;
  const url = page.url();
  expect(
    status === 403 || status === 404 || !/\/manage\/staff/.test(url),
    `a staff-level user reached /manage/staff (status ${status}, url ${url})`
  ).toBeTruthy();
});

test('act 6 — a walk-in appears on the board without a reload @writes', async ({ browser }) => {
  // Two contexts, deliberately: one watching, one acting. A single page that
  // navigated away and back would prove the QUERY works and say nothing at all
  // about the socket.
  const watcher = await browser.newContext({ locale: 'he-IL' });
  const walkin = await browser.newContext({ locale: 'he-IL' });

  try {
    const board = await watcher.newPage();
    await board.goto('/staff/login');
    await board.fill('input[name="username"]', PEOPLE.manager);
    await board.fill('input[name="password"]', PASSWORD);
    await submitFormWith(board, 'username');
    await expect(board).toHaveURL(/\/floor/);

    const before = await board.evaluate(() => document.body.innerText);

    const name = `QA Walkin ${Date.now() % 100000}`;
    const p = await walkin.newPage();
    await p.goto('/queue/checkin');
    await p.fill('input[name="name"]', name);
    // qaPhone(), not a constant. A hardcoded number makes this spec run
    // exactly once: the walk-in it created is still PENDING on the board next
    // time, the second check-in is refused as a duplicate, and the assertion
    // then fails with the board unchanged — which reads as "the websocket is
    // broken" when the socket was never given anything to deliver.
    await p.fill('input[name="phone"]', qaPhone());
    await submitFormWith(p, 'name');

    // NO RELOAD of the board. If this needs one, bus.bus is not reaching the
    // browser — and in production that means modryn-site.conf's
    // `location ^~ /websocket` lost one of the six proxy_set_header lines it
    // repeats, because nginx drops the inherited set when a level adds any of
    // its own. That break shows up ONLY on the floor board, ONLY in production,
    // and every HTTP check stays green through it.
    await expect(async () => {
      const now = await board.evaluate(() => document.body.innerText);
      expect(now).not.toBe(before);
      expect(now).toContain(name);
    }).toPass({ timeout: 15000 });
  } finally {
    await watcher.close();
    await walkin.close();
  }
});
