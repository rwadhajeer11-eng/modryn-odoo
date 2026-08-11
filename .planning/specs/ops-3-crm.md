# Spec 3: Customer CRM fields + budget gating

**Epic**: bridal-ops · **Plan**: `../plans/bridal-ops.md` §Phase 3 · **Status**: draft

## Problem

Brides are bare `res.partner` rows (name + phone). No wedding date, budget, party,
measurements, notes — and no place on the floor to see or edit them.

## Requirements

1. `res.partner` gains: `modryn_wedding_date` Date, `modryn_budget` Float (**gated**),
   `modryn_party_notes` Char, `modryn_measurements` Text (free text, no grid),
   `modryn_notes` Text, `modryn_category` Selection `purchased`/`not_purchased`
   (written only by the outcome flow — sold sets purchased; not_sold sets not_purchased
   unless already purchased).
2. Floor board: customer-profile modal reachable from any card (queue entry or booking)
   that has a partner/phone. Staff read/write wedding date, party, measurements, notes.
3. **Budget gating is the controller**: the profile read route includes the `budget` key
   only when `_is_manager()`; the save route rejects budget writes from non-managers.
   Under this codebase's security model (portal users, controller auth, sudo reads,
   plain dicts) omitting the key IS the field-level ACL — documented in the docstring.
4. CRM-field edits write audit rows (`res.partner.write()` diff).

## Acceptance criteria

- Staff payload has no `budget` key; manager payload does. Staff POST with budget → error.
- Category flips correctly from outcomes; purchased is never downgraded by a later
  not-sold visit.
- Partner edits appear in `/manage/audit` with correct actor.
- verify.sh §21 additions green (columns exist; staff-vs-manager payload difference
  asserted via the two seeded logins), §1–22 stay green.
