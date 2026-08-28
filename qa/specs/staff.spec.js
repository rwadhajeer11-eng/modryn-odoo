// Acts 5 and 6 — the staff surfaces, and the one that arrives over a socket.
const { test, expect } = require('@playwright/test');
const { submitFormWith } = require('../lib/form.js');

const { requirePeople } = require('../lib/people.js');
const { startShift, endShift } = require('../lib/shift.js');
const { qaPhone, readOtp } = require('../lib/otp.js');

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

  // The board is behind a door now: she starts her shift and the room appears.
  await startShift(page);

  // THE ASSERTION verify.sh §10a CANNOT MAKE. It proves /floor answers 200.
  // A 200 carrying a JS exception that leaves an empty div is exactly the shape
  // of the bug §10a was written after, and §10a still cannot see it.
  await expect(page.locator('body')).toBeVisible();
  // WAIT for the paint instead of sampling the instant after load: the board
  // is an OWL component that fetches before it renders, and over a WAN the
  // sample landed at 86 chars on a healthy server (SMOKE_REPORT.md, act 5).
  // The check's teeth are intact — a JS exception leaves the div empty
  // forever, and an empty div never crosses the threshold.
  await page.waitForFunction(
    () => document.body.innerText.trim().length > 120,
    undefined,
    { timeout: 15000 },
  ).catch(() => {});
  const painted = await page.evaluate(() => document.body.innerText.trim().length);
  expect(painted, '/floor returned 200 and rendered nothing — a JS exception left the board empty').toBeGreaterThan(120);

  // Off the floor again. Being on it is stored on hr.employee, so a spec that
  // clocks in and never out leaves somebody permanently on shift and the NEXT
  // run measures that as the starting state - the trap the roster's window
  // override already taught this suite once.
  await endShift(page);
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

test('act 5c — plain staff land on their own page, and the floor is not theirs by default', async ({ page }) => {
  await signIn(page, PEOPLE.staff);

  // The landing IS the product decision: her day, not the whole room.
  await expect(page).toHaveURL(/\/staff\/home/);
  const painted = await page.evaluate(() => document.body.innerText.trim().length);
  expect(painted, '/staff/home returned 200 and rendered nothing').toBeGreaterThan(60);

  // A direct URL to a page her role was not granted: the themed refusal
  // wearing the nav — never the board, and never a bare error page.
  const res = await page.goto('/floor');
  expect(res && res.status(), 'the floor board answered a staff member the matrix refuses').toBe(403);
  await expect(page.locator('owl-component[name="modryn_staff.floor_board"]')).toHaveCount(0);
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
    // The watcher has to be ON the floor, or there is no board to watch: /floor
    // shows the entry card until she starts her shift, and this act is about
    // what arrives over the socket once the room is open.
    await startShift(board);

    const before = await board.evaluate(() => document.body.innerText);

    const name = `QA Walkin ${Date.now() % 100000}`;
    const phone = qaPhone();
    const p = await walkin.newPage();
    await p.goto('/queue/checkin');
    await p.fill('input[name="name"]', name);
    // qaPhone(), not a constant. A hardcoded number makes this spec run
    // exactly once: the walk-in it created is still OPEN on the board next
    // time, the second check-in is refused as a duplicate, and the assertion
    // then fails with the board unchanged — which reads as "the websocket is
    // broken" when the socket was never given anything to deliver.
    await p.fill('input[name="phone"]', phone);
    await submitFormWith(p, 'name');

    // A code now stands between the form and the line, so submitting the form
    // creates NOTHING — asserting on the board straight after it would fail on
    // a perfectly healthy socket. readOtp recomputes the six digits from
    // database.secret exactly as scripts/verify.sh reverses a booking token;
    // the row only ever holds the HMAC.
    await expect(p).toHaveURL(/\/queue\/verify/);
    await p.fill('input[name="code"]', readOtp(TENANT, phone));
    await submitFormWith(p, 'code');

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
    await endShift(await watcher.newPage()).catch(() => {});
    await watcher.close();
    await walkin.close();
  }
});

test('act 6c — the workshop refuses a task without urgency, and takes one with it @writes', async ({ page }) => {
  // The contract the load test silently broke on once: priority and due date
  // are REQUIRED at the single creation door. Proven here over the real
  // route, with the manager's real session.
  await signIn(page, PEOPLE.manager);

  const rpc = async (params) => {
    const res = await page.request.post('/atelier/task/create', {
      data: { jsonrpc: '2.0', method: 'call', params },
    });
    return (await res.json()).result || {};
  };

  const name = `QA Alteration ${Date.now() % 100000}`;
  const bare = await rpc({ customer_name: name });
  expect(bare.error, 'a task without priority was accepted').toBe('missing_priority');

  const noDue = await rpc({ customer_name: name, priority: '2' });
  expect(noDue.error, 'a task without a due date was accepted').toBe('missing_due');

  const due = new Date(Date.now() + 3 * 864e5).toISOString().slice(0, 10);
  const made = await rpc({ customer_name: name, priority: '2', due_date: due });
  expect(made.ok, `the full create was refused: ${made.error}`).toBeTruthy();
  expect(made.task.priority).toBe('2');

  // And the dashboard shows it — in the queue or already on someone's rail,
  // whichever the pool allowed; both are the engine working.
  await page.goto('/atelier');
  await expect(page.getByText(name).first()).toBeVisible();
});

test('act 6b — the same number cannot hold two places, and she is told so once @writes', async ({ page }) => {
  // The whole flow twice with ONE number. The de-dupe used to be silent; now
  // the second pass must land on the SAME ticket wearing a one-shot notice.
  const phone = qaPhone();

  const checkIn = async (name) => {
    await page.goto('/queue/checkin');
    await page.fill('input[name="name"]', name);
    await page.fill('input[name="phone"]', phone);
    await submitFormWith(page, 'name');
    await expect(page).toHaveURL(/\/queue\/verify/);
    // Two codes on one number per run — within the 3-per-hour budget, and
    // qaPhone() is per-run-unique so consecutive runs never share a budget.
    await page.fill('input[name="code"]', readOtp(TENANT, phone));
    await submitFormWith(page, 'code');
    await expect(page).toHaveURL(/\/q\//);
    return page.url();
  };

  const first = await checkIn(`QA Walkin ${Date.now() % 100000}`);
  const second = await checkIn(`QA Walkin Again ${Date.now() % 100000}`);

  // Same access token — the line holds one place for this number, and it is
  // hers, not a rival ticket that would cost her her real spot.
  expect(second, 'a re-check-in minted a second ticket URL').toBe(first);

  // The notice is session-borne and popped on render: visible now, gone on a
  // plain reload — so a forwarded ticket link never carries it either.
  const notice = page.locator('[role="status"]');
  await expect(notice, 'the already-in-line notice did not render').toBeVisible();
  await page.reload();
  await expect(notice, 'the notice survived a reload — the session pop is broken').toHaveCount(0);
});
