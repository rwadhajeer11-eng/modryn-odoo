// @realsms — the one test that SENDS A REAL TEXT MESSAGE, on purpose.
//
// Everything else in this suite is built never to text a stranger: guard.js
// refuses @writes against any tenant that cannot prove modryn.twilio.disabled.
// This spec is the deliberate exception — its entire job is to prove that a
// Twilio-live production tenant delivers to a real handset. So it is NOT
// tagged @writes (the guard would be right to refuse it) and instead demands
// informed consent twice over:
//
//   QA_REAL_PHONE      the handset that will receive the message — YOURS
//   QA_ALLOW_REAL_SMS  literally '1'
//
// plus the Twilio credentials in the environment, because the proof reads the
// message back from Twilio's own record:
//
//   TWILIO_ACCOUNT_SID / TWILIO_API_KEY_SID / TWILIO_API_KEY_SECRET
//
// What one passing run proves, in order: the login form accepted the number;
// the send did NOT fall into the no-provider 'logged' branch (no demo box);
// Twilio carried the message to sent/delivered (SID printed); the code inside
// the delivered body completes the login. That is backlog #1, mechanised.
//
// Costs a few cents and one row of demo residue (a partner + a used OTP) per
// run. The per-phone cap is 3 codes/hour — a fourth run inside an hour fails
// with the rate-limit message, which is itself correct behaviour.
const { test, expect } = require('@playwright/test');

const PHONE = process.env.QA_REAL_PHONE || '';
const ALLOWED = process.env.QA_ALLOW_REAL_SMS === '1';
const SID = process.env.TWILIO_ACCOUNT_SID || '';
const KEY = process.env.TWILIO_API_KEY_SID || '';
const SECRET = process.env.TWILIO_API_KEY_SECRET || '';

function e164(raw) {
  const digits = raw.replace(/\D/g, '');
  return digits.startsWith('972') ? '+' + digits : '+972' + digits.replace(/^0/, '');
}

async function twilioMessagesTo(to) {
  const url = `https://api.twilio.com/2010-04-01/Accounts/${SID}/Messages.json` +
    `?To=${encodeURIComponent(to)}&PageSize=5`;
  const res = await fetch(url, {
    headers: { Authorization: 'Basic ' + Buffer.from(`${KEY}:${SECRET}`).toString('base64') },
  });
  if (!res.ok) throw new Error(`Twilio API ${res.status}: ${(await res.text()).slice(0, 200)}`);
  return (await res.json()).messages || [];
}

test('real SMS — a login code reaches a real handset and logs her in @realsms', async ({ page }) => {
  test.skip(!ALLOWED || !PHONE || !SID || !KEY || !SECRET,
    'opt-in only: set QA_ALLOW_REAL_SMS=1, QA_REAL_PHONE and the TWILIO_* variables');
  test.setTimeout(120000);

  const to = e164(PHONE);
  const startedAt = Date.now();

  // 1. Ask for a code, exactly as a customer would.
  await page.goto('/my/login');
  await page.fill('input[name="phone"]', PHONE);
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'domcontentloaded' }),
    page.click('form[action="/my/login"] button[type="submit"]'),
  ]);

  // Rate-limited is the cap doing its job, but it means no send happened —
  // say so distinctly rather than failing on a missing message downstream.
  const err = await page.locator('.modryn_field_error').textContent().catch(() => '');
  expect(err || '', 'the login form refused the number — with the 3/hour/phone cap, wait an hour and rerun').toBe('');

  // 2. No demo box: the code went to Twilio, not to the server log.
  await expect(page).toHaveURL(/\/my\/verify/);
  await expect(page.locator('.alert-info'),
    'a demo-code box rendered — the send fell into the no-provider branch, Twilio is NOT configured').toHaveCount(0);

  // 3. Twilio's own record: newest message to this handset, created after the
  //    test started, reaching sent/delivered. 'delivered' is carrier-confirmed;
  //    'sent' means handed to the carrier — accept it, log which one we got.
  let message = null;
  for (let i = 0; i < 30; i++) {
    const candidates = (await twilioMessagesTo(to)).filter(
      (m) => new Date(m.date_created).getTime() >= startedAt - 60000 &&
             ['queued', 'accepted', 'sending', 'sent', 'delivered'].includes(m.status));
    const settled = candidates.find((m) => ['sent', 'delivered'].includes(m.status));
    if (settled) { message = settled; break; }
    if (candidates.some((m) => ['failed', 'undelivered'].includes(m.status))) {
      throw new Error(`Twilio reports the message failed: ${JSON.stringify(candidates[0])}`);
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
  expect(message, 'no sent/delivered message to the handset appeared in Twilio within 60s').toBeTruthy();
  console.log(`REAL SMS: sid=${message.sid} status=${message.status} to=${to}`);

  // 4. The code from the delivered body completes the login — end to end.
  const code = (message.body.match(/\d{6}/) || [])[0];
  expect(code, `no 6-digit code in the delivered body: ${message.body}`).toBeTruthy();
  await page.fill('input[name="code"]', code);
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'domcontentloaded' }),
    page.click('form[action="/my/verify"] button[type="submit"]'),
  ]);
  await expect(page).toHaveURL(/\/my\/bookings/);
});
