# Where this project stands

_Last updated 2026-08-11, on branch `feature/tenancy-slug-and-ics`._

One question, answered with evidence: **should MODRYN be rebuilt on Odoo?** The answer is no —
see [`../docs/scorecard.md`](../docs/scorecard.md). Everything below exists to make that answer
defensible rather than theoretical.

## Health

| | |
|---|---|
| `scripts/verify.sh` | **301 passed, 0 failed, 2 skipped** — 303 checks, +40 on this run over the `modryn_ops` build's 263: 9 in §1 (cross-tenant product URLs in three languages, and the shared `database.secret` that let one tenant's booking token open another's), §10b-bis for the `.ics` export, 1 guarding the slug rule against over-reach (a non-canonical slug that *slugifies* to canonical must still 301), and 7 in the new §24 (opening hours seeded identically across all three databases, manifest/migration versions in step, and the grid following the table rather than a constant). The total moves with the demo data rather than being fixed: three of §10b-bis's checks need a future booking on bella. Both skips are fixture age, not gaps in the code: bella holds no *future* booking and no *cancelled future* booking, so the "add to calendar" and "remove from calendar" branches have no subject. They were verified by hand and fire again the moment anyone books ahead. The second skip is newly visible rather than newly broken — those checks were nested inside the future-booking branch, so when the last future booking aged into the past three assertions silently stopped running behind a single skip. A fixture guard now gates only the checks that need that fixture |
| Odoo | 19.0 **Community**, shallow gitignored clone at `odoo/`, never edited |
| Tenants | `bella` and `noga` — one Postgres database each, routed by `dbfilter = ^%d$` |
| Custom code | ~8,100 non-blank lines across eight addons |
| Walkthrough | 14 replayable acts in [`../docs/walkthrough.md`](../docs/walkthrough.md) |

## The eight addons

| Addon | Lines | What it is |
|---|---|---|
| `modryn_theme` | 359 | MODRYN palette (declared once, consumed by every frontend bundle), fonts and RTL through Odoo's native theming slots; per-dress price visibility; the slug guard that makes one tenant's product URL 404 on another |
| `modryn_booking` | 506 | Dual-path booking on `calendar.event` — dress-bound and standalone; server-enforced terms; **opening hours as owner data**, the one grid both booking and the waitlist read |
| `modryn_queue_poc` | 517 | QR walk-in queue, `bus.bus` realtime, Waitwhile-style intake with an invisible acceptance gate |
| `modryn_staff` | 2,917 | Employees, owner-defined roles, assignment with primary + helpers, drag-and-drop floor board, fitting rooms, SOS paging, `/manage/hours` |
| `modryn_portal` | 1,281 | Phone + SMS OTP login, my-bookings, confirmation and 24h reminder SMS, day-waitlist refill loop, `.ics` export |
| `modryn_atelier` | 474 | Garment pieces, alteration tasks, workshop dashboard, seamstress self-view |
| `modryn_roster` | 779 | Owner shift templates, staff availability, per-role coverage targets, publish |
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
bash scripts/verify.sh          # 303 checks — run before believing anything works
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
| _this_ | Opening hours become owner data — the fixed Sun–Thu 10–18 lattice, hardcoded in two controllers, is now one table both read |

## Before changing anything

Read [`../.memory/odoo-traps.md`](../.memory/odoo-traps.md) first, and
[`../.memory/decisions.md`](../.memory/decisions.md) before proposing a design. What to do next
is in [`BACKLOG.md`](BACKLOG.md).
