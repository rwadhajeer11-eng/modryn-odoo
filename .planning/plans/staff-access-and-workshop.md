# Plan: Feature hardening sweep — queue de-dupe, unified navbar + role access matrix, staff my-page, assignment SMS, workshop task queue

**Repo:** /Users/mrwen/Documents/Github/modryn-odoo (Odoo 19 Community, 8 custom addons, DB-per-boutique: bella/noga/modryn_template)
**Branch:** feature/walkin-verification (new work should branch from here or land after it merges)
**Created:** 2026-08-13 · **Status:** BUILT — landed on `feature/staff-access-and-workshop` (verify.sh 361/0/2, qa 21/21, engine + SMS proven live over HTTP). Designed by 2 architecture passes over 3 exploration reports; 35 technical claims adversarially fact-checked against the code before building (27 confirmed, 8 adjusted, corrections folded in).

---

## Context

The owner walked the whole product and asked for five things:

1. **One place in the queue per phone number** — they observed placing "the same number" three times.
2. **A staff page without the floor board** — staff should see only their own assignments and tasks; floor board visibility becomes owner-controlled.
3. **SMS to a staff member whenever they are assigned** — workshop tasks, floor customers, and follow-up tasks (owner chose "all assignments").
4. **A fixed, consistent navbar** with an owner-configurable role→page access matrix.
5. **A workshop task queue**: shift manager creates tasks with required priority + due date; workshop staff (a distinct pool) are auto-assigned the next task when they finish their current one, get SMS on assignment.

## Diagnosis of the "3 duplicate turns" (verified against the bella database)

The queue rows the owner created on 2026-08-13 06:19–06:20 are ids 14 and 15 — and they carry **two different phone numbers** (`+972544642743` vs `+972554642743`, an 054 vs 055 prefix). Different numbers are different people to the system, by design. Both rows are also in state `pending` — the **pre-verification default** — meaning the server was still running the old build when the test happened (the OTP build defaults new entries to `waiting` and creates nothing until the code is verified).

The per-phone de-dupe does exist (`modryn_check_in`, addons/modryn_queue_poc/models/queue_entry.py:95-121: same open phone → returns the existing ticket, silently). But it has three real holes worth closing regardless:

- **No database-level uniqueness** on `modryn_queue_entry` at all (Odoo 19 silently dropped `_sql_constraints`; nothing replaced it here, unlike the waitlist table which has `unique(phone, day)` + a partial UniqueIndex).
- **Race**: two concurrent verifications for the same number both pass the search and both `create()` — the search-then-create is unlocked.
- **Format mismatch**: legacy rows store unnormalized phones (`054-7778888`, `0500000009` are live in bella), which the normalized search can never match.

Honest limitation to state: identity is the phone number. One person with two SIM cards can hold two places; no phone-keyed system can prevent that.

## Product decisions (settled with the owner, 2026-08-13)

| # | Question | Decision |
|---|---|---|
| D1 | Duplicate phone re-check-in | Flow continues through the OTP; she lands on her **existing** ticket — never a second row — with a one-line notice on the ticket page: "This number is already in the line — this is your place." |
| D2 | Floor board visibility | Becomes a page in the new access matrix. Defaults: owner + shift manager ON, staff roles OFF. |
| D3 | Staff assignment SMS | **All** assignment kinds: workshop tasks, floor customer assignments, follow-up tasks. Via existing `send_async` outbox. |
| D4 | Workshop task priority | Required **priority level + required due date** at creation. Queue orders by priority, then earliest due date. |
| D5 | Access matrix rows | **Job roles** (`modryn.staff.role`, owner-created data). Permission levels keep gating actions; `/manage/*` stays owner-only; shift managers see all staff pages regardless of matrix; owner sees everything. |
| D6 | Workshop staff pool | Boolean flag on the job role ("workshop role") — owner ticks it on seamstress in /manage/roles. Every employee holding a flagged role is in the pool. |
| D7 | Auto-assignment behaviors | Manager can also assign directly to a person (jumps queue). New tasks assign immediately to an idle pool member (least-loaded first). Auto-assign only to people on **today's published rota** (fallback when no rota is published: gate waived). **No** staff decline/return in v1. |

## Constraints that bind every design choice (from .memory/)

- Managers and staff are **portal users** — no ORM access, no ir.rule; every route re-checks its group server-side and reads via `sudo()`, handing templates plain dicts.
- Uniqueness = `models.Constraint`/`models.UniqueIndex` (Odoo 19); savepoint + catch `UniqueViolation` by index name (pattern: day_waitlist.py:118-130).
- SMS: `send_async` → outbox + cron only (blocking `send` is reserved for OTP + 24h reminder). Only bella has Twilio; test on noga (log-only).
- No dedupe/error **before** the OTP code — answering "this number is queued" at the form is the ticket-hijack oracle the verification build closed (settled: revisit never).
- `translate=True` = jsonb — never SQL-unique it; owner data uses plain Char.
- New Python/routes → full server restart; XML-only → `-u module`. New models must reach `modryn_template` + existing tenants via upgrade.
- Changing a public form → check `qa/specs/*.js` and `loadtest/k6` (k6 asserts status only and can go green while measuring nothing).

---

## Architecture — Cluster A: unified nav, access matrix, staff home page

All new plumbing lives in `modryn_staff` (the common dependency); downstream modules extend via the two patterns the repo already uses (controller inheritance; a Python registry replacing the fragile XML xpath injections).

### A1. Page registry — `addons/modryn_staff/nav.py`
Module-level `PAGES` list + `register(key, url, label, sequence, section)`. modryn_staff registers `home/floor/roster/checkin` + manage `staff/roles/rooms/hours`; modryn_atelier registers `atelier` + `pieces`; modryn_ops registers `reports/checklists/audit`; modryn_roster registers `shifts`. Rendering sorts by `sequence` — kills the load-order bug where roster and ops both xpath-inject after the same anchor. Labels use `_lt(...)` (trap 9: `_()` on dict lookups hides literals from the extractor).

### A2. Matrix model — `modryn.role.page` (`addons/modryn_staff/models/role_page.py`)
- `role_id` M2o `modryn.staff.role` required `ondelete='cascade'`; `page_key` plain Char (NOT translate — must be SQL-comparable); `models.UniqueIndex('(role_id, page_key)')`.
- `modryn_can_view(page_key)`: owner → True; manager → True for any staff-section page; staff → `page_key == 'home'` or a sudo-searched row for her role; no role → home only.
- `modryn_nav()`: filters `nav.PAGES` through `modryn_can_view`, returns plain dicts; called directly from QWeb.
- **Rows are the single source of truth — no "empty means default" fallback** (otherwise "owner unchecked everything" is indistinguishable from "never configured"). Defaults are *seeded*: `create()` override on `modryn.staff.role` seeds default rows (`roster`, `checkin`) for every new role; a migration seeds the same for pre-existing roles on bella/noga/template.
- `home` is never a matrix row (always reachable). `checkin` is nav-only — `/queue/checkin` stays `auth='public'` (brides use it via QR); the matrix only controls the link.

### A3. One nav shell
`staff_layout` rewritten as two loops over `modryn_nav()`: row 1 = staff pages + lang toggle + user name + Sign out; row 2 renders only when the manage section returned entries (owner). `manage_layout` becomes a one-line alias t-calling `staff_layout` forwarding its body via `<t t-out="0"/>` (core precedent: portal_layout → frontend_layout, odoo/addons/portal/views/portal_templates.xml:144,168) — the **10** templates that t-call `manage_layout` (5 modryn_staff, 2 modryn_ops, 1 modryn_roster, 2 modryn_atelier) stay untouched.

**Atomicity constraint (verified):** the three xpath-injection views — `modryn_roster.manage_nav_shifts`, `modryn_ops.manage_nav_audit` (both anchor `//a[@href='/manage/rooms']`), `modryn_ops.staff_nav_reports` (anchors `//a[@href='/atelier']`) — must be deleted **in the same change** as the layout rewrite: once the anchors vanish from the parent arch, those inherit views fail at view load, breaking every page that renders the shell.

Existing `active_tab` keys stay as-is — the full set is **11**: floor, roster, staff, roles, rooms, hours, pieces, shifts, checklists, reports, audit. `modryn_queue_poc`'s dynamic `t-call` inherits the unified nav for free.

### A4. Enforcement — `addons/modryn_staff/controllers/access.py`
Free functions `is_staff()/is_manager()/is_owner()/can_view(page_key)/deny()`. Only **page** routes change gates: `/floor` + `/floor/data` (`floor`), `/roster` (`roster`), `/atelier` (`atelier`), `/manage/reports` (`reports`). `/floor/data` denial reuses the **existing `'forbidden'`** error code — floor_board.js's apply() guard (floor_board.js:151) already banners it via `errorText()`; a new code like `no_access` would render as a raw untranslated string. All action routes (`/floor/assign`, `/roster/publish`, `/atelier/advance`, `/tasks/done`…) keep existing level gates; all `/manage/*` stays owner-only. Two gates deliberately loosen: `/atelier` and `/manage/reports` go manager-only → matrix-granted (staff reach them only if the owner ticks the box; every mutating action inside remains level-gated). `deny()` renders a themed `no_access` page (HTTP 403) **wearing the unified nav** so a refused staff member sees exactly which pages she does have — the "show the user the error" requirement. Anonymous → login redirect; signed-in non-staff → `request.not_found()` (don't leak what exists).

### A5. Staff home — `/staff/home` (not `/my/*`, that's the customer portal namespace)
Server-rendered QWeb + plain dicts (house pattern). `ModrynHome._home()` in modryn_staff builds: me (name/role + "no role set" flag), my customers now (queue entries waiting/called + today's bookings where I'm primary or helper). Controller inheritance adds: roster → today's shift; atelier → my open alterations — `_my_open_tasks()` **already exists** (atelier.py:139-147, with a comment demanding one source); the work is hoisting it to a module-level function so `ModrynFloorAtelier._board()` (lines 22-30, currently a duplicate) and the new home controller both route through it; ops → my open follow-ups + my-month stats (reuse `_range_stats`). One template with `t-if="home.get(key) is not None"` sections — same precedent as floor_board.xml's ops-injected keys. Actions reuse existing staff-permitted jsonrpc routes (`/atelier/advance`, `/tasks/done`) via ~30 lines of vanilla fetch JS + reload; refresh = visibility-guarded 60s reload (ponytail: upgrade to bus-refetch only if staleness annoys).

### A6. Landing (`auth.py:landing_for`)
Owner → `/manage/staff` (unchanged), manager → `/floor` (unchanged — qa act 5 asserts it), staff → `/staff/home`.

### A7. Cluster A files
Create: `nav.py`, `models/role_page.py`, `controllers/access.py`, `controllers/home.py`, `views/home_templates.xml`, `static/src/home/home.js`, `migrations/19.0.1.6.0/post-migrate.py` (all in modryn_staff).
Modify: modryn_staff `models/__init__.py`, `staff_role.py`, `security/ir.model.access.csv`, `floor_templates.xml`, `manage_templates.xml`, `controllers/__init__.py`, `auth.py`, `floor.py`, `manage.py` (matrix editor POST `/manage/roles/pages`), `__manifest__.py`; modryn_roster `roster.py`, `roster_templates.xml`, manifest; modryn_ops `reports.py`, `floor_ops.py`/HomeOps, `ops_templates.xml`, manifest; modryn_atelier `atelier.py`, manifest; `scripts/verify.sh`, `qa/specs/staff.spec.js`, `loadtest/seed/seed_tenant.py`.

## Architecture — Cluster B: queue de-dupe, workshop task queue, assignment SMS

### B1. Duplicate-phone hardening (modryn_queue_poc)
The referee moves to Postgres: partial `models.UniqueIndex` on `modryn_queue_entry(phone)` over the three open states (`WHERE state IN ('pending','waiting','called') AND phone IS NOT NULL`) — same `(definition, message)` shape as `day_waitlist.py:80-83` (whose own predicate is `(day) WHERE state = 'offered'`; UniqueIndex signature verified at odoo/orm/table_objects.py:185-192). The SQL name derives from the attribute: `_modryn_open_phone_uniq` → `modryn_queue_entry_modryn_open_phone_uniq` (40 chars — safely under Postgres's 63-char identifier limit, so the hardcoded `diag.constraint_name` comparison holds). `modryn_check_in` keeps its search-first fast path, wraps `create` in a savepoint catching `UniqueViolation` by index name (day_waitlist.py:118-130 pattern), and returns `(entry, created)` — one caller exists (main.py:138). The `'phone': normalized or (phone or '').strip()` fallthrough becomes `normalized or False` (unnormalizable → NULL, exempt from index, can never be texted).

**Re-check-in UX (D1):** verify still runs (anti-oracle decision untouched). Controller sets a one-shot **session flag** when `created=False` (not `?already=1` — the ticket page self-reloads every 15s preserving the query string, and the URL is shareable; a popped session key survives neither reload nor share). `/q/<token>` pops it and renders the one-line notice, only while the entry is still open.

**Cleanup at upgrade** (schema_guard.py cloned from modryn_ops): normalize open-row phones via `normalize_il_phone`, then expire younger open siblings per phone — **oldest wins** (create_date asc IS the queue order; her true place; also the row the join-SMS fired for). Losers are `expired`, not deleted (they were texted ticket links; `/q` renders the warm exit). Wired to pre/post_init_hooks AND `migrations/19.0.1.2.0/` (version bump required — migration is keyed to it). `assert_indexes(cr)` fails loudly if the index is missing.

**Diagnosis (task 0, read-only):** partially done live — the owner's two rows carry *different* numbers (054… vs 055…) and pre-date the OTP build (state `pending`, the old default). Remaining classification at implementation time: format-mismatch rows (legacy `05…`/raw vs `+972…`), pre-`a45f3b6` rows, same-second races, and loadtest seed rows. The fix is identical regardless — the index makes every path honest, including the seed.

**Seed collision (verified — must fix with the index):** `loadtest/seed/seed_tenant.py:417-425` creates queue entries **directly** with deterministic phones (`+97252<NN>9000..9007`, same numbers every run), and its top-up loop `range(open_now, QUEUE)` assumes open entries are exactly indices 0..open_now-1. k6's `phoneForVu` shares the same prefix and 4-digit space, so after a k6 run a re-seed can try to create an already-open phone — which the new index turns from a silent duplicate into a crash. The seed must skip phones that already have an open entry (small change in seed_tenant.py, same commit as the verify.sh teeth).

### B2. Workshop task queue (modryn_staff + modryn_atelier)
- `modryn.staff.role.is_workshop` Boolean; owner toggles at `/manage/roles` (D6). Pool = employees whose role carries the flag.
- `modryn.alteration.task.priority` Selection `('0' Low / '1' Normal / '2' High)` — digit keys so `_order = 'priority desc, due_date asc, id asc'` sorts correctly as text; existing rows backfill to default `'1'` at upgrade. `due_date` stays nullable at DB level (legacy rows) but is **required at the single creation door** (`/atelier/task/create` returns `missing_due` / `missing_priority` errors) + required on form inputs. Postgres sorts NULLS LAST under ASC → legacy no-due tasks fall to the back of their priority band for free.
- **Auto-assignment: three model methods, all in-request, no cron.** `_modryn_pool()` (flag + rota gate), `_modryn_assign_idle()` (create-side: assign newborn unassigned task to an idle pool member — idle = zero open assigned tasks; tie-break lowest id, ponytail comment), `_modryn_pull_next(employee)` (finish-side: after a `delivered` write, the freed seamstress pulls the top queued task).
- **Rota gate (D7):** reads `modryn_rostered_on(today in Asia/Jerusalem)` via soft registry lookup `if 'modryn.shift.slot' in self.env:` — no manifest dependency atelier→roster (matches the repo's existing conditional-coupling style). `None` return (no published rota — also returned for published-but-empty day) ⇒ **gate waived, whole pool eligible** (decided fallback).
- **Concurrency:** `_modryn_pull_next` picks with one raw `SELECT … FOR UPDATE SKIP LOCKED` ordered `priority DESC, due_date ASC NULLS LAST, id ASC` — no unique index can referee this race (manual assignment may legitimately exceed one open task), and SKIP LOCKED gives two simultaneous finishers *different* tasks. Create-side idle race (two creates, same idle member) accepted with ponytail ceiling (upgrade: FOR UPDATE on hr_employee row).
- Manager direct assignment (existing `/atelier/assign`) unrestricted — may exceed one open task (D7). No staff decline (D7). `/atelier` dashboard gains a "Queue" panel (unassigned open tasks in priority order) + priority badges; floor finish-modal gains priority select + required due date.

### B3. Assignment SMS (D3) — `modryn.staff.notify` AbstractModel in modryn_staff
One dumb chokepoint (~35 lines), AbstractModel so `env['modryn.staff.notify']` needs no cross-module import and no ACL (modryn_staff is the common dep; `modryn.sms` reachable via its dependency chain). It does four things: **skip when actor == assignee** (resolve `env.uid` → employee — silences the noisiest case, the seamstress auto-pulling her own next task); resolve `work_phone or mobile_phone` (log-and-skip, task.py:205 pattern); `send_async` (never blocking `send`); log on refuse. Callers compose bodies under `with_context(lang=…)` per the task.py:203-211 precedent.

**Idempotence (decided):** notify only when the assignee field *changes to a non-null* employee (capture old ids before super().write()); notify on create when born assigned; never on unassign; never to the actor herself. **No cooldown stamp** — a stamp would suppress a *real* reassignment text (A at 10:00, B at 10:02 — B must hear); bounded by human drag speed (ponytail: upgrade = per-record notified-at stamp). Helpers deliberately do **not** text in v1 — the primary is the accountable one.

Hooks: `assignment.py` write() on `modryn_employee_id` for queue entries + bookings (same filter as the existing bus push); `modryn_ops/task.py` create+write on `employee_id`; `alteration_task.py` create+write on `seamstress_id`. Bodies: "New customer for you: %(name)s" / "New task for you: %(name)s" / "New alteration for you: %(customer)s — %(dress)s, due %(due)s".

## Task breakdown

### Cluster A (each task = one commit, ≤3 files)
1. Registry + model: `nav.py`, `models/role_page.py` + `models/__init__.py`.
2. Access helpers + themed refusal: `controllers/access.py` (+ `__init__.py`), ACL rows (owner CRUD + internal-user read — the pair every owner-data model here carries; the read row serves internal non-owner accounts like the admin back office, not the owner), `no_access` template.
3. Unified nav: rewrite `staff_layout`; `manage_layout` → alias; **same commit deletes the three xpath-inheritor views** in `ops_templates.xml` + `roster_templates.xml` (anchors vanish — deferring this breaks view load; 4 files, deliberately over the 3-file guideline because it cannot split).
4. Default seeding: `staff_role.py` create() override; migration; manifest version bump.
5. Matrix editor: `manage.py` (grant table + owner-gated CSRF `POST /manage/roles/pages`, replace-set semantics), checkbox grid in `manage_templates.xml` (archived roles greyed).
6. Gate the floor: `floor.py` `/floor` → deny() when `not can_view('floor')`; `/floor/data` → `{'error': 'no_access'}` (floor_board.js already renders `board.error`).
7. Staff home server side: `controllers/home.py`, `home_templates.xml`, manifest data entry.
8. Staff home actions + refresh: `static/src/home/home.js`, asset glob, button hooks.
9. Landing: `auth.py` staff → `/staff/home`.
10. modryn_roster: register `shifts`, gate `/roster`, `HomeRoster`; manifest (xpath template already deleted in task 3).
11. modryn_ops: register pages, gate `/manage/reports`, `HomeOps`; manifest (xpath templates already deleted in task 3).
12. modryn_atelier: register pages, gate `/atelier`, extract `my_open_task_rows()` + `HomeAtelier`; manifest.
13. Upgrade + translations: full restart, then per DB (template, bella, noga): `-u modryn_staff,modryn_roster,modryn_ops,modryn_atelier --stop-after-init`, then `sync_translations.py`, then `-u` again (trap 15 order).
14. Tests: verify.sh §, qa staff spec, loadtest seed grant.

### Cluster B (each task = one commit, ≤3 files)
0. Diagnosis (no commit): classify remaining dupes per tenant; findings go in commit 1's message.
1. A: DB referee + honest creator — index, savepoint create, `(entry, created)`, `phone: normalized or False` (queue_entry.py).
2. A: notice travel — session flag + template line (main.py, templates.xml).
3. A: install wiring — schema_guard.py, hooks, version bump 19.0.1.2.0.
4. A: upgrade wiring — migrations/19.0.1.2.0/{pre,post}-migrate.py.
5. A: teeth — verify.sh §6-bis + §17 index list (verify.sh:1213-1215) + new_boutique.sh index list (:51-52 — note: the two lists are NOT identical today, new_boutique's is a subset); loadtest/seed/seed_tenant.py open-phone skip.
6. A: browser proof — re-check-in act in qa/specs/staff.spec.js.
7. B: workshop flag — staff_role.py, manage.py toggle, manage_templates.xml.
8. B: priority + ordering + required-at-the-door + dashboard queue panel (alteration_task.py, atelier.py, atelier_templates.xml).
9. B: auto-assignment engine — pool, idle-assign, pull-next with SKIP LOCKED (alteration_task.py).
10. B: floor UI — finish modal priority/due, my-panel badge (floor_board.xml, floor_board.js).
11. C: the notifier — models/notify.py + models/__init__.py.
12. C: floor hooks — assignment.py (queue entry + booking primary).
13. C: ops + atelier hooks — task.py, alteration_task.py.
14. B/C teeth + translations — verify.sh checks; `-u` → sync_translations.py → `-u` (trap 15), `git checkout --` unrelated .po.

Dependencies: 1→2→(3,4)→5→6; 7→8→9→10; 11→(12,13); 9 before 13 (shared file); 14 last.
**Cross-cluster ordering:** Cluster A and B are independent except: (a) `manage_templates.xml` roles page is touched by both (A task 5 matrix grid, B task 7 workshop toggle) — land B task 7 first or rebase; (b) modryn_staff gets ONE version bump (19.0.1.6.0 carries A's seeding migration; B's is_workshop boolean needs no migration).

## Edge-case & scenario tables

### Cluster A

| Case | Handling | Where the error/notice renders |
|---|---|---|
| Employee with no job role | can_view → home only | Home banner: "Your role isn't set yet — ask the owner" |
| Owner unchecks every page for a role | Legal; staff keep `/staff/home` | Nav shrinks to Home; direct URLs → themed 403 |
| Revoked access mid-session | Per-request check against DB rows | Themed no_access (403) with unified nav; open floor board's poll returns `{'error':'no_access'}` and shows board error banner |
| Manager demoted mid-session | has_group re-read per request | Themed 403 on next navigation |
| Archived role | Rows kept (archive ≠ revoke); access unchanged until owner reassigns | Matrix editor greys archived roles |
| Role unlinked (backend only) | cascade clears rows; employee m2o → null → no-role case | Home-only + banner |
| Direct URL to denied page | render no_access, status 403 | Themed page shows what she CAN open |
| Anonymous / non-staff account | Unchanged: login redirect / not_found | Login page / 404 (existence not leaked) |
| Fresh tenant cloned from template | Template DB upgraded in task 13 → clones carry seeded defaults | verify.sh asserts rows on template |
| Owner/manager opens /staff/home | Allowed (harmless personal view) | — |

### Cluster B — Feature A (de-dupe)

| Scenario | Handling | Message seen where |
|---|---|---|
| Same phone re-checks in through OTP | search hit → existing returned, no row, join-SMS never re-fires | Ticket page one-liner: "This number is already in the line — this is your place." (he/ar/en) |
| Two verifies race past the search | loser's INSERT hits the index → savepoint absorbs → re-search → same ticket | Same notice on the second device |
| Staff-terminal duplicate | dedupe identical; redirect /floor; no notice by design (board shows her once) | Floor board |
| Ticket URL shared / 15s auto-reload | session flag already popped → notice shows exactly once | Plain ticket afterwards |
| Legacy raw-format phones | migration normalizes open rows before the index builds | Migration log |
| Existing open dupes at upgrade | oldest keeps her place; younger siblings expired (not deleted — they were texted links; /q renders the warm exit) | Migration warning |
| Unnormalizable phone from non-web caller | stored NULL, exempt from index (can never be texted anyway) | — |
| Loadtest seed's direct create | index polices it; a collision now fails loudly instead of duplicating | Seed stderr |
| Nightly closing cron | expires all opens; index constrains only open rows; tomorrow re-joins fresh | — |
| Two different numbers, one person | Two places — inherent to phone-keyed identity; stated, not solved | — |

### Cluster B — Feature B (workshop queue)

| Scenario | Handling | Seen where |
|---|---|---|
| Task created, idle rostered pool member exists | auto-assigned at create (lowest-id idle) | Dashboard workload + her my-panel + staff home; SMS (actor is the manager) |
| Task created, pool all busy / gated out | queues unassigned, priority desc → due asc → id | Dashboard "Queue" panel |
| Seamstress delivers her task | pulls top of queue iff she now has zero open (`ready` still counts as open — only `delivered` frees her) | My-panel refresh; **no SMS** (actor==assignee) |
| Two deliver simultaneously | FOR UPDATE SKIP LOCKED — each takes a different task | — |
| No published rota today / published-but-empty | `modryn_rostered_on` → None both ways ⇒ gate waived, whole pool eligible (decided) | Code comment |
| Rota published, no pool member on it | tasks queue; re-checked at every trigger | Queue panel |
| Manager assigns a busy member directly | allowed, may exceed one open (D7); no auto-rebalance | Workload counts |
| Manual reassign away from someone | freed member does NOT auto-pull (v1 ceiling, ponytail comment) | — |
| Employee archived with open tasks | tasks stay assigned + visible; manager reassigns manually (v1 ceiling) | Dashboard workload |
| Two creates race for one idle member | she may get two — accepted (manual can exceed one anyway); ponytail names the upgrade | — |
| Legacy rows: no priority / no due | backfilled '1'; NULL due sorts last in band; new creates require both | Finish-modal error text |
| modryn_roster not installed | registry check false ⇒ gate waived | — |

### Cluster B — Feature C (assignment SMS)

| Scenario | Handling | Seen where |
|---|---|---|
| Assignee changes A→B | B texted, in her own language, via outbox | SMS |
| Re-drop of the same person | old == new ⇒ silent | — |
| A→B→A shuffle | 3 texts, each to the then-current assignee — deliberate (throttling risks eating a real reassignment) | SMS |
| Actor assigns herself / auto-pull on own finish | actor==assignee ⇒ skipped — she's watching the screen that just changed | Floor panel |
| Assignee has no work/mobile phone | log-and-skip | Server log |
| Unassignment (→ null) | no SMS | — |
| Helper added to a card | no SMS (primary-only, v1 ceiling) | — |
| Auto-assign vs manual racing same person/task | possible double text; rare, bounded, accepted | Duplicate SMS |
| Cron contexts (escalation etc.) | actor is OdooBot ⇒ never equals assignee, texts flow | SMS |
| noga (no Twilio) | outbox drain logs — the honest fallback; bella sends for real | Log / handset |

## Verification

### Cluster A
- **verify.sh**: `modryn_role_page` exists per tenant + template; template row count ≥ 2 × seeded roles; add `/staff/home` to the §7 anonymous-refusal loop; grep-teeth for `can_view('floor')` in floor.py; assert the three deleted xpath views are gone from `ir_ui_view` after upgrade (catches a forgotten `-u`).
- **qa/ Playwright**: staff signs in → lands on `/staff/home`, page paints; staff opens `/floor` under default matrix → themed no-access (not the floor-board marker); owner sees matrix checkboxes on `/manage/roles`; existing act 5 (manager → /floor) stays green.
- **loadtest/k6**: `scenarios/staff.js` asserts the floor-board marker as staff — under the new staff-OFF default this breaks; fix by seeding a `floor` grant for the seeded sales role in `loadtest/seed/seed_tenant.py` (plausible owner choice), leaving k6 untouched.

### Cluster B
- **verify.sh — new §6-bis "the line cannot hold one number twice"** (noga, cookie-jar curl + `scripts/otp_code.sh` HMAC reversal): (1) submit → 0 rows; (2) wrong code → 0 rows; (3) right code → exactly 1 row at `waiting` (this closes the backlog's stated gap); (4) full flow again, same phone → still 1 row and the second verify's Location carries the *same* access_token; (5) DB teeth: direct psql INSERT of a second open row with the same phone must fail naming the new index — with an own-tenant control INSERT (fresh phone succeeds, then delete) so the check can't pass because inserts are broken generally.
- §17 + `scripts/new_boutique.sh`: append the new index name to both index lists (template included).
- B/C teeth: columns `modryn_alteration_task.priority` + `modryn_staff_role.is_workshop` exist per tenant + template; grep-teeth: `FOR UPDATE SKIP LOCKED` present in alteration_task.py; notify.py calls `send_async` and never `.send(`.
- **qa/ Playwright**: existing walk-in act safe (`qaPhone()` is per-run unique — the dedupe only got stronger). New act: check in, then checkin→verify again with the SAME phone (readOtp again — 2 of the 3/hour budget), assert same-token URL, notice visible, board shows the name exactly once, and a reload of the ticket does NOT re-show the notice (proves the session-pop).
- **k6**: no changes; a dedupe hit still answers 303 → /q/<token> so status assertions hold — noted explicitly that k6 stays green *because it measures nothing here*.
- **Upgrade sequence** (both clusters): full server restart (new Python/routes everywhere), then per DB (modryn_template, bella, noga): `-u modryn_queue_poc,modryn_staff,modryn_atelier,modryn_ops,modryn_roster --stop-after-init` (fires the queue migration + role_page seeding), then trap-15 translation order: `-u` → `scripts/sync_translations.py <db>` → `git checkout --` untouched modules' .po → `-u` again. Finish: `MODRYN_DEMO_PASSWORD=modryn2026 ./scripts/verify.sh` ≥ 328 passed, 0 failed, and `qa/` green on noga.

### Deliberate ceilings (named, each gets a ponytail comment)
Oldest-wins dedupe cleanup may retire the link a duplicate-holder most recently received; create-side idle race can double-book one seamstress; freed-by-reassign and archived-with-tasks don't auto-rebalance; helpers and A→B→A shuffles are un-throttled SMS; "least-loaded idle" degenerates to lowest-id pick; staff-home refresh is a 60s reload, not bus-driven.
