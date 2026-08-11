# Plan: Bridal Staff-Platform Missing Features (modryn_ops)

**Repo:** /Users/mrwen/Documents/Github/modryn-odoo (Odoo 19 Community, DB-per-boutique, Hebrew-first)
**After approval:** save this as the master plan at `.planning/plans/bridal-ops.md`, create the epic at `.planning/epics/bridal-ops.md`, then produce per-feature specs/plans as listed in "Epic & spec breakdown" below.

## Context

A research report compared the platform against BridalLive (appointments/outcomes/Smart Flows) and Deputy/7shifts (shifts/tasks/RBAC/notifications). Exploration confirmed the repo already has booking, walk-in queue, floor dispatch, SOS escalation, alterations, roster, SMS outbox, OTP portal, and waitlist — but is missing the research's automation spine: **a booking never ends** (no outcome state), so outcome→task automation, CRM fields, KPIs, and audit are all structurally impossible today.

**User decisions (final):**
- Build in **this Odoo repo**, extending existing addons. Clock-in/kiosk/punctuality is **out of scope**.
- Scope = 3 clusters: (A) outcomes + follow-up automation + CRM fields, (B) tasks + opening/closing checklists + escalation, (C) KPIs + field-gated RBAC + audit log.
- Sales = **manual outcome on the appointment** (sold + amount + free-text items). No POS/PSP. SMS-only (Twilio, already built).
- Outcome may be recorded by **a manager OR the booking's own stylist**; changing a set outcome is manager-only.
- Not-sold → **always auto-send** feedback SMS; stamp the send on the booking so managers can see it went out.
- No-shows are **manual** via the outcome modal + an "unclosed past bookings" nag badge on the manager board. No auto-sweep cron.
- Checklists **ship empty** — owner creates them at `/manage/checklists`.

**Codebase constraints to respect** (all verified): staff/managers are portal users → no backend views, no `mail.activity`; all auth is controller-side (`_is_staff/_is_manager/_require_owner`) then `sudo()` returning plain dicts; fields prefixed `modryn_`; SMS via `modryn.sms.outbox.send_async()`; realtime via `bus.bus` channel `modryn_queue`; lazy generation pattern (`modryn_ensure_week` in roster); schema_guard hooks + migrations both wired (install vs upgrade trap); `verify.sh` gates deploys.

## Architecture: one new addon `modryn_ops`

Depends: `modryn_staff`, `modryn_portal` (not `modryn_atelier`). Plus in-place edits to `modryn_staff/static/src/floor/` (precedent: atelier already extends the floor board and controller). Version bumps: `modryn_ops` starts 19.0.1.0.0; `modryn_staff` bumps per phase. Golden template rebuild (`scripts/build_template.sh`) must add `modryn_ops` to the install list.

## Data model (`addons/modryn_ops/models/`)

**`calendar_event.py`** — `_inherit calendar.event`:
- `modryn_outcome` Selection `sold / not_sold / no_show` (index). Cancelled bookings (existing `modryn_cancelled_at`) can never get an outcome.
- `modryn_outcome_at` Datetime, `modryn_outcome_by_id` M2O `hr.employee` (closing stylist; original stylist = existing `modryn_employee_id` → both stored for configurable attribution later).
- `modryn_sale_amount` Float (ILS), `modryn_sale_items` Text, `modryn_outcome_note` Char, `modryn_feedback_sent_at` Datetime.
- `modryn_set_outcome(outcome, employee, amount=0, items=None, note=None, force=False)` — idempotent; overwrite requires `force` (manager-only at controller); on overwrite, unlink the event's still-open auto-generated tasks. Flows (hardcoded Python, constants at module top — no config engine):
  - **sold** → thank-you SMS (`send_async`), partner `modryn_category = purchased`.
  - **not_sold** → create `modryn.task` type `follow_up` due `now + FOLLOW_UP_DAYS (7)` assigned to primary stylist (fallback: closer); **always** send feedback SMS + stamp `modryn_feedback_sent_at`; partner category → `not_purchased` unless already `purchased`.
  - **no_show** → create task type `no_show_recovery` due `+1d`; rebook SMS with `/book` link; bus notify managers (new `kind` on `modryn_queue`).
- SMS bodies follow the lang-aware `_modryn_body` pattern in `modryn_portal/models/booking_comms.py`; new kinds: `thank_you`, `feedback`, `rebook` (he/ar/en).

**`task.py`** — two models:
- `modryn.task`: `name`, `task_type` Selection `opening/closing/follow_up/no_show_recovery/adhoc`, `employee_id` M2O nullable (opening tasks = "whoever opens"), `due_at` Datetime (index), `state` `open/done` (index), `done_at`, `done_by_id`, `partner_id` (set-null) + `customer_name`/`customer_phone` denorm, `event_id` M2O calendar.event (origin — feeds follow-up KPI), `template_id`, `day` Date, `note`, `escalated_at`, computed non-stored `is_overdue`. `_row()` dict method, `action_done(employee)` / `action_reopen()` idempotent + audit-logged. Model shape cloned from `modryn_atelier/models/alteration_task.py`.
- `modryn.task.template`: `name`, `kind` `opening/closing`, `due_hour` Float, `sequence`, `active`. `modryn_ensure_today()` lazily spawns today's instances on floor-board read (roster's `modryn_ensure_week` pattern) — no spawner cron; a day nobody opens the board = closed day, no orphans. Race-safe via partial unique index + savepoint insert.
- **Index (schema_guarded):** `modryn_task_one_instance_per_day` UNIQUE `(template_id, day) WHERE template_id IS NOT NULL`.
- Escalation cron `_modryn_escalate_overdue()` every 10 min: open tasks overdue > `TASK_ESCALATE_AFTER_MINUTES (30)`, `escalated_at IS NULL` → bus toast (assignee + manager boards) + one SMS via outbox to manager/owner `work_phone`, stamp `escalated_at`. Mirrors `modryn_staff/models/sos_call.py::_modryn_escalate_unanswered`.

**`res_partner.py`** — `_inherit res.partner`: `modryn_wedding_date` Date, `modryn_budget` Float (**manager-gated**), `modryn_party_notes` Char, `modryn_measurements` Text (free text), `modryn_notes` Text, `modryn_category` Selection `purchased/not_purchased` (written only by outcome flow). `write()` override diffs these fields → audit rows.

**`audit_log.py`** — `modryn.audit.log`: `user_id`, `actor_name` denorm, `model`, `res_id`, `label` (human line, e.g. "Outcome: not_sold → sold"), `field`, `old`, `new`; `_order='create_date desc,id desc'`; no GC. Helper `modryn_audit(...)`. Sources: calendar.event write-diff on outcome fields + `modryn_employee_id` reassignment, partner CRM-field diff, task done/reopen.

**`schema_guard.py`** — copy `modryn_portal/schema_guard.py` pattern: `pre_init_hook` dedupes `(template_id, day)` task rows, `post_init_hook` asserts the index; wire **both** hooks and a `migrations/` pair (documented install-vs-upgrade trap).

**`security/ir.model.access.csv`** — like atelier's: internal (owner) access rows only; portal reaches everything via controller `sudo()`.

## Controllers & UI

- **`controllers/floor_ops.py`** — `class ModrynFloorOps(ModrynFloor)` (precedent: `ModrynFloorAtelier`):
  - `_board()` = super + `modryn_ensure_today()` + adds: `my_tasks`, `checklist` (today's open/close instances), per-booking `outcome` key, manager-only `unclosed_count` (past non-cancelled bookings with NULL outcome — the nag badge).
  - `/floor/finish/booking` (jsonrpc): allowed for manager **or** `booking.modryn_employee_id == _my_employee()`; calls `modryn_set_outcome`; on `sold`, response reuses the existing `finished` payload shape (`floor.py:277`) so the current alteration-handoff modal chains with zero new plumbing.
  - `/floor/customer/<id>` read + `/floor/customer/save`: staff read/write measurements/notes/wedding date/party; **`budget` key present in the dict and writable only when `_is_manager()`** — omitting the key from the controller-built dict IS the field-level ACL under this security model; document in docstring.
- **`controllers/tasks.py`**: `/tasks/done`, `/tasks/reopen` (staff: own/unassigned; manager: any — atelier `advance` rule); `/manage/checklists` + `/new` + `/archive/<id>` owner CRUD (clone `/manage/pieces` from `modryn_atelier/controllers/atelier.py`).
- **`controllers/reports.py`**: `/manage/reports?from=&to=` (manager+); `/floor/my/stats` (jsonrpc, self-only — per-stylist stats are private to the stylist + managers/owner; no leaderboard).
- **`controllers/audit.py`**: `/manage/audit` owner-only, paginated.
- **`modryn_staff/static/src/floor/floor_board.js|xml`** (edit in place): "Done" button on booking cards → outcome modal (Sold: amount+items; Not sold: note, shows "feedback SMS will be sent"; No-show) → sold chains into existing alteration modal; task panel (my tasks + today's checklist, checkbox complete); customer-profile modal from any card; escalation/no-show toasts on new bus kinds; unclosed-count badge for managers.

## Crons

One new cron only: `MODRYN: escalate overdue tasks`, every 10 min. (No checklist spawner — lazy. No no-show sweep — manual + nag. Outbox drain/reminders already exist.)

## KPI formulas (live SQL/read_group in `/manage/reports` — no stored aggregates at ~1,500 bookings/yr)

Filtered by range on `calendar_event.start`, `modryn_is_booking`, not cancelled unless stated:
- Appointment conversion = sold ÷ (sold + not_sold)
- Contact conversion = distinct `modryn_customer_phone` with sold ÷ distinct phones with any completed outcome (phone is the contact key — partners are phone search-or-create)
- ATV = Σ `modryn_sale_amount` ÷ count(sold)
- No-show rate = no_show ÷ all-with-outcome; Cancellation rate from `modryn_cancelled_at`
- Follow-up completion = tasks type `follow_up`/`no_show_recovery` done ÷ total due in range
- Checklist completion = `template_id IS NOT NULL` done ÷ total
- Unclosed = past bookings with NULL outcome
- Per-stylist: group by `modryn_employee_id` (original-stylist attribution default; closer stored for a later switch)

## verify.sh additions (must use existing `detects()` plant-and-fire pattern)

- **§21 outcomes**: columns exist (tenants + template); plant past NULL-outcome booking → unclosed query sees it; public gets non-200 on `/floor/finish/booking`.
- **§22 tasks**: `modryn_task_one_instance_per_day` index exists everywhere (extend §17/§18-style checks incl. modryn_ops hook wiring); plant overdue open task → escalation SQL sees it; `/manage/checklists` walled; cron row active per tenant (§11 pattern).
- **§23 reports & audit**: `/manage/reports`, `/manage/audit` walled; plant sold outcome → conversion numerator counts it; audit table exists + `/manage/audit` renders for owner (§10a auth-surface pattern).

## Epic & spec breakdown (Spartan artifacts)

All work lives under one epic: **`.planning/epics/bridal-ops.md`** — five ordered features, each with its own spec in `.planning/specs/` (Gate 1) and plan in `.planning/plans/` (Gate 2), built one at a time (Gate 3), verify.sh green as Gate 4. Sections of THIS document are the source material for each spec — the spec step formalizes them, it does not re-invent scope.

| # | Feature | Spec file | Depends on |
|---|---------|-----------|------------|
| 0 | Regression-test existing addons | `.planning/specs/ops-0-regression-baseline.md` | — (hard gate for everything) |
| 1 | Outcomes + audit core | `.planning/specs/ops-1-outcomes.md` | 0 |
| 2 | Tasks, checklists, escalation | `.planning/specs/ops-2-tasks.md` | 1 |
| 3 | CRM fields + budget gating | `.planning/specs/ops-3-crm.md` | 1 |
| 4 | KPIs / reports | `.planning/specs/ops-4-reports.md` | 1, 2 (3 for budget-gated views) |

Features 2 and 3 are independent of each other and can be built in either order (or parallel); 4 needs both outcome data (1) and task data (2).

## Phase 0 — Test already-built features (hard gate before any new code)

The existing seven addons are the foundation the new work extends (floor board, SMS outbox, schema_guard, roster). Known state from `.planning/specs/launch-readiness.md`: **210 passed, 7 failed** — 4× "template never rebuilt" + 3 stale demo rows. Before Phase 1:

1. Rebuild the golden template: `./scripts/build_template.sh` (asserts both partial unique indexes; exits non-zero on failure).
2. Clean the 3 stale demo rows named in launch-readiness.md's defect register.
3. Run `./scripts/verify.sh` against a running dev server with tenants bella + noga → **all sections green** (no FAIL; SKIP/NOTE acceptable per the harness's own semantics).
4. Replay the touched manual acts from `docs/walkthrough.md` (13 acts) for the surfaces Phase 1+ will extend: booking submit, floor board assign/finish (walk-in), atelier handoff, SMS outbox drain, roster publish.
5. Record the green baseline (counts + commit hash) in `.planning/STATE.md` — new-feature verify sections diff against this baseline.

Exit criterion: verify.sh exits 0. If any existing check cannot be made green, that's a stop-and-discuss, not something to build on top of.

## Phases 1–4 (each shippable, ordered by dependency)

1. **Outcomes + audit core** (`modryn_ops` 19.0.1.0.0): skeleton, outcome fields, `modryn_set_outcome` with SMS flows (task creation stubbed), `/floor/finish/booking`, outcome modal in floor JS, `modryn.audit.log` + event write-diff, `/manage/audit`. verify §21 + audit half of §23. Update `build_template.sh` install list.
2. **Tasks, checklists, escalation** (19.0.1.1.0): `modryn.task` + template + `ensure_today` + unique index + schema_guard, owner CRUD, floor task panel, escalation cron + bus + manager SMS, wire outcome flows to create follow_up/no_show_recovery tasks. verify §22.
3. **CRM** (19.0.1.2.0): partner fields, profile modal, budget gating, category auto-assign, partner audit diff. verify additions to §21.
4. **Reports** (19.0.1.3.0): `/manage/reports`, `/floor/my/stats`, per-stylist privacy. verify §23 KPI checks.

Per-feature workflow: write spec (from this plan's sections) → Gate 1 → write plan → Gate 2 → build → run full verify.sh (old sections must STAY green — regression gate) → rebuild template → bump `.planning/STATE.md`.

## Deliberately NOT building (lean by decision; all resurrectable without schema breakage)

Clock-in/kiosk/punctuality/labor KPIs (user), POS/PSP/invoicing (manual amount), configurable Smart-Flow engine (constants instead), photo-proof tasks, structured measurement grid, auto-no-show cron, checklist seed data (user: ship empty), customer segments beyond purchased/not_purchased, commissions, weekly/monthly checklist recurrence, audit GC, stored KPI snapshots, leaderboards (research: morale risk — private stats only).

## Verification (end-to-end)

**Baseline first (Phase 0):** existing suite green as described above — this is the regression floor every later phase is measured against.

Per phase: `./scripts/verify.sh` fully green — **new sections AND all pre-existing sections** (any old section going red = regression, fix before proceeding) — against a running dev server with tenants bella+noga; rebuild `modryn_template` and assert new index via schema_guard exit code; manual walkthrough — book → assign stylist → finish as stylist (not-sold) → confirm feedback SMS row in `modryn.sms.outbox` + `modryn_feedback_sent_at` stamped + follow-up task on stylist's floor panel due +7d → let it go overdue (shrink constant locally) → escalation toast + manager SMS row → mark done → `/manage/reports` shows conversion + follow-up completion; `/manage/audit` shows the outcome edit; budget invisible in staff profile payload (curl as staff, assert key absent).

## Key reference files

- `addons/modryn_staff/controllers/floor.py` (base class; `_board`, finish payload at :277)
- `addons/modryn_staff/static/src/floor/floor_board.js` (modal + panel extension point)
- `addons/modryn_atelier/models/alteration_task.py` + `controllers/atelier.py` (model/controller templates)
- `addons/modryn_portal/models/booking_comms.py` (SMS body/lang pattern), `models/sms_outbox.py` (send_async)
- `addons/modryn_portal/schema_guard.py`, `scripts/verify.sh`, `scripts/build_template.sh`
