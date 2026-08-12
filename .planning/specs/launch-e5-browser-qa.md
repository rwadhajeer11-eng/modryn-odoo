# E5 — browser QA: the assertions curl cannot make

_Closes no launch gate. That is the point._

## The problem

There is not one browser test in this repository. No Playwright, no Selenium, no Odoo tour, no
`package.json` outside the vendored `odoo/` clone. The entire safety net is `verify.sh` — which
speaks HTTP and reads Postgres — and `docs/walkthrough.md`, which is fourteen acts a human
replays by hand.

That leaves a whole class of failure invisible. `verify.sh` §10a asserts `/floor` returns 200. It
cannot assert the board **paints**. A page that returns 200 with a JS exception leaving an empty
div is exactly the shape of the bug §10a was written after, and §10a still cannot see it.

Worse: **LibSass dies silently** on one Color-Level-4 `rgb(a b c / d)` and takes the *entire*
frontend bundle rather than the offending rule. `curl` sees a 200 with correct HTML and zero
styling. Both `primary_variables.scss:24` and `modryn.scss:22` carry that warning because it has
already happened once.

## Footprint, kept deliberately small

The repo has no JS tooling and should gain as little as possible: no TypeScript, no bundler, no CI
config, no page-object framework.

```
qa/
├── package.json          one devDependency
├── playwright.config.js
├── lib/{otp,guard}.js
└── specs/*.spec.js
```

`workers: 1` — **not** a performance choice. The booking spec takes a real slot out of a shared
grid and the queue spec asserts on a board every other test's writes also land on. Parallel workers
would make both flaky in a way that reads as a product bug.

`locale: 'he-IL'` plus the matching `Accept-Language` header, because he_IL is the tenant default
and Odoo 303s to `/en/...` for anything that does not ask for Hebrew — different URLs, different
markup, different form actions. `loadtest/k6/lib/session.js` carries the full account of how that
silently broke every page marker in the k6 harness; it does not need repeating here, only obeying.

## Seven acts, chosen for what curl cannot see

| # | Act | Assertion | The failure it catches | Writes |
|---|---|---|---|---|
| 1 | RTL + palette | `<html dir="rtl">`; computed `h1` font-family starts `Frank Ruhl Libre`; a `.btn-primary` background is `rgb(197,160,89)`; no horizontal scroll at 375px | the silent LibSass bundle collapse | no |
| 2 | Arabic toggle | `/ar/shop`: `lang` starts `ar`, still `dir="rtl"`, same gold assertions, no console error | `/en/` and `/ar/` compile to a **different bundle** than `/he/`; a theme change can compile in one and break in the other | no |
| 3 | Booking | `/book` renders ≥1 valid slot option; submit **without** terms → error, no row; **with** terms → 303 to `/book/confirmed/<id>`; reload → that slot is gone | curl can POST a form but cannot prove the grid the *browser was shown* is the grid the server accepts | **@writes** |
| 4 | Phone OTP | wrong code → Hebrew error, attempts incremented; right code → `/my` lists act 3's booking with its dress and size | the OTP is the only auth path fail2ban cannot see; a regression here is invisible to every other check in the repo | **@writes** |
| 5 | Staff | `sara` lands on `/floor`, not `/manage/staff`; three panels paint; `/manage/staff` refused for her | §10a asserts the 200 and cannot assert the paint | no |
| 6 | Live board | `/floor` open; a **second** browser context POSTs `/queue/checkin/submit`; the first page shows the name without reload inside 10s | `modryn-site.conf`'s `location ^~ /websocket` repeats all six `proxy_set_header` lines because nginx drops the inherited set when a level adds one. An edit removing `X-Forwarded-Host` there breaks **only** the floor board, **only** in production, and every HTTP check stays green | **@writes** |
| 7 | Gate 6 | `page.on('request')`: **zero** matches for `fonts.(gstatic\|googleapis).com`, plus a positive control — ≥1 request to `/modryn_theme/static/src/fonts/*.woff2` and computed font-family resolving to Assistant | gate 6's own stated method ("browser network tab on /shop"), automated. The control is what stops it passing when the fonts simply never load | no |

## The write problem, solved with a tenant rather than a mode

A read-only production mode is the obvious answer and the wrong one: acts 3, 4 and 6 are precisely
the ones curl cannot do, so excluding them from production deletes most of the value.

Instead, a **dedicated throwaway tenant** — `qa.<DOMAIN>`, provisioned exactly like any boutique
except with the Twilio variables empty. Zero `modryn.twilio.*` parameters is the property
`modryn.sms._send_now` branches on: with no config it logs the body, returns `('logged')`, and no
message leaves the box. That is the same mechanism `loadtest/README.md` documents as "the actual
mechanism" — reused, not reinvented.

**Assert it, do not assume it.** `globalSetup` refuses to run `@writes` against a tenant that could
text a real person and against any tenant not named in `QA_TENANTS`, and fails **closed**: a
missing `QA_TENANTS` refuses everything rather than allowing everything.

**No per-test teardown, by design.** The qa tenant is dropped after launch; between runs
`deploy/scripts/restore.sh` resets it from the previous night's dump in about 30 seconds. Teardown
code that deletes bookings would have to be as correct as the product itself, in order to undo rows
nobody will ever look at.

## OTP in production, without the forbidden addon

Three non-starters, named so nobody retries them:

1. `/loadtest/otp` is forbidden — `loadtest/odoo_addons` off the production `addons_path` is the
   first of its three gates, and moving it is the accident the whole arrangement prevents.
2. The code is stored only as `hmac_sha256(database.secret, "<e164>|<code>")`
   (`addons/modryn_portal/models/otp.py`), so it cannot be read back.
3. Adding `log_handler = odoo.addons.modryn_portal.models.sms:INFO` to recover the log line would
   write **every real customer's OTP and every booking link** into the journal, for every tenant,
   because `log_handler` is global.

The mechanism that works is already in this repo: `scripts/verify.sh:56-63` reverses a booking
token by recomputing the HMAC from `database.secret`. Do the same — the code space is 10⁶ and one
million HMACs is under two seconds. This is the established idiom here, not a new trick.

**Budget trap:** `MAX_SENDS_PER_HOUR = 3` per phone number. Derive the QA phone from the run
timestamp, or a second run inside the hour reports `rate_limited` as a product failure — the same
reasoning `session.js::phoneForVu` already documents.

## Acceptance

| # | Check | Required |
|---|---|---|
| 1 | `cd qa && npm test` against dev | 7 specs pass |
| 2 | act 7 before E3 lands | **fails** — proving it detects the Google fonts |
| 3 | act 7 after E3 lands | passes, including the woff2 positive control |
| 4 | `globalSetup` with `QA_TENANTS` unset | refuses to run `@writes` |
| 5 | `qa/node_modules/` and the report dirs | gitignored |
| 6 | two consecutive `npm test` runs inside one hour | both pass — the phone derivation works |
