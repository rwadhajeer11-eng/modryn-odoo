# Spec 1: Appointment outcomes + audit core

**Epic**: bridal-ops · **Plan**: `../plans/bridal-ops.md` §Phase 1 · **Status**: draft

## Problem

A booking never ends. `calendar.event` has cancel timestamps but no terminal state, so
nothing downstream — follow-up automation, conversion KPIs, no-show recovery — can exist.

## Requirements

1. New addon `modryn_ops` (19.0.1.0.0, category Website, depends `modryn_staff`, `modryn_portal`).
2. `calendar.event` gains: `modryn_outcome` Selection(`sold`/`not_sold`/`no_show`, indexed),
   `modryn_outcome_at`, `modryn_outcome_by_id` (closing stylist — original stays
   `modryn_employee_id`), `modryn_sale_amount`, `modryn_sale_items` (free text),
   `modryn_outcome_note`, `modryn_feedback_sent_at`.
3. `modryn_set_outcome(...)`: refuses cancelled bookings; refuses overwrite unless `force`;
   hardcoded flows — **sold** → thank-you SMS; **not_sold** → feedback SMS always sent +
   `modryn_feedback_sent_at` stamped; **no_show** → rebook SMS with `/book` link + bus
   notify managers. (Task creation lands in Feature 2; flows are written to call a hook
   that is a no-op until then.)
4. SMS bodies via the lang-aware `_modryn_sms_env`/`_modryn_body` pattern; queued with
   `send_async` — a failed text never loses an outcome.
5. `/floor/finish/booking` (jsonrpc): manager **or** the booking's primary stylist; `force`
   only honoured for managers. On sold, response carries the existing `finished` payload
   shape so the alteration modal chains unchanged.
6. Floor board: Done button on booking cards → outcome modal (sold: amount + items;
   not-sold: note + "a feedback SMS will be sent"; no-show); managers see an
   `unclosed_count` nag badge (past, non-cancelled, outcome-NULL bookings).
7. `modryn.audit.log` (actor user + denormalised name, model, res_id, label, field,
   old, new): rows from a `calendar.event.write()` diff on outcome fields +
   `modryn_employee_id`, capturing the REAL actor (`env.user` — sudo does not change
   identity). `/manage/audit` page, owner-only, paginated, nav tab.

## Acceptance criteria

- Stylist can close her own booking; staff cannot close someone else's; manager can close
  any; second close without force → error; force from staff → error.
- Cancelled booking cannot receive an outcome.
- Each outcome kind queues exactly the right SMS (visible in `modryn_sms_outbox`).
- Audit rows appear for outcome set/overwrite and stylist reassignment, with correct actor.
- verify.sh §21 (columns exist everywhere incl. template; planted unclosed row detected;
  route walled from public) + §23 audit half — green, and §1–20 stay green.

## Out of scope

Follow-up task creation (Feature 2), CRM fields (3), KPIs (4).
