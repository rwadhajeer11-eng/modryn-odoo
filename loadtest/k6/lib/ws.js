// One floor board's websocket: connect, subscribe once, stay silent, react.
//
// Mirrors what addons/modryn_staff/static/src/floor/floor_board.js actually
// does: onWillStart it calls bus.addChannel("modryn_queue") and subscribes to
// "modryn_queue/update" with a handler that is exactly `() => this.refresh()`,
// i.e. one more POST /floor/data per notification. That refresh is the
// amplification the whole campaign is about, so a client that opens a socket
// and ignores it measures the wrong system.

import ws from 'k6/ws';
import { Trend, Counter } from 'k6/metrics';

import { sessionCookieHeader } from './session.js';

export const wsRt = new Trend('ws_rt', true);
export const wsNotifications = new Counter('ws_notifications');
export const wsDisconnects = new Counter('ws_disconnects');

// Counts exactly the ws_rt samples, and exists ONLY so a threshold can assert
// that ws_rt was measured at all. k6 rejects `count` as an aggregation on a
// Trend — `ws_rt: ['count>0']` is not a weak gate, it is a config error that
// aborts the whole run before setup() ("unsupported aggregation method count on
// metric of type trend"). So the non-emptiness gate has to live on a Counter.
// Incremented on the same line as wsRt.add so the two cannot drift apart.
export const wsSamples = new Counter('ws_rt_samples');

// A clocked write whose notification never came back. Counted, never guessed at
// with a fabricated latency sample: a fan-out that silently drops a
// notification is the failure this whole file exists to catch, so it must show
// up as its own number rather than as a missing one.
export const wsUnanswered = new Counter('ws_unanswered_writes');

const QUEUE_CHANNEL = 'modryn_queue';

// Notifications that arrive in this window after subscribing are replay, not
// news. Subscribing with last:0 makes bus.bus._poll return every notification
// created in the last TIMEOUT=50 seconds, so a latency measurement taken
// without draining first reports 50 seconds of other people's history as this
// VU's round trip.
const REPLAY_DRAIN_MS = 500;

// 1000 NORMAL_CLOSURE, and 1001 GOING_AWAY because that is what k6 reports for
// a close this harness initiates itself. Measured, not assumed.
const CLEAN_CLOSE_CODES = [1000, 1001];

/**
 * The application payload inside a bus frame, or null.
 *
 * The wire shape is fixed by odoo/addons/bus/models/bus.py#_poll, which builds
 * {'id': …, 'message': orjson.loads(…)} where the message is the
 * {'type': …, 'payload': …} envelope _sendone wrote — the same destructuring
 * bus_service.js does at `data.map(({id, message}) => ({id, ...message}))`.
 */
export function payloadOf(notif) {
  const message = notif && notif.message;
  return message && typeof message === 'object' ? message.payload : null;
}

/**
 * A clock for ONE write at a time that stops only on the notification that
 * write itself published.
 *
 * Taking "the next notification that arrives" instead is not a weaker
 * measurement, it is a different one: on a busy tenant queue check-ins publish
 * constantly, so an unattributed clock degenerates into the inter-arrival time
 * of ANYBODY's notification — a number that FALLS as load rises, making the
 * ws_rt gate easier to pass the worse the system behaves.
 *
 * `matcher(payload)` is supplied by the caller because only the caller knows
 * which record it just wrote. Callers must only start the clock on a write that
 * genuinely publishes; see the guards in modryn_queue_poc/models/queue_entry.py
 * and modryn_staff/models/assignment.py for which writes those are.
 */
export function notifyClock(timeoutMs) {
  let startedAt = null;
  let matcher = null;

  return {
    pending: function () {
      return startedAt !== null;
    },
    // `atMs` is the caller's own pre-write timestamp, and passing it is not
    // optional bookkeeping: the write's HTTP leg is where the commit — and
    // therefore the publish — happens, so starting the clock after call()
    // returns silently subtracts the whole request from every sample.
    //
    // false when a write is already in flight: overlapping writes would let a
    // later notification stop the earlier clock.
    start: function (fn, atMs) {
      if (startedAt !== null) {
        return false;
      }
      startedAt = atMs || Date.now();
      matcher = fn;
      return true;
    },
    // Returns true when this notification was the answer to our own write.
    stop: function (notif) {
      if (startedAt === null || !matcher(payloadOf(notif))) {
        return false;
      }
      wsRt.add(Date.now() - startedAt);
      wsSamples.add(1);
      startedAt = null;
      matcher = null;
      return true;
    },
    // Call every tick. Without this a write whose notification never arrives
    // wedges the clock forever and the instrument silently stops sampling.
    expire: function () {
      if (startedAt !== null && Date.now() - startedAt > timeoutMs) {
        wsUnanswered.add(1);
        startedAt = null;
        matcher = null;
      }
    },
    // The socket is gone; a clock still running belongs to no measurable trip.
    reset: function () {
      startedAt = null;
      matcher = null;
    },
  };
}

/**
 * Open one board socket and run it for `durationMs`.
 *
 * @param base        tenant base URL, e.g. http://lt07.localtest.me:8069
 * @param opts.durationMs   how long to hold the socket open
 * @param opts.pollEveryMs  board poll interval; omit to never poll on a timer
 * @param opts.onPoll       (socket) => void — the timer tick
 * @param opts.onNotify     (notification, socket) => void — id > high-water only
 * @param opts.onOpen       (socket) => void — after the replay drain
 */
export function runBoard(base, opts) {
  const wsUrl = base.replace(/^http/, 'ws') + '/websocket';
  const cookie = sessionCookieHeader(base);
  const host = base.replace(/^https?:\/\//, '');

  const params = {
    headers: {
      Cookie: cookie,
      // Odoo's handshake validator lists 'origin' in
      // _REQUIRED_HANDSHAKE_HEADERS, so a missing Origin is a flat 400 that
      // reads like the route is gone.
      Origin: base,
      Host: host,
    },
    tags: { class: 'ws', surface: 'floor' },
  };

  let maxSeenId = 0;
  let draining = true;

  const res = ws.connect(wsUrl, params, function (socket) {
    socket.on('open', function () {
      // Exactly one client->server frame for the life of the socket. The
      // inbound limiter allows a burst of 10 frames and then one per 0.2s;
      // a client that re-subscribes in a loop is disconnected, and a
      // disconnect looks exactly like a server failure in the results.
      socket.send(JSON.stringify({ event_name: 'subscribe', data: { channels: [QUEUE_CHANNEL], last: 0 } }));
    });

    socket.setTimeout(function () {
      draining = false;
      if (opts.onOpen) {
        opts.onOpen(socket);
      }
    }, REPLAY_DRAIN_MS);

    socket.on('message', function (raw) {
      let batch;
      try {
        batch = JSON.parse(raw);
      } catch (e) {
        return;
      }
      if (!Array.isArray(batch)) {
        return;
      }
      for (const notif of batch) {
        if (typeof notif.id !== 'number' || notif.id <= maxSeenId) {
          continue;
        }
        maxSeenId = notif.id;
        if (draining) {
          continue; // replay: counted as history, never as a round trip
        }
        wsNotifications.add(1);
        if (opts.onNotify) {
          opts.onNotify(notif, socket);
        }
      }
    });

    if (opts.pollEveryMs && opts.onPoll) {
      socket.setInterval(function () {
        if (!draining) {
          opts.onPoll(socket);
        }
      }, opts.pollEveryMs);
    }

    socket.on('close', function (code) {
      // 1013 TRY_LATER is what RateLimitExceededException closes with, and that
      // is what this counter is for.
      //
      // 1001 has to be here or the counter is pure noise: k6 reports OUR OWN
      // socket.close() below as 1001 (GOING_AWAY), not 1000, so the previous
      // `code !== 1000` test fired on every normal end-of-session close —
      // 10 sessions, 10 "disconnects" on the first smoke run. A real 1013
      // would have been indistinguishable from that background rate.
      if (code && CLEAN_CLOSE_CODES.indexOf(code) === -1) {
        wsDisconnects.add(1, { code: String(code) });
      }
    });

    socket.setTimeout(function () {
      socket.close();
    }, opts.durationMs);
  });

  return res;
}
