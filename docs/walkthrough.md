# Walkthrough — a real boutique day

Follow this top to bottom and you exercise every part of the system: two isolated
boutiques, a themed storefront, a customer booking a specific dress, an owner hiring a
saleswoman, a manager assigning her, and a walk-in arriving off the street.

Every step says what you should see. If something differs, that is a bug — say so.

> **Status of this document:** each step below was executed against a live server before
> this file was written; the "expect" lines are what actually happened, not what was
> intended. The verification script `./scripts/verify.sh` re-runs the machine-checkable
> half of it in about ten seconds.

---

## 0. Start it up

```bash
cd /Users/mrwen/Documents/Github/modryn-odoo
source .venv/bin/activate
./odoo/odoo-bin server -c odoo.conf --http-interface=127.0.0.1
```

Two boutiques, each a full tenant on its own subdomain:

| | |
|---|---|
| http://bella.localtest.me:8069 | Bella Bridal |
| http://noga.localtest.me:8069 | Noga Couture |

### The cast (seeded by `scripts/seed_staff.py`)

All demo passwords are `modryn2026`.

| Person | Role | Level | Username | Account |
|---|---|---|---|---|
| מירי לוי | מוכרת | **owner** | `miri` | internal (1 paid seat) |
| שרה כהן | מוכרת | **manager** | `sara` | portal (free) |
| רותם אברהם | מוכרת | staff | `rotem` | portal (free) |
| אורלי דוד | תופרת | staff | `orly` | portal (free) |
| נועה מזרחי | קבלת קהל | staff | `noa` | portal (free) |

Noga has its own separate people (`tamar`, `yael`, `dana`) — they cannot see Bella and
Bella's cannot see them.

---

## Act 1 — The owner hires a saleswoman

**1.1 Sign in as the owner.** Go to `/staff/login` and enter `miri` / `modryn2026`.

*Expect:* a cream-and-gold login card, Hebrew, right-to-left — **not** Odoo's purple login
page. After signing in you land on `/manage/staff`.

**1.2 Invent a new role.** Go to **תפקידים** and type any job title the boutique needs —
`מלווה כלות`, say — then **הוספה**.

*Expect:* it appears in the list immediately. This is the whole point of roles being
*data*: no developer, no module upgrade, no deploy. Now add the **same name again**.

*Expect:* refused with **התפקיד כבר קיים** in red, and the list still shows one of it.
(Worth knowing: this check is Python, not a SQL unique constraint — see trap 4 and 5 in the
[README](../README.md) for why a SQL one silently does nothing here.)

**1.3 Hire someone.** **צוות** → **הוספת עובדת**. Name `יעל שמש`, phone `052-5559999`,
role `מוכרת בכירה`, level `עובדת`, username `yaels`, password `modryn2026`.

*Expect:* she appears in the staff list showing her role, level, username and **פנויה**. A
**portal** account was created for her in the same step — she can sign in, but she cannot
reach `/odoo`.

(This exact employee already exists if you have run the walkthrough before; pick another
name rather than reusing the username, which is enforced unique.)

Verify the account type is right (this is the Enterprise-cost decision made real):

```bash
psql -d bella -tAc "
select u.login, string_agg(g.name->>'en_US', ', ')
from res_users u
join res_groups_users_rel r on r.uid = u.id
join res_groups g on g.id = r.gid
where u.login = 'yaels' group by u.login"
```
*Expect:* `Portal` plus `Staff` — and **not** `Internal User`.

---

## Act 2 — A customer books a dress

Open a **private window** (so you are the anonymous public, not the owner).

**2.1 Browse.** `http://bella.localtest.me:8069/shop`

*Expect:* cream background, gold buttons, Frank Ruhl Libre headings, RTL Hebrew, ₪ prices.
One dress shows **מחיר בתיאום** instead of a price — that is the per-dress
price-visibility switch.

**2.2 Pick a dress** → **קביעת תור למדידה**.

*Expect:* the size picker lists 36 / 38 / 40, with **40 marked (אזל המלאי)** because its
stock is zero. The dress and its photo ride along into the booking form.

**2.3 Try to cheat.** Fill in name, phone and a slot, but leave the terms checkbox
**unticked**, and submit.

*Expect:* refused, with a red Hebrew error under the checkbox, and your other answers
preserved. Nothing was written:

```bash
psql -d bella -tAc "select count(*) from calendar_event where modryn_is_booking"
```
The count must not have gone up. The `required` attribute is a courtesy; the real check is
server-side.

**2.4 Book properly.** Tick the terms, submit.

*Expect:* a confirmation page showing the date, the time, the dress **with its size**, and
your phone number.

**2.5 Check what was stored.**

```bash
psql -d bella -tAc "
select ce.id, ce.start, ce.modryn_booking_type, ce.modryn_customer_phone,
       u.login as organizer
from calendar_event ce join res_users u on u.id = ce.user_id
where ce.modryn_is_booking order by ce.id desc limit 1"
```

*Expect two things.* The `start` is stored in **UTC** — a 10:00 Israeli appointment is
`07:00` in August, which is correct because Israel is UTC+3 under DST. And the organizer is
**`miri`, never `public`** — that was a real defect (`sudo()` elevates privileges but does
not change identity, so every booking used to be owned by the anonymous visitor).

**2.6 The slot is now gone.** Reload `/book`.

*Expect:* the slot you just took is absent from Bella's list — but still offered on
`http://noga.localtest.me:8069/book`, because Noga is a different database.

---

## Act 3 — The manager assigns someone to her

**3.1 Sign in as the shift manager.** `/staff/login` → `sara` / `modryn2026`.

*Expect:* she lands on `/floor`, not on `/manage` — she is a manager, not the owner. Try
`/manage/staff` as her: refused. She cannot invent roles or hire people.

**3.2 Look at the floor.** `/floor` shows three panels: the walk-in queue, today's
bookings, and the staff roster with every saleswoman marked **פנויה**.

**3.3 Assign.** On the booking from Act 2, assign **רותם**.

*Expect:* the booking now names רותם. While that appointment's hour is current, רותם flips
to **תפוסה** on the roster — nobody typed a status anywhere. Occupancy is derived from live
assignments, so it cannot drift the way a manual toggle does.

```bash
psql -d bella -tAc "
select e.name, ce.name from calendar_event ce
join hr_employee e on e.id = ce.modryn_employee_id
where ce.modryn_is_booking and ce.modryn_employee_id is not null"
```

---

## Act 4 — A walk-in arrives

**4.1 The sign in the lounge.** `/queue/sign` shows a QR code. It points at
`/queue/checkin`. (The QR itself is Odoo's built-in barcode endpoint — no custom code.)

**4.2 She checks in.** Open `/queue/checkin` on a phone-sized window: name `דנה אברהם`,
phone, כלה. Submit.

*Expect:* she is told her position in the queue. The position is computed when read, never
stored — a stored number goes stale the moment someone ahead of her is served.

**4.3 The board already knows.** Without touching Sara's `/floor` tab, look at it.

*Expect:* דנה is **already there**. No refresh. To prove it is genuinely live rather than a
lucky reload, check someone in from outside the browser entirely and watch the open tab:

```bash
cd /tmp && rm -f q.txt
CSRF=$(curl -s -c q.txt "http://bella.localtest.me:8069/queue/checkin" \
  | grep -oE 'name="csrf_token"[^>]*value="[^"]*"' \
  | grep -oE 'value="[^"]*"' | sed 's/value="//;s/"//' | head -1)
curl -s -b q.txt -X POST "http://bella.localtest.me:8069/queue/checkin/submit" \
  --data-urlencode "name=לקוחה מהרחוב" --data-urlencode "phone=053-1112222" \
  --data-urlencode "client_type=evening" --data-urlencode "csrf_token=$CSRF" -o /dev/null
```

---

## Act 5 — The manager dispatches

**5.1 Send someone over.** On `/floor`, assign **אורלי** to דנה.

*Expect:* דנה moves to "called" and names אורלי; אורלי flips to **תפוסה** and the roster
says who she is with. Every open floor tab updates.

**5.2 Finish.** Mark the walk-in **סיום**.

*Expect:* she leaves the queue and אורלי returns to **פנויה** — again, derived, not typed.

---

## Act 6 — Prove nothing leaked

**6.1 Two boutiques, one server.**

```bash
./scripts/verify.sh
```

This re-checks all of it mechanically: isolated catalogs, the theme compiled, the
out-of-stock size, both booking paths, the organizer fix, Arabic, the QR endpoint, staff
and portal accounts, and that anonymous visitors are refused at `/floor`, `/manage/staff`
and `/manage/roles`.

**6.2 Levels actually mean something.** Verified end to end:

| Signed in as | `/floor` | `/manage/staff` | `/odoo` |
|---|---|---|---|
| `rotem` (staff, portal) | 200, read-only — no assign controls | **404** | **303 → `/my`**, never the back office |
| `sara` (manager, portal) | 200, with assign controls | **404** — she cannot hire or invent roles | 303 → `/my` |
| `miri` (owner, internal) | 200 | 200 | back office available |

Portal accounts are structurally barred from Odoo's back office — which is why staff cost
nothing under Enterprise and never meet purple Odoo chrome.

And the restriction is real, not cosmetic. Signed in as `rotem`, calling the manager-only
endpoint directly returns `{"error": "forbidden"}`:

```bash
curl -s -b rotem_cookies.txt -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"call","params":{"entry_id":1,"employee_id":3}}' \
  "http://bella.localtest.me:8069/floor/assign/queue"
```
Hiding a button is not a permission; every action route re-checks the group server-side.

**6.3 The other boutique is untouched.** Everything above happened on Bella. Noga still has
its own dresses, its own people and an empty queue.

---

## Act 7 — A returning customer manages her own appointment

**7.1 Sign in with a phone number.** Storefront header → **התורים שלי** (or `/my/login`).
Enter the phone used at booking. *Expect:* "we sent a 6-digit code". With Twilio configured
the SMS is real; in dev the code appears in the server log
(`grep modryn.sms /tmp/odoo*.log`).

**7.2 Wrong code first.** *Expect:* a Hebrew error, and after 5 failed tries the code is
burned. The right code lands you on **My appointments** — upcoming and past, dress and size
shown. No password was ever created, and no Odoo user account either.

**7.3 Cancel one.** *Expect:* the boutique's cancellation terms shown before you confirm;
after confirming, the appointment moves to Past marked Cancelled — and its slot is
**offered again** on `/book`. Verify like a skeptic:

```bash
psql -d bella -tAc "select id, modryn_cancelled_at, modryn_cancelled_by
                    from calendar_event where modryn_is_booking"
```

**7.4 Languages.** The storefront, booking and portal work in **he / ar / en** via the
header language switcher — English renders left-to-right with the same theme.

## Act 8 — The dispatch board (drag and drop)

Sign in as `sara` → `/floor`. The board is now: customer cards in the middle (walk-ins +
today's appointments), and the **bench** on the side — every staff member as a chip with
live פנויה/תפוסה.

**8.1 Assign by dragging.** Drag a chip from the bench onto a customer card.
*Expect:* first person dropped becomes **ראשית** (primary, gold chip); drop a second onto
the card body and she joins as a helper (dashed chip); drop someone onto the *primary slot*
and she swaps in — the old primary becomes a helper, never lost. Drag a chip back to the
bench to take her off. Every drop is a real server call with the manager check enforced
server-side; the select dropdown remains as the no-drag fallback.

**8.2 Occupancy is derived.** The moment someone is on a live card — primary *or* helper —
her bench chip flips to תפוסה, and back the moment the customer finishes. Nobody types a
status, so the bench cannot lie.

**8.3 It's live everywhere.** Open `/floor` in a second window; assignments made in one
appear in the other without a refresh (the same websocket the queue already used).

## Act 9 — Fitting done → the workshop

**9.1 Finish with a handoff.** Press **סיום** on a walk-in. *Expect:* a modal — "does the
gown need work?" Pick the dress, tick the garment pieces (מכפלת, שובל…), write
instructions, choose a seamstress and a due date — or **Skip** when there's nothing to
alter. Creating lands the task in **Intake** on the workshop board.

**9.2 The owner runs the pieces list.** `/manage/pieces` — garment pieces are data, like
roles: add "הינומה" (veil) and it's immediately available in the finish modal.

**9.3 The seamstress drives her own work.** Sign in as `orly` → `/floor` shows
**התיקונים שלי**: her open tasks with pieces, notes and due dates (overdue in red). She
presses **התחלה** and later **מוכן** herself — which is exactly what keeps the dashboard
honest.

**9.4 The manager sees the whole workshop.** As `sara`/`miri` → `/atelier`: every task by
state, workload per seamstress with overdue counts. Verify against the database, not the
screen:

```bash
psql -d bella -tAc "select state, count(*) from modryn_alteration_task group by state"
```

**9.5 Staff language.** The nav's **English** button flips the staff screens to English
(LTR) and back via **עברית** — per user, stored, and independent of what customers see.

## What this walkthrough does **not** cover

Honest gaps, each sized in [`scorecard.md`](scorecard.md):

- **Customers cannot log in yet.** Phone + SMS OTP and "my bookings" is stage 2. Today a
  customer books as a guest; her phone is always collected, but she cannot come back and
  manage the appointment herself.
- **No availability engine.** Slots are a fixed Sun–Thu 10:00–18:00 grid. Opening hours,
  capacity, holidays and per-staff calendars are the Phase-2 booking engine.
- **No deposit.** No Israeli payment provider (Grow/Meshulam/Tranzila) exists in Odoo;
  that is a custom addon.
- **No SMS or WhatsApp.** Nothing is actually sent to anyone.
- **No roster, no SOS, no alterations capacity.** PRD §9–§11 are untouched.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| Storefront renders LTR / unstyled | `rtlcss` missing, or a SCSS error killed the whole bundle. Check the server log for `Error:` — LibSass rejects modern `rgb(a b c / d)` syntax. |
| `/queue/sign` QR is a broken image | `rlPyCairo` not installed (it is absent from Odoo's `requirements.txt`). |
| Any page 500s right after an edit | Restart the server; Odoo caches compiled assets and the registry in memory. |
| `python: command not found` behaviour | A shell alias shadows the venv. Always use `python3`. |
| Changed an addon but nothing happened | Module data is only re-read on upgrade: `odoo-bin server -c odoo.conf -d bella --db-filter='^bella$' -u modryn_staff --stop-after-init`. |
