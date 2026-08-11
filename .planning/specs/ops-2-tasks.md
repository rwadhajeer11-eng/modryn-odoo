# Spec 2: Tasks, checklists, escalation

**Epic**: bridal-ops · **Plan**: `../plans/bridal-ops.md` §Phase 2 · **Status**: draft

## Problem

The only task model is alteration-specific. No opening/closing checklists, no follow-up
tasks, no overdue escalation — so outcomes can't drive work.

## Requirements

1. `modryn.task`: name, `task_type` (`opening`/`closing`/`follow_up`/`no_show_recovery`/`adhoc`),
   nullable `employee_id` (opening tasks belong to whoever opens), `due_at` (indexed),
   `state` open/done (indexed), `done_at`, `done_by_id`, `partner_id` set-null +
   `customer_name`/`customer_phone` denorm, `event_id` (origin booking), `template_id`,
   `day`, `note`, `escalated_at`, computed non-stored `is_overdue`. `_row()`,
   `action_done(employee)` / `action_reopen()` idempotent + audit-logged.
2. `modryn.task.template`: name, `kind` opening/closing, `due_hour` float, sequence,
   active. Plain Char name (translate=True is jsonb-contagious — traps §5).
3. Lazy generation: `modryn_ensure_today()` called from the floor `_board()` — no spawner
   cron; a day nobody opens the board is a closed day. Race-safe: partial unique index
   `modryn_task_one_instance_per_day` on `(template_id, day) WHERE template_id IS NOT NULL`
   + savepoint insert. Index asserted by `modryn_ops/schema_guard.py` (pre/post_init_hooks
   AND migration pair — the install-vs-upgrade trap).
4. Outcome flows (Feature 1's hook) now create tasks: not_sold → `follow_up` due +7d for
   primary stylist (fallback closer); no_show → `no_show_recovery` due +1d.
5. Escalation cron every 10 min: open tasks overdue >30 min and unescalated → bus event +
   one SMS to each manager/owner with a work phone, stamp `escalated_at`. Existing SOS
   pattern. verify must assert exists+active only (short-interval crons are permanently
   overdue — traps §11).
6. Floor board: "My tasks" + today's checklist panel with checkbox completion; overdue
   rows flagged. `/tasks/done`, `/tasks/reopen` (staff: own/unassigned; manager: any).
7. `/manage/checklists` owner CRUD (new/archive, never delete), nav tab, ships empty.

## Acceptance criteria

- Opening the floor board on a fresh day spawns exactly one instance per active template,
  even under concurrent first loads.
- not_sold/no_show outcomes create their tasks; overwriting an outcome unlinks the still-
  open auto-tasks it created.
- Overdue task escalates once: bus fires, manager SMS queued, `escalated_at` set.
- Task done/reopen writes audit rows.
- verify.sh §22 green (index everywhere incl. template; planted overdue row detected;
  `/manage/checklists` walled; cron present+active), §1–21 stay green.
