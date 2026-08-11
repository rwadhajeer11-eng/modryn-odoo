// How many floor boards can one tenant hold open?
//
// ONE tenant. Nothing else running against it. Three scenarios:
//
//   clients — 25 -> 200 boards, each polling and refreshing on every
//             notification, exactly as floor_board.js does.
//   driver  — one manager doing one cheap write every 2s, so there is always
//             something to fan out.
//   instrument — one VU that times its OWN write to its OWN notification.
//
// The instrument must be its own VU and must measure only itself. k6 VUs do not
// share memory: there is no way for VU 7 to tell VU 112 "I posted at t=…", so
// cross-VU correlation is impossible by construction and any design needing it
// is wrong. What this measures is server-side fan-out latency under the load
// the other 200 VUs are creating, which is the number that matters.
//
// THE STORM MUST ACTUALLY PUBLISH. Both the driver and the instrument used to
// toggle /floor/room, commented as "the cheapest write that still publishes".
// It is not: /floor/room writes modryn_room_id and nothing else, and that field
// appears in none of the three publish guards —
//
//   modryn_queue_poc/models/queue_entry.py:82   'state' in vals
//   modryn_staff/models/assignment.py:113       modryn_employee_id/modryn_helper_ids
//   modryn_staff/models/sos_call.py:96          state/escalated_at/target_id
//
// so the storm generated zero notifications, the instrument recorded zero ws_rt
// samples, and the k6 threshold on that empty metric PASSED. The driver and the
// instrument now toggle /floor/assign + /floor/unassign on a BOOKING card, which
// hits the calendar.event guard and publishes {kind:'booking_assignment', ids}.
//
// The driver and the instrument act on DIFFERENT cards, or the instrument
// cannot tell its own notification from the driver's — and the instrument now
// checks the id in the payload rather than trusting that separation.
//
//   k6 run -e TENANT=lt01 -e MODRYN_DEMO_PASSWORD=... focused/bus_storm.js

import http from 'k6/http';
import { check, sleep } from 'k6';
import exec from 'k6/execution';

import { call, isBoard } from '../lib/jsonrpc.js';
import { runBoard, notifyClock } from '../lib/ws.js';
import {
  TENANTS,
  guardTenants,
  staffLogin,
  staffOfLevel,
  staffPassword,
  pageTags,
} from '../lib/session.js';

const CLIENT_POLL_MS = 10000;
const CLIENT_SESSION_MS = 170000; // just under one 3-minute stage
const DRIVER_WRITE_MS = 2000;
const INSTRUMENT_WRITE_MS = 3000;
const INSTRUMENT_SESSION_MS = 900000;
// Well above the p(99)<5000 gate: slower than this is a lost notification, not
// a slow one, and is counted as unanswered rather than reported as a latency.
const CLOCK_TIMEOUT_MS = 15000;

// The driver and the instrument are constant-vus, 1 each, running the whole
// 18 minutes — so they are in vusActive for every sample the clients take.
// Bucketing by the raw vusActive put every point two boards too high on the one
// axis this entire test is plotted against.
const NON_CLIENT_VUS = 2;

function tenant() {
  const slug = __ENV.TENANT;
  const t = slug ? TENANTS.find((x) => x.slug === slug) : TENANTS[0];
  if (!t) {
    throw new Error(`TENANT=${slug} is not in loadtest/config/tenants.json`);
  }
  return t;
}

export const options = {
  scenarios: {
    // Tag each stage with its client count so /floor/data p95 can be bucketed
    // by "how many boards were open at that moment" — the whole output.
    clients: {
      executor: 'ramping-vus',
      exec: 'client',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 25 },
        { duration: '3m', target: 25 },
        { duration: '30s', target: 50 },
        { duration: '3m', target: 50 },
        { duration: '30s', target: 100 },
        { duration: '3m', target: 100 },
        { duration: '30s', target: 150 },
        { duration: '3m', target: 150 },
        { duration: '30s', target: 200 },
        { duration: '3m', target: 200 },
        { duration: '30s', target: 0 },
      ],
      gracefulRampDown: '30s',
    },
    driver: { executor: 'constant-vus', exec: 'driver', vus: 1, duration: '18m' },
    instrument: { executor: 'constant-vus', exec: 'instrument', vus: 1, duration: '18m' },
  },
  thresholds: {
    // ws_rt_samples first: this file's entire output is ws_rt, and a threshold
    // on a metric with zero samples PASSES. When the storm was driven by a write
    // that published nothing, every gate here was green over an empty set.
    // The gate is on the Counter because k6 rejects `count` on a Trend outright
    // — `ws_rt: ['count>0']` aborts the run as a config error instead of gating.
    ws_rt_samples: ['count>0'],
    ws_rt: ['p(95)<2000', 'p(99)<5000'],
    'http_req_duration{class:rpc_read}': ['p(95)<500'],
    ws_disconnects: ['count==0'],
    // Here it IS a gate, unlike the main run: this test does nothing but drive
    // one card and wait for its notification, so a write the bus never answered
    // is the finding, not background noise.
    ws_unanswered_writes: ['count==0'],
  },
};

export function setup() {
  guardTenants();
  return {};
}

function signIn(level) {
  const t = tenant();
  // stride 1: this file pins EVERY VU to one tenant, so consecutive VU ids are
  // consecutive VUs on it. The default stride (TENANTS.length) is myTenant()'s
  // round-robin and would leave most of the login pool unused here.
  const who = staffOfLevel(t, level, 1);
  const login = staffLogin(t.baseUrl, who.login, staffPassword());
  check(login, { 'signed in (303)': (l) => l.ok }, { class: 'form', surface: 'staff' });
  return login.ok ? t : null;
}

// -------------------------------------------------------------- floor clients

let clientTenant = null;

export function client() {
  if (!clientTenant) {
    clientTenant = signIn('staff');
    if (!clientTenant) {
      sleep(10);
      return;
    }
    http.get(clientTenant.baseUrl + '/floor', { tags: pageTags('floor') });
  }
  // Bucket every sample by the number of BOARDS open right now — the driver and
  // the instrument are VUs but not boards.
  exec.vu.tags.clients = String(Math.max(0, exec.instance.vusActive - NON_CLIENT_VUS));

  runBoard(clientTenant.baseUrl, {
    durationMs: CLIENT_SESSION_MS,
    pollEveryMs: CLIENT_POLL_MS,
    onPoll: function () {
      poll(clientTenant);
    },
    onNotify: function () {
      poll(clientTenant);
    },
  });
}

function poll(t) {
  const r = call(t.baseUrl, '/floor/data', {}, 'rpc_read', 'floor');
  check(r, { '/floor/data returned a board': (x) => x.ok && isBoard(x.result) }, {
    class: 'rpc_read',
    surface: 'floor',
  });
}

// -------------------------------------------------------------------- driver

let driverTenant = null;
let driverSeed = null;
let driverToggle = false;

export function driver() {
  if (!driverTenant) {
    driverTenant = signIn('manager');
    if (!driverTenant) {
      sleep(10);
      return;
    }
  }
  if (!driverSeed) {
    driverSeed = pickBooking(driverTenant, 0);
    if (!driverSeed) {
      sleep(5);
      return;
    }
  }
  driverToggle = !driverToggle;
  assignToggle(driverTenant, driverSeed, driverToggle);
  sleep(DRIVER_WRITE_MS / 1000);
}

/**
 * Put an employee on a booking card, or take her off again.
 *
 * Both directions write modryn_employee_id, so both trip the calendar.event
 * guard in modryn_staff/models/assignment.py:113 and both publish. Alternating
 * matters: /floor/assign returns the board untouched when the employee is
 * already the primary (floor.py:182), and a no-op write publishes nothing.
 *
 * A BOOKING, never a queue entry: assigning to a `waiting` walk-in calls
 * modryn_call (floor.py:204), which texts the customer. On a load tenant that
 * only fills the outbox, but a storm firing it every two seconds for eighteen
 * minutes is not a cost this test means to measure.
 */
function assignToggle(t, seed, on) {
  return call(
    t.baseUrl,
    on ? '/floor/assign' : '/floor/unassign',
    { target: 'booking', target_id: seed.bookingId, employee_id: seed.employeeId },
    'rpc_write',
    'floor'
  );
}

// index 0 for the driver, index 1 for the instrument — different cards AND
// different employees, so neither can no-op against the other's assignment.
function pickBooking(t, index) {
  const r = call(t.baseUrl, '/floor/data', {}, 'rpc_read', 'floor');
  if (!r.ok || !isBoard(r.result)) {
    return null;
  }
  const bookings = r.result.bookings || [];
  const staff = r.result.staff || [];
  if (bookings.length <= index || staff.length <= index) {
    return null;
  }
  return { bookingId: bookings[index].id, employeeId: staff[index].id };
}

// ---------------------------------------------------------------- instrument

let instTenant = null;

export function instrument() {
  if (!instTenant) {
    instTenant = signIn('manager');
    if (!instTenant) {
      sleep(10);
      return;
    }
  }
  const seed = pickBooking(instTenant, 1);
  if (!seed) {
    sleep(5);
    return;
  }

  const clock = notifyClock(CLOCK_TIMEOUT_MS);
  let toggle = false;

  runBoard(instTenant.baseUrl, {
    durationMs: INSTRUMENT_SESSION_MS,
    pollEveryMs: INSTRUMENT_WRITE_MS,
    onPoll: function () {
      // Only one write in flight at a time: overlapping writes would let a
      // later notification stop the earlier clock and report a latency that
      // never happened. expire() is what stops a dropped notification from
      // wedging that rule for the rest of the run.
      clock.expire();
      if (clock.pending()) {
        return;
      }
      toggle = !toggle;
      // Before the call: the commit that publishes happens inside this request.
      const startedAt = Date.now();
      const r = assignToggle(instTenant, seed, toggle);
      if (!r.ok || r.refused) {
        return; // a refused write publishes nothing; there is no trip to time
      }
      clock.start(
        // assignment.py:116 sends {'kind': 'booking_assignment', 'ids': [...]}.
        // Matching on OUR booking id is the whole point: the driver publishes
        // every 2s and 200 boards are reacting, so "the next notification" is
        // somebody else's far more often than it is ours.
        (p) =>
          !!p &&
          p.kind === 'booking_assignment' &&
          Array.isArray(p.ids) &&
          p.ids.indexOf(seed.bookingId) !== -1,
        startedAt
      );
    },
    // Only notifications with an id above the replay high-water mark reach
    // here; ws.js drains the 50 seconds of history that last:0 replays.
    onNotify: function (notif) {
      clock.stop(notif);
    },
  });
  clock.reset();
}
