# Load-test execution plan

The operational runbook. Its companion, [`../specs/load-test-spec.md`](../specs/load-test-spec.md),
says *what we are trying to learn and what counts as an answer*. This document says *how the rig is
built, how it is driven, and what to do when it goes wrong*.

Everything below was checked against the code as it stands. Where the code contradicts the obvious
assumption, that is called out inline rather than smoothed over — the assumptions are what break at
2am, not the thresholds.

---

## 0. What you must know before touching anything

| Fact | Value | Where it comes from | Why it decides something |
|---|---|---|---|
| Odoo mode | `workers = 0` (threaded) | `odoo.conf` | One process, one Python GIL, in-process websockets. Ceilings will be CPU-bound and thread-bound, not worker-bound. |
| Odoo DB pool | `db_maxconn` default **64** | `odoo/odoo/tools/config.py:393`, not overridden in `odoo.conf` | Hard ceiling on concurrent DB work for the whole instance. |
| Postgres | `max_connections = 100`, PG 16.14 | `show max_connections` | 64 (Odoo) + cron + your psql + observers. PG 16 means `dropdb --force` is available for resets. |
| Cron threads | `max_cron_threads` default **2** | `odoo/odoo/tools/config.py:444` | Two threads drain *every* cron on *every* database. The SMS outbox drain competes with reminder, expiry and escalation crons across all tenants. |
| Host | 10 CPU, 16 GB | `sysctl` | The load generator and the SUT are the same machine unless you move k6 off-box. Budget for it. |
| Tenancy router | `dbfilter = ^%d$`, `db_name = modryn_template,bella,noga` | `odoo.conf` | **Every load tenant must be added to `db_name`** or its crons never run and the hostname still routes. This is a config edit the restart/upgrade stage owns. |
| Sessions | `FilesystemSessionStore` under `.odoo-data/sessions` | `odoo/odoo/http.py:995` | Every VU login writes a file. 500 VUs × several stages = tens of thousands of small files, and `vacuum()` walks them. Watch inode count and `sessions/` size. |
| k6 | **not installed** | `which k6` → not found | Prerequisite. `brew install k6`. |

### The three rules that do not bend

1. **Never edit `odoo/`.** It is a gitignored shallow clone.
2. **Never run `odoo-bin` from this plan's steps by hand while the server is up.** The restart /
   upgrade stage owns process lifecycle. Seeding steps that need `odoo-bin shell` are explicitly
   marked *server-stopped*.
3. **`loadtest/odoo_addons/` is outside `addons/`.** It is physically incapable of being loaded by a
   production `addons_path`. That is the whole point; do not "tidy" it into `addons/`.

---

## 1. Harness architecture

Four layers, each replaceable without touching the others.

```
                     ┌───────────────────────────────────────────────┐
   loadtest/k6/      │  main.js — picks stage, composes k6 scenarios │
                     └───────┬───────────────────────────────┬───────┘
                             │                               │
              scenarios/ (journeys)              focused/ (single-question tests)
              visitor customer staff              bus_storm  booking_race
              manager owner
                             │                               │
                     ┌───────┴───────────────────────────────┴───────┐
   loadtest/k6/lib/  │ session.js  jsonrpc.js  ws.js  otp.js         │
                     │ cookie jar + CSRF · RPC envelope · websocket  │
                     │ subscribe/await · staging OTP read-back       │
                     └───────────────────┬───────────────────────────┘
                                         │ HTTP / WS
                     ┌───────────────────┴───────────────────────────┐
   the SUT           │ Odoo 19 threaded, N tenant databases          │
                     │ + loadtest/odoo_addons/modryn_loadtest (staging only)
                     └───────────────────┬───────────────────────────┘
                                         │
   loadtest/observe/ │ observe.sh — samples pg, process, log; writes CSV │
   loadtest/seed/    │ gen_tenants.sh · seed_tenant.py · snapshot/reset  │
   loadtest/results/ │ one directory per run (gitignored)                │
```

### File layout

```
loadtest/
├── README.md                       # how to run one stage, nothing else
├── k6/
│   ├── main.js                     # entry point; STAGE env var selects the profile
│   ├── config/
│   │   ├── thresholds.js           # per-tag thresholds + abort thresholds
│   │   └── tenants.json            # [{slug, host, staff:{...}, tenantIndex}]
│   ├── lib/
│   │   ├── session.js              # cookie jar, CSRF scrape, staff/customer login
│   │   ├── jsonrpc.js              # JSON-RPC envelope + double-layer error check
│   │   ├── ws.js                   # websocket connect/subscribe/await-notification
│   │   └── otp.js                  # read a code back from /loadtest/otp
│   ├── scenarios/
│   │   ├── visitor.js
│   │   ├── customer.js
│   │   ├── staff.js
│   │   ├── manager.js
│   │   └── owner.js
│   └── focused/
│       ├── bus_storm.js
│       └── booking_race.js
├── odoo_addons/
│   └── modryn_loadtest/            # NEVER moved into addons/
│       ├── __manifest__.py
│       ├── __init__.py
│       ├── models/
│       │   ├── __init__.py
│       │   └── sms_capture.py      # inherits modryn.sms, overrides _send_now
│       ├── controllers/
│       │   ├── __init__.py
│       │   └── otp.py              # GET /loadtest/otp
│       └── security/
│           └── ir.model.access.csv
├── seed/
│   ├── gen_tenants.sh              # N tenants from the gold template
│   ├── seed_tenant.py              # parameterised replacement for seed_staff.py
│   ├── snapshot_gold.sh            # freeze the post-seed state as modryn_gold
│   └── reset.sh                    # restore every load tenant from gold
├── observe/
│   ├── observe.sh                  # 1 Hz sampler -> CSVs
│   └── pg_stats.sql                # the queries observe.sh runs
└── results/                        # gitignored: add `loadtest/results/` to .gitignore
```

### Library contracts

**`lib/session.js`**

| Export | Contract |
|---|---|
| `newJar()` | k6 keeps cookies per-VU automatically; this returns a `http.CookieJar` when a VU needs two identities (the instrumented bus VU needs one). |
| `csrfToken(host, path, jar)` | `GET`s the page, regex-scrapes `name="csrf_token" value="([^"]*)"`, returns the token. Fails the iteration loudly if absent — a missing token means the page 500'd, and a blank token silently becomes a 400 later. |
| `staffLogin(host, username, password)` | The §2 sequence. Returns `{ok, landing}`. |
| `customerLogin(host, phone)` | The §3 sequence. Returns `{ok}`. |

**`lib/jsonrpc.js`** — one export, `call(host, path, params)`. It sets
`Content-Type: application/json`, wraps params as
`{"jsonrpc":"2.0","method":"call","params":{…}}`, and checks **two** error layers:

* envelope: `body.error` present → failure (this is what an expired session looks like);
* payload: `body.result.error` present → failure (this is what `{'error': 'forbidden'}` from
  `floor.py` looks like).

Both come back as **HTTP 200**. A k6 check of `r.status === 200` on a JSON-RPC route proves nothing.
This is the single most likely way to produce a green run that tested nothing.

**`lib/ws.js`** — `connect(host, jar)` opens `ws://<host>/websocket`, then sends exactly
`{"event_name":"subscribe","data":{"channels":["modryn_queue"],"last":<n>}}`.

Two traps encoded here:

* **Inbound rate limit.** `websocket_rate_limit_burst = 10`, `websocket_rate_limit_delay = 0.2`
  (`odoo/odoo/tools/config.py:218-219`). More than 10 client→server frames faster than one per
  0.2 s raises `RateLimitExceededException` and the socket is closed. Floor clients must subscribe
  **once** and then stay silent. A naive re-subscribe loop will read as a server failure.
* **`last: 0` replays history.** `bus.bus._poll` with `last == 0` returns every notification created
  in the last `TIMEOUT = 50` seconds (`odoo/addons/bus/models/bus.py:171-177`). For latency
  measurement that is 50 s of contamination. `ws.js` therefore subscribes with `last: 0`, drains and
  discards whatever arrives in the first 500 ms, remembers the highest `id` seen, and only counts
  notifications with a larger `id` thereafter.

Frames arrive as a JSON array of `{"id": <int>, "message": {"type": "modryn_queue/update",
"payload": {…}}}`.

**`lib/otp.js`** — `fetchCode(host, phone)` → `GET /loadtest/otp?phone=…&secret=…`, returns the
6-digit string or fails the iteration. Secret comes from `__ENV.LOADTEST_SECRET`.

### `config/thresholds.js`

Every request is tagged `surface` (`storefront`, `booking`, `portal`, `floor`, `roster`, `atelier`,
`manage`) and `kind` (`page`, `rpc`, `write`). Thresholds are declared per tag so a slow
`/manage/staff` page cannot hide behind a fast `/shop`:

```
http_req_duration{kind:page}   p(95)<1500  p(99)<4000
http_req_duration{kind:rpc}    p(95)<800   p(99)<2500
http_req_duration{kind:write}  p(95)<2000  p(99)<6000
http_req_failed                rate<0.01
checks                         rate>0.99
ws_notification_latency        p(95)<1500        (custom Trend, bus_storm only)
```

`abortOnFail: true` is set only on the abort thresholds in §8, never on the reporting ones — a stage
that trips a reporting threshold still has to run to completion, because *how* it degrades is the
finding.

---

## 2. Authentication — staff, manager, owner

Signing in is not optional decoration. `/floor`, `/atelier`, `/roster` and `/manage/*` return
`request.not_found()` to anyone who is not in the right group, so an unauthenticated harness
measures the 404 path and reports it as a fast, healthy server.

The canonical working sequence is `scripts/verify.sh` §10a (lines 145–161). Ported faithfully:

### The sequence

| # | Step | Detail |
|---|---|---|
| 1 | `GET http://<slug>.localtest.me:8069/staff/login` with a cookie jar | The GET handler calls `request.session.touch()` (`modryn_staff/controllers/auth.py:46`). Without that, Odoo emits **no session cookie at all** for a visitor whose first request is the login page, and the POST lands under a brand-new sid and is rejected with a bare 400. Do not skip the GET. Do not share a jar across VUs. |
| 2 | Scrape `name="csrf_token" value="…"` from the HTML | The token is `HMAC-SHA1(database.secret, sid[:42] ‖ max_ts)` (`odoo/odoo/http.py:1934-1954`). It is bound to the session that served the page. |
| 3 | `POST /staff/login` form-urlencoded, same jar | Fields: `username`, `password`, `csrf_token`, optional `redirect`. |
| 4 | Assert **303**, not 200 | Success is `request.redirect(...)`. Failure re-renders the login template — HTTP **200** with an error message in the body (`auth.py:57`, `:67`, `:77`). A `status === 200` check inverts pass and fail. |
| 5 | Re-scrape CSRF **after** login for any later `type='http'` POST | `session.finalize()` sets `should_rotate = True` (`http.py:1278`), and `_save_session` calls `rotate(sess, env)` with `soft` defaulting to **False** (`http.py:2175`, `:1035`) — a *hard* rotation that regenerates the whole sid. The pre-login token is dead. |

### The logins are usernames, not emails

`staff_login_submit` builds `{'login': username, …}` and hands it to `session.authenticate`
(`auth.py:60-62`). The seeded values are `miri`, `sara`, `rotem`, `orly`, `noa` for bella and
`tamar`, `yael`, `dana` for noga — see `scripts/seed_staff.py`. There is no email anywhere in the
staff flow; the controller docstring says so explicitly ("this asks for a *username*, not an email").

### Where each role lands, and what it may touch

| Role | Group | Post-login landing | Reachable | 404s for this role |
|---|---|---|---|---|
| staff | `group_boutique_staff` | `/floor` | `/floor`, `/floor/data`, `/floor/room`, `/floor/sos*`, `/roster`, `/roster/available`, `/atelier/my` | `/atelier`, `/manage/*`, all `/floor/assign`-class writes |
| manager | `+ group_shift_manager` | `/floor` | everything staff can, plus `/floor/assign`, `/unassign`, `/accept`, `/redirect`, `/finish`, `/atelier`, `/atelier/advance`, `/atelier/assign`, `/atelier/task/create`, `/roster/assign`, `/roster/publish` | `/manage/*` |
| owner | `+ group_boutique_owner` | `/manage/staff` | everything, plus `/manage/staff`, `/manage/roles`, `/manage/rooms`, `/manage/pieces`, `/manage/shifts` | — |

`landing_for()` (`auth.py:10-13`) sends the owner to `/manage/staff` and everyone else to `/floor`.

> **Correction to the working assumption:** `/atelier` requires **manager**, not staff —
> `atelier.py:98` calls `self._is_manager()`. A staff VU hitting `/atelier` gets a 404 and, if the
> check is `status !== 200`, a green run measuring nothing.

### CSRF applies to `type='http'` only

`HttpDispatcher.dispatch` is the only place that validates the token
(`odoo/odoo/http.py:2493`). `type='jsonrpc'` routes — every `/floor/*` action, `/roster/*`,
`/atelier/*` — need **only the session cookie**. That is exactly what `verify.sh` relies on when it
proves those routes refuse an anonymous caller. So: scrape CSRF for form posts
(`/staff/login`, `/book/submit`, `/queue/checkin/submit`, `/waitlist/join`, `/my/*`,
`/manage/*/new`); never for JSON-RPC.

---

## 3. The customer OTP problem

### Why the obvious approach cannot work

Customer login is phone + SMS code. `/my/login` issues a code; `/my/verify` checks it. The code is
stored **hashed**:

```python
# addons/modryn_portal/models/otp.py:39-45
secret = self.env['ir.config_parameter'].sudo().get_param('database.secret') or ''
msg = ('%s|%s' % (phone, code)).encode()
return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
```

`modryn_otp_code.code_hash` is an HMAC-SHA256 hex digest, salted with the database secret and bound
to the phone number. There is no reverse. `verify.sh` line 111 asserts exactly this
(`length(code_hash) < 40` must be zero rows). **A psql side channel is impossible**, and the right
response to that is to be pleased about it, not to weaken the hashing.

Reading the server log is possible but wrong for a harness: the log-only branch writes the body to
`server.log`, so a 500-VU run would have every k6 iteration tailing and regexing a shared,
concurrently-appended file. Non-deterministic, racy, and it couples the harness to log formatting.

### The solution: `modryn_loadtest`, a staging-only addon

Lives at `loadtest/odoo_addons/modryn_loadtest/`, physically outside `addons/`. It is loaded only by
appending that directory to `addons_path`, which no production configuration does.

**The capture seam.** `modryn.sms` was recently split into three methods:

| Method | Used by |
|---|---|
| `send(to, body)` | the OTP path (`otp.py:72`) and the 24 h reminder (`booking_comms.py:129`) and queue texts (`queue_entry.py:137`) — synchronous, because she is staring at the screen |
| `send_async(to, body)` | booking confirmation (`booking_comms.py:79`) and waitlist offers (`day_waitlist.py:109`) — enqueues into `modryn.sms.outbox` |
| `_send_now(to, body)` | **the single real sender**; both `send()` and the outbox drain (`sms_outbox.py:101`) funnel through it |

> **Correction:** overriding `send` — the obvious hook, and the one the brief assumed — would miss
> every message that goes through `send_async`. Override **`_send_now`**. It is the only place all
> traffic converges.

```python
# loadtest/odoo_addons/modryn_loadtest/models/sms_capture.py  (shape, not final code)
class ModrynSmsCapture(models.AbstractModel):
    _inherit = 'modryn.sms'

    @api.model
    def _send_now(self, to, body):
        # Capture BEFORE delegating: the log-only branch and a Twilio 4xx must both be
        # captured, or a misconfigured tenant silently stops the harness dead.
        if self.env['ir.config_parameter'].sudo().get_param(P_ENABLED) == '1':
            self.env['modryn.loadtest.message'].sudo().create({'phone': to, 'body': body})
        return super()._send_now(to, body)
```

The side table stores `phone`, `body`, `create_date` — the **whole body**, not a pre-extracted code.
Extraction is the reader's job, because the OTP body is translated (`he_IL` is the default language,
so the English wording in `otp.py:69` is not what ships) and because other message bodies carry
6-digit runs inside tokens and links. The reader takes the newest row for the phone and pulls its
first standalone 6-digit run.

**The route.**

```
GET /loadtest/otp?phone=<E.164>&secret=<S>   ->  {"code": "123456"}
```

`type='http'`, `auth='public'`, returns JSON. Every failure — flag off, wrong secret, unknown phone,
no code — returns the **same 404**, so the endpoint is indistinguishable from the addon not being
installed.

**The two-key gate.** Both `ir.config_parameter` keys must be set, per database:

| Key | Meaning |
|---|---|
| `modryn.loadtest.enabled` | must equal `'1'` or nothing is captured and the route 404s |
| `modryn.loadtest.secret` | must equal the `secret` query parameter |

Two keys, not one, because a *mistaken install* is then inert: the module can be present in the
addons path and installed in a database and still capture nothing and expose nothing. One key would
mean "installed" equals "leaking". Neither key is set by `gen_tenants.sh` for any tenant not on the
load-tenant list.

### The rate limit that shapes the whole customer scenario

```python
# addons/modryn_portal/models/otp.py:13, :55-60
MAX_SENDS_PER_HOUR = 3
recent = self.sudo().search_count([
    ('phone', '=', phone),
    ('create_date', '>=', datetime.utcnow() - timedelta(hours=1)),
])
if recent >= MAX_SENDS_PER_HOUR:
    return False, 'rate_limited'
```

Three issues per phone per rolling hour, counted on **rows created**, regardless of whether they were
used, expired or wrong. A 30-minute stage plus a 2-hour soak is inside one hour for most of its
length.

**Consequence:** one customer login per VU per run, with two retries of headroom for a genuinely
failed login. Not "one and only one" — the constraint is three — but budget one and treat a second
as an incident. VUs that need repeated customer activity must **hold the session** (`res.partner` id
in the session, `SESSION_KEY` in `portal.py:19`) and never re-login.

**Consequence:** phone numbers must be deterministic per VU, or a restarted stage collides with the
previous stage's rows and every login fails `rate_limited`.

### The phone scheme

```
+972 52 TT VVVV
       │  └──── 4-digit VU index, 0000–9999
       └─────── 2-digit tenant index, 00–99
```

Example: tenant 07, VU 0042 → `+972520070042`.

Checked against both normalisers, because the same number has to survive two different code paths:

| Path | Rule | Result |
|---|---|---|
| `normalize_il_phone` (`sms.py:30-31`) | starts with `+`, must match `\+\d{9,15}` | `+972520070042` is 12 digits after `+` → **passes** |
| `normalize_il_phone` local form | `0\d{8,9}` | `0520070042` is `0` + 9 → **passes**, normalises to the same E.164 |
| `PHONE_RE` in `modryn_booking` (`main.py:17`) | `^(?:\+972\|0)\d{1,2}[\d\-\s]{6,10}$` | `+972` then `52` then `0070042` (7 chars, within 6–10) → **passes** |
| `phone_variants` (`portal.py:34-47`) | `+972…` also matches `0…` and `972…` | booking-created partners (stored as typed, dashes stripped) and portal-created partners (E.164) find each other |

> **Safety note that is not paranoia:** `+97252…` is a real Israeli mobile prefix (052 = Cellcom).
> These numbers are plausible, deliverable numbers. The only thing standing between a load run and
> texting several hundred strangers is §4. Verify §4 before every run; do not assume it.

---

## 4. SMS neutralisation

**There is no code change. There is no mock. There is no patch.**

`modryn.sms._send_now` already branches on configuration:

```python
# addons/modryn_portal/models/sms.py:104-109
cfg = self._twilio_config()
if not cfg:
    _logger.info('[modryn.sms] (no Twilio configured) to=%s body=%s', number, body)
    return True, 'logged'
```

`_twilio_config()` returns `None` unless **all four** of `modryn.twilio.account_sid`,
`.api_key_sid`, `.api_key_secret`, `.from_number` are set (`sms.py:63`). A tenant that has never had
`scripts/configure_twilio.py` run against it has none of them, and every send returns
`(True, 'logged')` without a network call.

This is a **deliberate, pre-existing seam**, documented in the model's own docstring —
*"One seam, two implementations: Twilio when credentials exist, a logger otherwise. Nothing else in
the codebase knows which one is live, so tests and demos never text a real person and never need an
account."* The load harness is exactly the case it was built for. Nothing is bypassed:

* `modryn.otp.code.issue` still creates the row, still hashes, still rate-limits;
* `/my/verify` still runs the full HMAC compare, the attempt counter and the single-use burn;
* `modryn.sms.outbox` still enqueues, still wakes the drain cron via `_trigger()`, still walks its
  batch of 50 and its 3-attempt backoff;
* only the `requests.post` to Twilio is absent.

The measured path is the real path minus one HTTP call — which is the *correct* thing to exclude,
since a `SEND_TIMEOUT = 10` call to a third party is not a property of this system.

### The pre-flight assertion (mandatory, every run)

```sql
-- run against EVERY load tenant; expected: 0
select count(*) from ir_config_parameter where key like 'modryn.twilio.%';
```

Current state on this machine, verified:

| Database | Twilio params |
|---|---|
| `modryn_template` | none |
| `noga` | none |
| `bella` | **all four set** |

> **Correction:** `bella` is fully configured for live Twilio. It must **never** be used as a load
> tenant, and it must not be cloned from. Load tenants are cloned from `modryn_template` (or the
> gold template built from it), which is clean. If bella must be included in a run for any reason,
> delete its four parameters first and restore them afterwards — but the simpler answer is: don't.

`gen_tenants.sh` runs the assertion above after seeding and refuses to write `tenants.json` if any
tenant returns non-zero.

---

## 5. Scenario journeys

All timings are `sleep()` values representing human think time; they are what make VU count mean
something. Every request carries `tags: {surface, kind}`.

### 5.1 `visitor.js` — the anonymous storefront (no auth)

| Step | Request | Data source |
|---|---|---|
| 1 | `GET /shop` | — |
| 2 | scrape a `/shop/<slug>` product href from the grid | the response body; **never a hardcoded id** — catalogs differ per tenant (`seed_catalog.py` has per-slug lists) |
| 3 | `GET` that product page | step 2 |
| 4 | 60 % of iterations: `GET /book/dress/<id>` | the numeric id parsed out of the product URL |
| 5 | 40 %: `GET /book` | — |
| 6 | 20 %: `GET /en/shop`, 10 %: `GET /ar/shop` | exercises the second and third language bundles |

Think time 3–8 s. This is the read-heavy baseline and the one scenario that produces almost no
writes; it exists to separate "the server is slow" from "writes are slow".

Note `/book` and `/book/dress/<id>` both call `_slots()`, which does a full unbounded
`calendar.event` search of all future bookings (`modryn_booking/controllers/main.py:36-51`). That
query grows with booked volume over a long soak. Watch it.

### 5.2 `customer.js` — book, log in, look, cancel

| Step | Request | Data source | Notes |
|---|---|---|---|
| 1 | `GET /book` | — | scrape CSRF **and** the first `<option value="…">` from `select[name=slot]` |
| 2 | `POST /book/submit` | `name` = `LoadTest T{TT} V{VVVV}`; `phone` = the §3 scheme; `slot` from step 1; `terms=on`; `csrf_token` | expect **303** to `/book/confirmed/<id>`; a re-rendered 200 means a validation error — capture the body once, then fail the check |
| 3 | `GET /book/confirmed/<id>` | the `Location` header | |
| 4 | `GET /my/login`, scrape CSRF | — | `session.touch()` in `portal.py:100` is what emits the cookie |
| 5 | `POST /my/login` with `phone` | same phone as step 2 | expect **303** to `/my/verify`; a 200 is `rate_limited` or `invalid_number` |
| 6 | `GET /loadtest/otp?phone=…&secret=…` | §3 | retry twice at 250 ms — the row is written inside the request that issued it, so it is there by the time step 5 returns, but the retry costs nothing |
| 7 | `GET /my/verify`, scrape CSRF | — | must re-scrape: no rotation has happened yet (customer login is not `session.authenticate`), but the token is cheap and the code stays uniform |
| 8 | `POST /my/verify` with `code` | step 6 | expect **303** to `/my/bookings` |
| 9 | `GET /my/bookings` | — | |
| 10 | 25 %: `GET /my/cancel/<event_id>` then `POST` the same with CSRF | the event id from step 2's redirect | drives `modryn_cancel`, which fires the refill loop (`modryn_offer_next`) and therefore a `send_async` → outbox row → cron trigger |

Think time 5–15 s. **Steps 4–8 run at most once per VU per run** (§3). A VU that has already logged
in skips to step 9.

The 25 % cancellation branch is deliberately the most expensive thing a customer can do: it writes
`calendar.event`, frees a slot, hits the day-waitlist, enqueues an SMS and wakes a cron. It is where
write contention will show first.

### 5.3 `staff.js` — the floor, read-mostly

Auth: §2 as a **staff-level** login (`rotem`, `orly`, `noa`).

| Step | Request | Notes |
|---|---|---|
| 1 | `GET /floor` | full page render, includes `_board()` inline |
| 2 | open a websocket, subscribe to `modryn_queue` | one frame, then silent (§1 rate limit) |
| 3 | loop: `POST /floor/data` (JSON-RPC, no params) every 5–12 s | the board poll; assert `result.queue` is an array and `result.error` is absent |
| 4 | on any bus notification: one extra `/floor/data` | mirrors `floor_board.js:40-43`, which refreshes on every `modryn_queue/update` |
| 5 | 15 %: `POST /floor/room` with `{target, target_id, room_id}` | staff **may** set rooms (`floor.py:299` checks `_is_staff`) — the only write a plain staff VU makes |
| 6 | 10 %: `POST /atelier/my` | seamstress self-view |
| 7 | 10 %: `GET /roster` then `POST /roster/available {slot_id, week:0}` | slot ids come from the `/roster` page's rendered rows |

`_board()` is the hot path of the whole system: it runs six searches, builds four lists, and is
called by `/floor`, `/floor/data` **and as the return value of every floor mutation**. Every write a
manager makes pays for a full board rebuild. That is the single most important thing this scenario
exists to measure.

### 5.4 `manager.js` — the floor, write-heavy

Auth: §2 as `sara` / `yael`.

| Step | Request | Data source |
|---|---|---|
| 1–4 | as staff (page, socket, poll) | |
| 5 | `POST /queue/checkin/submit` from a *separate* anonymous jar | creates a pending walk-in for this manager to act on; fields `name`, `phone`, `client_type`, `csrf_token`; note `modryn_check_in` de-duplicates on an open phone (`queue_entry.py:94-100`), so the phone must be unique per iteration |
| 6 | `POST /floor/accept {entry_id}` | the entry id read back from the next `/floor/data` `pending` list |
| 7 | `POST /floor/assign {target:'queue', target_id, employee_id, as_primary:false}` | `employee_id` from `result.staff` in the board |
| 8 | 40 %: a second `/floor/assign` for the same card | exercises the helper path and the through-model ordering |
| 9 | `POST /floor/room {target:'queue', target_id, room_id}` | `room_id` from `result.rooms`; **expect collisions** — two managers racing for one room is a designed-for outcome and returns `result.error`, not a 500. A collision is a pass, a 500 is a finding. |
| 10 | `POST /floor/finish {entry_id}` | returns `result.finished` with the variant list |
| 11 | 30 %: `POST /atelier/task/create` | `customer_name`/`customer_phone` from step 10's `finished`, `variant_id` from its `variants` |
| 12 | 10 %: `POST /floor/sos {target:'manager', note:'load'}` then `/floor/sos/ack` then `/floor/sos/resolve` | three writes, three bus fan-outs |

Think time 2–6 s — managers act faster than they read. Every one of steps 6–12 returns a full
`_board()` **and** publishes a `modryn_queue/update` to every subscribed socket in that tenant. This
is the amplification the whole exercise is about: one manager tap → one board rebuild for the
actor + one bus notification × every open board + one `/floor/data` refresh per board.

### 5.5 `owner.js` — configuration, low concurrency

Auth: §2 as `miri` / `tamar`. Owners are **internal** users (`hr_employee.py:115-118`), so each one
consumes a seat; keep this scenario to 1–2 VUs per tenant, which is also what reality looks like.

| Step | Request | Notes |
|---|---|---|
| 1 | `GET /manage/staff` | the post-login landing |
| 2 | `GET /manage/roles`, `/manage/rooms`, `/manage/pieces`, `/manage/shifts` | four owner-only list pages |
| 3 | 20 %: `POST /manage/roles/new` with a unique name + fresh CSRF | re-scrape the token from the page in step 2 — the login rotation already happened, but each page render gives a valid token |
| 4 | 10 %: `GET /roster?week=0`, `POST /roster/assign {slot_id, employee_id, working:true, week:0}`, `POST /roster/publish {week:0}` | `/roster` materialises next week's slots **on read** (`modryn_ensure_week`) — the first hit per week per tenant is far more expensive than the rest, and that spike is a legitimate finding, not noise |

Think time 10–30 s.

### 5.6 `focused/bus_storm.js` — how many boards can one tenant hold open?

**One tenant. Nothing else running against it.**

| Component | Shape |
|---|---|
| Floor clients | k6 `ramping-vus`: 25 → 50 → 100 → 150 → 200, three minutes at each level. Each VU: staff login, open websocket, subscribe once, then `POST /floor/data` every 10 s and on every notification. |
| Driver | a `constant-vus: 1` scenario running a manager who performs exactly **one write every 2 seconds** — alternating `/floor/room` set and clear on a fixed card, because that is the cheapest write that still publishes to the channel. |
| Instrument | a `constant-vus: 1` scenario, described below. |
| Measured | `http_req_duration{surface:floor,kind:rpc}` p95 for `/floor/data`, **bucketed by the client count at that moment** (k6 tag `clients:<n>`, set from the stage). Plus `ws_notification_latency` p95. |

The output is one table: client count → `/floor/data` p95 → notification p95 → error rate. The
answer to "how many tablets can a boutique have open" is the largest row where both p95s stay inside
threshold.

**The instrumented VU, and why it must be its own VU.** k6 VUs do not share memory. There is no way
for VU 7 to tell VU 112 "I just posted at t=…". So cross-VU correlation is impossible by
construction, and any design that needs it is wrong.

Instead, one dedicated VU does both halves of the round trip on itself:

```
1. staff/manager login (own jar)
2. open own websocket, subscribe, drain the 50 s replay, record maxSeenId
3. loop:
     t0 = Date.now()
     POST /floor/room   (its own dedicated card, so no other VU's write can be mistaken for it)
     await a frame containing a notification with id > maxSeenId
     record ws_notification_latency = Date.now() - t0
     maxSeenId = that id
     sleep 3s
```

This measures *server-side fan-out latency under the load the other 200 VUs are creating*, which is
the number that actually matters. It does not measure any individual floor client's latency — that
is fine, and pretending otherwise with a shared-state hack would produce a worse number, not a
better one.

The driver and the instrument must act on **different cards**, or the instrument cannot tell its own
notification from the driver's.

### 5.7 `focused/booking_race.js` — the test that is supposed to fail

**Purpose:** prove that the submit-time collision guard is a TOCTOU race, then prove the fix closes
it. This test's value is entirely in the before/after pair.

The guard, as it stands:

```python
# addons/modryn_booking/controllers/main.py:193-201
taken_domain = [('modryn_is_booking', '=', True), ('start', '=', start)]
if 'modryn_cancelled_at' in request.env['calendar.event']._fields:
    taken_domain.append(('modryn_cancelled_at', '=', False))
if request.env['calendar.event'].sudo().search_count(taken_domain):
    errors['slot'] = _("That time was just taken, please choose another")
```

A `SELECT count(*)` followed later by an `INSERT`, with nothing between them but hope. Under
`READ COMMITTED` two concurrent requests both count zero and both insert.

| Component | Shape |
|---|---|
| VUs | 50, `shared-iterations`, exactly 1 iteration each |
| Barrier | all 50 pre-fetch `/book` (page + CSRF + slot list) during a 10 s setup window, then `sleep()` until a wall-clock instant passed in via `__ENV.RACE_AT` (epoch ms). Every VU wakes within a few ms of the same moment and posts immediately. |
| Target | **the same** `slot` value for all 50, on a tenant with an otherwise empty calendar |
| Phones | distinct per VU (the §3 scheme) — identical phones would collapse into one `res.partner` and muddy the reading |

**The assertion is not in k6.** It is a psql query run immediately afterwards:

```sql
select start, count(*)
from calendar_event
where modryn_is_booking and modryn_cancelled_at is null
group by start
having count(*) > 1;
```

| State of the code | Expected result | Meaning |
|---|---|---|
| today, no unique index | **one or more rows** — typically 2–6 duplicates out of 50 | the race is real and reproducible; the test **fails**, and that failure is the deliverable |
| after `models.UniqueIndex` is added | **zero rows**, and 49 of the 50 VUs get the friendly "that time was just taken" error rather than a 500 | the fix holds under real concurrency |

The fix, for the record — `models.UniqueIndex` exists in this clone
(`odoo/odoo/orm/table_objects.py:185-205`) and accepts a partial definition plus a message:

```python
_modryn_one_booking_per_slot = models.UniqueIndex(
    "(start) WHERE modryn_is_booking AND modryn_cancelled_at IS NULL",
    "That time was just taken, please choose another",
)
```

Whoever implements it must also catch the resulting `IntegrityError` at the controller and re-render
the form — otherwise the race becomes a 500 instead of a duplicate, which is a different bug, not a
fix. `manage.py` already imports `psycopg2.IntegrityError` and `odoo.tools.mute_logger` for exactly
this pattern; follow it.

Run `booking_race` **before** any staged run (to record the baseline failure) and again after the
fix. Never run it against a tenant that is also carrying a staged run — its whole design assumes an
empty, quiet calendar.

---

## 6. Data seeding

### 6.1 The prerequisite nobody expects: bake a gold template first

Checked, and it changes the plan:

```
$ psql -d modryn_template -tAc "select count(*) from ir_module_module where name like 'modryn%'"
0
$ psql -d modryn_template -tAc "select count(*) from hr_employee"
ERROR:  relation "hr_employee" does not exist
```

> **Correction:** `modryn_template` contains **none** of the seven modryn addons, and not even `hr`.
> `scripts/new_boutique.sh` clones it and applies per-tenant fixups (uuid, `web.base.url`, company
> name, website domain) — and that is all it does. It does **not** install the modryn addons. A
> tenant produced by `new_boutique.sh` alone has no `/floor`, no `/book`, no OTP table. Wrapping it
> directly, as originally planned, would produce thirty databases that 404 on every scenario.

Installing seven addons per tenant would dominate the budget (roughly 1–2 minutes of module
installation each). So the first thing `gen_tenants.sh` does is build the template **once**:

```
modryn_template  ──(install 7 addons + seed shared content)──>  modryn_gold  ──(createdb -T)──> lt01…ltNN
```

`modryn_gold` is not served: it is deliberately absent from `dbfilter`'s reach and from `db_name`,
so nothing ever connects to it and `createdb -T` never trips the zero-connections rule.

### 6.2 `seed/gen_tenants.sh`

```
gen_tenants.sh <count> [--parallel 4] [--prefix lt]
```

*Server must be stopped for phase A and B.* Phases:

| Phase | Work | Parallel? | Cost |
|---|---|---|---|
| **A. gold build** | `createdb -T modryn_template modryn_gold`; then `odoo-bin server -d modryn_gold -i modryn_theme,modryn_booking,modryn_queue_poc,modryn_staff,modryn_portal,modryn_atelier,modryn_roster --stop-after-init`. `-i` implies a module-list refresh (`Module.update_list()` at `odoo/odoo/modules/loading.py:427`), so the addons are discovered without a separate `-u base`. | no | one-off, ~2–4 min |
| **B. gold seed** | one `odoo-bin shell` against `modryn_gold` running `seed_tenant.py` in *template mode*: catalog, garment pieces, fitting rooms, shift templates. Anything identical across tenants belongs here, so it is paid for once. | no | one-off, ~1 min |
| **C. clone** | per tenant: `createdb -T modryn_gold lt07`; `cp -R .odoo-data/filestore/modryn_gold .odoo-data/filestore/lt07`. | yes | ~2–4 s each (80 MB) |
| **D. per-tenant fixups** | the body of `new_boutique.sh` — regenerate `database.uuid`, set `web.base.url` to `http://lt07.localtest.me:8069`, freeze it, set company + website name. **Reuse the script**, do not re-implement it; it is the tenancy-ops evidence. | yes (4) | ~10–15 s each (registry load dominates) |
| **E. per-tenant people** | `MODRYN_SLUG=lt07 MODRYN_TENANT_INDEX=07 MODRYN_DEMO_PASSWORD=… seed_tenant.py` | yes (4) | ~10–15 s each |
| **F. gate** | assert `modryn.twilio.disabled` is set (§4 — was "zero `modryn.twilio.%` params" until 2026-08-14, when credentials moved into the process environment and an empty parameter table stopped proving anything); set `modryn.loadtest.enabled=1` and `modryn.loadtest.secret`; write `k6/config/tenants.json` | yes | seconds |

**The zero-connections rule.** `new_boutique.sh` lines 38–43 already refuse to run when anything is
connected to the source database, with a message that says why:

```bash
CONNS=$(psql -d postgres -tAc "select count(*) from pg_stat_activity where datname='$TEMPLATE'")
if [ "$CONNS" != "0" ]; then
  echo "!! $CONNS connection(s) open to $TEMPLATE — stop the Odoo server first"
```

`createdb -T` takes an exclusive lock on the source. `gen_tenants.sh` inherits this check verbatim
and points it at `modryn_gold`. Because `modryn_gold` is never in `db_name` or reachable through
`dbfilter`, phase C can run **with the server up** — but phases A, B, D and E all use
`odoo-bin shell`, so the whole of `gen_tenants.sh` is documented as *server-stopped*. Hand the
restart to the stage that owns it.

**Parallelism 4, not 10.** Each `odoo-bin shell` builds a full registry and opens several
connections. Postgres allows 100 total. Four concurrent shells plus a live server is comfortable;
ten is how you discover `FATAL: sorry, too many clients already` at minute nine of a fifteen-minute
job.

### 6.3 `seed/seed_tenant.py` — parameterised people

`scripts/seed_staff.py` cannot be used as-is:

```python
PEOPLE = {'bella': [...5 people...], 'noga': [...3 people...]}
for name, job, level, username, phone in PEOPLE[SLUG]:
```

> **Correction:** the brief called this "the fixed 8-person list". It is worse than fixed — it is a
> dict keyed by slug, and `PEOPLE['lt07']` raises `KeyError` and kills the shell. There is no
> fallback branch.

> **Correction:** `seed_staff.py` no longer carries a password literal. It now reads
> `MODRYN_DEMO_PASSWORD` and calls `raise SystemExit` when it is unset — deliberately, with a
> comment explaining that a default is how a credential gets re-committed. **The harness must never
> hardcode `modryn2026`.** (`scripts/verify.sh` §10a still posts that literal and still passes only
> because `bella` and `noga` were seeded before the change. Do not copy that line.)

`seed_tenant.py` keeps the structure and the hard-won details of the original, and generalises the
data:

| Behaviour | Kept from `seed_staff.py` | Why it must be kept |
|---|---|---|
| owner reuses `base.user_admin` | yes | avoids a second internal seat, and avoids the `hr_employee_user_uniq` constraint by adopting the employee `hr` already created for admin |
| everyone else via `modryn_provision_login` | yes | portal group vs internal group is set there, and it raises on a duplicate username |
| idempotent by employee name | yes | re-running must be free |
| `env.cr.commit()` at the end | yes | `odoo-bin shell` does **not** commit on exit |
| password from `MODRYN_DEMO_PASSWORD` | yes, still required | see above |
| the roster of people | **generalised** | see below |

Generated per tenant, from `MODRYN_TENANT_INDEX`:

| Level | Count | Usernames | Purpose |
|---|---|---|---|
| owner | 1 | `owner` | `owner.js`, `/manage/*` |
| manager | 3 | `mgr1`–`mgr3` | `manager.js` |
| staff | 12 | `staff01`–`staff12` | `staff.js`, `bus_storm` floor clients |

Usernames repeat across tenants. That is safe: `res.users.login` is unique **per database**, and one
database per boutique is the tenancy model. It also means `k6/config/tenants.json` carries one shared
credential shape for every tenant, which keeps the scenarios free of per-tenant special cases.

16 people per tenant is enough for 200 concurrent floor clients (VUs share logins; Odoo has no
per-user session cap) while keeping the `/floor/data` staff list a realistic length — a board with
200 employee chips would be measuring a rendering pathology, not a floor.

### 6.4 `seed/snapshot_gold.sh` and `seed/reset.sh`

**Snapshot.** After `gen_tenants.sh` finishes and *before* the first run, freeze one fully-seeded
tenant as the reset source:

```
snapshot_gold.sh  ->  dropdb --if-exists modryn_gold_seeded
                      createdb -T lt01 modryn_gold_seeded
                      cp -R .odoo-data/filestore/lt01 .odoo-data/filestore/modryn_gold_seeded
```

Taken from `lt01` rather than rebuilt, so the reset source is provably identical to what the first
stage actually ran against. `lt01`'s per-tenant fixups (uuid, base url) are wrong for every other
tenant, so `reset.sh` re-applies them after restoring.

**Reset.**

```
reset.sh [--tenants lt01..ltNN]
```

| Step | Command | Note |
|---|---|---|
| 1 | stop the server | hand to the lifecycle stage |
| 2 | per tenant, parallel 4: `dropdb --force lt07` | PG 16 supports `--force`; it terminates leftover backends. Odoo's pool holds connections open past a request, so without `--force` this blocks. |
| 3 | `createdb -T modryn_gold_seeded lt07` | ~2–4 s |
| 4 | `rm -rf` + `cp -R` the filestore | ~2 MB per tenant |
| 5 | re-apply per-tenant fixups (`new_boutique.sh` body) | uuid + base url + names |
| 6 | re-assert the §4 Twilio gate and re-set the two loadtest keys | they live in `ir_config_parameter`, which the restore just overwrote with lt01's |
| 7 | start the server, wait for `GET /shop` → 200 on the first tenant | |

### 6.5 Timing budget

Measured components where they could be measured (database sizes, clone cost); estimated where
running `odoo-bin` was out of scope for this stage. Treat the first real execution as the
calibration run and write the actuals back into this table.

| Job | Budget | Composition |
|---|---|---|
| gold build + gold seed (one-off) | **4–6 min** | module install dominates |
| 30 tenants, parallelism 4 | **~15 min** | 30 × (clone 3 s + fixup shell ~13 s + seed shell ~13 s) ≈ 30 × 29 s ≈ 14.5 min ÷ 4 ≈ 4 min of wall clock in theory; budget 15 min because registry loads contend and the first shell on a cold page cache is far slower than the tenth |
| full reset of 30 tenants | **~4 min** | server stop ~15 s + 30 × (dropdb + createdb ≈ 4 s + filestore copy ≈ 1 s + fixup shell ≈ 13 s) ÷ 4 ≈ 2.5 min + server start and warm-up ~45 s |

Without the gold template (phase A), the 30-tenant figure becomes 30 × ~90 s of module installation
÷ 4 ≈ 11 minutes *on top* of everything else. That is the whole reason phase A exists.

---

## 7. Test schedule and protocol

Runs are ordered so that each one is only worth doing if the previous one passed. Do not reorder to
"get to the interesting part" — the interesting part is uninterpretable without the boring part.

| # | Run | VUs | Duration | Tenants | Purpose |
|---|---|---|---|---|---|
| 0 | `booking_race` (baseline) | 50 | ~1 min | 1 | record the pre-fix duplicate count; this is evidence, not a gate |
| 1 | **smoke** | 10 | 5 min | 2 | every journey executes end to end at least once; every check passes. Purpose: prove the *harness*, not the server. |
| 2 | **baseline** | 100 | 30 min | 10 | the reference numbers every later stage is compared against |
| 3 | **staged ramp** | 100 → 200 → 350 → 500 → … | 30 min per stage | 10 → 30 | find the ceiling |
| 4 | **soak** | 60 % of the highest **passed** stage | 2 h | as that stage | leaks, unbounded growth, cron backlog, session-file accumulation |
| 5 | **spike** | 0 → peak in 60 s | 10 min | as baseline | recovery behaviour, not throughput |
| 6 | `bus_storm` | 25 → 200 | ~20 min | 1 | websocket ceiling |
| 7 | `booking_race` (post-fix) | 50 | ~1 min | 1 | prove the unique index closes the race |

### Smoke (run 1) — the harness gate

Ten VUs, five minutes, all five journeys. It passes only if:

* every scenario completed at least 3 full iterations;
* `checks` rate is **1.00** — not 0.99; at 10 VUs there is no excuse;
* at least one row exists in each of `calendar_event` (booking), `modryn_queue_entry` (walk-in),
  `modryn_alteration_task` (atelier handoff), `modryn_sos_call` (paging);
* the `/loadtest/otp` endpoint served at least one code;
* zero rows in `ir_logging` at level `ERROR` (or zero `ERROR` lines in the server log for the window).

A smoke run that passes on status codes but writes nothing means the scenarios are hitting 404s or
re-rendered error pages. That is the failure this gate exists to catch.

### Staged ramp (run 3) — the shape of every stage

```
   VUs
    │        ┌──────────────────────────┐
    │       ╱                            ╲
    │      ╱                              ╲
    └─────┴──────────────────────────────┴─────► t
      5 min          20 min           5 min
      ramp-in        steady           ramp-out
```

Only the **20-minute steady window** is measured. The ramp-in is JIT warm-up, asset-bundle
compilation and pool growth; including it drags every percentile and hides real regressions.
`main.js` tags requests with the phase and `thresholds.js` filters to `phase:steady`.

Between every pair of stages:

1. ramp-out completes, all VUs are gone;
2. `observe.sh` keeps sampling for a further 2 minutes — **the tail is data**. A server that takes
   90 seconds to drain its cron backlog after the load stops has told you something the steady-state
   p95 did not.
3. stop the server; `reset.sh`; start the server;
4. warm-up: `curl /shop`, `/book`, and one authenticated `/floor` on each of two tenants, discarded;
5. **the gate** (§8). If the stage did not pass, the ramp is over. Do not run the next stage "just to
   see" — an overloaded server produces numbers that describe the overload, not the system.

### Soak (run 4)

Sixty per cent of the highest stage that **passed**, not the highest that ran. Two hours, no reset,
one continuous sample stream. What it is looking for, specifically:

| Signal | Query / source | What a bad answer looks like |
|---|---|---|
| RSS growth | `ps -o rss= -p <odoo pid>` each sample | monotonic climb with no plateau |
| `modryn_sms_outbox` pending | `select count(*) from modryn_sms_outbox where state='pending'` per tenant | grows without bound → 2 cron threads cannot keep up with N tenants |
| cron lateness | `select name, nextcall, now() at time zone 'utc' from ir_cron where active` | `nextcall` falling further behind each sample |
| session files | `ls .odoo-data/sessions \| wc -l` | unbounded growth (vacuum runs, but the store is a flat directory) |
| `bus_bus` rows | `select count(*) from bus_bus` | GC retention is 24 h by default; a 2 h soak should not be alarming, but a table growing faster than the notification rate suggests double-publishing |
| pg dead tuples on `calendar_event`, `modryn_queue_entry` | `pg_stat_user_tables` | autovacuum falling behind |

### Spike (run 5)

Zero to peak in 60 s, hold 5 min, drop to zero in 10 s, watch for 5 min. The question is not
throughput — it is: does the instance recover on its own, or does it need a restart? Record
time-to-first-healthy-`/shop` after the drop.

---

## 8. Stage gates and abort criteria

### Stage gate — all must hold over the **steady window**

| Criterion | Threshold |
|---|---|
| HTTP error rate | < 1 % |
| Check pass rate | > 99 % |
| `kind:page` p95 / p99 | < 1.5 s / < 4 s |
| `kind:rpc` p95 / p99 | < 0.8 s / < 2.5 s |
| `kind:write` p95 / p99 | < 2 s / < 6 s |
| `ERROR` lines in the Odoo log | zero unexplained; a `ValidationError` from the room-collision rule is **explained** and does not count |
| Outbox drain | `modryn_sms_outbox` pending returns to 0 within 2 minutes of ramp-out |
| DB connections | peak < 80 % of `max_connections` (i.e. < 80) |
| Post-run integrity | the duplicate-booking query (§5.7) returns zero rows *unless this is the pre-fix baseline* |

One failed criterion ends the ramp. Record the stage as FAILED with the criterion named, and treat
the previous stage as the ceiling.

### Abort criteria — stop the run immediately

`observe.sh` evaluates these each sample and writes a sentinel file that `main.js` polls via a
`setup()`-installed threshold with `abortOnFail: true`; the operator also watches them.

| Condition | Window | Rationale |
|---|---|---|
| HTTP error rate > 5 % | sustained 2 min | past this the run is measuring the failure mode, not the system |
| `http_req_duration` p99 > 10 s | sustained 2 min | requests are queueing, not serving |
| Postgres connections > 90 % of `max_connections` (> 90) | any sample | the next sample is `FATAL: too many clients`, which locks *you* out too |
| Load-generator CPU > 80 % | sustained 2 min | k6 is now the bottleneck and every latency number is contaminated. Same box as the SUT, so this is likely — this is the criterion that most often fires first |
| Any Odoo worker OOM-kill loop | any occurrence | `limit_memory_hard` defaults to 2560 MB; in threaded mode there is one process, so an OOM kill is a full outage, and a restart loop produces garbage data |
| Filesystem free space < 2 GB | any sample | session files, filestore, logs and 30 databases |

### On abort — capture before you clean up

The instinct is to reset and retry. Resist it; the aborted state *is* the finding. Capture, in this
order, into `results/<run>/abort/`:

1. `k6` summary JSON as far as it got (k6 writes it on abort);
2. the last 200 lines of the Odoo log, plus the full log for the run window;
3. `pg_stat_activity` in full — every backend, its state, its `wait_event`, its `query`;
4. `pg_locks` joined to `pg_stat_activity` for anything not `granted`;
5. `pg_stat_user_tables` for the modryn tables and `calendar_event`;
6. `ps aux` for the Odoo process, plus RSS history from `observe.sh`;
7. `ls .odoo-data/sessions | wc -l`;
8. a one-paragraph note, written **at the time**, saying what you saw happen — the thing that never
   survives to the next morning.

Only then reset.

---

## 9. Result capture and reporting

### Per-run directory

Every run — including aborted ones — gets exactly one directory, created by `main.js`'s wrapper
script before k6 starts:

```
loadtest/results/2026-08-11T09-14-00_ramp-350vu/
├── meta.json          # git sha, dirty flag, stage name, VU profile, tenant count,
│                      # odoo.conf snapshot, start/end ISO timestamps, operator note
├── k6-summary.json    # --summary-export
├── k6-raw.json.gz     # --out json, gzipped; large, but it is the only way to re-cut
│                      # percentiles by tag after the fact
├── observe/
│   ├── pg.csv         # ts, connections, active, idle_in_txn, xact_commit, xact_rollback,
│   │                  # tup_returned, deadlocks, blks_hit, blks_read
│   ├── proc.csv       # ts, odoo_rss_mb, odoo_cpu_pct, host_load1, host_cpu_pct, free_mb
│   ├── app.csv        # ts, per-tenant: outbox_pending, bus_rows, queue_open, bookings_today,
│   │                  # cron_lateness_sec
│   └── sessions.csv   # ts, session_file_count, sessions_dir_mb
├── odoo.log           # the slice of the server log covering the run window
├── assertions.txt     # post-run psql assertions and their output (duplicates, orphans, etc.)
└── notes.md           # written by hand, during the run
```

`meta.json` carries the git sha **and a dirty flag**. A run against uncommitted code whose provenance
cannot be reconstructed is worth nothing three weeks later; the flag is what stops it being quoted as
if it were.

### `observe/observe.sh`

One sampler, 1 Hz for process metrics and 0.2 Hz (every 5 s) for the per-tenant application queries —
the app queries are themselves load, and sampling 30 tenants every second would be a measurable
fraction of the run. It appends CSV, never buffers in memory, and evaluates the §8 abort conditions
each sample, touching `results/<run>/ABORT` when one trips.

It runs as its own process, started before k6 and stopped after k6, so the ramp-out tail is captured.

### The findings report

Written to `docs/` (not `results/`) once the ramp is complete, and structured to answer questions in
the order someone actually asks them:

1. **The number.** One sentence: *"On a 10-core / 16 GB host in threaded mode, this instance served
   N concurrent users across M tenants inside the latency budget; at N+1 it did X."* If there is no
   single number, say why in one sentence.
2. **The ceiling and what caused it.** Named resource — CPU, `db_maxconn`, cron threads, GIL,
   websocket threads — with the sample data that identifies it. "It got slow" is not a cause.
3. **The stage table.** One row per stage: VUs, tenants, p50/p95/p99 by `kind`, error rate, verdict,
   the criterion that failed.
4. **Per-surface breakdown.** Which surface degrades first. The prediction to test is `/floor/data`,
   because `_board()` runs six searches and is the return value of every floor mutation.
5. **The two focused findings**, each stated as a before/after pair:
   * `booking_race`: duplicate count before the unique index, zero after. This one is a **defect
     with a fix**, and it is the most valuable single output of the whole exercise.
   * `bus_storm`: clients → `/floor/data` p95 → notification p95, and the largest passing row,
     phrased as "a boutique can have K boards open".
6. **Soak deltas.** Every counter from §7's soak table, start vs end, with a verdict on each.
7. **What was not tested, and why.** Twilio's real latency (deliberately excluded, §4). Multi-worker
   mode (`workers = 0` is the configuration under test). Anything the ramp never reached.
8. **Reproduction.** The exact commands, the tenant count, the git sha. A load-test result nobody can
   re-run is an anecdote.

---

## 10. Landmines, collected

Every one of these was verified against the code. Each has cost someone a run.

| # | Landmine | Consequence if missed |
|---|---|---|
| 1 | `modryn_template` has **zero** modryn addons installed | thirty tenants that 404 on every scenario |
| 2 | `seed_staff.py` is keyed `PEOPLE[SLUG]` — any slug but `bella`/`noga` raises `KeyError` | the seed shell dies mid-loop, leaving half-seeded tenants |
| 3 | `seed_staff.py` requires `MODRYN_DEMO_PASSWORD`; the old `modryn2026` literal is gone | seeding exits immediately, or the harness logs in with a password that no longer exists |
| 4 | `bella` has all four Twilio params set | a load run texts real Israeli mobile numbers, at 10 s per blocking call |
| 5 | Override `_send_now`, not `send` | the OTP capture misses everything routed through `send_async` |
| 6 | JSON-RPC errors come back as **HTTP 200** | a green run that tested nothing |
| 7 | Failed `/staff/login` re-renders with **HTTP 200**; success is **303** | pass and fail inverted |
| 8 | Session sid is **hard-rotated** on login (`should_rotate=True`, `soft=False`) | every post-login form POST 400s on a stale CSRF token |
| 9 | Websocket inbound rate limit: 10 frames / 0.2 s | chatty ws clients are disconnected and it reads as a server failure |
| 10 | `subscribe` with `last: 0` replays 50 s of history | notification latency measured as negative or absurd |
| 11 | `/atelier` needs **manager**, not staff | staff VUs measure the 404 path |
| 12 | `createdb -T` needs zero connections to the **source** | clone fails with a confusing error mid-job |
| 13 | `dropdb` blocks on Odoo's pooled connections | use `dropdb --force` (PG 16) or stop the server |
| 14 | `odoo-bin shell` does **not** commit on exit | seeds silently vanish; every seed script ends with `env.cr.commit()` |
| 15 | New tenants must be added to `db_name` in `odoo.conf` | their crons never run — the outbox never drains and reminders never fire |
| 16 | `max_cron_threads = 2` across **all** databases | the outbox backlog is a cross-tenant queue, not a per-tenant one |
| 17 | Load generator shares the host with the SUT | above ~300 VUs, k6's own CPU contaminates every latency number. Move k6 off-box before claiming a ceiling above that. |

---

## 11. Execution checklist

Tick in order. Do not skip ahead.

- [ ] `brew install k6`; confirm `k6 version`
- [ ] Add `loadtest/results/` to `.gitignore`
- [ ] Write `modryn_loadtest`; confirm it is under `loadtest/odoo_addons/`, not `addons/`
- [ ] Hand the lifecycle stage: append `loadtest/odoo_addons` to `addons_path`, append the load
      tenants to `db_name`, restart
- [ ] `seed/gen_tenants.sh 2` — two tenants, for the smoke run
- [ ] Assert `select count(*) from ir_config_parameter where key like 'modryn.twilio.%'` = 0 on both
- [ ] Confirm `GET /loadtest/otp` 404s without the secret and returns a code with it
- [ ] Run 0: `booking_race` baseline; record the duplicate count
- [ ] Run 1: smoke, 10 VU; all five gates in §7 pass
- [ ] `seed/gen_tenants.sh 30`; `seed/snapshot_gold.sh`; verify `reset.sh` round-trips once
- [ ] Run 2: baseline, 100 VU, 30 min
- [ ] Run 3: the ramp, gate between every stage
- [ ] Run 4: soak at 60 % of the highest passed stage
- [ ] Run 5: spike
- [ ] Run 6: `bus_storm`
- [ ] Implement the unique index + `IntegrityError` handling; Run 7: `booking_race` post-fix
- [ ] Write the findings report; record the actual seeding timings back into §6.5
