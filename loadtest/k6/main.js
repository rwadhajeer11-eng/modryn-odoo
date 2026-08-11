// Entry point. STAGE selects the ramp profile; the role mix is fixed.
//
//   k6 run -e STAGE=smoke -e MODRYN_DEMO_PASSWORD=... main.js
//   k6 run -e STAGE=s1    -e MODRYN_DEMO_PASSWORD=... main.js
//
// STAGE=smoke is the harness gate, not a server test: 10 VUs, five minutes,
// every journey executing end to end at least once. A smoke run that passes on
// status codes but writes nothing means the scenarios are hitting 404s or
// re-rendered error pages — check the row counts, not the percentiles.

import { guardTenants, staffPassword, tagPhase, TENANTS } from './lib/session.js';
import { ROLE_MIX, STAGES, thresholdsFor } from './config/thresholds.js';

import visitor from './scenarios/visitor.js';
import customer from './scenarios/customer.js';
import staff from './scenarios/staff.js';
import manager from './scenarios/manager.js';
import owner from './scenarios/owner.js';

const STAGE_NAME = __ENV.STAGE || 'smoke';
const STAGE = STAGES[STAGE_NAME];
if (!STAGE) {
  throw new Error(`unknown STAGE "${STAGE_NAME}"; one of: ${Object.keys(STAGES).join(', ')}`);
}

// Every role needs at least one VU even at 10 total, or the smoke run silently
// skips owner (0.2% of 10 rounds to 0) and reports green on four journeys.
function vusFor(share) {
  return Math.max(1, Math.round(STAGE.target * share));
}

function ramp(share) {
  const target = vusFor(share);
  return [
    { duration: `${STAGE.rampUpSec}s`, target: target },
    { duration: `${STAGE.steadySec}s`, target: target },
    // Ramp-out is RUN but excluded from the gates: how it drains is a finding
    // (plan §7), so the samples are kept and tagged phase:rampdown.
    { duration: `${STAGE.rampDownSec}s`, target: 0 },
  ];
}

// Tenant stickiness and the deterministic phone scheme both key off
// exec.vu.idInTest, which k6 allocates uniquely across the whole test run — so
// a customer VU and a manager VU never share an id, and therefore never share a
// phone number. (idInInstance would NOT be safe here: it restarts per scenario.)
function scenario(fn, share) {
  return {
    executor: 'ramping-vus',
    exec: fn,
    startVUs: 0,
    stages: ramp(share),
    gracefulRampDown: '30s',
    tags: { role: fn, stage: STAGE_NAME },
  };
}

export const options = {
  discardResponseBodies: false, // every page check needs its body marker
  // k6 wipes the per-VU cookie jar at the END of every iteration by default,
  // but the scenarios keep a module-level `signedIn` / `loggedIn` flag that
  // survives iterations. Those two defaults together mean a staff, manager or
  // owner VU signs in during iteration 1 and then runs iteration 2 onward with
  // NO session while believing it is authenticated — /floor redirects to
  // /web/login and every /floor/* rpc answers "Odoo Session Expired". Measured
  // on the first smoke run: '/floor rendered' passed exactly twice, once per
  // authenticated VU, and /floor/data scored 86/289.
  //
  // Holding the jar is also the truthful model: a staff member signs in once
  // and works a shift, she does not re-authenticate every five minutes. The
  // OTP budget depends on it too — modryn.otp.code allows three issues per
  // phone per hour, so a customer VU that lost its cookies each iteration
  // would exhaust the budget and then report rate_limited as a failure.
  noCookiesReset: true,
  scenarios: {
    visitor: scenario('visitorVu', ROLE_MIX.visitor),
    customer: scenario('customerVu', ROLE_MIX.customer),
    staff: scenario('staffVu', ROLE_MIX.staff),
    manager: scenario('managerVu', ROLE_MIX.manager),
    owner: scenario('ownerVu', ROLE_MIX.owner),
  },
  thresholds: thresholdsFor(STAGE.target),
};

export function setup() {
  // Allowlist, fail-closed: refuses outright unless EVERY tenant matches what
  // loadtest/seed/gen_tenants.sh writes. See guardTenants() for what running
  // against a live boutique actually costs — it is writing fake customers onto a
  // working floor board, not the handset story an earlier comment told here.
  guardTenants();
  // Fail here rather than at the first staff login: an unset password produces
  // a session-less jar and every authenticated page then answers 303, which
  // reads as four broken routes instead of one missing environment variable.
  staffPassword();
  return { tenants: TENANTS.length, stage: STAGE_NAME };
}

export function visitorVu() {
  tagPhase(STAGE);
  visitor();
}
export function customerVu() {
  tagPhase(STAGE);
  customer();
}
export function staffVu() {
  tagPhase(STAGE);
  staff();
}
export function managerVu() {
  tagPhase(STAGE);
  manager();
}
export function ownerVu() {
  tagPhase(STAGE);
  owner();
}
