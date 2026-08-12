# Epic: bridal-availability — the booking grid becomes real

**Created**: 2026-08-11 · **Status**: done 2026-08-12 (verify 326/0/2) — except F1, which is blocked on the owner
**Master plan**: [`../plans/bridal-availability.md`](../plans/bridal-availability.md)
**Baseline**: `verify.sh` 263/0/0 at commit `7006ad6`

The scorecard's verdict is settled — do not rebuild MODRYN on Odoo — and this epic does not
reopen it. It pursues the *other* conclusion the scorecard reached: that the defensible use of
Odoo is **as a per-boutique back office a boutique would really run**. That is the owner's
stated purpose for this batch, and it is what justifies spending on an availability engine
that a pure evaluation would have skipped.

The gap being closed is the one scorecard question 2 scored 🟡 and called "the expensive
80%": booking has no availability engine. The offered slots are a hardcoded Sunday–Thursday,
10:00–18:00, one-hour lattice with capacity hard-wired to 1 — constants in
`addons/modryn_booking/controllers/main.py:11-15`, duplicated in
`addons/modryn_portal/controllers/waitlist.py:10`. And a published rota
(`modryn.shift.slot.published`) is read by nothing outside `modryn_roster`, which is the
difference between a rota and a spreadsheet.

## Features, in order

**Already shipped on this branch before the epic was written** — `feature/tenancy-slug-and-ics`
(commits `56cfa46`, `de547b7`, `f8c377b`) closed the cross-tenant slug fallback *and* the `.ics`
export, which were backlog items 3 and 6. This epic does not rebuild them. A first draft of it
listed both as work to do; that is what the branch reconciliation on 2026-08-11 corrected.

| # | Feature | Spec | Size | Status | Depends on |
|---|---------|------|------|--------|------------|
| 0 | Palette consolidation + slug-normalisation fix | [`../specs/avail-0-baseline-hygiene.md`](../specs/avail-0-baseline-hygiene.md) | S | ✅ done — `6b7687d` | — |
| 1 | Record the SMS-delivery evidence | — | S | ⛔ blocked on the owner — needs a real handset | — |
| 2 | Opening hours as data (`/manage/hours`) | [`../specs/avail-2-opening-hours.md`](../specs/avail-2-opening-hours.md) | M | ✅ done | 0 |
| 3 | Blackout dates and holidays | — | S | ✅ done | 2 |
| 4 | Per-window capacity (duration deferred) | — | M | ✅ done | 2 |
| 5 | The roster means something on the floor | — | M | ✅ done | 0 |
| 6 | The roster feeds availability | [`../specs/avail-6-roster-feeds-availability.md`](../specs/avail-6-roster-feeds-availability.md) | M | ✅ done | 2, 4, 5 |

Sizes: **S** ≤ half a day · **M** ≤ 2 days · **L** ≤ a week.

Features 1 and 5 are independent of the availability spine and can fill any gap. **Every
feature ends with the FULL `verify.sh` suite green** — an older section regressing is a stop,
not a note. That rule came from `bridal-ops` and it held.

## Settled decisions (do not re-litigate)

Each was argued from the existing code during exploration on 2026-08-11, not invented.

- **Config is a model plus a `/manage/*` page, never `res.config.settings`.** There is no
  `res.company`, no `company_id` and no settings form anywhere in `addons/` — under
  DB-per-tenant, "per-tenant config" is just "config". Three worked instances of the real
  pattern already exist: `modryn.shift.template` + `/manage/shifts`, `modryn.task.template` +
  `/manage/checklists`, and rooms/roles in `modryn_staff/controllers/manage.py`.

- **One slot generator, called by both booking paths.** `main.py:_slots()` and
  `waitlist.py:_free_slots_on()` are near-duplicates and the waitlist copy has **no weekday
  filter at all** — a latent bug. The engine lands on a model in `modryn_booking`;
  `modryn_portal` already depends on it, so it can call in that direction. The `create()`
  duplication documented at `waitlist.py:160-166` is a genuine load-cycle problem and stays.

- **Capacity > 1 means changing the unique index, and that index is the only real guarantee.**
  `modryn_portal/models/calendar_event.py:54-58` declares a partial unique index on `(start)`
  alone, mirrored in `schema_guard.py:39`. Chosen: add `modryn_slot_seat` and index
  `(start, modryn_slot_seat)`, taking the lowest free seat inside the savepoint /
  `UniqueViolation` retry both create paths already have. Contention keeps resolving in the
  database, not in Python. Rejected: counting in Python (loses the guarantee), a counter row
  (a second thing to keep correct).

- **An empty roster means "no rota published", not "nobody works".** Slots are generated
  lazily on read by `modryn_ensure_week()`; a boutique that never opened `/roster` this week
  has zero rows for today. Floor board and availability engine both degrade to
  "everyone / unconstrained". **The floor board must never call `modryn_ensure_week` — it
  writes rows.**

- **Off-roster assignment warns, never blocks.** A manager pulling in someone covering a sick
  colleague is legitimate. That rules out `@api.constrains`, which can only raise. The hook is
  a controller override returning `{**board, warning: ...}`; the client already handles
  board-plus-message payloads deliberately (`floor_board.js:137-146`), precedent at
  `floor.py:304-316`.

- **`modryn_roster` extends `modryn_staff`, never the reverse.** Dependency runs
  roster → staff and there is currently zero functional coupling. The floor board's
  controller-inheritance seam is already used twice (`modryn_ops/controllers/floor_ops.py:15-21`,
  `modryn_atelier/controllers/atelier.py:13-19`).

- **The slug fix is an `ir.http._pre_dispatch` override, not a controller override.** Two
  backlog corrections here: the symptom is a **301 to the local canonical URL**, not a silent
  resolve; and a controller `not_found()` would fix `/shop` only, leaving every other
  `<model()>` route falling back. Accepted trade-off: a renamed record's old URL will 404
  instead of 301-ing, because the two cases are indistinguishable from the URL alone.

- **Bundle membership is the SCSS import mechanism.** `web.assets_frontend` includes
  `web._assets_helpers` includes `web._assets_primary_variables`, all one SCSS unit — so the
  backlog's "extract `_tokens.scss` and `@import` it" is the wrong mechanism. Odoo does not
  resolve `@import` across addon paths. The palette moves into `primary_variables.scss` and
  the two duplicate blocks are deleted. That slot is also pulled into `web.assets_backend`, so
  the file may contain **only** variable declarations.

## Risks

- **F4's index change is the only schema change, and the only one that can silently stop
  enforcing double-booking.** Verify the negative case — the over-capacity booking is
  *rejected* — not just the happy path.
- **F6 can empty every boutique's booking grid** if the no-rota fallback is wrong. Test a
  tenant that never opened `/roster`; `noga` is the natural subject.
- **Two generators, one engine.** If F2 replaces `_slots()` but leaves `_free_slots_on()`, the
  waitlist claim page offers hours the booking page refuses. They land together or not at all.
- **Serial execution is forced.** One Odoo server holds port 8069 and the shared databases, so
  `verify.sh`, `-u` and `odoo-bin shell` cannot be parallelised.
- **Restart tiers.** New controller routes and new Python files need a **full server restart** —
  registry signalling does not re-import Python. Getting this wrong looks exactly like
  "my change did nothing".

## Read first

[`../../.memory/odoo-traps.md`](../../.memory/odoo-traps.md) — twelve failure modes with no
error and no log line, several of which this epic walks into: `_sql_constraints` removal in
Odoo 19 (F4), LibSass dying on modern colour syntax (F0), `t-key` being OWL-only (F5).
Then [`../../.memory/decisions.md`](../../.memory/decisions.md).
