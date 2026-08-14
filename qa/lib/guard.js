// The one guard, run once before any spec.
//
// Acts 3, 4 and 6 write real rows: a booking on a shared grid, an OTP send, a
// walk-in on a live floor board. A read-only production mode is the obvious
// answer and the wrong one — those three are precisely the acts curl cannot do,
// so excluding them from production deletes most of the value.
//
// The answer is a DEDICATED THROWAWAY TENANT, provisioned exactly like any
// boutique except with SMS explicitly switched off:
//
//   sudo MODRYN_SMS_DISABLED=1 \
//        /opt/modryn/deploy/scripts/new_boutique_prod.sh qa "QA — not a boutique"
//
// which writes `modryn.twilio.disabled` into that database. That parameter is
// what modryn.sms._twilio_config checks first: set, it returns no config no
// matter what the process environment holds, so _send_now logs the body,
// returns ('logged'), and no message leaves the box.
//
// READ THE DIRECTION OF THAT SENTENCE — it is weaker than what it replaced.
// While Twilio credentials lived in each database, a tenant was dangerous only
// if someone had written four modryn.twilio.* rows into it, so this guard could
// look for their ABSENCE: safe was the default and a fresh database was safe by
// construction. Credentials now come from the process environment and every
// database inherits them, so safe is a state somebody has to opt into and this
// guard can only verify a PRESENT flag. Provision the QA tenant without
// MODRYN_SMS_DISABLED and it will send for real; this guard refuses it, which
// is the point, but nothing else in the system will notice.
//
// FAILS CLOSED. A missing QA_TENANTS refuses everything rather than allowing
// everything, and a flag that is unset, empty or unreadable refuses @writes,
// because the failure mode of the opposite default is texting a bride.
const { execFileSync } = require('node:child_process');

// The tenant a baseURL addresses is its first hostname label — dbfilter = ^%d$
// takes exactly that, so this is the same rule the server uses, not a parallel
// one that could disagree with it.
function tenantOf(baseUrl) {
  return new URL(baseUrl).hostname.split('.')[0];
}

// Empty string for "no such row" and for "row holds an empty value" alike, and
// both mean the same thing here: not disabled. Anything else counts as set,
// which mirrors the Python side exactly — `if icp.get_param(P_DISABLED)` treats
// even '0' and 'false' as truthy, so a tenant whose flag reads '0' really is
// log-only and really should be permitted, however badly it reads.
function smsDisabledFlag(db) {
  const q = `select value from ir_config_parameter where key = 'modryn.twilio.disabled'`;
  const out = process.env.QA_SSH
    ? execFileSync('ssh', [process.env.QA_SSH, `sudo -u odoo psql -d ${db} -tAc ${JSON.stringify(q)}`])
    : execFileSync('psql', ['-d', db, '-tAc', q]);
  return out.toString().trim();
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

  // THE DISABLED-FLAG RULE APPLIES EVERYWHERE, dev included. This guard used to
  // skip dev on the theory that a developer's own fixtures hold nothing worth
  // protecting. That is false on this repo and was false the first time it ran:
  // `bella` carried four live modryn.twilio.* parameters, so act 3's booking
  // confirmation and act 4's OTP both took the Twilio branch and made real API
  // calls from a laptop. `noga` was the log-only tenant — which is exactly why
  // the project's own notes say to do SMS-triggering work there. That is the
  // incident this guard was written after, and it is why it is trusted.
  //
  // What changed is only the evidence, not the rule: `noga` was safe then by
  // holding nothing, and is safe now by holding modryn.twilio.disabled. Every
  // laptop that exports the TWILIO_* variables is a `bella` for these purposes.
  //
  // A guard that is switched off in the environment where people actually run
  // things is not a guard; it is a comment.
  if (writesAllowed) {
    // Assert the property, do not assume it. The tenant name being on an
    // allowlist proves someone typed it; this proves the thing that actually
    // matters.
    let flag;
    try {
      flag = smsDisabledFlag(tenant);
    } catch (e) {
      throw new Error(
        `refusing to run @writes: could not read modryn.twilio.disabled from ` +
          `"${tenant}" (${e.message.split('\n')[0]}). Set QA_SSH=user@box, or run ` +
          `without QA_ALLOW_WRITES. An unverifiable guard is not a guard.`
      );
    }
    if (!flag) {
      throw new Error(
        `refusing to run @writes against "${tenant}": modryn.twilio.disabled is not ` +
          `set there, so modryn.sms._twilio_config falls through to the TWILIO_* ` +
          `process environment and every OTP in act 4 could be a real message to a ` +
          `real handset. Provision the QA tenant with MODRYN_SMS_DISABLED=1, or set ` +
          `the parameter on this one.`
      );
    }
    console.log(`qa: ${tenant} has modryn.twilio.disabled set — @writes permitted`);
  }
};
