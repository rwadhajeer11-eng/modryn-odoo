# Context prompt

Paste everything below into a fresh Claude session to bring it up to speed.

---

I have two separate projects. Read this before touching anything.

## 1. MODRYN — the real platform (DO NOT MODIFY)

`/Users/mrwen/Documents/Github/Ryan + rawad + mrwen` (note the spaces — always quote the path).
A shipped multi-tenant SaaS for bridal boutiques in Israel: FastAPI + Postgres RLS +
React 19/Vite/Tailwind 4, Hebrew-first RTL, subdomain per boutique. ~45 features merged; its own
roadmap runs autonomously via `/modryn-loop` with state in `.planning/LOOP-STATE.md`.

**Treat it as read-only.** Other sessions commit to it while I work, so its HEAD and file mtimes
move on their own — that is expected, not corruption. You may read it for reference (design
tokens live in `Frontend/packages/ui/src/theme.css`). You may not edit, commit, or leave files in
it. Verify with `git status --porcelain` that only its own pre-existing untracked files remain.

## 2. modryn-odoo — the evaluation project (this is what we work on)

`/Users/mrwen/Documents/Github/modryn-odoo` — a *separate* git repo asking one question with
evidence: should MODRYN be rebuilt on Odoo? Odoo 19 **Community** is a shallow, gitignored clone
at `odoo/`; core is never edited, everything lives in `addons/`.

Two tenant databases, one per boutique, routed by `dbfilter = ^%d$`:
`bella.localtest.me:8069` → db `bella`, `noga.localtest.me:8069` → db `noga`.

**Seven addons, ~6,400 lines:** `modryn_theme` (palette/fonts/RTL, per-dress price visibility),
`modryn_booking` (dual-path booking on `calendar.event`), `modryn_queue_poc` (QR walk-in queue,
`bus.bus` realtime, invisible acceptance gate), `modryn_staff` (employees, owner-defined roles,
drag-and-drop floor board, fitting rooms, SOS paging), `modryn_portal` (phone+OTP login,
my-bookings, confirmation and reminder SMS, day-waitlist refill loop), `modryn_atelier` (garment
pieces, alteration tasks, workshop dashboard), `modryn_roster` (shift templates, availability,
coverage targets, publish).

Run it:

```bash
cd /Users/mrwen/Documents/Github/modryn-odoo && source .venv/bin/activate
./odoo/odoo-bin server -c odoo.conf --http-interface=127.0.0.1
bash scripts/verify.sh          # 85 checks — run this before believing anything works
```

Logins seeded by `scripts/seed_staff.py` (demo password `modryn2026`): `miri` owner · `sara`
shift manager · `rotem`/`noa`/`orly` staff. Staff sign in at `/staff/login`; owner admin is `/manage/*`, floor terminal is
`/floor`, rota is `/roster`.

## Read these first — they exist so you don't have to ask me

The repo carries its own memory. **Start here, in this order:**

| File | Why |
|---|---|
| `.planning/STATE.md` | Where the project stands, and — importantly — what is *proven* vs merely *written* |
| `.planning/BACKLOG.md` | Ranked next steps. Start at #1 unless I say otherwise |
| `.memory/odoo-traps.md` | **Before writing any addon code.** Twelve failures that produce no error and no log line |
| `.memory/decisions.md` | Before proposing a design. Everything here was already argued through |
| `.memory/bugs-and-fixes.md` | Real bugs that shipped here, with root causes |
| `.memory/verification-lessons.md` | Before trusting any test result |
| `docs/design-system.md` | Tokens, the three load-bearing UI rules, component classes |
| `docs/scorecard.md` | The verdict and its evidence — the actual deliverable |
| `docs/walkthrough.md` | 13 replayable acts covering every feature end to end |

Do not make me re-explain what is in those files. If something there is wrong, say so and fix it
rather than working around it.

## The two things most likely to trip you up

**Catching a `ValidationError` does not undo the write that raised it.** It only stops Odoo's
handler rolling the request back. This shipped a bug where the UI said "that room is taken" and
saved it anyway. Use `with request.env.cr.savepoint():`. Full list of the other eleven in
`.memory/odoo-traps.md`.

**An anonymous 303 proves the gate, not the page.** `/floor` was returning 500 for every
signed-in manager while the entire check suite stayed green. Any authenticated surface needs an
authenticated check.

## Known state you should not rediscover

- **Verdict: don't rebuild MODRYN on Odoo.** Settled, with evidence. The one place Odoo genuinely
  won: `bus.bus` gives true websocket realtime in ~50 lines, versus MODRYN's 5s polling.
- **Community only.** Appointment, Planning and WhatsApp are Enterprise-only, and hosting
  Enterprise for customers needs an Odoo partner agreement.
- **SMS is integrated but never delivered.** Twilio accepts the calls and errors come back
  correctly; no message has reached a second handset. I owe you a destination number — ask for
  it. Until then do not describe SMS as working.
- **The Twilio credentials in `.env` were pasted into a chat transcript** and need rotating.

## How I want you to work

- **Verify before claiming.** Run it, read the row back from Postgres, look at the screenshot.
  "It should work" is not a result.
- When a grep over fetched HTML/CSS returns zero for something that ought to be there, **suspect
  the measurement first**: `$(curl …)` mangles large pages. Fetch to a file.
- Playwright MCP is rooted to the MODRYN repo and writes screenshots into it — move each one out
  immediately, then re-check that repo is clean. Never `git add` a directory afterwards without
  looking.
- Tell me plainly when something is broken, unverified, or a guess. I would rather hear "two of
  these 85 checks fail and here's why" than a clean summary that isn't true.
