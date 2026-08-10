# Where this project stands

_Last updated 2026-08-10, at commit `0b42239`._

One question, answered with evidence: **should MODRYN be rebuilt on Odoo?** The answer is no —
see [`../docs/scorecard.md`](../docs/scorecard.md). Everything below exists to make that answer
defensible rather than theoretical.

## Health

| | |
|---|---|
| `scripts/verify.sh` | **85 passed, 0 failed** — from a cold upgrade of all seven addons on both tenants |
| Odoo | 19.0 **Community**, shallow gitignored clone at `odoo/`, never edited |
| Tenants | `bella` and `noga` — one Postgres database each, routed by `dbfilter = ^%d$` |
| Custom code | ~6,400 non-blank lines across seven addons |
| Walkthrough | 13 replayable acts in [`../docs/walkthrough.md`](../docs/walkthrough.md) |

## The seven addons

| Addon | Lines | What it is |
|---|---|---|
| `modryn_theme` | 271 | MODRYN palette, fonts and RTL through Odoo's native theming slots; per-dress price visibility |
| `modryn_booking` | 460 | Dual-path booking on `calendar.event` — dress-bound and standalone; server-enforced terms |
| `modryn_queue_poc` | 517 | QR walk-in queue, `bus.bus` realtime, Waitwhile-style intake with an invisible acceptance gate |
| `modryn_staff` | 2,774 | Employees, owner-defined roles, assignment with primary + helpers, drag-and-drop floor board, fitting rooms, SOS paging |
| `modryn_portal` | 1,136 | Phone + SMS OTP login, my-bookings, confirmation and 24h reminder SMS, day-waitlist refill loop |
| `modryn_atelier` | 474 | Garment pieces, alteration tasks, workshop dashboard, seamstress self-view |
| `modryn_roster` | 779 | Owner shift templates, staff availability, per-role coverage targets, publish |

## Proven vs merely written

Being precise about this matters more than the feature list.

**Proven end to end** — exercised against a running server and read back from Postgres:
tenancy isolation, both booking paths, the queue with its acceptance gate and warm states, the
floor board including realtime over `bus.bus`, drag-and-drop assignment, fitting rooms and their
collision rule, SOS paging with acknowledgement and escalation, the refill loop from cancellation
through claim to booking, the roster from offer through assignment to publish, and tri-language
rendering on customer surfaces.

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
bash scripts/verify.sh          # 85 checks — run before believing anything works
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

## Before changing anything

Read [`../.memory/odoo-traps.md`](../.memory/odoo-traps.md) first, and
[`../.memory/decisions.md`](../.memory/decisions.md) before proposing a design. What to do next
is in [`BACKLOG.md`](BACKLOG.md).
