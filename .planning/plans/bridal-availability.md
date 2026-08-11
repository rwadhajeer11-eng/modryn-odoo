# Plan: bridal-availability — the booking grid becomes real

**Epic**: [`../epics/bridal-availability.md`](../epics/bridal-availability.md) — read it first
for the *why*, the settled decisions and the risks. This file is the *how*, per feature.

Anchors below were verified by reading the code on 2026-08-11. Line numbers drift; the file
and symbol names are the durable part.

---

## F0 — Palette consolidation + slug-normalisation fix · S

Spec: [`../specs/avail-0-baseline-hygiene.md`](../specs/avail-0-baseline-hygiene.md).

The nine `$modryn-*` variables move into
`modryn_theme/static/src/scss/primary_variables.scss` and the duplicate blocks leave
`floor.scss` and `roster.scss`. No new file and no `@import`: `web.assets_frontend` includes
`web._assets_helpers` includes `web._assets_primary_variables`, one SCSS unit, so bundle
membership *is* the import — and a local `@import` is not merely inert, `assetsbundle.py:618-627`
rejects it and emits the error in place of the stylesheet.

Plus one fix to the slug guard that shipped in `56cfa46`: it compared the raw URL segment
against `cls._slug(record)`, but `_slugify` lowercases, strips combining marks and collapses
duplicate dashes — so `/shop/Aurora-Gown-7` and `/shop/Café-Blanc-7` 404'd on the boutique's own
dress where core used to 301 them onto canonical. Slugifying the requested half restores that,
keeps the tenancy guard intact, and makes the comparison survive `python-slugify` ever being
installed. Guarded by a new assertion in the cross-tenant section.

---

## F1 — Record the SMS-delivery evidence · S

`STATE.md`, `BACKLOG.md` and `docs/scorecard.md` all still say SMS delivery is unproven. The
owner reports texts arriving on their handset. In this repo "proven" has meant recorded
evidence, not recollection — so record it rather than editing the claim on assertion.

Run the chain once on **`bella`** (the only tenant with live Twilio credentials; `noga` is the
log-only fallback and is where every non-delivery test belongs): book → confirmation SMS
arrives → open the `/b/<token>` link from it → cancel → the claim link reaches the next person
on that day's waitlist. Capture the `modryn_sms_outbox` rows and a screenshot. One commit
updates all three documents.

**Owner's to do, not a build task:** rotate the Twilio API key SID, secret and phone SID
(backlog #2) — they were pasted into a chat transcript on 2026-08-10. Never committed (`.env`
is gitignored), but a transcript is not a secret store. Rotate → update `.env` → re-run
`scripts/configure_twilio.py` → confirm with a send.

---

## F2 — Opening hours as data · M

The spine. New model `modryn.opening.hours` shaped on `modryn.shift.template`
(`modryn_roster/models/shift_template.py:21-51`), which already solves the Sunday-first Israeli
week (`:6,9-18`) and validates `end > start` (`:44-51`): `weekday` (str `'0'`–`'6'`, Sunday
`'6'`), `start_hour` / `end_hour` (Float), `slot_minutes`, `active`.

Owner page `/manage/hours` + a nav tab added by xpath-inheriting `modryn_staff.manage_layout`
(precedent: `modryn_roster/views/roster_templates.xml:4-9`). Controller shape: owner-guarded,
GET list / POST new / POST archive-toggles-`active` — `modryn_ops/controllers/tasks.py:67-118`.

Both generators read it through **one shared method**: `modryn_booking/controllers/main.py`
`_slots()` (`:34-96`, constants at `:11-15`) and `modryn_portal/controllers/waitlist.py`
`_free_slots_on()` (`:48-84`, constants at `:10`). Note the waitlist copy has **no weekday
filter** — fixing that is part of this feature, not a separate bug.

Promote two constants that are currently invisible policy: `DAYS_AHEAD = 14`, and the booking
lead time — `_slots()` starts at `offset=1` (`main.py:78`), so **same-day booking is impossible
today**. Probably deliberate; it is a decision hiding in a loop bound either way.

**Seed the current behaviour on install** — Sun–Thu, 10:00–18:00, 60 minutes. F2 changes what
is *configurable*, not what is *offered*. Every existing `verify.sh` booking assertion stays
green **without being edited**. That is the acceptance test.

Both submit paths already re-validate by regenerating the slot set (`main.py:242-244`,
`waitlist.py:150-152`) — keep that seam and both submissions follow the new engine for free.

---

## F3 — Blackout dates and holidays · S

Model `modryn.closure` (date or date range + reason) and a section on `/manage/hours`. Both
generators subtract closures. Israeli holidays are **entered as data, not computed** — a
boutique closes when it decides to, and a Hebrew-calendar dependency is not worth carrying.

---

## F4 — Appointment types: duration + capacity · M

The riskiest feature. Do it alone and verify the negative case hardest.

Model `modryn.appointment.type` (name, duration minutes, capacity, `active`), replacing the
flat 60 minutes hardcoded in both create paths (`main.py:254`, `waitlist.py:188`).

`modryn_booking_type` (`dress` / `consult`,
`modryn_booking/models/calendar_event.py:21-27`) **stays and keeps its meaning** — the new
model is orthogonal, and collapsing them would ripple through the portal, the floor board and
`modryn_ops` reporting for no gain.

Capacity is the schema-touching part. Add `modryn_slot_seat` (Integer, default 0); change the
partial unique index at `modryn_portal/models/calendar_event.py:54-58` from `(start)` to
`(start, modryn_slot_seat)` and mirror it in `modryn_portal/schema_guard.py:39`. Take the
lowest free seat inside the savepoint / `UniqueViolation` retry both create paths already have
(`main.py:287-299`, `waitlist.py:196-204`) — contention resolves in Postgres, not Python.
Existing rows migrate to seat 0.

`schema_guard.py` **and** a migration are both needed; the install-versus-upgrade trap is
already documented in this repo and this is exactly the change that trips it. Odoo 19 removed
`_sql_constraints` — see `.memory/odoo-traps.md` trap 4.

**Acceptance:** two concurrent bookings on a capacity-2 slot both succeed on seats 0 and 1; a
third is rejected **by the database**; a capacity-1 slot behaves exactly as today. An index
that no longer enforces still lets every happy-path test pass — test the rejection.

---

## F5 — The roster means something on the floor · M

Backlog #5.

`modryn.shift.slot._modryn_rostered_on(day)` — new `@api.model` beside `modryn_ensure_week` in
`modryn_roster/models/shift_slot.py`, querying `day = D AND published = True`, returning the
**union** of `employee_ids` across the day's slots (a day can hold a morning *and* a late
shift), or `None` when no published slot exists. Use the Asia/Jerusalem `today()` helper at
`shift_slot.py:11-18`, never `fields.Date.today()`.

`ModrynFloorRoster(ModrynFloor)` in `modryn_roster` overriding `_board()` to flag each staff
row `rostered: bool`, and `assign()` to append a `warning` when the assignee is not rostered.
Same seam as `modryn_ops/controllers/floor_ops.py:15-21`.

**Flag, do not filter.** `_board()['staff']` feeds five surfaces — the bench, two `<select>`
fallbacks (`floor_board.xml:141`, `:225`), `freeCount` (`floor_board.js:530`) and the
alteration-assignee list (`floor_board.js:534-536`). The off-roster cover person must stay
assignable; that is the whole reason the rule warns instead of blocking. The bench header
already reads "Team on shift" (`floor_board.xml:346`) and currently lies — flagging makes it
true.

Client: `state.warning` in `apply()` and a banner beside the existing error one
(`floor_board.xml:30-32`). `apply()` already handles board-plus-message payloads deliberately
(`floor_board.js:137-146`); precedent `floor.py:304-316`. Note `state.error` is cleared on
every `apply()` and the bus refreshes often (`floor_board.js:57-58`), so a warning modelled the
same way is transient — probably right, but decide it explicitly.

Two client-side lines in `modryn_staff` are the only unavoidable edits there (assets are
globbed per addon). Xpath-inheriting the OWL template from `modryn_roster` is the
smaller-blast-radius alternative and more machinery; take the two lines. Hebrew strings go in
`addons/modryn_roster/i18n/he.po` — the staff terminal defaults to `he_IL` RTL.

---

## F6 — The roster feeds availability · M

Where the halves meet, and the reason F5 sits in this epic.

Slot capacity becomes `min(appointment-type capacity, rostered stylist count)` for the day,
intersected with opening hours minus closures. A day with nobody rostered offers nothing —
**unless no rota is published at all**, in which case it degrades to F2/F4 behaviour. Without
that fallback, installing this empties the booking grid of every boutique that has not adopted
the rota.

`modryn_booking` cannot import `modryn_roster` (wrong direction). Either the engine takes an
optional capacity provider that `modryn_roster` registers, or `modryn_roster` overrides the
engine method. **Prefer the override** — same subclass-and-`super()` trick as everywhere else,
no new machinery.

---

## Verification, every feature

1. `MODRYN_DEMO_PASSWORD=modryn2026 bash scripts/verify.sh` — the **full** suite, not just the
   new section. Below baseline is a stop.
2. New `verify.sh` checks in the existing section style for anything that changes observable
   behaviour.
3. A real browser where the surface is visual — F5's warning banner, F7's confirmation link.
4. Both tenants. Anything that could send a text runs on `noga` unless the test is F1.

**End of epic:** `docs/walkthrough.md` gains acts for the new surfaces, `docs/scorecard.md`
question 2 is re-scored against what now exists, and `STATE.md` / `BACKLOG.md` are updated.
