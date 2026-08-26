// Role C — the floor, read-mostly. 3% of the workload.
//
// Staff hold ONE websocket each and refresh the whole board on every
// notification, which is the amplification loop the campaign is built around:
// one manager tap -> one board rebuild for the actor, one bus notification to
// every open board, one more /floor/data per board.
//
// /atelier is NOT in this scenario. atelier.py:98 calls _is_manager(), so a
// staff VU gets a 404 there — and a status check would call that healthy.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter } from 'k6/metrics';

import { call, isBoard } from '../lib/jsonrpc.js';
import { runBoard } from '../lib/ws.js';
import {
  authedPage,
  myTenant,
  pageTags,
  hasMarker,
  staffLogin,
  staffOfLevel,
  staffPassword,
} from '../lib/session.js';

const SESSION_MS = 60000; // how long one VU holds its board open per iteration
const POLL_MIN_MS = 5000;
const POLL_MAX_MS = 12000;

let signedIn = false;

export default function staff() {
  const t = myTenant();
  if (!signedIn) {
    const who = staffOfLevel(t, 'staff');
    const login = staffLogin(t.baseUrl, who.login, staffPassword());
    check(login, { 'staff signed in (303)': (l) => l.ok }, { class: 'form', surface: 'staff' });
    if (!login.ok) {
      sleep(10);
      return;
    }
    signedIn = true;
  }

  const floor = http.get(t.baseUrl + '/floor', { tags: pageTags('floor') });
  // The board is an Owl island mounted from a placeholder tag, not server-side
  // markup — this is the only string that proves /floor really rendered rather
  // than the website layer serving a styled 200 error page.
  check(floor, { '/floor rendered': (r) => hasMarker(r, 'name="modryn_staff.floor_board"') }, {
    class: 'page',
    surface: 'floor',
  });

  const pollEvery = POLL_MIN_MS + Math.floor(Math.random() * (POLL_MAX_MS - POLL_MIN_MS));
  runBoard(t.baseUrl, {
    durationMs: SESSION_MS,
    pollEveryMs: pollEvery,
    onPoll: function () {
      pollBoard(t);
      // Occasional side errands, done on the poll tick so they inherit the
      // board's think-time rhythm rather than adding a second timer.
      const roll = Math.random();
      if (roll < 0.15) {
        setRoom(t);
      } else if (roll < 0.25) {
        call(t.baseUrl, '/atelier/my', {}, 'rpc_read', 'atelier');
      } else if (roll < 0.35) {
        rosterAvailability(t);
      }
    },
    // floor_board.js subscribes with `() => this.refresh()`. One extra board
    // fetch per notification, no debounce — modelling it any other way
    // understates the fan-out this whole campaign is measuring.
    onNotify: function () {
      pollBoard(t);
    },
  });
}

let lastBoard = null;

function pollBoard(t) {
  const r = call(t.baseUrl, '/floor/data', {}, 'rpc_read', 'floor');
  if (r.ok && isBoard(r.result)) {
    lastBoard = r.result;
  }
  check(r, { '/floor/data returned a board': (x) => x.ok && isBoard(x.result) }, {
    class: 'rpc_read',
    surface: 'floor',
  });
}

// The only write a plain staff VU makes: floor.py:299 gates set_room on
// _is_staff, not _is_manager.
function setRoom(t) {
  if (!lastBoard || !lastBoard.rooms || !lastBoard.rooms.length) {
    return;
  }
  // _board() rows carry no discriminator, so the target kind has to come from
  // WHICH list the row was in — _resolve_target browses modryn.queue.entry for
  // 'queue' and calendar.event for 'booking', and the two id spaces overlap.
  const cards = (lastBoard.queue || [])
    .map((c) => ({ target: 'queue', id: c.id }))
    .concat((lastBoard.bookings || []).map((c) => ({ target: 'booking', id: c.id })));
  if (!cards.length) {
    return;
  }
  const card = cards[Math.floor(Math.random() * cards.length)];
  const room = lastBoard.rooms[Math.floor(Math.random() * lastBoard.rooms.length)];
  // A room collision comes back as a board with error= set, not a 500. That is
  // the fitting-room rule firing and it is counted as a refusal, not a failure.
  call(
    t.baseUrl,
    '/floor/room',
    { target: card.target, target_id: card.id, room_id: room.id },
    'rpc_write',
    'floor'
  );
}

// Slot ids come off the rendered /roster rows. GET /roster materialises next
// week's slots ON READ, so the first hit per week per tenant is far more
// expensive than the rest — a legitimate finding, not noise.
function rosterAvailability(t) {
  const page = http.get(t.baseUrl + '/roster', { tags: pageTags('roster') });
  // modryn_roster_grid is the week grid itself; /web/login renders 200 without
  // it, so this distinguishes "roster rendered" from "silently logged out".
  check(page, { '/roster rendered': (r) => authedPage(r, 'modryn_roster_grid') }, {
    class: 'page',
    surface: 'roster',
  });
  const cells = availabilityCells(page.body);
  if (!cells.length) {
    // Counted, not silently returned from. This guard used to hide a whole
    // class of breakage: if the cell markup changed shape, the scrape came back
    // empty, /roster/available was never called, and rpc_write stayed green on
    // samples that were never taken. rosterCellsMissing is gated in
    // config/thresholds.js so an empty scrape now fails the run.
    rosterCellsMissing.add(1);
    return;
  }
  const cell = cells[Math.floor(Math.random() * cells.length)];
  call(
    t.baseUrl,
    '/roster/available',
    { day: cell.day, shift_type: cell.type, week: 0 },
    'rpc_write',
    'roster'
  );
}

// Gated at zero in config/thresholds.js: an empty scrape means the write
// under test was never attempted, and a success rate over zero samples is
// a perfect score for doing nothing.
const rosterCellsMissing = new Counter('roster_cells_missing');

// The availability grid is addressed by DAY and PART OF DAY now, not by slot
// id: a cell exists for Friday evening whether or not the boutique has ever
// run a Friday evening shift, so there is no slot id to name it by.
const CELL_RE = /data-day="(\d{4}-\d{2}-\d{2})"\s+data-type="([a-z]+)"/g;

export function availabilityCells(body) {
  const out = [];
  let m;
  CELL_RE.lastIndex = 0;
  while ((m = CELL_RE.exec(body || '')) !== null) {
    out.push({ day: m[1], type: m[2] });
  }
  return out;
}

// Still used by the manager's assign checkboxes, which DO carry a slot id -
// she is filling a real shift, not declaring when she is free.
const SLOT_ID_RE = /data-slot="(\d+)"/g;

export function slotIds(body) {
  const seen = {};
  const out = [];
  let m;
  SLOT_ID_RE.lastIndex = 0;
  while ((m = SLOT_ID_RE.exec(body || '')) !== null) {
    if (!seen[m[1]]) {
      seen[m[1]] = true;
      out.push(parseInt(m[1], 10));
    }
  }
  return out;
}
