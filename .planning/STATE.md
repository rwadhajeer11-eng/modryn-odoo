# Where this project stands

_Last updated 2026-08-14, on branch `feature/railway-demo-deploy`._

**Latest: the demo-web build (2026-08-14).** The Railway demo greeted visitors with Odoo's
empty-div homepage, boilerplate footer ("Powered by Odoo", yourcompany.example.com), an empty
shop and phone-code flows that dead-ended into a server log. Now shipped: a real homepage +
per-tenant footer in `modryn_theme` (COW-propagating inherits of `website.homepage` /
`website.footer_custom`, brand promotion removed), `/book` in the nav (`modryn_booking` menu
data, seq 30; per-website copies re-translated by `scripts/seed_demo_web.py` because .po never
reaches them), an empty-state for a slotless `/book` and a book-a-consultation CTA on an empty
`/shop`, **OTP demo mode** (`modryn.sms_demo` param + send()'s `'logged'` no-provider branch —
both must hold — shows the code on the verify pages so the demo is enterable; off everywhere but
`te`), and the workshop's own **add-task form + inline reassign** on `/atelier` (the dead
`/atelier/assign` jsonrpc route became the form POST; `/atelier/task/new` reuses `task_create`'s
validation body, so the qa-pinned contract stays single-sourced). `seed_staff.py` now always
fixes what earlier seeds broke: `is_workshop` on the seamstress role (the auto-assign pool was
dead on every tenant), the `atelier` page grant, and role names that sat Hebrew-under-`en_US`
in the jsonb. `te` got five dresses (`seed_catalog.py`), atelier board content, and company
identity for the footer. verify.sh grew §25 (21 checks: served homepage/footer/nav per tenant,
menu-copy translations, sms_demo default-off + the `'logged'`-gate grep, anonymous-POST
refusals, manager re-check greps, workshop pool on tenants and template).

One question, answered with evidence: **should MODRYN be rebuilt on Odoo?** The answer is no —
see [`../docs/scorecard.md`](../docs/scorecard.md). Everything below exists to make that answer
defensible rather than theoretical.

## Health

| | |
|---|---|
| `qa/` (Playwright) | **22 passed** against `noga` (dev) and **15 passed** against the Railway `te` demo (prod) as of 2026-08-14 — act 1d asserts the homepage hero is served and `/book` is in the nav; act 5 now *waits* for the board paint instead of sampling the instant after load (the 86-chars-over-WAN false alarm from SMOKE_REPORT act 5 — the teeth are intact, an empty div never crosses the threshold). Previously: **21 passed** against `noga` — act 5c proves plain staff land on `/staff/home` and meet a themed 403 (never the board) at `/floor`, act 6b proves one number cannot hold two places and the one-shot notice dies on reload, act 6c pins the workshop create contract (priority and due date refused by name when missing). Previously: **18 passed** twice consecutively. Browser assertions `verify.sh` structurally cannot make: that the bundle **applied** (LibSass dies silently and takes the whole stylesheet), that `/floor` **paints**, that the walk-in board updates over `bus.bus` **without a reload** — act 6 now drives the full check-in including the six-digit code, reading it back by recomputing the HMAC from `database.secret` (`qa/lib/otp.js`), the same trick `verify.sh` uses to reverse a booking token — and launch gate 6's own stated method. `@writes` acts run only against a tenant carrying `modryn.twilio.disabled` — since 2026-08-14 credentials live in the process environment, so holding zero parameters no longer proves anything and `lib/guard.js` now refuses **both** `bella` and `noga`. Provision a throwaway with `MODRYN_SMS_DISABLED=1` |
| `deploy/scripts/verify_edge.sh` | **12 nginx-layer checks**, the ones `verify.sh` cannot reach: catch-all, database manager under every language prefix, rate limits with a positive control, TLS chain and expiry, HSTS, `fail2ban-regex`. `--remote-only` runs 8 and `skip()`s 4 with reasons |
| `scripts/verify.sh` | **388 passed, 0 failed, 2 skipped** as of 2026-08-16 (Twilio-live build: +3 for the per-IP OTP cap — structural gate grep plus the `ip` column on every tenant; two stale 2026-08-14 qa-residue partners on noga cleared, their OTP rows aged out of §15's exclusion by the 24h GC). Before: **385 passed, 0 failed, 2 skipped** as of 2026-08-14 (demo-web build): +21 in the new §25 (demo web presence — homepage/footer/nav on the served page per tenant, menu-copy translations, `modryn.sms_demo` default-off with the `detail == 'logged'` gate grep, anonymous atelier-POST refusals, `_is_manager` re-check greps, workshop pool live on tenants and template), and the noga orphan-partner rows from 2026-08-13's QA runs were cleared. Before this build: **363 passed, 1 failed, 2 skipped** as of 2026-08-14. The +3 are §10k-quinquies: the five-state precedence ladder run against a real tenant on each of bella and noga, plus the cross-tenant probe that they inherit the *same* sender. The 1 failure is **not code** — it is four orphan `res_partner` rows on noga created by public-route QA runs on 2026-08-13, one day before any file in this build existed; §15's check is untouched by it. Clear them and it is 364/0/2. Before this build: **361 passed, 0 failed, 2 skipped** (354 twice consecutively before the review round added §9's engine teeth). The access/workshop build closed the backlog's stated gap: §6-bis now drives the whole check-in twice with one number and counts rows (submit 0 → wrong code 0 → right code exactly 1 at `waiting` → re-check-in still 1, same token), pokes the new unique index by name with an own-tenant control, and §7 asserts the role→page matrix (table everywhere, seeded defaults on the template, the four `can_view` gates by grep, no stale nav-injection views in `ir_ui_view`). Before this build: **328 passed, 0 failed, 2 skipped** — unchanged across the walk-in verification build, which was the point: it asserted nothing about the check-in flow, so its steadiness was a control rather than evidence. The claims about verification live in the acceptance tables of `.planning/specs/walkin-*.md` and in `qa/` act 6. +2 over the availability build: a `bella`/`noga` membership gate that refuses to run against a server missing either name, and §15 no longer false-positiving on portal logins. Takes `BASE_HOST`/`BASE_SCHEME`/`ODOO_CONF`, so it now runs **through nginx** against production hostnames rather than bypassing nginx via `/etc/hosts`. The new ground: cross-tenant product URLs in three languages and the shared `database.secret` (§1), the `.ics` export (§10b-bis), and §24 for the whole availability engine — opening hours seeded identically across all three databases, closures, the one-booking index proven to key on `(start, modryn_slot_seat)` **and** proven to still reject a duplicate seat, and the rota cap proven not to empty a grid. The total moves with the demo data rather than being fixed: three of §10b-bis's checks need a future booking on bella. Both skips are fixture age, not gaps in the code — bella holds no future booking and no cancelled future booking, so those two branches have no subject. They fire again the moment anyone books ahead |
| Odoo | 19.0 **Community**, shallow gitignored clone at `odoo/`, never edited |
| Tenants | `bella` and `noga` — one Postgres database each, routed by `dbfilter = ^%d$` |
| Custom code | ~10,250 non-blank lines across eight addons (+1,300 in the access/workshop build) |
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

The access/workshop build (2026-08-13, later the same day) added to the proven list:
**one open place per phone number** — §6-bis drives the flow twice with one number and reads the
row count and the *same* access token back; a raw psql INSERT of a second open row is refused by
`modryn_queue_entry_modryn_open_phone_uniq` by name, with an own-tenant control. **The role→page
matrix** — rotem (staff) landed on `/staff/home`, met a themed 403 at `/floor`, the owner ticked
Floor for her role over POST `/manage/roles/pages`, her next request got the board, the revert
took it away again. **The workshop engine** — a task created with an idle flagged seamstress was
auto-assigned at birth; two more queued; delivering her last piece pulled the High one, not the
Low one; a create without a due date was refused with `missing_due`. **Assignment SMS** — a
manual alteration assignment and a floor walk-in assignment each left exactly one Hebrew body in
`modryn_sms_outbox` for the seamstress's number, and the auto-pull of her own next task
deliberately texted nobody (actor-skip). One honest note: noga's `yael`/`dana` employee rows
predated the seeder's phone column and carried none — the notifier logged its designed skip; the
demo rows were fixed by SQL.

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

**PROVEN 2026-08-16 — SMS delivery, on production, carrier-confirmed.** The owner supplied a
real handset, `TWILIO_*` went onto the Railway service, and `qa/specs/realsms.spec.js`
(opt-in, `@realsms`) drove the whole chain through a browser: `/my/login` with the real number →
no demo-code box → Twilio Messages API **`status=delivered`** for
`SMad8b061c8679619d227031398580bcac` → the code from the delivered body completed the login to
`/my/bookings`. 23.6s end to end. The build also added the per-IP OTP cap
(`IP_MAX_SENDS_PER_HOUR = 30`, `modryn_otp_code.ip`) — verified live, 31 requests from one IP
and the 31st refused — because real Twilio behind anonymous forms with only a per-phone cap is
an SMS-bomb relay. Still narrower than the full comms story: the booking-confirmation /
cancel / waitlist-claim templates have not had a handset run; the transport has.

**Since 2026-08-14 every tenant sends through one Twilio account.** The four credentials moved out
of each database and into the Odoo process environment (`EnvironmentFile` on the unit in prod,
`set -a; . ./.env` in dev); `_twilio_config()` resolves the tenant's off switch, then the tenant's
own four parameters, then the platform's four variables — each level all-or-nothing. bella's private
copy was deleted by `scripts/migrate_twilio_to_platform.sh`, so **no database holds a credential
now** and both tenants provably resolve the same sender (§10k-quinquies asks them separately and
compares — a claim no single tenant can make about itself).

What that cost, stated plainly: **the guarantee inverted from opt-in to opt-out.** "This database
holds zero `modryn.twilio.*`" used to mean "this database cannot reach a handset", and four
harnesses were built on it. It now means only that the tenant has no override. A tenant is safe
because someone set `modryn.twilio.disabled`, not because nobody set credentials — so `noga` is
live, and `qa/lib/guard.js` refuses `@writes` against it until a flagged throwaway exists.

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
| `a45f3b6` | The walk-in queue learns who is holding the phone — SMS verification, a staff door, and the text that says you are in the line |
| `d6c9cc1` | One navbar, an owner-run role→page matrix, a staff home page, a workshop queue that hands out its own work by priority, an SMS on every assignment — and one open place per phone number, decided by Postgres |
| _this_ | One Twilio account behind every database: credentials move to the process environment, the per-tenant copies are deleted, and the four harnesses that keyed on their absence learn an explicit off switch instead. Customer texts now name the boutique, because one shared number cannot |

## Before changing anything

Read [`../.memory/odoo-traps.md`](../.memory/odoo-traps.md) first, and
[`../.memory/decisions.md`](../.memory/decisions.md) before proposing a design. What to do next
is in [`BACKLOG.md`](BACKLOG.md).
