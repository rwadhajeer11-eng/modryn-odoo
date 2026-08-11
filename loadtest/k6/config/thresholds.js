// Per-class SLOs from .planning/specs/load-test-spec.md §7.2 / §7.3.
//
// Every request and every check carries exactly one `class` tag. Without it a
// slow /manage/staff render hides inside the same percentile as a fast /shop,
// which is how a ramp reports "fine" right up to the stage it falls over on.
//
// `phase` is set per-iteration by lib/session.js#tagPhase. Gates apply to
// phase:steady only: ramp-in is JIT warm-up, asset-bundle compilation and pool
// growth, and including it drags every percentile far enough to hide a real
// regression (plan §7).

// rpc_read relaxes from 500ms to 1s at 2500+ VU. Pre-declared in the spec so it
// cannot be quietly introduced after a failing run.
const RPC_READ_P95_RELAXED_FROM_VUS = 2500;

export function thresholdsFor(targetVus) {
  const rpcReadP95 = targetVus >= RPC_READ_P95_RELAXED_FROM_VUS ? 1000 : 500;
  return {
    // §7.1: an error is a 5xx, OR a jsonrpc body carrying "error", OR a page
    // missing its body marker. Checks encode all three; http_req_failed alone
    // cannot, because Odoo answers jsonrpc failures with HTTP 200.
    'checks{class:page,phase:steady}': ['rate>0.999'],
    'checks{class:form,phase:steady}': ['rate>0.999'],
    'checks{class:rpc_read,phase:steady}': ['rate>0.999'],
    'checks{class:rpc_write,phase:steady}': ['rate>0.999'],

    'http_req_duration{class:page,phase:steady}': ['p(95)<800', 'p(99)<2000'],
    'http_req_duration{class:form,phase:steady}': ['p(95)<1500', 'p(99)<3000'],
    'http_req_duration{class:rpc_read,phase:steady}': [`p(95)<${rpcReadP95}`, 'p(99)<2000'],
    'http_req_duration{class:rpc_write,phase:steady}': ['p(95)<800', 'p(99)<2000'],

    // static is recorded but never gated: asset bundles are a reverse-proxy
    // question, not an application one. A collapse here still shows in the CSV.
    'http_req_duration{class:static}': [],

    ws_rt: ['p(95)<2000', 'p(99)<5000'],

    // The non-emptiness gate for ws_rt, and it is not decoration. A k6 threshold
    // on a metric with zero samples PASSES, so both percentiles above are
    // vacuous until something has actually been measured — and a sample only
    // exists if a write's own notification survived the whole fan-out back to
    // the VU that wrote it. A bus that delivers nothing reported green.
    //
    // It lives on this Counter rather than on ws_rt because k6 refuses `count`
    // as an aggregation on a Trend: `ws_rt: ['count>0']` is a config error that
    // aborts every run before setup(), which is how it shipped and why no run
    // could start. lib/ws.js increments it on the same line as wsRt.add.
    ws_rt_samples: ['count>0'],

    // Recorded, never gated: a clocked write whose notification never came back
    // (lib/ws.js#notifyClock). It is the other half of ws_rt — the trips that
    // produced no sample — so reading the two together is what tells a fast bus
    // from a silent one. Not a gate because a socket torn down at exactly the
    // wrong moment can produce one, and a campaign must not fail on that.
    ws_unanswered_writes: [],

    // A run that booked NOTHING is not a passing run, whatever the percentiles
    // say. Booking is the one write the visitor and customer journeys exist to
    // exercise, and every way it can fail wholesale — the slot list regressing,
    // /book/submit refusing every hour, the seeded grid drifting out from under
    // the scrapers — leaves the latency gates green because the requests
    // themselves stay fast and well-formed. The rate is not asserted (how many
    // brides book is a workload property, not an SLO); the existence of a single
    // one is, because zero is only ever a broken harness or a broken server.
    booking_created: ['count>0'],

    // A dropped iteration means k6 could not start work it had scheduled — the
    // load generator, not the SUT, is the bottleneck, and every latency number
    // in the run is contaminated.
    dropped_iterations: ['count==0'],
  };
}

// Ramp shapes. Only the steady window is measured; ramp-out is still RUN
// (the tail is data) but excluded from the gates.
export const STAGES = {
  // Smoke proves the harness, not the server: near-zero ramp so almost every
  // sample lands in phase:steady and a broken scenario cannot hide in warm-up.
  smoke: { target: 10, rampUpSec: 10, steadySec: 280, rampDownSec: 10 },
  s1: { target: 100, rampUpSec: 120, steadySec: 600, rampDownSec: 60 },
  s2: { target: 500, rampUpSec: 180, steadySec: 600, rampDownSec: 60 },
  s3: { target: 1000, rampUpSec: 300, steadySec: 900, rampDownSec: 120 },
  s4: { target: 2500, rampUpSec: 300, steadySec: 900, rampDownSec: 120 },
  s5: { target: 5000, rampUpSec: 480, steadySec: 900, rampDownSec: 180 },
  s6: { target: 10000, rampUpSec: 600, steadySec: 900, rampDownSec: 180 },
  // Recovery behaviour, not throughput: 0 -> peak in 60s, hold, drop hard.
  spike: { target: 500, rampUpSec: 60, steadySec: 300, rampDownSec: 10 },
};

// Role mix, spec §4.1. Staff/manager/owner shares are a decision derived from
// "one floor has ~13 staff, ~3 managers, 1 owner" scaled by the tenant count —
// they only hold because tenants grow with VUs.
export const ROLE_MIX = {
  visitor: 0.88,
  customer: 0.08,
  staff: 0.03,
  manager: 0.008,
  owner: 0.002,
};
