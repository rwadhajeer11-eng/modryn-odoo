# Spec 0: Regression baseline

**Epic**: bridal-ops · **Status**: ✅ done 2026-08-11

## Requirement

Before any `modryn_ops` code exists, the existing seven addons must be provably green so
later phases have a regression floor.

## What was done

- `./scripts/verify.sh` initial run: 209 passed, **4 failed**.
- Fixes applied:
  - `bella` calendar_event #3 (demo fitting, off-grid hour) — cancelled via SQL
    (`modryn_cancelled_at`/`modryn_cancelled_by='boutique'`), not deleted, per the
    archive-is-cancel convention. Direct SQL, not `modryn_cancel()`, deliberately: the ORM
    path offers the freed day to the waitlist and bella carries live Twilio credentials.
  - `noga` calendar_event #3 (reminder test, off-grid minute) — same treatment; noga had 2
    waitlist rows for that day which must not be texted by a data cleanup.
  - `noga` calendar_event #8 — organizer reassigned from `public` to `__system__`
    (the sudo-keeps-identity trap, `.memory/odoo-traps.md` §7).
  - `MODRYN_DEMO_PASSWORD=modryn2026` exported so §10a authenticated surfaces run.

## Result

**217 passed, 0 failed, 0 skipped.** Recorded in `.planning/STATE.md`. Manual walkthrough
replay skipped deliberately: §10a–10k already exercise every surface the epic touches.
