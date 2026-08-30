// Act 9 — a woman holds more than one job, and the form has to survive saying so.
//
// Everything here is checked through the OWNER's own screen rather than the
// database, because the two bugs this act was written after both looked fine in
// SQL: a form that crashed while re-rendering, and a form that saved an empty
// set. Both are only visible from the chair.
//
// It hires its OWN team member and archives her again. The first version edited
// whichever worker was last on the Team page — which is the staff login every
// other spec signs in as — and when its restore did not land, that worker kept
// a second role into the next file and took home.spec.js down with her. A spec
// that can reconfigure the tenant it shares is worse than no spec: the next run
// measures the last run's leftovers.
const { test, expect } = require('@playwright/test');
const { submitFormWith } = require('../lib/form.js');
const { requirePeople } = require('../lib/people.js');

const PASSWORD = process.env.MODRYN_DEMO_PASSWORD;
const TENANT = new URL(process.env.BASE_URL || 'http://bella.localtest.me:8069').hostname.split('.')[0];
const PEOPLE = requirePeople(TENANT);

// Unique per run, because a username is unique per database and a spec that
// fails before its archive step must not block the next run from hiring.
const STAMP = String(Date.now()).slice(-8);
const HIRE = { name: `Roles Probe ${STAMP}`, username: `rolesprobe${STAMP}` };

test.beforeAll(() => {
  if (!PASSWORD) throw new Error('MODRYN_DEMO_PASSWORD is unset — export the password the tenant was seeded with');
});

async function signInOwner(page) {
  await page.goto('/staff/login');
  await page.fill('input[name="username"]', PEOPLE.owner);
  await page.fill('input[name="password"]', PASSWORD);
  await Promise.all([
    page.waitForURL((u) => !/\/staff\/login/.test(u.toString())),
    submitFormWith(page, 'username'),
  ]);
}

const ticks = (page) => page.locator('input[name="role_ids"]');
const tickedValues = async (page) =>
  (await ticks(page).evaluateAll((els) => els.filter((e) => e.checked).map((e) => e.value))).sort();

// Her edit form, found by name in the team box on the manager's screen. The
// Team screen folded into that box, so a row is a card and the form is a state
// of the tile rather than a page of its own.
const herCard = (page) =>
  page.locator('article.modryn_team_card', { hasText: HIRE.name }).first();

async function hersHref(page) {
  await page.goto('/manage/team-screen?view=team');
  return await herCard(page).locator('a[href*="edit="]').first().getAttribute('href');
}

// Archived whatever happened above, so a failure cannot leave a spare account
// on the tenant. Archiving and not deleting: it is what the owner's own screen
// offers, so this is the path the product actually supports.
test.afterAll(async ({ browser }) => {
  const page = await browser.newPage();
  try {
    await signInOwner(page);
    await page.goto('/manage/team-screen?view=team');
    const row = herCard(page);
    if (await row.count()) {
      // Archive is a form POST, not a link — it changes state, so it may not be
      // something a crawler or a prefetch can trigger by following an href.
      await Promise.all([
        page.waitForURL(/view=team/),
        row.locator('form[action*="/manage/staff/archive/"] button[type="submit"]')
          .first().click(),
      ]);
    }
  } catch {
    // Nothing to clean up, or the tenant is already in the state we wanted.
  } finally {
    await page.close();
  }
});

test('act 9 — hiring with two roles ticked, and both survive the save', async ({ page }) => {
  await signInOwner(page);
  await page.goto('/manage/team-screen?view=team&new=1');

  const boxes = await ticks(page).all();
  expect(boxes.length, 'the role field is not a set of tick boxes').toBeGreaterThan(1);

  await page.fill('input[name="name"]', HIRE.name);
  await page.fill('input[name="username"]', HIRE.username);
  await page.fill('input[name="password"]', PASSWORD);
  // Two roles at once — the thing a single dropdown could not say.
  await boxes[0].setChecked(true);
  await boxes[1].setChecked(true);
  const want = [await boxes[0].getAttribute('value'), await boxes[1].getAttribute('value')].sort();
  await Promise.all([page.waitForURL(/view=team/), submitFormWith(page, 'name')]);

  // Her card shows both, not whichever sorts first.
  await expect(herCard(page)).toContainText('·');

  await page.goto(await hersHref(page));
  expect(await tickedValues(page), 'the second role was dropped on save').toEqual(want);
});

test('act 9b — clearing every tick is refused, and her roles are left alone', async ({ page }) => {
  await signInOwner(page);
  const href = await hersHref(page);
  await page.goto(href);
  const before = await tickedValues(page);
  expect(before.length, 'act 9 did not leave a worker to edit').toBeGreaterThan(0);

  for (const box of await ticks(page).all()) {
    await box.setChecked(false);
  }
  await submitFormWith(page, 'name');
  await page.waitForLoadState('load');

  // An error on the page, NOT a redirect: a save that silently succeeded would
  // leave her able to open her home and her profile and nothing else, with
  // nothing anywhere to say why.
  await expect(page.locator('.modryn_field_error').first()).toBeVisible();
  // The form comes back as a form — it used to come back as a 500, because the
  // re-render compared an int role id against the string the browser posted.
  expect(await ticks(page).count(), 'the re-rendered form lost its tick boxes').toBeGreaterThan(1);
  // It shows what she SENT, which here is nothing. Not what the database still
  // holds: she is being asked to fix the choice she just made, and quietly
  // re-ticking her old roles under the error would read as the save having
  // half-worked.
  expect(await tickedValues(page), 'the refused form re-ticked something she had cleared')
    .toEqual([]);

  // And nothing was written. This is the assertion the whole act exists for.
  await page.goto(href);
  expect(await tickedValues(page), 'a refused save changed her roles anyway').toEqual(before);
});

test('act 9c — a hire that trips another rule keeps every role already ticked', async ({ page }) => {
  // The 500 this act was written after: the re-render asked
  // `role.id in values['role_ids']`, an int against the string a form posts,
  // so a hire that failed ANY validation crashed instead of showing the error.
  // It also threw the ticks away, which is its own small cruelty on a form
  // with three of them.
  await signInOwner(page);
  await page.goto('/manage/team-screen?view=team&new=1');
  const boxes = await ticks(page).all();
  await page.fill('input[name="name"]', `Roles Probe Reject ${STAMP}`);
  await page.fill('input[name="username"]', `rolesreject${STAMP}`);
  await page.fill('input[name="password"]', 'x');   // too short, on purpose
  await boxes[0].setChecked(true);
  await boxes[1].setChecked(true);
  const want = [await boxes[0].getAttribute('value'), await boxes[1].getAttribute('value')].sort();

  await submitFormWith(page, 'name');
  await page.waitForLoadState('load');

  await expect(page.locator('.modryn_field_error').first()).toBeVisible();
  expect(await tickedValues(page), 'the error re-render dropped the roles she had chosen')
    .toEqual(want);
  // Nobody was hired, so there is nothing for afterAll to clean up here.
  await page.goto('/manage/team-screen?view=team');
  await expect(page.locator('article.modryn_team_card',
                            { hasText: `Roles Probe Reject ${STAMP}` })).toHaveCount(0);
});
