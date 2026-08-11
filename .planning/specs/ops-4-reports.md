# Spec 4: KPIs / reports

**Epic**: bridal-ops · **Plan**: `../plans/bridal-ops.md` §Phase 4 · **Status**: draft

## Problem

Nothing is measured. With outcomes (1) and tasks (2) recorded, conversion, ATV, no-show
and completion rates become computable — live SQL at boutique volumes, no stored
aggregates.

## Requirements

1. `/manage/reports?from=&to=` (manager and owner; default range = current month,
   Asia/Jerusalem):
   - Appointment conversion = sold ÷ (sold + not_sold)
   - Contact conversion = distinct customer phones with a sold ÷ distinct phones with any
     completed outcome (phone is the contact key)
   - ATV = Σ sale_amount ÷ sold count
   - No-show rate; cancellation rate (from `modryn_cancelled_at`)
   - Follow-up completion (follow_up + no_show_recovery tasks done ÷ due in range)
   - Checklist completion (template instances done ÷ due in range)
   - Unclosed past bookings count
   - Per-stylist table (attribution = original stylist `modryn_employee_id`; closer is
     stored for a future switch): appointments, sold, conversion, revenue, ATV.
2. `/floor/my/stats` (jsonrpc): the signed-in stylist's own numbers only — same formulas
   scoped to her. Rendered as a small panel on the floor board. **No peer visibility, no
   leaderboard** (settled decision).
3. Feedback-SMS visibility for managers: not-sold rows in the per-range detail show
   `modryn_feedback_sent_at`.
4. Nav tab "Reports" on the manage shell (manager+ sees it; audit stays owner-only).

## Acceptance criteria

- Formulas verified against planted rows (verify.sh §23: plant a sold outcome inside a
  rolled-back transaction, conversion numerator must count it — `detects()` pattern).
- Stylist A cannot see stylist B's numbers anywhere; `/manage/reports` walled from staff.
- Empty ranges render zeros, not 500s (the empty-recordset `mapped` trap — use
  comprehensions/read_group).
- verify.sh §23 green, §1–22 stay green.
