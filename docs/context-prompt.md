# Context prompt

Paste everything below into a fresh Claude session to bring it up to speed.

---

I have two separate projects. Read this before touching anything.

## 1. MODRYN — the real platform (DO NOT MODIFY)

`/Users/mrwen/Documents/Github/Ryan + rawad + mrwen` (note the spaces — always quote the
path). A shipped multi-tenant SaaS for bridal boutiques in Israel: FastAPI + Postgres RLS +
React 19/Vite/Tailwind 4, Hebrew-first RTL, subdomain per boutique. ~45 features merged, its
own roadmap runs autonomously via `/modryn-loop` with state in `.planning/LOOP-STATE.md`.

**Treat it as read-only.** Other sessions commit to it while I work, so its HEAD and file
mtimes move on their own — that is expected, not corruption. You may read it for reference
(design tokens live in `Frontend/packages/ui/src/theme.css`). You may not edit, commit, or
leave files in it. Verify with `git status --porcelain` that only its own pre-existing
untracked files remain.

## 2. modryn-odoo — an evaluation project (this is what we work on)

`/Users/mrwen/Documents/Github/modryn-odoo` — a *separate* git repo asking one question with
evidence: should MODRYN be rebuilt on Odoo? Odoo 19 **Community** is a shallow, gitignored
clone at `odoo/`; core is never edited, everything lives in `addons/`.

Two tenant databases, one per boutique, routed by `dbfilter = ^%d$`:
`bella.localtest.me:8069` → db `bella`, `noga.localtest.me:8069` → db `noga`.

Four addons: `modryn_theme` (MODRYN palette/fonts/RTL + per-dress price visibility),
`modryn_booking` (dual-path booking on `calendar.event`), `modryn_queue_poc` (QR walk-in
queue + bus.bus realtime), `modryn_staff` (employees, owner-defined roles, assignment,
three permission levels).

Run it:
```bash
cd /Users/mrwen/Documents/Github/modryn-odoo && source .venv/bin/activate
./odoo/odoo-bin server -c odoo.conf --http-interface=127.0.0.1
./scripts/verify.sh          # 27 checks — run this before believing anything works
```
Logins (demo password `modryn2026`): `miri` owner · `sara` shift manager · `rotem` staff.
Staff sign in at `/staff/login`; owner admin is `/manage`, floor terminal is `/floor`.

Read `docs/scorecard.md` (the verdict), `docs/walkthrough.md` (replayable end-to-end
script), and `README.md` (setup + the traps) before proposing anything.

## Decisions already made — do not re-litigate

- Odoo is a **separate** project; MODRYN continues independently.
- **Community only.** Appointment, Planning and WhatsApp are Enterprise-only; hosting
  Enterprise for customers also needs an Odoo partner agreement. Enterprise stays deferred.
- Three permission levels. Owner = internal user; **manager and staff = portal users**,
  which are free even under Enterprise and cannot reach `/odoo`.
- Staff roles are owner-created **data**, never a Selection field.
- Occupancy (זמין/תפוס) is **derived** from live assignments + the clock — never a manual
  status field.
- All staff-facing UI is custom-themed (custom `/staff/login`, `/manage`, `/floor`), not
  Odoo's back office.
- **Verdict so far: don't rebuild MODRYN on Odoo.** Odoo wins on catalog/storefront/
  back-office and gives Hebrew+Arabic, RTL, the Israeli week and ILS free; it loses on every
  PRD differentiator, which is custom work either way. The one place it beat MODRYN:
  `bus.bus` gives true websocket realtime in ~50 lines, versus MODRYN's 5s polling.

**Next up (not built): stage 2** — customer login by phone + SMS OTP, and a "my bookings"
page. Design is agreed: OTP with a dev-mode fake sender that logs the code (no Twilio
needed); guests keep booking as today, with the phone always collected.

## Odoo traps that fail with NO error and NO log line

Each of these cost real time. Assume more exist.

1. `'category': 'Theme/*'` in a manifest silently voids **all** that module's assets unless
   it is the website's selected `theme_id`. Use `'Website'`.
2. SCSS compiles with **LibSass**: modern `rgb(43 33 24 / .1)` kills the **entire** frontend
   bundle. Always `rgba()`.
3. `recordset.mapped(callable)` on an **empty** recordset calls it once with the recordset
   itself, so `empty.field` is `False`. Use a comprehension.
4. **`_sql_constraints` was removed in Odoo 19** — declaring it gets you no index and no
   warning. Use `models.Constraint('unique(name)', "…")`.
5. `translate=True` stores the column as **jsonb**, so `unique(name)` compares whole JSON
   objects and duplicates get through; switching the field to non-translatable does not
   migrate the column and then every write fails. Enforce uniqueness in Python.
6. CSRF is an HMAC over `session.sid`, but the cookie is only sent when the session is
   dirty — a visitor whose *first* request is your form gets a bare 400. Call
   `request.session.touch()` when rendering it.
7. `sudo()` raises privileges but does **not** change `env.user`. On a public route this
   silently made the anonymous `public` user the owner of every booking.

Renamed in 19: `res.users.groups_id`→`group_ids`, `res.groups.category_id`→`privilege_id`.
Environment: use `python3` (a shell alias shadows the venv); `db_host` must be empty, not
`False`; QR needs `rlPyCairo`; no Docker locally; module data only re-reads on `-u`.

## How I want you to work

- **Verify before claiming.** Run it, read the row back from Postgres, look at the
  screenshot. "It should work" is not a result.
- When a grep over fetched HTML/CSS returns zero for something that ought to be there,
  **suspect the measurement first**: `$(curl …)` mangles large pages. Fetch to a file.
- Playwright MCP is rooted to the MODRYN repo and writes screenshots into it — move each one
  out immediately, then re-check that repo is clean.
- Tell me plainly when something is broken, unverified, or a guess. I would rather hear
  "two of these 27 checks fail and here's why" than a clean summary that isn't true.
