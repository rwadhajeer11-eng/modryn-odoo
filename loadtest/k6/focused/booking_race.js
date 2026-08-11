// Fifty brides, one hour, one winner.
//
// This test's value is the before/after pair. Before the partial unique index
// on calendar_event it produced duplicate live bookings on one slot; after, it
// must produce exactly one booking and 49 friendly "that time was just taken"
// re-renders — and zero 500s.
//
// THE k6 SIDE ASSERTS THE WINNER COUNT; psql still asserts the rows. Run this
// afterwards, against the tenant:
//
//   select start, count(*) from calendar_event
//   where modryn_is_booking and modryn_cancelled_at is null
//   group by start having count(*) > 1;
//
// Zero rows is the pass. k6 sees how the losers were told and how many 303s came
// back; psql sees what the database actually holds, which is the stronger claim.
//
//   RACE_AT=$(( ($(date +%s) + 30) * 1000 ))
//   k6 run -e TENANT=lt01 -e RACE_AT=$RACE_AT focused/booking_race.js
//
// Run it on a tenant with an otherwise empty calendar and nothing else pointed
// at it. Its whole design assumes a quiet slot list.

import { check, sleep, fail } from 'k6';
import { Counter } from 'k6/metrics';
import exec from 'k6/execution';

import {
  TENANTS,
  guardTenants,
  getForm,
  hasMarker,
  phoneForVu,
  slotValues,
  submitBooking,
} from '../lib/session.js';

const VUS = 50;
const PREFETCH_WINDOW_SEC = 10;

const raceWon = new Counter('race_won');
const raceLost = new Counter('race_lost');
const raceBroken = new Counter('race_broken');

export const options = {
  scenarios: {
    race: {
      executor: 'shared-iterations',
      vus: VUS,
      iterations: VUS, // exactly one attempt each
      maxDuration: '5m',
    },
  },
  thresholds: {
    // EXACTLY ONE bride gets the hour. This is the gate that makes the file able
    // to fail for the regression it was written to detect: drop the partial
    // unique index on calendar_event and all 50 VUs get a 303, so race_won=50,
    // race_lost=0, race_broken=0 — green, with 50 duplicate bookings on one slot.
    // race_broken alone could never see that, because nothing was broken from
    // the racers' point of view; they all won.
    //
    // == and not <=: zero winners is equally a failure. It means every POST was
    // refused, which is a dead slot list or a dead tenant, not a race.
    race_won: ['count==1'],
    // The losers must be TOLD, not crashed. A 500, a non-slot form error, or a
    // server that refuses an hour it is still offering all land here.
    race_broken: ['count==0'],
  },
};

function tenant() {
  const slug = __ENV.TENANT;
  const t = slug ? TENANTS.find((x) => x.slug === slug) : TENANTS[0];
  if (!t) {
    throw new Error(`TENANT=${slug} is not in loadtest/config/tenants.json`);
  }
  return t;
}

export function setup() {
  guardTenants();
  if (!__ENV.RACE_AT) {
    fail('RACE_AT is required: epoch milliseconds when all 50 VUs should post');
  }
  const raceAt = parseInt(__ENV.RACE_AT, 10);
  const leadSec = (raceAt - Date.now()) / 1000;
  if (leadSec < PREFETCH_WINDOW_SEC) {
    fail(
      `RACE_AT is only ${leadSec.toFixed(1)}s away; every VU needs at least ` +
        `${PREFETCH_WINDOW_SEC}s to fetch /book and scrape its CSRF token first`
    );
  }
  return { raceAt: raceAt };
}

export default function bookingRace(data) {
  const t = tenant();

  // Prefetch during the lead-in window: page, CSRF, slot list. All of this must
  // be done BEFORE the barrier, or the requests themselves stagger the start.
  const form = getForm(t.baseUrl + '/book', 'booking');
  check(form.res, { '/book rendered': (r) => hasMarker(r, 'action="/book/submit"') }, {
    class: 'page',
    surface: 'booking',
  });
  const slots = slotValues(form.res.body);
  if (!form.csrf || !slots.length) {
    fail('cannot race: /book offered no slots or no csrf token');
  }

  // THE SAME slot for all 50. Deterministic, not random: the first offered hour
  // is the same string on every VU because they all read the same page.
  const slot = slots[0];

  // Distinct phones. Identical numbers would collapse into one res.partner and
  // muddy the reading of what the losers left behind.
  const phone = phoneForVu(t, exec.vu.idInTest);

  const waitMs = data.raceAt - Date.now();
  if (waitMs > 0) {
    sleep(waitMs / 1000);
  }

  const r = submitBooking(
    t.baseUrl,
    slot,
    {
      name: `Race V${exec.vu.idInTest}`,
      phone: phone,
      csrf_token: form.csrf,
    },
    'booking'
  );

  if (r.ok && !r.lost) {
    raceWon.add(1);
  } else if (r.lost) {
    raceLost.add(1);
  } else {
    // Everything submitBooking classifies as a rejection rather than a lost
    // race: a 500, an error on some field other than the slot, or the server
    // still offering on a fresh /book the very hour it just refused
    // (reason 'slot_offered_then_refused'). None of those is "somebody beat me
    // to it", so none of them may be counted as one.
    raceBroken.add(1, { reason: r.reason || 'unknown' });
  }
  check(r, { 'racer was booked or told the slot was taken': (x) => x.ok }, {
    class: 'form',
    surface: 'booking',
  });
}
