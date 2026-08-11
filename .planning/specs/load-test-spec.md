# Load-test specification

_Written 2026-08-10 against commit `ae84376`. Every code claim below was checked against the
files in `addons/` and, where marked, against the running server and Postgres._

---

## 1. Purpose, and the honest framing

This campaign does **not** exist to prove MODRYN-on-Odoo survives 10,000 concurrent users. It
exists to find the number where it stops surviving, and to attach a named, file-referenced cause
to that number.

State this plainly to anyone reading the results:

> This architecture is unlikely to pass 2,500–5,000 concurrent cleanly, and very unlikely to
> reach 10,000, without the Phase 1 fixes. The deliverable is the **measured ceiling with
> per-bottleneck evidence**, not a 10k pass.

Three reasons that framing is the honest one, all of them structural rather than tuning
problems:

| # | Structural fact | Where |
|---|---|---|
| 1 | Every write on the floor board broadcasts on one per-tenant bus channel, and every connected board answers by refetching the **entire** board. Cost of one manager's drag is `O(connected boards)` full board builds. | `addons/modryn_staff/static/src/floor/floor_board.js:38-49`, `addons/modryn_staff/models/assignment.py:108-120` |
| 2 | The board build itself is 10 `search()` calls plus two separate N+1 loops, none of it cached. | `addons/modryn_staff/controllers/floor.py:48-135` + `addons/modryn_atelier/controllers/atelier.py:18-37` |
| 3 | Twilio is called **synchronously inside the request handler** with a 10-second timeout. The booking POST that texts a confirmation cannot return before Twilio does. | `addons/modryn_portal/models/sms.py:88-93` (`SEND_TIMEOUT = 10`) |

A campaign that reported "PASS at 10k" against this code would be reporting a measurement
error, not a result. Design the run so that a false pass is impossible — see §7 on what counts
as an error, which is the single most important definition in this document.

**Success criterion for the campaign itself:** for each of the six ramp stages we can state
(a) whether SLOs held, and (b) if they did not, which of the named bottlenecks in §10 was the
first to break, supported by server-side instrumentation from §8. A run that produces latency
numbers with no attributable cause has failed even if the numbers are good.

---

## 2. Scope — the five roles and the exact routes each exercises

The route inventory below is the complete set of `@http.route` decorators in `addons/`. It was
produced by grepping every controller, not from memory. Storefront routes (`/`, `/shop`,
`/shop/<slug>`) come from stock `website` / `website_sale`, which `modryn_theme` depends on
(`addons/modryn_theme/__manifest__.py`) — there is no MODRYN controller for them.

Route type legend: **http** = HTML render; **jsonrpc** = JSON-RPC POST (Odoo 19 renamed
`type='json'` to `type='jsonrpc'`; `type='json'` survives as a deprecated alias —
`odoo/odoo/http.py:815`).

### 2.1 Role A — Public visitor (unauthenticated)

| Method | Route | Type | Auth | Source |
|---|---|---|---|---|
| GET | `/` | http | public | stock `website` |
| GET | `/shop` | http | public | stock `website_sale` |
| GET | `/shop/<slug>-<id>` | http | public | stock `website_sale` |
| GET | `/en/shop`, `/ar/shop`, `/en/…`, `/ar/…` | http | public | stock `website` i18n prefixes |
| GET | `/book` | http | public | `modryn_booking/controllers/main.py:116` |
| GET | `/book/dress/<int:dress_id>` | http | public | `main.py:126` |
| POST | `/book/submit` | http, csrf | public | `main.py:150` |
| GET | `/book/confirmed/<int:event_id>` | http | public | `main.py:238` |
| POST | `/waitlist/join` | http, csrf | public | `modryn_portal/controllers/waitlist.py:16` |
| GET | `/waitlist/done` | http | public | `waitlist.py:36` |
| GET | `/queue/checkin` | http | public | `modryn_queue_poc/controllers/main.py:9` |
| POST | `/queue/checkin/submit` | http, csrf | public | `main.py:16` |
| GET | `/q/<string:token>` | http | public | `main.py:40` |
| GET | `/queue/sign` | http | public | `main.py:64` |

Verified live (`curl -H 'Host: bella.localtest.me'`): `/`, `/shop`, `/book`, `/queue/checkin`,
`/waitlist/done` all return 200.

Product URLs on a seeded tenant are Hebrew-slugged and percent-encoded in flight, e.g.
`/shop/שמלת-כלה-אמילי-2`. The k6 script must use real slugs pulled from the tenant, not
synthesised ones — and must exercise the `/en/` and `/ar/` prefixes, because those are distinct
render paths and distinct cache keys.

### 2.2 Role B — Verified customer (phone + SMS OTP, session-bound partner id)

A verified customer is a **partner id in the session, not a `res.users`** — see the class
docstring at `modryn_portal/controllers/portal.py:50-57`. There is no login record to
provision, which matters: role B does not consume Odoo user rows.

| Method | Route | Type | Source |
|---|---|---|---|
| GET | `/my/login` | http | `portal.py:92` |
| POST | `/my/login` | http, csrf | `portal.py:103` |
| GET | `/my/verify` | http | `portal.py:116` |
| POST | `/my/verify` | http, csrf | `portal.py:126` |
| GET | `/my/bookings` | http | `portal.py:149` |
| GET | `/my/cancel/<int:event_id>` | http | `portal.py:159` |
| POST | `/my/cancel/<int:event_id>` | http, csrf | `portal.py:178` |
| GET | `/my/logout` | http | `portal.py:206` |
| GET | `/b/<string:token>` | http | `booking_link.py:36` |
| POST | `/b/<string:token>/confirm` | http, csrf | `booking_link.py:51` |
| POST | `/b/<string:token>/cancel` | http, csrf | `booking_link.py:61` |
| GET | `/claim/<string:token>` | http | `waitlist.py:70` |
| POST | `/claim/<string:token>` | http, csrf | `waitlist.py:84` |

**OTP is not scriptable end-to-end without a seam.** `modryn.otp.code` stores the code hashed
(`otp.py:38-45`) and the plaintext exists only in the SMS body. On a load tenant with no Twilio
credentials, `modryn.sms.send` logs the body and returns `(True, 'logged')`
(`sms.py:76-81`). Two options, decide before the run:

- **B1 (preferred):** the load harness reads the code out of Postgres by recomputing the HMAC —
  impossible, the hash is one-way. So instead: pre-issue codes via `odoo-bin shell` before the
  run and hand k6 a `(phone, code)` fixture list, sized so no code is reused inside its
  5-minute TTL (`CODE_TTL_MINUTES = 5`).
- **B2:** scrape the server log for `[modryn.sms] (no Twilio configured) to=… body=…` lines.
  Fragile and it couples the generator to log shipping. Use only if B1 is impractical.

Either way, respect `MAX_SENDS_PER_HOUR = 3` per phone (`otp.py:13,55-60`) — a customer VU that
loops `/my/login` on one number gets `rate_limited` after three attempts, which is correct
behaviour that would otherwise read as a load failure. Give each customer VU its own phone from
a pool of at least `peak_customer_VUs × 2`.

`/b/<token>` and `/claim/<token>` tokens: the booking token is a signed HMAC of the id
(`booking_comms.py:31-53`), the claim token is `secrets.token_urlsafe(16)` stored on the row
(`day_waitlist.py:46`). Both must be pre-seeded into fixture files; neither is guessable and
neither should be.

### 2.3 Role C — Staff (portal user, `group_boutique_staff`)

Sign-in is `/staff/login`, which calls the same `session.authenticate()` the stock controller
calls (`modryn_staff/controllers/auth.py:60-62`). Staff are **portal** users, not internal
(`hr_employee.py:115-119`).

| Method | Route | Type | Source |
|---|---|---|---|
| GET | `/staff/login` | http | `auth.py:35` |
| POST | `/staff/login` | http, csrf | `auth.py:49` |
| GET | `/staff/logout` | http | `auth.py:82` |
| POST | `/staff/lang` | http, csrf | `auth.py:95` |
| GET | `/floor` | http | `floor.py:138` |
| POST | `/floor/data` | jsonrpc | `floor.py:148` |
| POST | `/floor/room` | jsonrpc | `floor.py:291` |
| POST | `/floor/sos` | jsonrpc | `floor.py:319` |
| POST | `/floor/sos/ack` | jsonrpc | `floor.py:339` |
| POST | `/floor/sos/resolve` | jsonrpc | `floor.py:349` |
| POST | `/atelier/advance` | jsonrpc | `modryn_atelier/controllers/atelier.py:107` |
| POST | `/atelier/my` | jsonrpc | `atelier.py:149` |
| GET | `/roster` | http | `modryn_roster/controllers/roster.py:51` |
| POST | `/roster/available` | jsonrpc | `roster.py:81` |
| GET/WS | `/websocket` | websocket | stock `bus` (`odoo/addons/bus/controllers/websocket.py:11`) |

Staff hold **one websocket** each: `FloorBoard.setup()` calls `bus.addChannel("modryn_queue")`
and subscribes to `modryn_queue/update` (`floor_board.js:38-43`).

Every one of `/floor/room`, `/floor/sos`, `/floor/sos/ack`, `/floor/sos/resolve` returns
`self._board()` — a full board build — as its response body.

### 2.4 Role D — Shift manager (`group_shift_manager`)

Everything role C does, plus the assignment surface. All of these are manager-gated server-side
(`if not self._is_manager(): return {'error': 'forbidden'}`), so a staff VU calling them is a
legitimate 200-with-error-envelope, not a failure — see §7.

| Method | Route | Type | Source |
|---|---|---|---|
| POST | `/floor/assign` | jsonrpc | `floor.py:165` |
| POST | `/floor/unassign` | jsonrpc | `floor.py:207` |
| POST | `/floor/accept` | jsonrpc | `floor.py:231` |
| POST | `/floor/redirect` | jsonrpc | `floor.py:246` |
| POST | `/floor/finish` | jsonrpc | `floor.py:258` |
| POST | `/atelier/assign` | jsonrpc | `atelier.py:128` |
| POST | `/atelier/task/create` | jsonrpc | `atelier.py:156` |
| GET | `/atelier` | http | `atelier.py:95` |
| POST | `/roster/assign` | jsonrpc | `roster.py:94` |
| POST | `/roster/publish` | jsonrpc | `roster.py:105` |

`/floor/assign`, `/floor/unassign`, `/floor/accept`, `/floor/redirect`, `/floor/finish` all
return a full board **and** trigger a bus broadcast that makes every other connected board
fetch another full board. This is the amplification loop the whole campaign is built around.

`/floor/finish` is the most expensive single call in the system: it does the full board build
**plus** a `product.product` search over every published variant (`floor.py:273-287`).

### 2.5 Role E — Owner (internal user, `group_boutique_owner`)

The only **internal** user (`hr_employee.py:115-119`), so the only role that consumes an Odoo
seat. Lands on `/manage/staff` after login (`auth.py:6,10-13`).

| Method | Route | Type | Source |
|---|---|---|---|
| GET | `/manage/staff` | http | `modryn_staff/controllers/manage.py:63` |
| GET | `/manage/staff/new` | http | `manage.py:72` |
| POST | `/manage/staff/new` | http, csrf | `manage.py:82` |
| GET | `/manage/staff/edit/<int:employee_id>` | http | `manage.py:129` |
| POST | `/manage/staff/edit/<int:employee_id>` | http, csrf | `manage.py:149` |
| POST | `/manage/staff/archive/<int:employee_id>` | http, csrf | `manage.py:196` |
| GET | `/manage/roles` | http | `manage.py:212` |
| POST | `/manage/roles/new` | http, csrf | `manage.py:222` |
| POST | `/manage/roles/archive/<int:role_id>` | http, csrf | `manage.py:241` |
| GET | `/manage/rooms` | http | `manage.py:253` |
| POST | `/manage/rooms/new` | http, csrf | `manage.py:264` |
| POST | `/manage/rooms/archive/<int:room_id>` | http, csrf | `manage.py:279` |
| GET | `/manage/shifts` | http | `roster.py:116` |
| POST | `/manage/shifts/new` | http, csrf | `roster.py:128` |
| POST | `/manage/shifts/archive/<int:template_id>` | http, csrf | `roster.py:155` |
| POST | `/manage/shifts/target` | http, csrf | `roster.py:168` |
| GET | `/manage/pieces` | http | `atelier.py:194` |
| POST | `/manage/pieces/new` | http, csrf | `atelier.py:205` |
| POST | `/manage/pieces/archive/<int:piece_id>` | http, csrf | `atelier.py:220` |

Owner VUs must **not** create staff/roles/rooms unboundedly during a run — `/manage/staff/new`
provisions a `res.users` (`hr_employee.py:120-128`) and would inflate the user table across the
campaign, changing the system under measurement between stages. Owner writes are limited to
idempotent-ish operations (archive-toggle on a fixed fixture row, `/manage/shifts/target` on a
fixed template+role pair) and rolled back between stages.

### 2.6 Route excluded from the workload

`/queue/channel` (`modryn_queue_poc/controllers/main.py:76`) returns a constant string and is
`auth='user'`. It is not exercised: it measures nothing and it is `type='json'`, the deprecated
alias, which would add noise to the logs.

---

## 3. Tool decision — k6

### 3.1 Comparison

| | **k6** | Locust | Gatling |
|---|---|---|---|
| Memory per VU | ~0.5–2 MB (vendor figure; verify in the smoke stage) | ~20–40 MB per greenlet-backed user in practice | ~1–5 MB (Akka actors) |
| 10k VUs on one box | Yes — ~5–20 GB, fits 32 GB | No — needs a distributed master/worker cluster | Yes |
| Cookie jar | Native, per-VU, automatic | `requests.Session` per user, manual | Native |
| WebSockets | Native `k6/ws` / `k6/experimental/websockets` | Not built in — hand-roll `websocket-client` + a thread per socket | Native |
| Mixed HTTP + WS in one VU | Yes | Awkward (blocking WS recv fights the greenlet loop) | Yes |
| Scenario executors (independent ramps per role, arrival-rate vs VU-based) | First-class `scenarios{}` with per-scenario executors | One `LoadTestShape` for the whole run; per-role weighting only via `@task` weights | `setUp()` with per-scenario injection profiles |
| Scripting language | JS (ES6 modules) | Python | Scala/Java DSL |
| Per-request custom metrics + thresholds | `Trend`/`Rate` + `thresholds{}`, fails the run non-zero exit | Manual | `assertions` |

### 3.2 Reasoning

Three of those rows decide it.

1. **Native websockets.** §4 puts ~380 open sockets at peak, each of which must stay open,
   receive `modryn_queue/update` pushes, and — critically — let us measure the round trip from
   "manager POSTs `/floor/assign`" to "another VU's socket sees the push". Locust has no
   websocket support; doing this there means `websocket-client` plus a thread per user plus
   hand-written round-trip correlation, which is exactly the kind of harness code that
   silently becomes the bottleneck and voids the run.

2. **Memory per VU.** 10,000 Locust users at ~20–40 MB is 200–400 GB. That is a cluster with a
   master, workers, and clock skew between them. k6's 10k on one box removes the entire
   distributed-generator failure mode.

3. **Scenario executors.** The five roles have genuinely different shapes: visitors are an
   open-model arrival rate (people arrive whether or not the site is slow), staff are a
   closed-model fixed VU count (there are N people on the floor, and if the board is slow they
   wait rather than multiply). k6 expresses that directly — `constant-arrival-rate` for
   visitors, `ramping-vus` for staff — in one script. Locust's single `LoadTestShape` cannot;
   modelling visitors as closed-loop would hide exactly the coordinated-omission effect we are
   trying to measure.

Gatling would work technically. It loses on operator cost: the team writes TypeScript and
Python, and a Scala DSL for a one-off campaign is a maintenance liability nobody asked for.

### 3.3 Load-generator hardware — mandatory

One VM, **16 vCPU / 32 GB RAM**, in the **same region** as staging. Not the laptop.

```bash
ulimit -n 1048576
sysctl -w net.ipv4.ip_local_port_range="1024 65535"
sysctl -w net.ipv4.tcp_tw_reuse=1
sysctl -w net.core.somaxconn=65535
sysctl -w net.ipv4.tcp_max_syn_backlog=65535
```

**The laptop cannot generate clean 10k load, and results from a saturated generator are void.**
Two independent reasons, and either alone is disqualifying:

- Ephemeral ports. 10,000 concurrent VUs against one host:port need ~10k source ports plus
  churn. macOS defaults to a ~16k range with a long `TIME_WAIT`; connections start failing and
  k6 reports them as request errors that look like server failures.
- Generator CPU. If k6 saturates its own cores, its scheduler falls behind, requests are issued
  late, and every latency number is inflated by generator queueing — the classic false-negative.

**Gate, enforced per stage:** the run is void unless, for the whole stage,
generator CPU < 70%, `dropped_iterations == 0`, and `http_req_blocked` p99 < 10 ms. Record all
three alongside the results. A stage that violates any of them is re-run, not reported.

Same-region is not an optimisation. Cross-region RTT is added to every single sample and would
swamp the 500 ms `rpc_read` p95 budget before the server does any work.

---

## 4. Workload model

### 4.1 Role mix

| Role | Share | 10k VUs | Reasoning |
|---|---|---|---|
| Public visitor | **88%** | 8,800 | Boutique traffic is overwhelmingly brides browsing the catalogue and booking. Nothing else scales with marketing spend. |
| Verified customer | **8%** | 800 | The `/my/*` portal is a returning-customer surface: a fraction of visitors, gated behind an SMS round trip. |
| Staff | **3%** | 300 | **Physically bounded.** Staff headcount does not scale with traffic — a boutique with 3× the brides does not have 3× the floor. This share only holds because the tenant count grows with the VU count (§5). |
| Shift manager | **0.8%** | 80 | One to three managers per boutique floor. |
| Owner | **0.2%** | 20 | One owner per boutique, and she is in `/manage/*` occasionally, not continuously. |

The staff/manager/owner shares are a **decision**, not a measurement: they are derived from
"one boutique floor has ~13 staff of whom 2–3 are managers and 1 is the owner" and then scaled
by the 30-tenant model in §5. They should be revisited if real headcount data arrives.

### 4.2 Visitor sub-split

| Sub-scenario | Share of visitors | Flow |
|---|---|---|
| Browse-and-book | **60%** | `/` → `/shop` → `/shop/<slug>` → `/book/dress/<id>` → POST `/book/submit` → `/book/confirmed/<id>` |
| Queue check-in | **25%** | `/queue/checkin` → POST `/queue/checkin/submit` → `/q/<token>` → poll `/q/<token>` a few times |
| Waitlist | **15%** | `/book` (lands on a full day) → POST `/waitlist/join` → `/waitlist/done` |

Both POST-bearing sub-scenarios must first GET the form page to obtain the CSRF token —
`csrf=True` on `/book/submit`, `/queue/checkin/submit`, `/waitlist/join`. The token is an HMAC
over `session.sid`, and several controllers call `request.session.touch()` specifically because
Odoo only sets the session cookie when the session is dirty (`main.py:11-13`,
`portal.py:96-100`, `auth.py:41-46`). k6's per-VU cookie jar handles this natively; a harness
that reuses one cookie jar across VUs will produce a wall of 400s that are the harness's fault.

### 4.3 Think times

| Role | Think time | Reasoning |
|---|---|---|
| Visitor | **5–15 s** uniform | She is reading dress descriptions on a phone. |
| Verified customer | **3–8 s** uniform | Task-focused: check a date, cancel, done. |
| Staff / manager / owner | **2–5 s** uniform | A working tool. Taps come fast; nobody reads the floor board. |

These are decisions, not measurements. They are deliberately *not* zero: zero think time turns
a closed-model scenario into a throughput benchmark and stops it modelling concurrency at all.

### 4.4 WebSockets

**One websocket per staff VU and per manager VU** — the floor board opens exactly one
(`floor_board.js:38-43`). At the 10k stage that is 300 + 80 = **380 open sockets** (the brief's
"~400" is the right order). Visitors, customers and owners hold none: the owner's `/manage/*`
pages carry no bus component, and the backend queue board is registered in
`registry.category("actions")` (`queue_board.js:60`), i.e. reachable only from `/odoo`, which
no VU visits.

Each socket VU must record the **`ws_rt` metric**: the elapsed time from a manager VU's
`/floor/assign` POST returning to a *different* VU's socket receiving the matching
`modryn_queue/update` frame. Correlate via the `ids` array in the booking payload
(`assignment.py:116-119`) or the entry `id` in the queue payload (`queue_entry.py:65-72`).
Without this metric the campaign cannot say anything about bottleneck (a), which is its main
subject.

---

## 5. Tenancy model

**30 load tenants, `lt01` … `lt30`.**

`dbfilter = ^%d$` in `odoo.conf` routes on the first hostname label — one PostgreSQL database
per boutique. So the tenant a request lands in is decided entirely by the `Host` header, and k6
sets it per-VU.

Why 30:

| Model | Concurrent per tenant | Staff per tenant | Verdict |
|---|---|---|---|
| 2 tenants (`bella`, `noga`) | 5,000 | ~190 | Fiction. No boutique has 190 people on the floor, and 5,000 concurrent brides in one shop is not a scenario anyone is buying capacity for. |
| **30 tenants** | **~333** | **~13** | Matches a real boutique floor. 333 concurrent visitors on one shop's site is a busy launch day; 13 staff of whom ~3 are managers is a real Thursday. |
| 300 tenants | ~33 | ~1.3 | Measures Odoo's per-database overhead, not the application. Useful later, not now. |

30 is the number that makes the *per-tenant* behaviour realistic, which is what the bottlenecks
in §10 are sensitive to — the floor board fan-out cost depends on boards **per tenant**, not on
total VUs.

**Stages at or below 500 VU use a 10-tenant subset (`lt01`…`lt10`).** At 100 VU across 30
tenants each tenant sees ~3 users and the floor board has ~0 concurrent boards, so the fan-out
being measured does not exist. Ten tenants keeps per-tenant density meaningful at low stages.

| Stage | Tenants | Concurrent / tenant | Staff+mgr / tenant |
|---|---|---|---|
| 100 | lt01–lt10 | 10 | ~0.4 |
| 500 | lt01–lt10 | 50 | ~1.9 |
| 1,000 | lt01–lt30 | 33 | ~1.3 |
| 2,500 | lt01–lt30 | 83 | ~3.2 |
| 5,000 | lt01–lt30 | 167 | ~6.3 |
| 10,000 | lt01–lt30 | 333 | ~12.7 |

Tenant assignment is **sticky per VU** for the whole iteration — a VU that browses `lt07`'s shop
must book on `lt07`. Randomising the Host header mid-session produces a session-cookie mismatch
and a CSRF 400 that has nothing to do with load.

Tenants are provisioned from `modryn_template` by `scripts/new_boutique.sh`, which requires
**zero open connections to the template** and therefore the server stopped (§11).

---

## 6. Ramp stages

Six stages. Each is a separate k6 run, not a single continuous ramp — a continuous ramp cannot
attribute a breakage to a stage, and it carries connection-pool and cache state forward.

| Stage | Concurrent VUs | Ramp-up | Steady | Ramp-down | Cool-down before next |
|---|---|---|---|---|---|
| S1 | 100 | 2 min | 10 min | 1 min | 5 min |
| S2 | 500 | 3 min | 10 min | 1 min | 5 min |
| S3 | 1,000 | 5 min | 15 min | 2 min | 10 min |
| S4 | 2,500 | 5 min | 15 min | 2 min | 10 min |
| S5 | 5,000 | 8 min | 15 min | 3 min | 15 min |
| S6 | 10,000 | 10 min | 15 min | 3 min | — |

Only the **steady** window is measured. Ramp-up and ramp-down samples are discarded — they
conflate connection establishment with steady-state service time.

The cool-down exists so cron backlog from stage N drains before stage N+1 starts. With the
one-minute SOS escalation cron across 30 databases (§10f) this is not theoretical.

**Stop rule.** Abort the remaining stages and report the ceiling when, in a steady window:
error rate exceeds **1%** (ten times the budget), or any p95 exceeds its SLO by **5×**, or the
server process restarts. Do not "push through to get a 10k number" — a 10k run against a
server that already failed at 5k produces garbage that will be quoted out of context later.

---

## 7. Service level objectives

### 7.1 The error definition — read this before anything else

> An **error** is: an HTTP 5xx response, **OR** a JSON-RPC response body containing an `"error"`
> key, **OR** a response missing its expected body marker.

**Odoo answers JSON-RPC failures with HTTP 200 and an error envelope. HTTP status alone is a
lying success metric.**

Verified on the running server just now:

```
$ curl -s -o /dev/null -w "HTTP=%{http_code}\n" -H "Host: bella.localtest.me" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","method":"call","params":{}}' \
    http://127.0.0.1:8069/floor/data
HTTP=200

$ # …and the body:
{"jsonrpc": "2.0", "id": null, "error": {"code": 100, "message": "Odoo Session Expired", …}}
```

The mechanism is `JsonRPCDispatcher.handle_error()` → `_response(error=…)` →
`make_json_response(response)`, whose `status` parameter defaults to **200**
(`odoo/odoo/http.py:2604-2635`, `odoo/odoo/http.py:2074`). Every unhandled server exception on
every `/floor/*`, `/atelier/*` and `/roster/*` route arrives as a 200.

A k6 check of `r.status === 200` would therefore report **100% success while the floor board is
completely broken**. That is not a hypothetical; it is the default failure mode of any
Odoo load test written without this paragraph.

Three consequences for the script:

1. Every jsonrpc call is checked with `!('error' in r.json())`, never on status.
2. Every http render is checked for a **body marker** — a stable string that only appears when
   the page actually rendered. Odoo's website layer serves a styled 200 error page for many
   failures, so "got 200 and some HTML" proves nothing. Suggested markers: `/book` → the slot
   picker's form action `"/book/submit"`; `/floor` → the mount point for
   `modryn_staff.floor_board`; `/shop` → an `href="/shop/"` product link.
3. **Application-level refusals are not errors.** These are correct behaviour and must be
   counted separately, in their own `Rate` metrics, not in the error budget:
   - `{'error': 'forbidden'}` from a staff VU hitting a manager route (`floor.py:174-175` etc.)
   - `{'error': 'not_found'}` for a fixture row that a concurrent VU archived
   - `"That time was just taken"` on `/book/submit` (`main.py:200-201`) — this is the slot race
     working, and its **rate is itself a headline result** (§10c)
   - `rate_limited` from `/my/login` (`otp.py:59-60`)
   - a board returned with `error=` set from `/floor/room` (`floor.py:315`) — the fitting-room
     collision rule firing

### 7.2 SLOs by endpoint class

Every request is tagged with exactly one class.

| Class | What it covers | p95 | p99 |
|---|---|---|---|
| `page` | Public HTML render: `/`, `/shop`, `/shop/<slug>`, `/book`, `/book/dress/<id>`, `/book/confirmed/<id>`, `/queue/checkin`, `/q/<token>`, `/waitlist/done`, `/my/*` GETs, `/b/<token>`, `/claim/<token>` GET, `/staff/login` GET, `/floor`, `/roster`, `/atelier`, `/manage/*` GETs | **800 ms** | **2 s** |
| `form` | Public HTML POST: `/book/submit`, `/queue/checkin/submit`, `/waitlist/join`, `/claim/<token>` POST, `/my/login`, `/my/verify`, `/my/cancel/<id>` POST, `/b/<token>/confirm`, `/b/<token>/cancel`, `/staff/login` POST | **1.5 s** | **3 s** |
| `rpc_read` | jsonrpc that only reads: `/floor/data`, `/atelier/my` | **500 ms** ≤ 1k VU<br>**1 s** at 2.5k+ | **2 s** |
| `rpc_write` | jsonrpc that mutates: `/floor/assign`, `/floor/unassign`, `/floor/accept`, `/floor/redirect`, `/floor/finish`, `/floor/room`, `/floor/sos`, `/floor/sos/ack`, `/floor/sos/resolve`, `/atelier/advance`, `/atelier/assign`, `/atelier/task/create`, `/roster/available`, `/roster/assign`, `/roster/publish` | **800 ms** | **2 s** |
| `ws_rt` | Bus round trip: manager write returns → another VU's socket receives the matching frame | **2 s** | **5 s** |
| `static` | `/web/assets/*`, images, fonts | **excluded from gates** |

The `rpc_read` relaxation from 500 ms to 1 s at 2,500+ VU is a **deliberate, pre-declared
concession**, recorded here so it cannot be quietly introduced after a failing run. It exists
because `/floor/data` is a full board build and the model above puts ~3 concurrent boards per
tenant at 2.5k. Every other class holds its budget at every stage.

`static` is excluded because Odoo asset bundles are served with far-future cache headers and
are a CDN/nginx question, not an application question. They are still *recorded* — a collapse
in static latency indicates the reverse proxy is the bottleneck, which is worth knowing.

### 7.3 Error budget

**< 0.1%** of all non-`static` requests, per stage, using the §7.1 definition. Expressed as a k6
threshold so the run exits non-zero:

```js
thresholds: {
  'checks{class:page}':     ['rate>0.999'],
  'checks{class:form}':     ['rate>0.999'],
  'checks{class:rpc_read}': ['rate>0.999'],
  'checks{class:rpc_write}':['rate>0.999'],
  'http_req_duration{class:page}':     ['p(95)<800',  'p(99)<2000'],
  'http_req_duration{class:form}':     ['p(95)<1500', 'p(99)<3000'],
  'http_req_duration{class:rpc_read}': ['p(95)<500',  'p(99)<2000'],
  'http_req_duration{class:rpc_write}':['p(95)<800',  'p(99)<2000'],
  'ws_rt':                             ['p(95)<2000', 'p(99)<5000'],
  'dropped_iterations':                ['count==0'],
}
```

---

## 8. Server-side instrumentation

Latency numbers without server-side evidence produce "it got slow at 2,500" — which is not a
finding. All five of these must be live before S1, and their output archived per stage.

### 8.1 nginx access log

```nginx
log_format modryn_load
  '$time_iso8601 host=$host status=$status '
  'rt=$request_time urt=$upstream_response_time '
  'method=$request_method uri=$uri '
  'bytes=$body_bytes_sent ua_status=$upstream_status '
  'conn=$connection_requests';
access_log /var/log/nginx/modryn_load.log modryn_load;
```

`$request_time` minus `$upstream_response_time` is the queueing-plus-network share. When k6
reports 3 s and `$upstream_response_time` is 200 ms, the bottleneck is in front of Odoo
(worker saturation, accept backlog) — a completely different fix from a slow board build.
`host=$host` is what attributes a slow request to a tenant.

### 8.2 `pg_stat_statements`

Not currently loaded — verified: `shared_preload_libraries` is empty on the dev box, though the
extension is available. Staging must set:

```
shared_preload_libraries = 'pg_stat_statements'
pg_stat_statements.max = 10000
pg_stat_statements.track = all
```

`CREATE EXTENSION pg_stat_statements;` in **every** `lt*` database. Snapshot
`pg_stat_statements_reset()` before each stage and dump after:

```sql
SELECT calls, total_exec_time, mean_exec_time, rows,
       left(query, 200) AS q
FROM pg_stat_statements
ORDER BY total_exec_time DESC
LIMIT 40;
```

This is the instrument that turns "the floor board is slow" into "the
`modryn_floor_helper` lookup ran 14,000 times in a 15-minute window" — i.e. §10a, proven.

### 8.3 `pg_stat_activity` sampling

Every 5 s, appended to a file:

```sql
SELECT now(), datname, state, wait_event_type, wait_event, count(*)
FROM pg_stat_activity
WHERE backend_type = 'client backend'
GROUP BY 1,2,3,4,5;
```

Two things to watch. **Connection exhaustion:** the dev box runs `max_connections = 100`
(verified), while Odoo's `db_maxconn` defaults to 64 *per worker process* — with 30 tenant
databases and multiple workers this is the most likely non-application failure of the campaign
(§11). **Lock waits:** `wait_event_type = 'Lock'` concentrated on `calendar_event` is
bottleneck (c) showing itself.

### 8.4 Cron lag, across all tenant databases

Cron lag is a **first-class metric**, not a footnote — see §10f. Sample every 30 s, per
database:

```sql
SELECT c.cron_name, c.nextcall,
       EXTRACT(EPOCH FROM (now() AT TIME ZONE 'UTC' - c.nextcall)) AS lag_seconds
FROM ir_cron c
WHERE c.active
ORDER BY lag_seconds DESC;
```

Driven across all 30 with a loop:

```bash
for db in lt{01..30}; do
  psql -d "$db" -tAc "SELECT '$db', cron_name, EXTRACT(EPOCH FROM (now() AT TIME ZONE 'UTC' - nextcall)) FROM ir_cron WHERE active;"
done
```

Report **max and p95 lag for `MODRYN: escalate unanswered calls for help`** specifically. That
cron carries a 30-second product promise (`sos_call.py:7`, `ESCALATE_AFTER_SECONDS = 30`) on a
60-second schedule; the gap between promise and lag is the finding.

### 8.5 Per-worker CPU

`pidstat -u -p ALL 5` or `top -b -d 5` filtered to Odoo PIDs, plus system-wide CPU, memory and
load average. Distinguishes "one worker pinned at 100% while seven idle" (a serialisation
problem — likely the cron threads, or a lock) from "all workers at 90%" (an honest capacity
limit). Record the worker count and `workers=` setting used.

---

## 9. Known deviations from production — disclose these with the results

Any result set that omits these is misleading.

### 9.1 Twilio is not configured on load tenants — the synchronous SMS cost is NOT exercised

Verified against Postgres:

| Database | `modryn.twilio.*` params |
|---|---|
| `modryn_template` | **none** |
| `bella` | all four SET |
| `noga` | none |

`lt01`…`lt30` are cloned from `modryn_template` (`scripts/new_boutique.sh`), so they inherit
zero Twilio params. `ModrynSms.send()` then takes the log-only path and returns
`(True, 'logged')` without any network call (`sms.py:76-81`).

**What this hides.** In production, `/book/submit` calls `modryn_send_confirmation()`
(`main.py:232-234`) which calls `requests.post()` to Twilio with `SEND_TIMEOUT = 10`
(`sms.py:11,88-93`) **inside the request handler**. The same is true of `/queue/checkin/submit`
via `modryn_check_in` → `modryn_accept` → `_notify_next_in_line` → `_send`
(`queue_entry.py:141-151`), of `/my/login` via `otp.issue()` (`otp.py:72`), and of
`/floor/assign` on a waiting walk-in via `modryn_call` (`floor.py:203-204`,
`queue_entry.py:153-171`). Every one of those holds an HTTP worker for the duration of an
outbound TLS round trip to `api.twilio.com`.

The default run therefore **understates production latency and worker occupancy on every
SMS-bearing path.** Say so in the report, next to the numbers, not in an appendix.

### 9.2 The focused SMS-tax run (required, run once)

One additional S4-only run (2,500 VU) with an injected delay, to price the tax separately:

- Point the four `modryn.twilio.*` params on `lt01`…`lt05` at a local stub that sleeps a
  configurable interval and returns Twilio's 201 JSON shape (`sms.py:98-99` reads
  `response.json()['sid']`).
- Run at **200 ms** (a healthy Twilio p50) and again at **2,000 ms** (a bad day, still well
  under the 10 s timeout).
- Report the delta in `form` p95 on `/book/submit` and `/queue/checkin/submit`, and the
  change in worker-busy time from §8.5.

Do **not** point load tenants at the real Twilio account. `bella` holds live credentials; a
2,500-VU run would send real messages and bill for them.

### 9.3 Other disclosed deviations

- **Data volume.** `bella` today holds 6 calendar events, 5 queue entries, 6 employees, 19
  partners, 4 products (verified). At these sizes Postgres chooses sequential scans regardless
  of indexing, so §10 items (c) and (e) are **unmeasurable until the tenants are seeded** —
  see §11.3.
- **Threaded vs prefork.** `odoo.conf` currently sets `workers = 0` (threaded), chosen for
  macOS dev so the bus websocket is served in-process. Staging must run prefork
  (`workers > 0`) with a separate gevent port for the bus, which is a materially different
  concurrency model. Record the staging config verbatim in the results.
- **No CDN.** Static assets are served by nginx directly. `static` is excluded from gates
  (§7.2), so this affects realism of total page weight, not the graded numbers.
- **`bella` and `noga` are not load tenants.** They carry hand-made demo data and, in `bella`'s
  case, live Twilio credentials. They are never targets.

---

## 10. The bottlenecks this campaign is designed to expose

Each item names what to measure and what evidence proves it. These are the report's headings.

> **In-flight Phase 1 work — re-verify before writing the k6 script.** This section was written
> against commit `ae84376`. While it was being written, a parallel stage began landing Phase 1
> fixes in the working tree that target items (b), (c) and (e) directly: an
> `addons/modryn_portal/models/sms_outbox.py` with `modryn.sms.send_async()` and a
> `MODRYN: drain the SMS outbox` cron (a **sixth** cron, not in the §10f table); an upper bound
> on `_slots()`'s domain in `modryn_booking/controllers/main.py`; and a `UniqueViolation` catch
> around the booking `create()`, demoting the read-then-write pre-check to a UX affordance
> behind a partial unique index.
>
> None of that changes what this campaign is **for** — a fix is only a fix once it is measured,
> and (b), (c) and (e) remain the things to measure. But the line references and the "is it
> synchronous?" claims below describe `ae84376`, not the tree you will run against. Before
> scripting, re-read `sms.py`, `sms_outbox.py`, `booking/controllers/main.py` and
> `ir_cron_data.xml`, and record which fixes are present in the results header. A campaign that
> reports "(b) not observed" without noting that (b) was fixed between spec and run is a
> misleading result.

### (a) Floor-board fan-out — one write, N full board rebuilds

**The loop.** `FloorBoard.setup()` subscribes to `modryn_queue/update` on channel
`modryn_queue`, and the handler is `() => this.refresh()`, where `refresh()` is
`rpc("/floor/data")` — a **complete board rebuild**
(`addons/modryn_staff/static/src/floor/floor_board.js:38-43, 103-105`). There is no diffing:
the payload that arrives on the socket is discarded and the whole board is refetched.

**The publishers.** `calendar.event.write()` sends on `modryn_queue` whenever
`modryn_employee_id` or `modryn_helper_ids` changes
(`addons/modryn_staff/models/assignment.py:108-120`); `modryn.queue.entry` sends on create and
on any state change (`addons/modryn_queue_poc/models/queue_entry.py:57-84`) and on assignment
change (`assignment.py:154-160`); `modryn.sos.call` sends on create and on state/escalation
change (`addons/modryn_staff/models/sos_call.py:80-98`). The channel is scoped per database by
`channel_with_db(self.env.cr.dbname, target)` (`odoo/addons/bus/models/bus.py:121`) — so the
fan-out is per tenant, which is why per-tenant density (§5) is the variable that matters.

**The cost of one rebuild.** `ModrynFloor._board()` (`floor.py:48-135`) issues **seven**
`search()` calls: pending queue entries (51), waiting/called entries (59), today's bookings
(83), staff (101), fitting rooms (112), the signed-in employee (38-39), open SOS calls (119).
`modryn_atelier` then overrides `_board()` via `super()` (`atelier.py:18-37`) and adds **three
more**: a second `hr.employee` lookup for the same user the parent already fetched (23-24), the
user's open alteration tasks (27-28), and every garment piece (34-35). Both addons are
installed on both tenants (verified). **Ten searches per board build.**

**On top of that, a genuine N+1.** `modryn_helper_ids` is a non-stored compute, and
`_compute_modryn_helper_ids` loops `for record in self` calling `record._helper_links()`, which
is one `modryn.floor.helper` search per record (`assignment.py:48-57`). `_board()` reads
`entry.modryn_helper_ids` for every queue entry (`floor.py:71`) **and**
`event.modryn_helper_ids` for every booking (`floor.py:97`) — two independent N+1 storms per
build.

> **Correction to the brief.** The N+1 is *not* `modryn_is_occupied`.
> `_compute_modryn_is_occupied` (`hr_employee.py:49-93`) is correctly **batched**: it issues
> three searches for the whole recordset regardless of size. It is expensive but it is O(1) in
> employee count. The N+1 is `_compute_modryn_helper_ids`. Attributing the cost to the wrong
> compute would send the Phase 1 fix to the wrong file.

**Amplification.** With `B` boards open in a tenant, one manager drag costs
`1 + B` board builds — one for the POST's own return value (every mutating `/floor/*` route
returns `self._board()`) and one per board reacting to the push. At S6, `B ≈ 12.7` per tenant.

**Measure:** `ws_rt` p95/p99; `/floor/data` request rate versus `/floor/assign` request rate
(the ratio *is* the amplification factor); `pg_stat_statements` call counts for the
`modryn_floor_helper` and `hr_employee` queries.

### (b) Synchronous Twilio inside request handlers

`sms.py:88-93` — `requests.post(..., timeout=SEND_TIMEOUT)` with `SEND_TIMEOUT = 10`, called
from within the HTTP request. Callers listed in §9.1. Not exercised by default (§9.1);
quantified by the focused run (§9.2). **Measure:** the `form` p95 delta between the injected-delay
run and the baseline, and worker-busy time from §8.5.

### (c) Read-then-write booking slot guard, with no unique constraint

`/book/submit` re-checks the slot with `search_count` (`main.py:193-201`) and then, if clear,
`create()`s the event (`main.py:228`). Between those two statements another request can create
the same slot. Nothing in the database prevents it: **verified**, the only UNIQUE index on
`calendar_event` is `calendar_event_pkey` on `id`. `start` carries a plain non-unique btree.

`/claim/<token>` POST has the identical shape (`waitlist.py:106-133`), so a claim and a booking
race each other too.

**Measure two things.** First, the *rate* of `"That time was just taken"` responses — this is
the guard working and it is a headline number (a boutique whose booking form rejects 8% of
submissions at peak has a product problem, not just a performance one). Second, **actual
double-bookings**, by querying after each stage:

```sql
SELECT start, count(*) FROM calendar_event
WHERE modryn_is_booking AND modryn_cancelled_at IS NULL
GROUP BY start HAVING count(*) > 1;
```

Any row returned proves the guard is losing the race under concurrency. This is the single most
valuable correctness result the campaign can produce, and it is invisible to latency graphs.

The fix is a partial unique index, and `models.UniqueIndex` exists in this Odoo 19 clone
(`odoo/odoo/orm/table_objects.py`), accepting a partial definition — but that is Phase 1 work,
not this document's.

### (d) No rate limiting on public POSTs

> **Correction to the brief.** "No rate limiting on public POSTs" is too broad. OTP issuance
> **is** limited: `MAX_SENDS_PER_HOUR = 3` per phone number, enforced by a `search_count` over
> the last hour (`otp.py:13, 55-60`), and verification is capped at `MAX_VERIFY_ATTEMPTS = 5`
> per code (`otp.py:12, 88-89`).

What is genuinely unlimited:

| Route | Limiter | Effect of abuse |
|---|---|---|
| `/book/submit` | none | Unbounded `res.partner` + `calendar.event` creation |
| `/queue/checkin/submit` | de-duplicated by phone only (`queue_entry.py:95-101`) | Unbounded rows from rotated numbers |
| `/waitlist/join` | unique `(phone, day)` constraint only (`day_waitlist.py:53-54`) | Unbounded rows from rotated numbers |
| `/claim/<token>` POST | token validity only | — |
| `/b/<token>/confirm`, `/b/<token>/cancel` | token validity only | — |
| `/my/login` | per **phone**, not per IP | Bypassed entirely by rotating numbers |

The per-phone limiter is not an IP limiter, so one client with a list of numbers is
unthrottled. **Measure:** run a dedicated abuse scenario at S3 — 50 VUs POSTing
`/queue/checkin/submit` with fresh numbers at 10 rps each, no think time — and record its effect
on the `page` and `form` p95 of the *legitimate* scenarios sharing that tenant. The number to
report is how much one abusive client degrades everyone else.

### (e) Unbounded future-booking scans in the slot lists

`/book`'s `_slots()` builds its taken-set from `[('modryn_is_booking','=',True),
('start','>=', datetime.utcnow())]` (`main.py:36-51`) — bounded below, **unbounded above**. It
loads every future booking ever made in that tenant to render a 14-day picker, and it runs on
every `/book` and every `/book/dress/<id>` GET, which is the busiest visitor path in the model.

> **Correction to the brief.** `/book` is not the worst case.
> `waitlist.py::_free_slots_on()` (`waitlist.py:44-50`) has **no date bound at all** — its
> domain is `[('modryn_is_booking','=',True)]` plus the cancelled filter. It loads *every*
> booking in the tenant's history, and it runs on both the GET and the POST of
> `/claim/<token>`, including on the error re-render (`waitlist.py:112-118`).

**Additional finding, not in the brief: two hot public lookups have no usable index.**

Verified with `EXPLAIN` against `bella`:

```
-- /book/submit: Partner.search([('phone','=',phone)])   main.py:207
Seq Scan on res_partner  Filter: ((phone)::text = '0521234567'::text)

-- /queue/checkin/submit: modryn_check_in()              queue_entry.py:97-99
Seq Scan on modryn_queue_entry
  Filter: (((phone)::text = '+972521234567'::text) AND ((state)::text = ANY (…)))
```

`res_partner` has four phone indexes but every one of them is an **expression** index on
`regexp_replace(phone, …)` (Odoo's phone-sanitisation indexes) — a plain `phone = 'x'` predicate
cannot use any of them. `modryn_queue_entry.phone` has no index at all (`queue_entry.py:28`,
`phone = fields.Char()` with no `index=True`).

At today's row counts the planner would choose a seq scan regardless, so **this is unmeasurable
until §11.3 seeding lands** — which is precisely why seeding is a hard gate.

**Measure:** `pg_stat_statements` `mean_exec_time` and `rows` for the `calendar_event`,
`res_partner` and `modryn_queue_entry` selects, plotted against seeded row count across stages.

### (f) Per-database crons against a ~60-second-per-pass scheduler

**Cron lag is a first-class metric.** Five active crons, all defined in `addons/*/data/ir_cron_data.xml`:

| Cron | Interval | Model / method |
|---|---|---|
| MODRYN: escalate unanswered calls for help | **1 minute** | `modryn.sos.call._modryn_escalate_unanswered()` |
| MODRYN: pass on unclaimed waitlist offers | 10 minutes | `modryn.day.waitlist._modryn_expire_offers()` |
| MODRYN: send 24h fitting reminders | 15 minutes | `calendar.event._modryn_send_reminders()` |
| MODRYN: purge expired login codes | 6 hours | `modryn.otp.code._gc_codes()` |
| MODRYN: close the floor for the day | 1 day | `modryn.queue.entry._modryn_expire_open_tickets()` |

**The scheduler.** `odoo/odoo/service/server.py:68` sets `SLEEP_INTERVAL = 60`. Each cron thread
blocks in `select.select([pg_conn], [], [], SLEEP_INTERVAL + number)` (line 565) and, when it
wakes without a notification, re-lists **all** databases and processes them **serially** in a
`for db_name in db_names:` loop calling `IrCron._process_jobs(db_name)` (lines 596-604).
`max_cron_threads` defaults to 2 (`odoo/odoo/tools/config.py:444`).

`ir.cron._trigger()` does fire `pg_notify('cron_trigger')` post-commit, and the loop
preferentially processes notified databases first (lines 583-590) — so *triggered* work wakes
within about a second. But **none of the five crons above is trigger-driven.** They are all
interval-scheduled, which means they are reached only on the once-per-`SLEEP_INTERVAL`
full-database sweep, and within that sweep they are serialised across all 30 tenants on 2
threads.

**The ceiling this creates.** SOS escalation promises 30 seconds
(`sos_call.py:7`, `ESCALATE_AFTER_SECONDS = 30`) and is scheduled at 60. If a sweep across 30
databases takes longer than 60 seconds, escalation lag grows without bound and the product
promise is simply not kept — silently, with no error anywhere. That is a **capacity ceiling on
tenant count**, independent of HTTP load, and it is exactly the kind of finding that never
shows up in a latency graph.

**Measure:** per §8.4 — max and p95 lag per cron per database, and specifically for SOS
escalation. Report lag at every stage, including S1, so the tenant-count effect can be
separated from the HTTP-load effect. Also run one **HTTP-idle control**: 30 tenants, zero VUs,
15 minutes, cron lag sampled. If lag is already non-trivial with no load at all, the ceiling is
purely the database count and no amount of HTTP tuning moves it.

### (g) Additional finding: `GET /roster` performs writes

Not in the brief, found while enumerating routes. `/roster` calls `_grid()` →
`modryn_ensure_week()` (`roster.py:46-48`, `shift_slot.py:59-88`), which **creates
`modryn.shift.slot` rows on a GET**. It is idempotent, and the design rationale is stated in the
docstring, but it means a plain page load takes a write transaction. `_row()` then issues two
further searches per slot (`shift_slot.py:131-137`) — another N+1, over `modryn.availability`.

Worth a tagged sub-scenario at S4+: `/roster` GET latency versus concurrent staff VUs on the
same tenant, watching for write-lock contention on `modryn_shift_slot`.

---

## 11. Cross-track requirements — staging prerequisites

The campaign cannot start until all of these are true. Each is a hard gate.

### 11.1 Tenant provisioning

- [ ] 30 databases `lt01`…`lt30`, each created by `scripts/new_boutique.sh <slug> "<Name>"`.
- [ ] **The server must be stopped during provisioning.** `new_boutique.sh` refuses to run if
      any connection is open to `modryn_template`, because `createdb -T` requires zero
      connections to the source. Plan a maintenance window; do not work around the check.
- [ ] Each tenant's filestore directory copied — the script does this, verify it happened.
- [ ] Each tenant has a regenerated `database.uuid` and its own `web.base.url` — the script
      does this; verify `web.base.url.freeze` is `True` or the first login rewrites it.

### 11.2 `odoo.conf` for staging

- [ ] **`db_name` must list every tenant.** Currently
      `db_name = modryn_template,bella,noga`. The comment in `odoo.conf` explains why this
      exists: without it the cron scheduler enumerates every database on the server. But the
      converse is the trap — **a database absent from `db_name` never gets its crons run at
      all.** If `lt01`…`lt30` are not listed, §10f measures nothing and the omission is
      invisible.
- [ ] `dbfilter = ^%d$` retained, and DNS/hosts resolves `lt01.<staging-host>` …
      `lt30.<staging-host>` to the load balancer.
- [ ] `workers > 0` (prefork), sized to the staging box, **plus a gevent port for the bus** —
      the current `workers = 0` is a macOS dev accommodation and does not represent production.
      Record the exact values used.
- [ ] `list_db = False`. The database manager is reachable at `/web/database/manager` when
      `list_db = True`; a staging box under load test must not expose create/drop/backup.
- [ ] `limit_time_real` (default 120 s) and `limit_time_cpu` (default 60 s) recorded. A worker
      killed by `limit_time_real` produces a 5xx that must be attributed to the limit, not
      mistaken for an application crash.

### 11.3 Data seeding — the gate that findings (c) and (e) depend on

Empty tenants make the campaign a measurement of an empty database. Per tenant, minimum:

| Model | Rows | Why this number |
|---|---|---|
| `product.template` (published) | ≥ 50 | `/shop` pagination and `/floor/finish`'s variant search (`floor.py:273-275`) must both do real work. |
| `res.partner` | ≥ 50,000 | Makes the un-indexed `phone` seq scan (§10e) measurable. Below ~10k rows Postgres seq-scans regardless and the finding is invisible. |
| `calendar.event` (`modryn_is_booking`, future) | ≥ 5,000 | Makes `_slots()`'s unbounded scan (§10e) measurable. |
| `calendar.event` (booking, past) | ≥ 20,000 | Makes `_free_slots_on()`'s *fully* unbounded scan measurable and separates it from `/book`'s. |
| `modryn.queue.entry` | ≥ 10,000, of which ~20 open | Makes the un-indexed queue `phone` lookup measurable while keeping the open-queue board realistic. |
| `hr.employee` | 13, of which 3 manager-level + 1 owner | The §5 floor model. Provisioned with logins via `modryn_provision_login`. |
| `modryn.fitting.room` | 4 | Exercises the collision constraint (`fitting_room.py:60-83`). |
| `modryn.staff.role`, `modryn.garment.piece` | as seeded by template | — |

Extend `scripts/seed_catalog.py`, `scripts/seed_staff.py` and `scripts/seed_atelier.py`, or add
a `scripts/seed_load.py`. **Seed identically across all 30 tenants** — divergent data makes
per-tenant latency incomparable and destroys the ability to attribute a slow tenant to load
rather than to its own contents.

### 11.4 Fixture export

The k6 script needs, per tenant, as JSON:

- product slugs and ids for `/shop/<slug>` and `/book/dress/<id>`
- staff/manager/owner credentials (username + password as provisioned)
- `(phone, code)` OTP pairs, pre-issued, sized so none is reused within its 5-minute TTL
- `/b/<token>` booking tokens (HMAC — must be computed server-side, `booking_comms.py:31-41`)
- `/claim/<token>` offer tokens, with rows in state `offered` and unexpired `offer_expires_at`
- `/q/<token>` queue access tokens
- a phone pool per role, ≥ 2× peak VUs for that role

### 11.5 Database and OS

- [ ] `shared_preload_libraries = 'pg_stat_statements'`, extension created in all 30 databases.
- [ ] `max_connections` sized deliberately. The dev box runs 100 (verified); Odoo's
      `db_maxconn` defaults to 64 (`odoo/odoo/tools/config.py:393`) and is **per worker
      process**. `workers × db_maxconn + max_cron_threads` must fit under `max_connections`
      with headroom, or the campaign measures connection starvation and nothing else.
      **Compute this explicitly and record the arithmetic in the results.** Consider PgBouncer
      in transaction mode — noting that it changes session semantics, so it is a decision to
      make before S1, not a mid-campaign fix.
- [ ] Postgres and all app containers on `TZ=UTC`. The code localises to `Asia/Jerusalem` at
      render time (`floor.py:9`, `main.py:14`); a non-UTC server timezone would shift
      `_today_bounds_utc()` (`floor.py:41-46`) and change which bookings the board shows.
- [ ] Server clocks NTP-synced. `ws_rt` is measured across two VUs on one generator, so
      generator clock skew is not an issue — but the cron-lag query compares
      `now() AT TIME ZONE 'UTC'` to `nextcall` on the database, and app/db skew corrupts it.

### 11.6 Reset between stages

- [ ] `pg_stat_statements_reset()` on all 30 databases.
- [ ] Truncate or restore the rows the run created: `calendar_event` (bookings),
      `modryn_queue_entry`, `modryn_day_waitlist`, `modryn_otp_code`, `modryn_sos_call`,
      `modryn_floor_helper`, and the `res_partner` rows created by `/book/submit`. A stage that
      starts with the previous stage's 40,000 new bookings is not comparable to the one before
      it — and §10e's scans get monotonically slower, which would read as a load effect.
- [ ] `VACUUM ANALYZE` on all 30 after reset. Stale planner statistics after a bulk delete
      change query plans mid-campaign.
- [ ] Restart Odoo between stages: clears the registry cache, connection pools and any leaked
      worker state, so each stage starts from the same place.

### 11.7 Observability of the campaign itself

- [ ] Generator metrics (§3.3) archived per stage — a stage without them is unreportable.
- [ ] All server-side artefacts (§8) archived per stage, named by stage.
- [ ] Staging `odoo.conf`, Postgres config, worker count and instance sizes recorded verbatim
      in the results document. A performance number without its configuration is not a result.

---

## Appendix — what to write down when it breaks

For each stage, one row:

| Stage | VUs | Tenants | page p95/p99 | form p95/p99 | rpc_read p95/p99 | rpc_write p95/p99 | ws_rt p95/p99 | error % | slot-race % | double-bookings | max SOS cron lag | gen CPU | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

And for the first stage that fails, one paragraph naming which of §10 (a)–(g) broke, with the
`pg_stat_statements` row or the nginx `$upstream_response_time` distribution that proves it.

That paragraph is the deliverable. Everything above it is the method for earning the right to
write it.
