// The one guard, run once before any spec.
//
// Acts 3, 4 and 6 write real rows: a booking on a shared grid, an OTP send, a
// walk-in on a live floor board. A read-only production mode is the obvious
// answer and the wrong one — those three are precisely the acts curl cannot do,
// so excluding them from production deletes most of the value.
//
// The answer is a DEDICATED THROWAWAY TENANT, provisioned exactly like any
// boutique except with the Twilio variables empty:
//
//   sudo TWILIO_ACCOUNT_SID= TWILIO_API_KEY_SID= TWILIO_API_KEY_SECRET= \
//        TWILIO_FROM_NUMBER= \
//        /opt/modryn/deploy/scripts/new_boutique_prod.sh qa "QA — not a boutique"
//
// Zero `modryn.twilio.*` parameters is the property modryn.sms._send_now
// branches on: with no config it logs the body, returns ('logged'), and no
// message leaves the box. That is the same mechanism loadtest/README.md
// documents as "the actual mechanism" — reused, not reinvented.
//
// FAILS CLOSED. A missing QA_TENANTS refuses everything rather than allowing
// everything, because the failure mode of the opposite default is texting a
// bride.
const { execFileSync } = require('node:child_process');

// The tenant a baseURL addresses is its first hostname label — dbfilter = ^%d$
// takes exactly that, so this is the same rule the server uses, not a parallel
// one that could disagree with it.
function tenantOf(baseUrl) {
  return new URL(baseUrl).hostname.split('.')[0];
}

function twilioParamCount(db) {
  const q = `select count(*) from ir_config_parameter where key like 'modryn.twilio.%'`;
  const out = process.env.QA_SSH
    ? execFileSync('ssh', [process.env.QA_SSH, `sudo -u odoo psql -d ${db} -tAc ${JSON.stringify(q)}`])
    : execFileSync('psql', ['-d', db, '-tAc', q]);
  return parseInt(out.toString().trim(), 10);
}

module.exports = async () => {
  const baseUrl = process.env.BASE_URL || 'http://bella.localtest.me:8069';
  const tenant = tenantOf(baseUrl);
  const writesAllowed = !!process.env.QA_ALLOW_WRITES || !process.env.CI;
  const isProd = new URL(baseUrl).protocol === 'https:';

  // QA_TENANTS is a PRODUCTION gate: on a live box, naming the throwaway tenant
  // explicitly is what stops a typo addressing a boutique.
  if (isProd) {
    const allowed = (process.env.QA_TENANTS || '').split(',').map((s) => s.trim()).filter(Boolean);
    if (!allowed.length) {
      throw new Error(
        'refusing to run against https:// with QA_TENANTS unset. Name the throwaway ' +
          'tenant explicitly, e.g. QA_TENANTS=qa. This fails closed on purpose: the ' +
          'failure mode of the opposite default is a real bride receiving a test SMS.'
      );
    }
    if (!allowed.includes(tenant)) {
      throw new Error(
        `refusing to run: BASE_URL addresses tenant "${tenant}", which is not in ` +
          `QA_TENANTS (${allowed.join(', ')}). A boutique is not a test fixture.`
      );
    }
  }

  // THE ZERO-TWILIO RULE APPLIES EVERYWHERE, dev included. This guard used to
  // skip dev on the theory that a developer's own fixtures hold nothing worth
  // protecting. That is false on this repo and was false the first time it ran:
  // `bella` carries four live modryn.twilio.* parameters, so act 3's booking
  // confirmation and act 4's OTP both took the Twilio branch and made real API
  // calls from a laptop. `noga` carries zero and is the log-only tenant — which
  // is exactly why the project's own notes say to do SMS-triggering work there.
  //
  // A guard that is switched off in the environment where people actually run
  // things is not a guard; it is a comment.
  if (writesAllowed) {
    // Assert the property, do not assume it. The tenant name being on an
    // allowlist proves someone typed it; this proves the thing that actually
    // matters.
    let n;
    try {
      n = twilioParamCount(tenant);
    } catch (e) {
      throw new Error(
        `refusing to run @writes: could not read modryn.twilio.* from "${tenant}" ` +
          `(${e.message.split('\n')[0]}). Set QA_SSH=user@box, or run without ` +
          `QA_ALLOW_WRITES. An unverifiable guard is not a guard.`
      );
    }
    if (n !== 0) {
      throw new Error(
        `refusing to run @writes against "${tenant}": it holds ${n} modryn.twilio.* ` +
          `parameter(s), so modryn.sms._send_now would take the Twilio branch and ` +
          `every OTP in act 4 would be a real message to a real handset.`
      );
    }
    console.log(`qa: ${tenant} has zero modryn.twilio.* — @writes permitted`);
  }
};
