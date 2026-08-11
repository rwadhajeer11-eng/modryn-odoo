# Epic: bridal-ops — outcomes, tasks, CRM, KPIs

**Created**: 2026-08-11 · **Status**: done 2026-08-11 (verify 263/0/0)
**Master plan**: [`../plans/bridal-ops.md`](../plans/bridal-ops.md)

Close the gap between what the seven addons do and what a staff-management platform needs
(per the BridalLive/Deputy research): a booking must *end* with an outcome, outcomes must
drive follow-up work automatically, and the owner must be able to read conversion off real
data. Clock-in/kiosk/punctuality was considered and excluded by the owner's decision.

## Features, in order

| # | Feature | Spec | Status | Depends on |
|---|---------|------|--------|------------|
| 0 | Regression baseline on the existing seven addons | [`../specs/ops-0-regression-baseline.md`](../specs/ops-0-regression-baseline.md) | ✅ done — 217/0/0 | — |
| 1 | Appointment outcomes + audit core (`modryn_ops`) | [`../specs/ops-1-outcomes.md`](../specs/ops-1-outcomes.md) | ✅ done — reviewed | 0 |
| 2 | Tasks, opening/closing checklists, escalation | [`../specs/ops-2-tasks.md`](../specs/ops-2-tasks.md) | ✅ done — reviewed | 1 |
| 3 | Customer CRM fields + budget gating | [`../specs/ops-3-crm.md`](../specs/ops-3-crm.md) | ✅ done — reviewed | 1 |
| 4 | KPIs / reports | [`../specs/ops-4-reports.md`](../specs/ops-4-reports.md) | ✅ done — reviewed | 1, 2 (3 for budget views) |

Features 2 and 3 are independent of each other. Every feature ends with the FULL
`verify.sh` suite green — old sections regressing is a stop, not a note.

## Settled decisions (do not re-litigate — argued with the owner 2026-08-11)

- Build in this Odoo repo; one new addon `modryn_ops` + in-place floor-board edits.
- Sales are a **manual outcome** on the appointment (sold + amount + free-text items).
- Outcome recorded by a **manager or the booking's own stylist**; changing a set outcome is manager-only.
- Not-sold **always** auto-sends the feedback SMS; the send is stamped so managers can see it.
- No-shows are **manual** + an unclosed-bookings nag badge. No auto-sweep cron.
- Checklists ship **empty**; the owner defines them at `/manage/checklists`.
- SMS-only comms. No POS, PSP, payroll, WhatsApp, email in this epic.
- Per-stylist stats are private to the stylist + managers/owner. No leaderboards.
