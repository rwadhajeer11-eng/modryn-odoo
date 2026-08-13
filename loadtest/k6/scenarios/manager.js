// Role D — the floor, write-heavy. 0.8% of the workload.
//
// Every one of these writes returns a full _board() to the caller AND publishes
// modryn_queue/update to every subscribed socket in the tenant, each of which
// answers with another /floor/data. One manager tap costs one board rebuild for
// the actor plus one per open board. That amplification is the campaign's main
// subject; this scenario is what drives it.

import http from 'k6/http';
import { check, sleep } from 'k6';
import exec from 'k6/execution';

import { call, isBoard } from '../lib/jsonrpc.js';
import { runBoard, notifyClock } from '../lib/ws.js';
import {
  csrfFrom,
  hasMarker,
  myTenant,
  pageTags,
  formTags,
  phoneForVu,
  staffLogin,
  staffOfLevel,
  staffPassword,
  queueVerify,
} from '../lib/session.js';

const SESSION_MS = 60000;
// Managers act faster than they read (spec §4.3: 2-5s for a working tool).
// The poll interval IS the think time here — the board tick is what paces a
// manager, and adding a second sleep on top would double-count it.
const POLL_MS = 3000;

let signedIn = false;
let board = null;
let walkin = 0;

// ws_rt is gated in config/thresholds.js, and until this existed NOTHING in the
// main run ever recorded a sample: wsRt.add() lived only in focused/bus_storm.js.
// A k6 threshold on a metric with zero samples PASSES — the first smoke run
// reported ws_rt p(95)=0s, green, while measuring nothing at all.
//
// The manager is the right place to close the loop because it owns both halves:
// it makes the write, and its own board socket receives the notification that
// write published.
//
// WHICH writes may be clocked is not a free choice. There are exactly three bus
// publish sites, and only these two floor routes reach one:
//
//   /floor/accept  modryn_accept() sets state -> queue_entry.write() publishes
//                  because 'state' is in vals (queue_entry.py:82). DEAD since
//                  2026-08-13: a verified check-in goes straight to 'waiting',
//                  so board.pending is empty for every entry this harness
//                  creates and the accept branch below never fires. Left in
//                  place because legacy pending rows still reach it, but it is
//                  NO LONGER a source of ws_rt samples.
//   /floor/finish  action_done() writes state='done' -> same guard. Now the
//                  only clocked publish site this scenario actually reaches.
//
// /floor/room was clocked here too and publishes NOTHING: it writes only
// modryn_room_id, which appears in no guard at any of the three sites. Its clock
// was therefore stopped by the next UNRELATED notification, and on a tenant
// where queue check-ins publish constantly that made ws_rt the inter-arrival
// time of anybody's notification — a number that FALLS as load rises, so the
// gate got easier to pass the worse the system behaved.
//
// 15s is well above the p(99)<5000 gate: a trip slower than this is lost, not
// slow, and is counted as unanswered rather than reported as a latency.
const CLOCK_TIMEOUT_MS = 15000;
const clock = notifyClock(CLOCK_TIMEOUT_MS);

// Clocked writes. `call` is unchanged so the rpc_write SLO still measures the
// HTTP leg; ws_rt measures write-accepted -> OUR OWN notification observed,
// which is the fan-out latency the campaign is actually about.
//
// `entryId` is how the notification is attributed: both routes above publish
// modryn.queue.entry#_payload(), whose `id` is the entry that was written
// (queue_entry.py:65-72).
function clockedCall(base, path, params, surface, entryId) {
  // Timestamped before the call, started after: the commit that publishes
  // happens inside this request, so a clock started on the response would
  // subtract the write's own duration from every sample. A clock already in
  // flight must not be replaced, and the write must still happen either way.
  const startedAt = clock.pending() ? null : Date.now();
  const r = call(base, path, params, 'rpc_write', surface);
  // Only start the clock if the write was actually accepted; a refused write
  // publishes no notification, so its clock would never stop and would instead
  // absorb the NEXT write's notification.
  if (startedAt !== null && r.ok && !r.refused) {
    clock.start((p) => !!p && p.id === entryId, startedAt);
  }
  return r;
}

export default function manager() {
  const t = myTenant();
  if (!signedIn) {
    const who = staffOfLevel(t, 'manager');
    const login = staffLogin(t.baseUrl, who.login, staffPassword());
    check(login, { 'manager signed in (303)': (l) => l.ok }, { class: 'form', surface: 'staff' });
    if (!login.ok) {
      sleep(10);
      return;
    }
    signedIn = true;
  }

  const floor = http.get(t.baseUrl + '/floor', { tags: pageTags('floor') });
  check(floor, { '/floor rendered': (r) => hasMarker(r, 'name="modryn_staff.floor_board"') }, {
    class: 'page',
    surface: 'floor',
  });

  runBoard(t.baseUrl, {
    durationMs: SESSION_MS,
    pollEveryMs: POLL_MS,
    onPoll: function () {
      clock.expire();
      refresh(t);
      workTheFloor(t);
    },
    onNotify: function (notif) {
      // ws.js only calls this for ids above the replay high-water mark, so the
      // 50 seconds of history that `last: 0` replays cannot be timed as a round
      // trip. That is why this reads as milliseconds and not as ~50 seconds.
      //
      // stop() records a sample only when this notification carries the id of
      // the entry OUR clocked write touched; everybody else's is still a real
      // notification and still triggers the refresh floor_board.js does, it just
      // does not stop our clock.
      clock.stop(notif);
      refresh(t);
    },
  });
  // The socket is gone; a clock still running belongs to no measurable trip —
  // and is not an unanswered write either, since we stopped listening.
  clock.reset();
}

function refresh(t) {
  const r = call(t.baseUrl, '/floor/data', {}, 'rpc_read', 'floor');
  if (r.ok && isBoard(r.result)) {
    board = r.result;
  }
  check(r, { '/floor/data returned a board': (x) => x.ok && isBoard(x.result) }, {
    class: 'rpc_read',
    surface: 'floor',
  });
}

function workTheFloor(t) {
  if (!board) {
    return;
  }
  // Manufacture something to act on. A manager with an empty floor measures
  // nothing, and on a reset tenant the floor starts empty.
  if (!board.pending.length && !board.queue.length) {
    checkInWalkIn(t);
    return;
  }

  if (board.pending.length) {
    const entry = board.pending[Math.floor(Math.random() * board.pending.length)];
    clockedCall(t.baseUrl, '/floor/accept', { entry_id: entry.id }, 'floor', entry.id);
    return;
  }

  const card = board.queue[Math.floor(Math.random() * board.queue.length)];
  const staffPool = board.staff || [];
  if (staffPool.length) {
    const who = staffPool[Math.floor(Math.random() * staffPool.length)];
    call(
      t.baseUrl,
      '/floor/assign',
      { target: 'queue', target_id: card.id, employee_id: who.id, as_primary: false },
      'rpc_write',
      'floor'
    );
    // A second assign on the same card exercises the helper path and the
    // through-model ordering, which the first assign never touches.
    if (staffPool.length > 1 && Math.random() < 0.4) {
      const helper = staffPool[(staffPool.indexOf(who) + 1) % staffPool.length];
      call(
        t.baseUrl,
        '/floor/assign',
        { target: 'queue', target_id: card.id, employee_id: helper.id, as_primary: false },
        'rpc_write',
        'floor'
      );
    }
  }

  if (board.rooms && board.rooms.length) {
    // Two managers racing for one room is a designed-for outcome: the collision
    // rule answers with a board carrying error=, not a 500. jsonrpc.js counts
    // that as a refusal. A 500 here is the finding.
    //
    // NOT clocked: this writes modryn_room_id only, and no publish site guards
    // on that field, so there is no notification for a clock to wait for.
    const room = board.rooms[Math.floor(Math.random() * board.rooms.length)];
    call(
      t.baseUrl,
      '/floor/room',
      { target: 'queue', target_id: card.id, room_id: room.id },
      'rpc_write',
      'floor'
    );
  }

  // The most expensive single call in the system: a full board build plus a
  // product.product search over every published variant. Clocked: it writes
  // state='done', which publishes.
  const done = clockedCall(t.baseUrl, '/floor/finish', { entry_id: card.id }, 'floor', card.id);
  if (done.ok && Math.random() < 0.3) {
    handoffToAtelier(t, done.result);
  }

  if (Math.random() < 0.1) {
    sosRound(t);
  }
}

function checkInWalkIn(t) {
  // A SEPARATE jar: the check-in form is public, and posting it through the
  // manager's authenticated session would exercise a path no walk-in takes.
  const jar = new http.CookieJar();
  const params = { jar: jar, tags: pageTags('queue') };
  const form = http.get(t.baseUrl + '/queue/checkin', params);
  const token = csrfFrom(form.body);
  if (!token) {
    return;
  }
  // modryn_check_in de-duplicates on an open phone, so a repeated number
  // returns the existing ticket and creates no new card for the manager to
  // work — the scenario would silently stop testing anything.
  walkin += 1;
  const phone = phoneForVu(t, exec.vu.idInTest * 101 + walkin);
  const res = http.post(
    t.baseUrl + '/queue/checkin/submit',
    {
      name: `LoadTest walk-in ${exec.vu.idInTest}/${walkin}`,
      phone: phone,
      client_type: 'bride',
      csrf_token: token,
    },
    { jar: jar, tags: formTags('queue'), redirects: 0 }
  );
  // NOT "issued a ticket": since 2026-08-13 this 303 goes to /queue/verify and
  // creates nothing. Left at the redirect, this function would post two forms
  // per iteration, write ZERO rows, and keep every check green — so the
  // write-heavy manager role would report a healthy floor board with an empty
  // queue behind it.
  check(res, { '/queue/checkin/submit issued a code': (r) => r.status === 303 }, {
    class: 'form',
    surface: 'queue',
  });
  if (res.status !== 303) {
    return;
  }

  // Same jar: /queue/verify reads the half-finished check-in out of the session
  // that /queue/checkin/submit wrote it to.
  const verified = queueVerify(t.baseUrl, phone, { jar: jar });
  check(verified, { '/queue/verify joined the line': (v) => v.ok }, {
    class: 'form',
    surface: 'queue',
  });
}

// /floor/finish returns the whole board with a `finished` key bolted on:
// {customer, phone, variants:[{id,label}]} — the modal's data, so the manager
// can open an alteration task without a second round trip.
function handoffToAtelier(t, boardWithFinished) {
  const finished = (boardWithFinished && boardWithFinished.finished) || {};
  const variants = finished.variants || [];
  call(
    t.baseUrl,
    '/atelier/task/create',
    {
      customer_name: finished.customer || `LoadTest V${exec.vu.idInTest}`,
      customer_phone: finished.phone || null,
      variant_id: variants.length ? variants[0].id : null,
      // Both REQUIRED since the workshop-queue build: the create door refuses
      // a task without them ('missing_priority'/'missing_due'), and neither
      // code is a known refusal — every handoff would fail the rpc_write
      // threshold against a healthy server, the exact silent-drift failure
      // checkInWalkIn's comment warns about.
      priority: ['0', '1', '2'][Math.floor(Math.random() * 3)],
      due_date: new Date(Date.now() + 7 * 864e5).toISOString().slice(0, 10),
    },
    'rpc_write',
    'atelier'
  );
}

function sosRound(t) {
  const raised = call(t.baseUrl, '/floor/sos', { target: 'manager', note: 'load' }, 'rpc_write', 'floor');
  if (!raised.ok || !raised.result || !raised.result.sos || !raised.result.sos.length) {
    return;
  }
  const callId = raised.result.sos[raised.result.sos.length - 1].id;
  call(t.baseUrl, '/floor/sos/ack', { call_id: callId }, 'rpc_write', 'floor');
  call(t.baseUrl, '/floor/sos/resolve', { call_id: callId }, 'rpc_write', 'floor');
}
