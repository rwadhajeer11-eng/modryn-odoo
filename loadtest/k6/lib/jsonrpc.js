// JSON-RPC transport with the double error check built in, so no scenario can
// forget it.
//
// Odoo answers jsonrpc failures with HTTP 200 at BOTH layers:
//
//   transport: {"jsonrpc":"2.0","id":null,"error":{"code":100,...}}
//              — JsonRPCDispatcher.handle_error -> make_json_response, whose
//                status parameter defaults to 200. This is what an expired
//                session and every unhandled server exception look like.
//
//   payload:   {"jsonrpc":"2.0","result":{"error":"forbidden"}}
//              — floor.py's own guards. HTTP 200, transport 200, and a body
//                that says the call did nothing.
//
// A check of `r.status === 200` on a /floor/* route therefore reports 100%
// success while the floor board is completely broken. That is the default
// failure mode of any Odoo load test written without this file.

import http from 'k6/http';
import { check } from 'k6';
import { Rate } from 'k6/metrics';

// Application-level refusals are CORRECT behaviour and must not spend the error
// budget (spec §7.1.3): a staff VU on a manager route, a fixture row a
// concurrent VU archived, a fitting-room collision. They get their own metric
// because their rate is itself a result.
export const appRefusals = new Rate('app_refusals');

// Refusal payloads the controllers return deliberately. Anything else in
// result.error is a finding.
// window_closed and published are the roster answering correctly, not
// failing. window_closed in particular already fired on every
// campaign run outside Thursday 09:00 - Saturday 21:00 Jerusalem, scoring as a
// failed write; now that a manager can move that window per tenant, "is the
// load test red" would otherwise become a data question nobody running it can
// see.
const KNOWN_REFUSALS = [
  'forbidden', 'not_found', 'no_employee',
  'window_closed', 'published',
];

// /floor/room answers a fitting-room collision with `dict(self._board(),
// error=<ValidationError message>)` — a COMPLETE board that also carries an
// error string. The message is a translated sentence, so it can never be on
// the list above; the board alongside it is what identifies the shape. Two
// managers racing for one room is the collision rule working, and its rate is
// a result, not a failure.
// /roster/available answers `{'slots': grid, 'error': message}` the same way,
// and for the same reason: modryn_toggle refuses a slot whose week a manager has
// already published (availability.py:35), and says so in a TRANSLATED sentence
// — he_IL here, so it can no more be listed above than the room-collision one.
//
// It needs its own shape test because a roster grid is not a board: isBoard
// looks for `pending` and `queue`, which the roster has never had. Without this
// the manager scenario publishing a week made every staff availability toggle
// for that week score as a failed check — the manager and staff scenarios run
// concurrently, so whether a smoke run came out clean depended on which of them
// happened to reach the roster first.
function isGrid(result) {
  return !!(result && Array.isArray(result.slots));
}

function isRefusal(result) {
  if (!result || typeof result !== 'object' || !result.error) {
    return false;
  }
  return KNOWN_REFUSALS.indexOf(result.error) !== -1 || isBoard(result) || isGrid(result);
}

// `kind` is 'rpc_read' or 'rpc_write' — the SLO class. Callers pass it rather
// than the helper guessing from the path, because /roster/available reads like
// a read and writes like a write.
export function call(base, path, params, kind, surface) {
  const res = http.post(
    base + path,
    JSON.stringify({ jsonrpc: '2.0', method: 'call', params: params || {} }),
    {
      headers: { 'Content-Type': 'application/json' },
      tags: { class: kind, surface: surface },
    }
  );

  let body = null;
  try {
    body = res.json();
  } catch (e) {
    body = null;
  }

  const transportError = !!(body && body.error);
  const result = body && body.result !== undefined ? body.result : null;
  const payloadError = !!(result && typeof result === 'object' && result.error);

  // A refusal means the server answered correctly; it did not fail.
  const refusal = payloadError && isRefusal(result);
  const ok = res.status === 200 && !transportError && result !== null && (!payloadError || refusal);

  check(res, { [`${path} rpc ok`]: () => ok }, { class: kind, surface: surface });
  appRefusals.add(refusal, { surface: surface });

  return {
    ok: ok,
    refused: refusal,
    error: transportError ? body.error : payloadError ? result.error : null,
    // Returned even on a refusal: a room collision hands back a usable board
    // alongside its message, and discarding it would make the caller re-fetch.
    result: result,
    res: res,
  };
}

// A board is what /floor/data and every floor mutation return. Checking for the
// arrivals gate specifically — a body that is merely an object proves nothing,
// since {'error': ...} is also an object.
export function isBoard(result) {
  return !!(result && Array.isArray(result.pending) && Array.isArray(result.queue));
}
