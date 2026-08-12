# Spec 6: The roster feeds availability

**Epic**: bridal-availability · **Plan**: `../plans/bridal-availability.md` §F6 · **Status**: done
**Suite**: 326 passed, 0 failed, 2 skipped

## Problem

A boutique could sell more concurrent fittings than it had stylists to run them. F4 made capacity
a property of the opening-hours window; nothing connected it to who is actually working.

## What shipped

`modryn.opening.hours.modryn_daily_caps(days)` → `{date: int}`, base returning `{}`.
`modryn_roster` overrides it by model inheritance — the same seam `floor_roster.py` uses for the
controller — so `modryn_booking` never learns a rota exists. Both grids apply
`min(window capacity, cap)`, and that **effective** number is what each offered slot carries, so
both seat-retry loops are bounded by what the boutique can staff rather than by the room.

Who counts: rostered employees at `modryn_level` in `manager`/`staff` — the same definition the
floor board's own team list uses, so the two features cannot disagree about who is working.

## The decision this feature turns on

**A date absent from the mapping is uncapped. A cap of 0 is never emitted.** Three different
inputs collapse to that one silence:

- no published slot on the date;
- published slots naming nobody — publishing is week-wide, so a manager who fills Sunday leaves
  Monday to Thursday published and empty;
- a date rostered only by people who do not work the floor.

Emitting 0 for any of them would empty every hour of that day on a page nobody would think to
check. The code writes `caps[day]` only inside `if working:`, so 0 is unreachable by construction
rather than by care.

## Verified against live data, not in principle

Called on `bella` over the rendered fortnight through `odoo-bin shell`:

```
modryn_daily_caps(2026-08-13 .. 08-26) -> {2026-08-16: 2}
any cap of 0? -> False
uncapped dates: 13 of 14
2026-08-16: rota cap=2  window=1  effective=1
```

bella's four published-but-unstaffed days (08-17…08-20) emit no key and remain bookable —
confirmed by `<optgroup>` markers on `/book`. `noga` has never opened `/roster`, so its mapping is
`{}` and not a single hour moved. **Neither tenant lost a day or an hour**, which was the whole
risk: get the fallback wrong and every boutique's grid empties.

## Query cost

`/book` renders a fortnight and F2/F3 both worked to keep it at a fixed number of queries. The cap
is fetched **once for all rendered dates** (+2 searches with `modryn_roster` installed, +0 without,
and the `hr.employee` read is skipped entirely when every published slot is empty — bella's case).
No read path calls `modryn_ensure_week()`, which writes.

## Defects found by review and fixed before commit

The production code survived all three lenses. Both real defects were in the **verification**:

- The "positive control that the cap can bite" was a **tautology** — it planted rows with SQL and
  asserted them with SQL, never touching the override, and passed with the feature deleted. It has
  been replaced with structural assertions that fail when the wiring goes (the override is
  imported, inherits the right model, defines the method, calls `super()`, and both grids ask for
  it) plus an explicit note that behaviour is proved by hand, recorded above. A check that cannot
  fail is worse than no check.
- The never-zero guard tested day **presence** with a bare date grep — but a day capped to 0 is
  still *listed*, inside the waitlist `<select>`. It was structurally blind to the exact failure it
  was named after. Both day-presence loops now require the `<optgroup label="…">` that only a
  bookable day emits.

## Out of scope

Per-appointment-type **duration**, still. Interval overlap cannot be expressed by a unique index;
it needs a `tstzrange` EXCLUDE constraint and `btree_gist`, plus a non-uniform grid.

A rota that genuinely says "nobody works today" cannot be expressed — it reads as silence. That is
inherited from F5's fallback and is the accepted cost of not emptying a grid by accident.
