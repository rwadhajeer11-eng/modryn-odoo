// Read an OTP back, in BOTH environments, by one path.
//
// Three non-starters, named so nobody retries them:
//
//  1. modryn_loadtest's /loadtest/otp is FORBIDDEN in production. Living
//     outside the production addons_path is the first of its three gates, and
//     moving it there is the accident the whole arrangement prevents.
//  2. The code is only ever stored as hmac_sha256(database.secret,
//     "<e164>|<code>") — see addons/modryn_portal/models/otp.py. It cannot be
//     read back out of the row.
//  3. Recovering the log line by adding
//     `log_handler = odoo.addons.modryn_portal.models.sms:INFO` would write
//     EVERY real customer's OTP and EVERY booking link into the journal, for
//     every tenant, because log_handler is global.
//
// The mechanism that does work is already in this repo: scripts/verify.sh:56-63
// reverses a booking token by recomputing the HMAC from database.secret. Do the
// same. The code space is 10^6 and one million HMACs is under two seconds, so
// this is the established idiom here rather than a new trick.
const { createHmac } = require('node:crypto');
const { execFileSync } = require('node:child_process');

const sql = (db, q) =>
  (process.env.QA_SSH
    ? execFileSync('ssh', [process.env.QA_SSH, `sudo -u odoo psql -d ${db} -tAc ${JSON.stringify(q)}`])
    : execFileSync('psql', ['-d', db, '-tAc', q])
  ).toString().trim();

/**
 * The six-digit code currently live for `phoneE164` on `db`.
 * Throws with the reason rather than returning null — a silent null here would
 * surface three steps later as "the portal rejected a correct code".
 */
exports.readOtp = (db, phoneE164) => {
  const secret = sql(db, `select value from ir_config_parameter where key='database.secret'`);
  if (!secret) throw new Error(`no database.secret in ${db}`);

  // `now() at time zone 'UTC'`, NOT `now()`.
  //
  // Odoo stores every Datetime as naive UTC in a `timestamp without time zone`
  // column. Postgres `now()` is a timestamptz, and comparing it against that
  // column casts it to the SERVER's local time — Asia/Jerusalem here, UTC+3.
  // So a code issued at 19:53 UTC with ten minutes to live was compared against
  // a local 22:48 and filtered out as expired, and the failure surfaced three
  // steps later as "no live OTP row", pointing at the send.
  //
  // Measured on noga while writing this: row expires_at 19:53:39,
  // now()::timestamp 22:48:56, (now() at time zone 'UTC') 19:48:56.
  const hash = sql(
    db,
    `select code_hash from modryn_otp_code where phone='${phoneE164}' ` +
      `and used_at is null and expires_at > (now() at time zone 'UTC') ` +
      `order by create_date desc limit 1`
  );
  if (!hash) {
    throw new Error(
      `no live OTP row for ${phoneE164} in ${db}. Either /my/login refused the ` +
        `send (MAX_SENDS_PER_HOUR is 3 per number — see qaPhone below), or the ` +
        `number normalised to something other than what was typed.`
    );
  }

  for (let n = 0; n < 1e6; n++) {
    const code = String(n).padStart(6, '0');
    if (createHmac('sha256', secret).update(`${phoneE164}|${code}`).digest('hex') === hash) {
      return code;
    }
  }
  throw new Error('no 6-digit preimage — modryn.otp.code::_hash changed shape');
};

/**
 * A phone number derived from the clock.
 *
 * MAX_SENDS_PER_HOUR is 3 PER NUMBER. A fixed QA number makes the fourth run in
 * an hour report `rate_limited`, which reads as a product failure and is not
 * one. The same reasoning session.js::phoneForVu already documents.
 *
 * +97252 + 7 digits derived from the minute-of-epoch: a WELL-FORMED Israeli
 * mobile, twelve digits in E.164.
 *
 * The load harness deliberately generates eleven-digit numbers so Twilio
 * answers 21211 invalid-To. That trick is not available here, and copying it
 * was the first thing that broke this spec: modryn.otp.code::issue() calls
 * modryn.sms.send() and returns 'send_failed' when the send is rejected, so an
 * invalid number never produces a code row at all and the code field never
 * renders. A malformed number does not make the OTP path safe to test — it
 * makes it impossible to test.
 *
 * Safety here is guard.js asserting the tenant holds zero modryn.twilio.*,
 * which makes _send_now log the body and return ('logged') without a message
 * leaving the box. That is a property of the TENANT, checked before the run,
 * rather than a property of the number, hoped for during it.
 */
// MILLISECOND resolution, not minute. Minute resolution was the first attempt
// and it failed on the second consecutive run: two runs inside the same minute
// produce the same number, /queue/checkin refuses the duplicate walk-in, the
// board does not change, and act 6 reports the websocket broken when nothing
// was ever handed to it. "Unique per run" is the requirement; per minute only
// looked like it.
exports.qaPhone = () => '+97252' + String(Date.now() % 1e7).padStart(7, '0');
