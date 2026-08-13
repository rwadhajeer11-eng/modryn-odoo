# Where this project stands

_Last updated 2026-08-12, on branch `feature/tenancy-slug-and-ics`._

One question, answered with evidence: **should MODRYN be rebuilt on Odoo?** The answer is no —
see [`../docs/scorecard.md`](../docs/scorecard.md). Everything below exists to make that answer
defensible rather than theoretical.

## Health

| | |
|---|---|
| `qa/` (Playwright) | **18 passed** against `noga`, twice consecutively. Browser assertions `verify.sh` structurally cannot make: that the bundle **applied** (LibSass dies silently and takes the whole stylesheet), that `/floor` **paints**, that the walk-in board updates over `bus.bus` **without a reload** — act 6 now drives the full check-in including the six-digit code, reading it back by recomputing the HMAC from `database.secret` (`qa/lib/otp.js`), the same trick `verify.sh` uses to reverse a booking token — and launch gate 6's own stated method. `@writes` acts run only against a tenant with zero `modryn.twilio.*` — `lib/guard.js` refuses `bella`, which holds four |
| `deploy/scripts/verify_edge.sh` | **12 nginx-layer checks**, the ones `verify.sh` cannot reach: catch-all, database manager under every language prefix, rate limits with a positive control, TLS chain and expiry, HSTS, `fail2ban-regex`. `--remote-only` runs 8 and `skip()`s 4 with reasons |
| `scripts/verify.sh` | **328 passed, 0 failed, 2 skipped** — unchanged across the walk-in verification build, which is the point: it asserts nothing about the check-in flow, so its steadiness is a control rather than evidence. The claims about verification live in the acceptance tables of `.planning/specs/walkin-*.md` and in `qa/` act 6. +2 over the availability build: a `bella`/`noga` membership gate that refuses to run against a server missing either name, and §15 no longer false-positiving on portal logins. Takes `BASE_HOST`/`BASE_SCHEME`/`ODOO_CONF`, so it now runs **through nginx** against production hostnames rather than bypassing nginx via `/etc/hosts`. The new ground: cross-tenant product URLs in three languages and the shared `database.secret` (§1), the `.ics` export (§10b-bis), and §24 for the whole availability engine — opening hours seeded identically across all three databases, closures, the one-booking index proven to key on `(start, modryn_slot_seat)` **and** proven to still reject a duplicate seat, and the rota cap proven not to empty a grid. The total moves with the demo data rather than being fixed: three of §10b-bis's checks need a future booking on bella. Both skips are fixture age, not gaps in the code — bella holds no future booking and no cancelled future booking, so those two branches have no subject. They fire again the moment anyone books ahead |
| Odoo | 19.0 **Community**, shallow gitignored clone at `odoo/`, never edited |
| Tenants | `bella` and `noga` — one Postgres database each, routed by `dbfilter = ^%d$` |
| Custom code | ~8,950 non-blank lines across eight addons |
| Walkthrough | 14 replayable acts in [`../docs/walkthrough.md`](../docs/walkthrough.md) |

## The eight addons

| Addon | Lines | What it is |
|---|---|---|
| `modryn_theme` | 359 | MODRYN palette (declared once, consumed by every frontend bundle), fonts and RTL through Odoo's native theming slots; per-dress price visibility; the slug guard that makes one tenant's product URL 404 on another |
| `modryn_booking` | 768 | Dual-path booking on `calendar.event` — dress-bound and standalone; server-enforced terms; **the availability engine** — opening hours, closures and per-window capacity as owner data, read by both the booking grid and the waitlist claim page |
| `modryn_queue_poc` | 701 | QR walk-in queue, `bus.bus` realtime, Waitwhile-style intake — **phone-verified since 2026-08-13**: a six-digit code stands between the form and the ticket, and no row exists until it is right |
| `modryn_staff` | 3,198 | Employees, owner-defined roles, assignment with primary + helpers, drag-and-drop floor board, fitting rooms, SOS paging, `/manage/hours` |
| `modryn_portal` | 1,518 | Phone + SMS OTP login, my-bookings, confirmation and 24h reminder SMS, day-waitlist refill loop, `.ics` export |
| `modryn_atelier` | 474 | Garment pieces, alteration tasks, workshop dashboard, seamstress self-view |
| `modryn_roster` | 863 | Owner shift templates, staff availability, per-role coverage targets, publish — and publishing now means something outside `/roster`: the floor board flags who is on today's rota, and the rota caps what the booking grid can sell |
| `modryn_ops` | ~1,700 | Appointment outcomes (sold / not sold / no-show) with SMS flows, follow-up tasks + owner-defined opening/closing checklists with overdue escalation, bride CRM fields with manager-gated budget, conversion/ATV reports, append-only audit trail |

## Proven vs merely written

Being precise about this matters more than the feature list.

**Proven end to end** — exercised against a running server and read back from Postgres:
tenancy isolation, both booking paths, the queue with its acceptance gate and warm states, the
floor board including realtime over `bus.bus`, drag-and-drop assignment, fitting rooms and their
collision rule, SOS paging with acknowledgement and escalation, the refill loop from cancellation
through claim to booking, the roster from offer through assignment to publish, and tri-language
rendering on customer surfaces. Since 2026-08-11 that list also holds the cross-tenant product
URL 404 and the `.ics` export — the latter checked byte-for-byte against the booking's real
`start` in Postgres, not merely for a 200.

Since 2026-08-13 it also holds **walk-in phone verification**, proven the only way an absence
can be: by counting rows. Three codes issued to a number that never verified left **zero**
rows; five wrong codes exhausted the attempt cap and a sixth was refused outright, all six leaving
zero; abandoning at the code step left zero; the right code created exactly one at `waiting`. The first-in-line **fold** was proven by outbox contents — one text to a
bride arriving into an empty line, two distinct bodies across two brides — and the promotion chain
by finishing the front bride and reading the next one's Hebrew `את הבאה בתור` out of the outbox. That last one only
passes because of a repair this build made: `/floor/finish` wrote `state='done'` directly and had
**never** promoted anyone, which every acceptance used to paper over.

**"Tenancy isolation" was on that list before it had earned its place**, and saying so is the
point of this section. On 2026-08-11 it failed in two independent ways: bella's product URL
301'd onto noga's same-numbered dress, and — far worse — all three databases shared one
`database.secret`, because `createdb -T` copies it and `new_boutique.sh` never rotated it. Ids
restart at 1 per database, so **bella's booking token was byte-identical to noga's**, and the
token is the entire auth model for `/b/<token>`: it read, confirmed and could cancel another
boutique's appointment. Both are fixed and both are now asserted in §1. The lesson worth keeping
is why 263 green checks missed it — **every one of them asked a single tenant about itself**. A
cross-tenant claim needs a cross-tenant probe. See `.memory/odoo-traps.md` §13.

**Verified in a real browser:** the SOS overlay reaching a second signed-in user over the
websocket with no reload, and drag-and-drop on the floor board.

**Written but NOT proven:** SMS delivery. Every Twilio call is accepted and errors come back
correctly — a live attempt returned error 21266 (`'To' and 'From' cannot be the same`), which
proves credentials, adapter and error handling all work. **No message has ever been delivered to
a second handset.** That needs a destination phone number. Until then, treat the comms engine as
integrated, not delivered.

Only `bella` has Twilio credentials. `noga` logs `(no Twilio configured)` — the honest fallback,
deliberately not a silent success.

## Run it

```bash
cd /Users/mrwen/Documents/Github/modryn-odoo && source .venv/bin/activate
./odoo/odoo-bin server -c odoo.conf --http-interface=127.0.0.1
bash scripts/verify.sh          # 328 checks — run before believing anything works
```

Logins seeded by `scripts/seed_staff.py`, demo password `modryn2026`: `miri` owner ·
`sara` shift manager · `rotem` / `noa` / `orly` staff. (A `bella` database that has been
walked through may carry extra accounts created via `/manage/staff` — `yaels` is one — which
a fresh seed will not reproduce.) Staff sign in at `/staff/login`; owner admin is `/manage/*`, the floor
terminal is `/floor`, the rota is `/roster`.

After editing an addon, upgrade it or nothing changes:

```bash
./odoo/odoo-bin server -c odoo.conf -d bella --db-filter='^bella$' -u modryn_staff --stop-after-init
```

## History

| Commit | What |
|---|---|
| `9ed0e65` | Odoo 19 Community PoC — multi-tenant bridal storefront |
| `c14a717` | Staff layer: employees, owner-defined roles, assignment |
| `4945ced` | Tri-language support and the customer portal |
| `f4f0138` | Drag-and-drop dispatch board, atelier handoff, staff language toggle |
| `5928a95` | Fixed roaming crons and alphabetical helper promotion |
| `85b9056` | Comms engine: confirmation SMS and an answerable 24h reminder |
| `f331ba5` | Premium walk-in waitlist: invisible gate, three warm states |
| `ab942c9` | Advance-cancellation refill loop |
| `13bdba8` | Fitting-room registry and SOS paging |
| `a5a65cb` | Weekly roster |
| `0b42239` | Walkthrough acts 10–13, refreshed scorecard, two verify fixes |
| `7006ad6` | `modryn_ops` — outcomes, tasks, CRM, reports, audit |
| `56cfa46` `de547b7` `f8c377b` | Cross-tenant product URLs 404; `.ics` export; and the shared `database.secret` found while building it |
| `6b7687d` | One palette, declared once; the slug rule stops over-reaching on case and accents |
| `11261a6` | Opening hours become owner data — the fixed Sun–Thu 10–18 lattice, hardcoded in two controllers, is now one table both read |
| `c270603` | Closed days; and the published rota reaches the floor board |
| `76119af` | More than one fitting an hour, still decided by Postgres |
| `8602551` | The rota caps what the grid can sell |
| _this_ | The walk-in queue learns who is holding the phone — SMS verification, a staff door, and the text that says you are in the line |

## Before changing anything

Read [`../.memory/odoo-traps.md`](../.memory/odoo-traps.md) first, and
[`../.memory/decisions.md`](../.memory/decisions.md) before proposing a design. What to do next
is in [`BACKLOG.md`](BACKLOG.md).
