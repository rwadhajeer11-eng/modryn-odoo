// The main screen — the page every staff member opens first, and the one with
// no browser coverage at all until now.
//
// verify.sh can prove /staff/home answers 200. It cannot see that the week
// panel renders seven days, that a colleague's shift shows for somebody not on
// it, or that an unpublished week says so instead of drawing seven empty days —
// and that last one shipped: a tenant whose week had never been generated read
// as a boutique closed all week.
const { test, expect } = require('@playwright/test');
const { submitFormWith } = require('../lib/form.js');
const { requirePeople } = require('../lib/people.js');

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

test('the main screen shows the whole week, not just her own shifts', async ({ page }) => {
  await signIn(page, PEOPLE.staff);
  await page.goto('/staff/home');

  // The nav entry the owner asked to be renamed. Matched across the three
  // languages because the staff terminal is Hebrew-first and a worker may have
  // set her own.
  await expect(
    page.getByRole('link', { name: /main screen|מסך ראשי|الشاشة الرئيسية/i }).first()
  ).toBeVisible();

  // Both panels exist and are DIFFERENT questions: hers, and everyone's.
  await expect(
    page.getByRole('heading', { name: /my shifts|המשמרות שלי|ورديّاتي/i })
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: /this week's shifts|משמרות השבוע|ورديات الأسبوع/i })
  ).toBeVisible();

  // Either the week is published and draws seven days, or it says plainly that
  // it is not. What must NOT happen is seven "nothing on this day" rows for a
  // week nobody has published — a shop that trades Sunday to Thursday reading
  // as shut. Asserted as a real either/or rather than skipped, so the panel
  // cannot quietly render nothing at all.
  const days = page.locator('.modryn_weekday');
  const notPublished = page.getByText(
    /rota hasn't been published|לא פורסם|ما انتشر/i);
  const dayCount = await days.count();
  if (dayCount) {
    expect(dayCount).toBe(7);
    // The point of this panel: shifts she is NOT on are still listed.
    await expect(page.locator('.modryn_weekslot').first()).toBeVisible();
  } else {
    await expect(notPublished).toBeVisible();
  }
});

test('a worker whose role cannot open the rota does not get it here instead', async ({ page }) => {
  // The cross-staff panel is the team's rota. It belongs to whoever the owner
  // granted the work schedule to — otherwise the role matrix is a suggestion:
  // she gets a 403 on /roster and the same information on her front page.
  await signIn(page, PEOPLE.owner);
  await page.goto('/manage/roles');
  await expect(page.locator('input[name="pages"]').first()).toBeAttached({ timeout: 30000 });

  // The staff member's role is the one carrying the seeded atelier grant.
  const row = page.locator('tr:has(input[value$=":atelier"]:checked)').first();
  const rosterBox = row.locator('input[value$=":roster"]');
  const had = await rosterBox.isChecked();

  if (had) {
    await rosterBox.uncheck();
    await Promise.all([
      page.waitForURL(/\/manage\/roles/),
      page.getByRole('button', { name: /save page access|שמירת|حفظ/i }).click(),
    ]);
  }

  try {
    await signIn(page, PEOPLE.staff);
    await page.goto('/staff/home');
    // Her own shifts stay — they are hers by name.
    await expect(
      page.getByRole('heading', { name: /my shifts|המשמרות שלי|ورديّاتي/i })
    ).toBeVisible();
    // The team's rota does not.
    await expect(
      page.getByRole('heading', { name: /this week's shifts|משמרות השבוע|ورديات الأسبوع/i })
    ).toHaveCount(0);
    await expect(page.locator('.modryn_weekday')).toHaveCount(0);
  } finally {
    // Give the grant back whatever happened above: every other spec and the
    // seeded state assume the roles as the seeder left them.
    if (had) {
      await signIn(page, PEOPLE.owner);
      await page.goto('/manage/roles');
      const box = page.locator(
        'tr:has(input[value$=":atelier"]:checked) input[value$=":roster"]');
      if (!(await box.isChecked())) {
        await box.check();
        await Promise.all([
          page.waitForURL(/\/manage\/roles/),
          page.getByRole('button', { name: /save page access|שמירת|حفظ/i }).click(),
        ]);
      }
    }
  }
});
