# Stage 2+ plan — customer portal, tri-language, dispatch board, alterations

**Status:** SHIPPED 2026-08-10 — all four phases built and verified (verify.sh 44/44); see docs/walkthrough.md Acts 7-9
**Baseline:** commit `35cbb79` — Stage 1 staff layer complete, `./scripts/verify.sh` 27/27 green

## Context

Stage 1 gave the boutique employees, owner-defined roles, assignment, and a live floor
terminal. This stage adds the four things agreed in the grilling session:

1. **Customer portal** — phone + OTP login, view + cancel bookings (Stage 2 as originally scoped).
2. **Tri-language** — customer-facing he/ar/en; staff screens he/en. All 152 hardcoded
   Hebrew literals become English-source + `.po` translations.
3. **Dispatch board v2** — drag-and-drop staff bench → customer cards, one primary + helpers.
4. **Alterations (atelier)** — finish-screen handoff to seamstress tasks on garment pieces,
   workshop dashboard, seamstress self-view.

### Decisions locked (do not re-litigate)

| Decision | Answer |
|---|---|
| Portal powers | View upcoming/past + **cancel** (terms shown, slot freed). Reschedule = cancel + rebook |
| OTP delivery | **Real Twilio** on the user's existing account, behind a sender port with **log-fallback** when unconfigured. IL-capable sender number still pending (user action) — real-SMS proof deferred on it |
| OTP policy | 6 digits, 5-min expiry, rate-limited (3 sends/hour/phone, 5 verify attempts/code) |
| i18n scope | Customers: he/ar/en via storefront switcher. Staff screens: he/en via per-user preference. English = source language |
| Content i18n | Dress names/descriptions stay Hebrew as fallback; per-record translation stays available in backend |
| Assignment | **One primary + helpers** per customer. First drop = primary; drop on primary slot swaps (old primary → helper); drag off = remove |
| Drag model | **Staff bench → customer cards.** Bench doubles as the live who-is-free dashboard. Managers drag; staff read-only (server-enforced) |
| Garment pieces | Owner-editable list (like roles), seeded: מכפלת, מחוך, שרוולים, שובל, כתפיות. Task = piece(s) + free-text note + due date |
| Task states | intake → in progress → ready → delivered |
| Dashboard | Manager/owner workshop board (by state, per-seamstress load, overdue). Each seamstress sees + advances **her own** tasks on /floor |
| Bench vs workshop | Bench shows fitting-floor presence only; alteration workload lives on the workshop dashboard (a seamstress sewing in back is still callable) |

## Phase A — i18n foundation (FIRST: every later phase writes English-source strings)

**A1. Activate English.** `en_US` is Odoo's source language (already loaded, inactive).
Activate in both tenants + template; add to `website.language_ids` (customer pages);
keep `he_IL` default. Update `scripts/build_template.sh`/`configure_template.py`.

**A2. String refactor (152 literals).** Per addon: QWeb template text → English (Odoo
auto-extracts text nodes); Python strings → `_()` from `odoo`; OWL/JS → `_t` from
`@web/core/l10n/translation`. Then export POT per addon and write:
- `modryn_theme/i18n/`, `modryn_booking/i18n/`, `modryn_queue_poc/i18n/`: `he.po` + `ar.po`
- `modryn_staff/i18n/`: `he.po` only (staff screens are he/en — no Arabic)

Hebrew translations are the *current* strings moving into `.po` files, so Hebrew output
stays pixel-identical after the refactor — that is the regression check.

**A3. Staff language toggle.** he/en switch on the staff nav (`manage_layout` +
`floor_page`) writing `res.users.lang`; pages render in user language automatically.

**A4. LTR verification.** English storefront must render `dir="ltr"` with the theme intact
— the SCSS already uses logical properties (`margin-inline`, `border-inline-start`), verify
nothing assumes RTL. Screenshot he + en side by side.

**Traps that apply:** role/piece *names* are owner data — never `translate=True` on them
(trap 5, jsonb). Odoo picks `.po` up only on module `-u` with `--load-language`.

## Phase B — Stage 2 customer portal (new addon `modryn_portal`)

**B1. Sender port.** `ModrynSmsSender` interface; `TwilioSender` (REST API via `requests`,
already an Odoo dep; credentials from `ir.config_parameter`, set by seed script from env,
platform-wide for now) and `LogSender` (logs the code, active whenever Twilio is
unconfigured — dev/tests never send real SMS).

**B2. OTP model.** `modryn.otp.code`: normalized phone, **hashed** code, `expires_at`,
`attempts`, `used_at`. Python-side rate limiting per the locked policy. Uniqueness/checks
via `@api.constrains` (trap 4: `_sql_constraints` is dead in 19).

**B3. Login flow.** `/my/login` (phone form) → `/my/verify` (code). On success, store the
resolved `partner_id` in `request.session` — **no `res.users` is created**; a customer is a
session-identified partner, which sidesteps portal-account provisioning entirely.
`request.session.touch()` on both GETs (trap 6: CSRF needs a persisted sid on first visit).

**B4. My bookings + cancel.** `/my/bookings`: upcoming/past `calendar.event` matched by
partner/phone (via sudo + plain dicts, the established pattern). Cancel → confirm screen
showing the cancellation terms → sets `modryn_cancelled_at` (new field; **not** unlink —
history stays). `_slots()` in `modryn_booking/controllers/main.py` and the floor board
exclude cancelled events, which is what frees the slot.

**B5. Entry point.** "התורים שלי" link in the storefront header; portal pages themed via
`web.frontend_layout` like the staff screens, in the customer's current website language.

## Phase C — Dispatch board v2 (extends `modryn_staff`)

**C1. Helpers model.** `modryn_helper_ids` m2m on `modryn.queue.entry` **and**
`calendar.event` (primary stays `modryn_employee_id`). Occupancy compute
(`hr_employee._compute_modryn_is_occupied`) extends to helpers. Queue `_payload()` gains
helper ids/names so the existing bus channel carries it (no new realtime machinery);
assignment writes on bookings publish on the same channel.

**C2. Assignment API.** JSON routes (jsonrpc, manager-checked server-side like today):
`/floor/assign` (target queue|booking, employee, auto primary-or-helper per the locked
rule), `/floor/unassign`, `/floor/promote` (swap into primary). `/floor/finish` returns the
finish-screen payload (customer, dress) instead of just closing.

**C3. DnD board.** Rework `floor_board.js/.xml`: bench of staff chips (live פנויה/תפוסה) +
customer cards with primary slot and helper chips. Drag via native HTML5 events in OWL
(frontend bundle; Odoo's backend sortable hooks aren't needed) **plus click-to-assign kept
as fallback** — tablets and accessibility both want a non-drag path. Refresh from
`/floor/data` after each action; bus push keeps other boards live.

## Phase D — Alterations (new addon `modryn_atelier`)

**D1. Models.** `modryn.garment.piece` (name, sequence, active — owner data, seeded with
the five pieces, Python-unique like roles). `modryn.alteration.task`: partner, phone,
dress/variant ref, `piece_ids` m2m, note, `seamstress_id` (hr.employee), state
(intake/in_progress/ready/delivered), `due_date`, timestamps. Owner-only ACLs; portal
access through controllers + sudo (established pattern).

**D2. Finish screen.** After סיום on the board: modal — seamstress, pieces (multi), note,
due date, or **skip** (nothing to alter). Creates the task in intake.

**D3. Workshop dashboard.** `/atelier` (manager/owner): tasks grouped by state,
per-seamstress load counts, overdue highlighted (due_date < today, not delivered).

**D4. Seamstress self-view.** Her tasks as a panel on `/floor` (portal login she already
has), with in-progress/ready buttons — she advances her own work; that keeps the dashboard
truthful (same anti-staleness principle as derived occupancy).

**D5. Pieces admin.** `/manage/pieces` — clone of the roles page (reuse `manage_layout`,
same controller pattern, same Python uniqueness).

## Order & effort

A (≈1–1.5d) → B (≈1.5d) → C (≈1–1.5d) → D (≈1.5d). C before D because the finish screen
lives on the board. Total ≈ 5–6 build-days equivalent.

## Verification

- **Regression first:** `./scripts/verify.sh` stays green after every phase; Hebrew output
  pixel-identical after A2 (the .po round-trip check).
- **A:** `/shop` in en → `dir="ltr"`, English UI, Hebrew dress names (fallback by design);
  `/ar/shop` unchanged; staff toggle flips `/floor` he↔en; `modryn_staff` has no `ar.po`.
- **B:** OTP round-trip with LogSender (code read from log in dev); wrong/expired/6th
  attempt rejected; cancel shows terms, sets `modryn_cancelled_at`, and the slot reappears
  in `/book` on bella while noga is untouched; Twilio adapter unit-exercised against a
  mocked endpoint (real SMS deferred on the IL number — recorded, not hidden).
- **C:** Playwright drag chip→card assigns primary; second chip becomes helper; drop on
  primary slot swaps; drag-off removes; occupancy counts include helpers; second browser
  window updates via bus without refresh; plain staff drag is refused server-side.
- **D:** finish → task appears in intake; seamstress advances it from her /floor view;
  dashboard load counts and overdue flag verified from Postgres, not just the screen.
- New checks appended to `scripts/verify.sh`; `docs/walkthrough.md` gains Acts 7–9
  (customer portal, dispatch board, atelier).

## Out of scope (unchanged verdicts)

Israeli PSP / deposits, WhatsApp, weekly roster, SOS paging, Enterprise anything — still
phase-map rows in `docs/scorecard.md`. MODRYN repo remains read-only reference.

## User actions owed

1. Twilio: Israel-capable sender number (real-SMS proof blocks only on this).
2. Nothing else — all other credentials/config exist locally.
