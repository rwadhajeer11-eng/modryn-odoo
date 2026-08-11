// Role E — configuration. 0.2% of the workload, and deliberately tiny.
//
// The owner is the only INTERNAL user, so she is the only role that consumes an
// Odoo seat. One or two per tenant, which is also what reality looks like.
//
// Owner writes must not inflate the tables between stages — /manage/staff/new
// provisions a res.users, and a run that adds hundreds of them changes the
// system under measurement (spec §2.5). So the only write here is a role
// creation with a FIXED name per tenant: the first iteration creates one row,
// every later one hits the uniqueness constraint and redirects with "that role
// already exists". Same code path, bounded growth.

import http from 'k6/http';
import { check, sleep } from 'k6';

import { call } from '../lib/jsonrpc.js';
import { slotIds } from './staff.js';
import {
  authedPage,
  csrfFrom,
  myTenant,
  pageTags,
  formTags,
  staffLogin,
  staffOfLevel,
  staffPassword,
} from '../lib/session.js';

const THINK_MIN = 10;
const THINK_MAX = 30;
const ROLE_WRITE_SHARE = 0.2;
const ROSTER_SHARE = 0.1;

let signedIn = false;

function think() {
  sleep(THINK_MIN + Math.random() * (THINK_MAX - THINK_MIN));
}

export default function owner() {
  const t = myTenant();
  if (!signedIn) {
    const who = staffOfLevel(t, 'owner');
    const login = staffLogin(t.baseUrl, who.login, staffPassword());
    check(login, { 'owner signed in (303)': (l) => l.ok }, { class: 'form', surface: 'staff' });
    if (!login.ok) {
      sleep(10);
      return;
    }
    signedIn = true;
  }

  // landing_for() sends the owner here and everyone else to /floor.
  // Every /manage/<x> page renders its rows as `action="/manage/<x>/..."`
  // forms (archive on each row, plus a create form on all but staff). The
  // login page cannot contain that, which is the whole point: a logged-out
  // request lands on /web/login and answers 200, so a status check passes
  // while the owner journey is not happening. Verified against all five.
  const staffPage = http.get(t.baseUrl + '/manage/staff', { tags: pageTags('manage') });
  check(staffPage, { '/manage/staff rendered': (r) => authedPage(r, 'action="/manage/staff/') }, {
    class: 'page',
    surface: 'manage',
  });
  think();

  const rolesPage = http.get(t.baseUrl + '/manage/roles', { tags: pageTags('manage') });
  check(rolesPage, { '/manage/roles rendered': (r) => authedPage(r, 'action="/manage/roles/') }, {
    class: 'page',
    surface: 'manage',
  });

  for (const path of ['/manage/rooms', '/manage/pieces', '/manage/shifts']) {
    think();
    const res = http.get(t.baseUrl + path, { tags: pageTags('manage') });
    check(res, { [`${path} rendered`]: (r) => authedPage(r, `action="${path}/`) }, {
      class: 'page',
      surface: 'manage',
    });
  }
  think();

  if (Math.random() < ROLE_WRITE_SHARE) {
    // Fresh token from the page just rendered. The login rotation already
    // happened, but every render hands out a valid one and re-scraping keeps
    // the rule uniform: never reuse a token across a login boundary.
    const token = csrfFrom(rolesPage.body);
    if (token) {
      const res = http.post(
        t.baseUrl + '/manage/roles/new',
        { name: 'LoadTest role', csrf_token: token },
        { tags: formTags('manage'), redirects: 0 }
      );
      // Both the create and the duplicate answer 303 — the duplicate carries
      // ?error= in the Location. Either is correct.
      check(res, { '/manage/roles/new accepted': (r) => r.status === 303 }, {
        class: 'form',
        surface: 'manage',
      });
    }
  }

  if (Math.random() < ROSTER_SHARE) {
    rosterRound(t);
  }
  think();
}

function rosterRound(t) {
  // GET /roster materialises next week's slots ON READ (modryn_ensure_week), so
  // the first hit per week per tenant is far more expensive than the rest. That
  // spike is a legitimate finding, not noise — do not warm it away.
  const page = http.get(t.baseUrl + '/roster?week=0', { tags: pageTags('roster') });
  check(page, { '/roster rendered': (r) => authedPage(r, 'modryn_roster_grid') }, {
    class: 'page',
    surface: 'roster',
  });
  const ids = slotIds(page.body);
  if (!ids.length) {
    return;
  }
  const slotId = ids[Math.floor(Math.random() * ids.length)];
  const employeeId = employeeIdFrom(page.body);
  if (employeeId) {
    call(
      t.baseUrl,
      '/roster/assign',
      { slot_id: slotId, employee_id: employeeId, working: true, week: 0 },
      'rpc_write',
      'roster'
    );
  }
  call(t.baseUrl, '/roster/publish', { week: 0 }, 'rpc_write', 'roster');
}

// The assign checkbox carries both ids; taking the employee from the same
// markup as the slot keeps the pair valid without a second request. It is
// absent until somebody has offered availability for that week, so on a freshly
// reset tenant this returns null and the assign is skipped — expected, not a
// fault. /roster/publish still runs, which is the write worth measuring here.
const EMPLOYEE_RE = /data-employee="(\d+)"/;

function employeeIdFrom(body) {
  const m = EMPLOYEE_RE.exec(body || '');
  return m ? parseInt(m[1], 10) : null;
}
