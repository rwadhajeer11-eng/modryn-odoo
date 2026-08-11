// Role B — the verified customer. 8% of the workload.
//
// A verified customer is a res.partner id in the session, not a res.users, so
// this role consumes no Odoo seat. It does consume OTP issues, and those are
// the constraint that shapes the whole scenario: modryn.otp.code allows THREE
// per phone per rolling hour, counted on rows created regardless of whether
// they were used, expired or wrong. So: log in once per VU per run, hold the
// session, and treat a second login as an incident rather than a retry.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate } from 'k6/metrics';
import exec from 'k6/execution';

import {
  authedPage,
  customerLogin,
  getForm,
  hasMarker,
  myTenant,
  pageTags,
  formTags,
  phoneForVu,
  slotValues,
  submitBooking,
} from '../lib/session.js';

const THINK_MIN = 3;
const THINK_MAX = 8;
const CANCEL_SHARE = 0.25;

const otpLoginOk = new Rate('otp_login_ok');

// Per-VU, survives iterations within the VU. k6 VUs do not share memory, which
// is exactly what we want here — one phone, one login, one VU.
let loggedIn = false;
let myEventId = null;

function think() {
  sleep(THINK_MIN + Math.random() * (THINK_MAX - THINK_MIN));
}

export default function customer() {
  const t = myTenant();
  const phone = phoneForVu(t, exec.vu.idInTest);

  if (!loggedIn) {
    // Book first, so the portal has something to show and something to cancel.
    myEventId = bookOnce(t, phone);
    think();

    const login = customerLogin(t.baseUrl, phone);
    otpLoginOk.add(login.ok);
    check(login, { 'customer OTP login': (l) => l.ok }, { class: 'form', surface: 'portal' });
    if (!login.ok) {
      // Do NOT retry. Three issues per hour is the whole budget and a hot loop
      // here burns it in one iteration, after which every customer VU on this
      // tenant reports rate_limited for the rest of the run.
      sleep(30);
      return;
    }
    loggedIn = true;
    think();
  }

  // No body marker here on purpose: a verified customer with zero live bookings
  // renders a legitimate empty list, so any row-shaped marker would fail on a
  // correct page. The landing URL is the discriminator — an unverified session
  // is redirected to /my/login, which also answers 200.
  const list = http.get(t.baseUrl + '/my/bookings', { tags: pageTags('portal') });
  check(list, { '/my/bookings rendered': (r) => authedPage(r) }, {
    class: 'page',
    surface: 'portal',
  });
  think();

  // The most expensive thing a customer can do: writes calendar.event, frees
  // the slot, runs the day-waitlist refill, enqueues an SMS through the outbox
  // and wakes its drain cron. Write contention shows here first.
  if (myEventId && Math.random() < CANCEL_SHARE) {
    cancel(t, myEventId);
    myEventId = null;
  }
  think();
}

function bookOnce(t, phone) {
  const form = getForm(t.baseUrl + '/book', 'booking');
  check(form.res, { '/book rendered': (r) => hasMarker(r, 'action="/book/submit"') }, {
    class: 'page',
    surface: 'booking',
  });
  const slots = slotValues(form.res.body);
  if (!form.csrf || !slots.length) {
    return null;
  }
  const booked = submitBooking(
    t.baseUrl,
    slots[Math.floor(Math.random() * slots.length)],
    {
      name: `LoadTest customer V${exec.vu.idInTest}`,
      phone: phone,
      csrf_token: form.csrf,
    },
    'booking'
  );
  check(booked, { '/book/submit accepted or lost the slot': (b) => b.ok }, {
    class: 'form',
    surface: 'booking',
  });
  return booked.eventId;
}

function cancel(t, eventId) {
  // The GET renders the confirmation and calls session.touch(); the CSRF token
  // for the POST must come from it.
  const form = getForm(`${t.baseUrl}/my/cancel/${eventId}`, 'portal');
  if (form.res.status !== 200 || !form.csrf) {
    // 404 means the event is already cancelled or in the past — correct, not an
    // error, and not worth a check that would fail on a legitimate state.
    return;
  }
  const res = http.post(
    `${t.baseUrl}/my/cancel/${eventId}`,
    { csrf_token: form.csrf },
    { tags: formTags('portal'), redirects: 0 }
  );
  check(res, { '/my/cancel accepted': (r) => r.status === 303 }, {
    class: 'form',
    surface: 'portal',
  });
}
