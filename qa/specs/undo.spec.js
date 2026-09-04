// Act 10 — the boutique can take back what it typed.
//
// Four screens gained a way OUT this round: a role, a kind of visitor, a
// checklist item, and a date's own booking hours. Every one of them is a
// confirmation drawn by the SERVER — a question in words, on the row she
// pressed — rather than a browser confirm(), because a confirm() string is a
// JavaScript literal no .po file can reach and Hebrew is the product's first
// language.
//
// Checked from the chair and not from SQL, for the reason act 9 already gives:
// the failures worth catching here are a button that refuses when it should
// not, and a button that deletes when it should refuse. Both look identical in
// the table afterwards — one row fewer, or one row more — and only the screen
// says which of them happened.
//
// It creates and removes its OWN rows. A spec that deletes a row the tenant was
// seeded with reconfigures the database every other spec measures.
const { test, expect } = require('@playwright/test');
const { submitFormWith } = require('../lib/form.js');
const { requirePeople } = require('../lib/people.js');

const PASSWORD = process.env.MODRYN_DEMO_PASSWORD;
const TENANT = new URL(process.env.BASE_URL || 'http://bella.localtest.me:8069').hostname.split('.')[0];
const PEOPLE = requirePeople(TENANT);
const STAMP = String(Date.now()).slice(-8);

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

// The row holding a piece of text, wherever the table happens to be. Matched on
// the text the spec itself typed, never on a label: the frontend's language is
// a cookie, so a spec that matched a translated word would pass in one browser
// profile and fail in a fresh one.
const rowFor = (page, text) => page.locator('tr', { hasText: text });

// The ROLES table, not the page-access matrix below it. Both are
// table.modryn_table and both list every role by name, so an unscoped row
// lookup finds each role twice and every count is out by a factor of two.
const roleRow = (page, name) =>
  page.locator('table.modryn_table').first().locator('tbody tr', { hasText: name });

test('act 10a — a role added by mistake can be deleted, and one in use cannot @writes', async ({ page }) => {
  await signInOwner(page);
  const name = `Undo Role ${STAMP}`;

  await page.goto('/manage/roles');
  await page.fill('form[action="/manage/roles/new"] input[name="name"]', name);
  await submitFormWith(page, 'name');
  await expect(roleRow(page, name)).toHaveCount(1);

  // Nobody wears it, so the way out is offered.
  const row = roleRow(page, name);
  const remove = row.locator('a[href*="confirm="]');
  await expect(remove, 'a brand-new role nobody has cannot be deleted').toHaveCount(1);
  await remove.click();

  // ASKED, not done. A press that deleted straight away would be the bug this
  // act is about: page access goes with the role and nothing brings it back.
  await expect(page.locator('.modryn_field_error'), 'Delete acted without asking').toBeVisible();
  await expect(roleRow(page, name)).toHaveCount(1);

  await page.locator('form[action*="/manage/roles/delete/"] button').click();
  await expect(roleRow(page, name)).toHaveCount(0);

  // And a role the team actually carries is refused. Every seeded role is on
  // somebody, so this asserts against whatever the tenant has rather than
  // hiring a woman to prove it.
  await page.goto('/manage/roles');
  const worn = page.locator('table.modryn_table').first().locator('tbody tr').filter({
    has: page.locator('td .modryn_sub'),
  });
  expect(await worn.count(), 'no role is on anybody — the refusal has nothing to refuse').toBeGreaterThan(0);
  await expect(worn.first().locator('a[href*="confirm="]'),
    'a role somebody carries was offered a Delete button').toHaveCount(0);
});

test('act 10b — a kind of visitor added by mistake can be deleted @writes', async ({ page }) => {
  await signInOwner(page);
  const name = `Undo Kind ${STAMP}`;

  await page.goto('/manage/team-screen?view=queue');
  await page.fill('input[name="name"]', name);
  await submitFormWith(page, 'name');
  const row = page.locator('tr', { hasText: name });
  await expect(row).toHaveCount(1);

  await row.locator('a[href*="confirm="]').click();
  await expect(page.locator('.modryn_field_error')).toBeVisible();
  await page.locator('form[action*="/manage/queue-kind/delete/"] button').click();
  await expect(page.locator('tr', { hasText: name })).toHaveCount(0);
});

test('act 10c — a checklist item can be corrected in place, and removed @writes', async ({ page }) => {
  await signInOwner(page);
  const name = `Undo Task ${STAMP}`;

  await page.goto('/manage/checklists');
  await page.fill('form[action="/manage/checklists/new"] input[name="name"]', name);
  await page.fill('form[action="/manage/checklists/new"] input[name="due_time"]', '09:30');
  await submitFormWith(page, 'name');
  await expect(rowFor(page, name)).toHaveCount(1);
  await expect(rowFor(page, name)).toContainText('09:30');

  // EDIT IN PLACE. The row becomes the form, which is the whole point: a time
  // typed wrong is one number, and sending her to another page to fix it is
  // three screens for it.
  await rowFor(page, name).locator('a[href*="edit="]').click();
  const form = page.locator('form[action*="/manage/checklists/edit/"]');
  await expect(form).toBeVisible();
  await form.locator('input[name="due_time"]').fill('16:45');
  await form.locator('button[type="submit"]').click();
  await expect(rowFor(page, name)).toContainText('16:45');

  await rowFor(page, name).locator('a[href*="confirm="]').click();
  await expect(page.locator('.modryn_field_error')).toBeVisible();
  await page.locator('form[action*="/manage/checklists/delete/"] button').click();
  await expect(rowFor(page, name)).toHaveCount(0);
});

test('act 10e — a fitting room typed by mistake can be deleted @writes', async ({ page }) => {
  await signInOwner(page);
  const name = `Undo Room ${STAMP}`;

  await page.goto('/manage/team-screen?view=rooms');
  await page.fill('form[action="/manage/rooms/new"] input[name="name"]', name);
  await submitFormWith(page, 'name');
  const row = page.locator('tr', { hasText: name });
  await expect(row).toHaveCount(1);

  await row.locator('a[href*="confirm="]').click();
  await expect(page.locator('.modryn_field_error'), 'Delete acted without asking').toBeVisible();
  await expect(page.locator('tr', { hasText: name })).toHaveCount(1);

  await page.locator('form[action*="/manage/rooms/delete/"] button').click();
  await expect(page.locator('tr', { hasText: name })).toHaveCount(0);
});

test('act 10d — a date can be given its own booking hours, and handed back @writes', async ({ page }) => {
  await signInOwner(page);
  await page.goto('/manage/team-screen?view=queue');

  // A day inside the month that is not in the past. The cells carry their date
  // in the href, so this never depends on where in the grid the month starts.
  const days = page.locator('a.modryn_month_day');
  expect(await days.count(), 'the month drew no pressable day').toBeGreaterThan(0);
  const before = await days.count();
  const target = days.last();
  const key = (await target.getAttribute('href')).split('day=')[1];
  await target.click();

  const form = page.locator('form[action="/manage/queue-day"]');
  await expect(form).toBeVisible();
  await expect(form.locator('input[type="number"]'), 'a day has twenty-four hours').toHaveCount(24);

  // Every hour to nothing except one. "Open, and the website gives nothing
  // away" is a real answer and the only way to say it, so a day of zeroes has
  // to be STORED rather than read as "she said nothing".
  await form.locator('input[type="number"]').evaluateAll((els) => els.forEach((e) => { e.value = '0'; }));
  await form.locator('input[name="hour_11-0"]').fill('4');
  await form.locator('button[type="submit"]').click();

  const own = page.locator(`a.modryn_month_day.is_own[href*="day=${key}"]`);
  await expect(own, 'the day did not take its own hours').toHaveCount(1);
  await expect(own).toContainText('4');
  // Every other day is untouched: this is a date's own answer, not an edit to
  // the week that every date follows.
  expect(await page.locator('a.modryn_month_day.is_own').count()).toBe(1);
  expect(await page.locator('a.modryn_month_day').count()).toBe(before);

  await page.locator('form[action="/manage/queue-day/clear"] button').click();
  await expect(page.locator(`a.modryn_month_day.is_own[href*="day=${key}"]`),
    'the day would not go back to following the week').toHaveCount(0);
});
