# Spec 2: Opening hours as data

**Epic**: bridal-availability · **Plan**: `../plans/bridal-availability.md` §F2 · **Status**: done
**Branch**: `feature/tenancy-slug-and-ics` · **Suite**: 301 passed, 0 failed, 2 skipped

## Problem

The booking grid was a fixed Sunday–Thursday, 10:00–18:00 lattice hardcoded in
`modryn_booking/controllers/main.py` and **again** in
`modryn_portal/controllers/waitlist.py` — and the second copy had lost the weekday half
entirely, so a waitlist claim link would happily offer a Friday the boutique does not sell.
No two boutiques keep the same hours, and none of them should need a developer to change them.

## What shipped

1. **`modryn.opening.hours`** in `modryn_booking` — `weekday` (Python `weekday()` values as
   strings, Sunday `'6'`, the same encoding `modryn.shift.template` uses), `start_hour` /
   `end_hour` as Floats, `active`. Uniqueness on `(weekday, start_hour)`, not weekday alone:
   **several windows per weekday are allowed**, because a shop that shuts for the afternoon and
   reopens at 16:00 is an ordinary Israeli retail week, not an edge case.
2. **One interface, two callers.** `modryn_hours_on(day)` returns the slot start hours for a
   date, `[]` when shut — and that empty list *is* the weekday filter, so a caller cannot offer
   a Friday by forgetting to check. `modryn_hours_by_weekday()` returns the whole week in one
   read. `modryn_hour_label()` formats a float as a wall clock.
3. **Both generators rewired.** `_slots()` and `_free_slots_on()` now read the table. The
   waitlist's missing weekday filter is fixed as a side effect rather than as a separate bug.
4. **`/manage/hours`**, owner-guarded, in `modryn_staff` — the model cannot live there
   (`modryn_staff` depends on `modryn_booking`, not the reverse), so the ACL splits: the model's
   own module grants `base.group_user` read, and `modryn_staff` grants the owner row.
5. **Seeded on both lifecycle paths** — `post_init_hook` for a genuine `-i`, and
   `migrations/19.0.1.1.0/post-migrate.py` for `-u`, both calling one `seed()` so they cannot
   drift. Seeding is idempotent and reads with `active_test=False`, so an owner who archived
   every window does not have the shop reopened behind her by the next upgrade.

## Decisions worth keeping

- **No `slot_minutes` field.** Per-appointment-type duration and capacity are F4. A per-boutique
  slot length *and* a per-type duration is two competing sources of truth for one number — the
  exact bug this model exists to remove. `SLOT_MINUTES` lives in one module and both controllers
  import it.
- **A fitting must finish by closing time**, so a window that shuts at 18:00 never offers 18:00.
- **Lead time is named, not configurable.** `_slots()` looping from offset 1 was the entire
  meaning of "no same-day booking"; it is now `LEAD_DAYS = 1` with a comment. A boutique wanting
  someone in today already has the walk-in queue.
- **`modryn_template` must be in every `-u` run.** `new_boutique.sh` provisions with
  `createdb -T`, a Postgres-level clone that runs neither hook nor migration, so a boutique
  inherits the template's rows. Leaving the template out would give every future boutique an
  empty grid — and an empty grid is a boutique that takes no bookings at all.

## Acceptance

- Seeded Sun–Thu 10:00–18:00, no Friday or Saturday row, **identical in `bella`, `noga` and
  `modryn_template`** — asserted per database in `verify.sh` §24.
- Manifest version matches the migrations directory.
- `/book` offers a 10:00 slot and renders exactly the days the table opens — both derived from
  the table, so the checks follow an owner's edits.
- **Every pre-existing booking assertion stayed green without being edited.** That was the real
  acceptance test for the seeding, and it held.

## Defects found by review and fixed before commit

Fifteen confirmed; the ones that mattered:

- **Blocker.** `weekday_selection()` took no arguments, but Odoo hands a callable Selection the
  recordset (`determine()` in `odoo/orm/fields.py`), so `fields_get('weekday')` raised TypeError
  and `/manage/hours` 500'd on every load. The write path never notices — `convert_to_cache`
  short-circuits while `_selection` is None — which is why the identical helper in
  `modryn_roster` has survived without a parameter. Fixed here; **`modryn_roster` still carries
  the same latent trap** and is fine only because nothing calls `fields_get` on it.
- **Major.** A window closing at midnight is legal (`end_hour == 24`), which made the claim
  page's scan bound reach hour 24 — `datetime.replace(hour=24)` raises, 500ing every `/claim`
  that weekday. The upper edge is now an instant (last start + one slot), which is also more
  DST-correct.
- **Major.** `loadtest/seed/seed_tenant.py` still imported the deleted constants — an
  `ImportError` that would have killed every load-test tenant seed.
- `SLOT_MINUTES` had been declared in three files; `/book` fired one query per rendered day
  (14 per render, 42 on a failed submit) against a five-row table; and `schema_guard`'s docstring
  was wrong about how boutiques are provisioned.

## Notes

`verify.sh` §10b-bis nested three `.ics` assertions inside the future-booking branch, so when
bella's last future booking aged into the past they silently stopped running behind one skip.
The guards now gate only the checks that need that fixture. Two skips remain, both fixture age
rather than gaps in the code, and both self-heal the moment anyone books ahead.
