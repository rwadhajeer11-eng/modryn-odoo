// Act 4 — phone OTP login.
//
// The OTP is the only authentication path fail2ban cannot see: jail-modryn.conf
// says so explicitly. Its sole protection is `limit_req zone=modryn_otp` plus
// the five-attempt burn. A regression here is invisible to every other check in
// this repository.
const { test, expect } = require('@playwright/test');
const { submitFormWith } = require('../lib/form.js');
const { readOtp, qaPhone } = require('../lib/otp.js');

// dbfilter = ^%d$ takes the first hostname label as the database name, so the
// tenant this suite is addressing IS its first label. Same rule the server
// uses, not a parallel one that could disagree.
const DB = new URL(process.env.BASE_URL || 'http://bella.localtest.me:8069').hostname.split('.')[0];

test('act 4 — a wrong code is refused, the right one signs in @writes', async ({ page }) => {
  const phone = qaPhone();

  await page.goto('/my/login');
  await page.fill('input[name="phone"]', phone);
  await submitFormWith(page, 'phone');

  // The code field appearing is what tells us the send was accepted rather than
  // rate-limited. MAX_SENDS_PER_HOUR is 3 per number, which is why qaPhone()
  // derives from the clock instead of being a constant.
  const code = page.locator('input[name="code"]');
  await expect(code, 'no code field — /my/login refused the send (rate_limited?) or the number normalised differently').toBeVisible();

  await code.fill('000000');
  await submitFormWith(page, 'code');
  await expect(page, 'a wrong OTP signed the user in').not.toHaveURL(/\/my\/bookings|\/my$/);

  const real = readOtp(DB, phone);
  expect(real).toMatch(/^\d{6}$/);

  await page.locator('input[name="code"]').fill(real);
  await submitFormWith(page, 'code');
  await expect(page).toHaveURL(/\/my/);

  // Signed in as a brand-new number, so there are no bookings — the assertion
  // is that the page RENDERS for her, not that it lists anything. A tenant's
  // first visitor having zero rows is legitimate; restore.sh makes the same
  // distinction between "must be non-empty" and "must be readable".
  const painted = await page.evaluate(() => document.body.innerText.trim().length);
  expect(painted, '/my returned 200 and rendered nothing').toBeGreaterThan(40);
});
