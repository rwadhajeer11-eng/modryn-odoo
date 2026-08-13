# Epic: the walk-in queue learns who is holding the phone

_Opened 2026-08-13 on `feature/walkin-verification`. Baseline: `scripts/verify.sh`
328 passed / 0 failed / 2 skipped, and `qa/` 18/18 — both unchanged after the work landed. The
specs were written against HEAD while the implementation was built in parallel, so each one
describes the problem as it stood, not the diff that closed it._

## The question this epic answers

`/queue/checkin` asks a woman for her name and her phone number and gives her a ticket. It never
finds out whether the number is hers. Everything downstream — the de-dupe that protects her place,
the texts that cost money, the ticket URL that carries her name and her state — is keyed on a
nine-digit string anyone standing outside the shop can type.

This epic puts a six-digit code between the form and the ticket, opens the same door for staff at
the desk, and makes the queue say the one thing it has never said: *you are in the line*.

**Almost none of it is new code.** `modryn.otp.code` already ships — HMAC-SHA256 hashed and salted
with `database.secret` (`modryn_portal/models/otp.py:39-45`), phone-bound, five-minute TTL
(`otp.py:11`), five attempts counted before the compare (`otp.py:88`, `:95`), three sends per hour
per number (`otp.py:13`), single-use burn (`otp.py:103`), nightly GC
(`modryn_portal/data/ir_cron_data.xml:7-11`). `normalize_il_phone` already turns `052-123-4567`
into E.164 (`modryn_portal/models/sms.py:21`). `send_async` → `modryn_sms_outbox` → cron already
exists (`sms.py:123`). `/my/login` → `/my/verify` already runs the exact flow being mirrored
(`modryn_portal/controllers/portal.py:92-146`). And `modryn_queue_poc` already depends on
`modryn_portal` (`__manifest__.py:22`), so none of it needs a manifest change to reach.

The work is wiring, one state default, two SMS bodies and one `<a>`.

## What is open today

| | Where | What it does |
|---|---|---|
| **Ticket hijack** | `queue_entry.py:95-101` | `modryn_check_in` searches by phone and **returns the existing open ticket**. `main.py:38` then 303s to `/q/<access_token>`. Type a stranger's number and the response hands you her live ticket URL; the page behind it carries her name, her state, her stylist and a `/book` link pre-filled with her name and phone (`main.py:57-62`). No session, no code, no cookie. One POST |
| **Queue flooding** | `main.py:16-38` | Every POST that clears two `strip()` checks writes a row. Two hundred invented numbers is two hundred `waiting` rows and a floor board nobody can read |
| **SMS amplification** | `queue_entry.py:133-144` | Every accepted entry is worth outbound messages through `_send`, at the boutique's expense, for free |

Two things in that table are commonly stated wrong, and both would be believed:

**"There is no rate limiting anywhere in the module."** True of the Python. False in production —
`deploy/nginx/modryn-site.conf:177-178` already limits `~ ^(/[^/]+)?/queue/checkin/submit$` to
`modryn_post` 10r/m burst 5 per client IP. Flooding was already bounded per *source address*. What
this epic adds is the per-**number** axis, which no IP limit can supply.

**"Each entry can fire three texts."** Two is the ceiling. `_send` has three call sites, but
`modryn_redirect` only acts on `pending`/`waiting` (`queue_entry.py:117`) and `modryn_call` writes
`state='called'` before its send (`queue_entry.py:161`), so redirect and your-turn are mutually
exclusive for any one row. The reachable maxima are next+turn or next+redirect.

Neither correction makes the door less open. They make the fix describable.

## Two settled decisions, reversed

Both are recorded in `.memory/decisions.md`. Both are being broken deliberately. `.memory/` exists
so that arriving at the opposite conclusion means re-running an argument that already happened —
so the reversals are quoted here rather than left for someone to discover as a contradiction.

### `.memory/decisions.md:75-77` — the acceptance gate

> **The acceptance gate is invisible.** Staff accept an arrival into the line or suggest booking
> instead. She is never told she was turned away — her page simply becomes a warm invitation to
> book. Rejection is never surfaced.

**What it bought:** a hold. A manager could watch an arrival land and keep her out of the line
while deciding, unobserved.

**What is lost:** exactly that. A verified check-in goes straight to `waiting`; there is no state
between arriving and being in the line. A manager who wants to send someone to `/book` now does it
*after* that person is counted in the queue and already holds a ticket that told her so.

**Why the trade is taken:** the judgement the gate encoded — is this a real person, really here —
is now made by an SMS she has to read, before any row exists. What remained of the accept step was
a manager moving a row from a state nobody sees to a state nobody sees.

**What survives:** the second sentence, intact. `modryn_redirect` already accepts a `waiting` row
(`queue_entry.py:117`), so rejection stays invisible. Only the moment at which it can happen moves.

### `.memory/decisions.md:79-81` — two texts, maximum

> **Two SMS per walk-in, maximum:** one at you're-next, one at your-turn naming her stylist. Both
> idempotent via notified-at fields. Checking in twice with the same number resumes the same
> ticket rather than issuing a second.

**What it bought:** a hard ceiling that could be checked by counting.

**What is lost:** the number two. Idempotence survives — both stamps stay, the de-dupe stays, and
the join text is *folded* rather than added when she is already first — but the honest new
statement is **three queue texts plus the code, folded to two-plus-the-code in an empty shop**.
Solo bride: code, folded join ("you're in the queue — and you're next"), your-turn. Busy shop:
code, join, you're-next, your-turn. Anyone who wants two back has to give back either the
verification or the confirmation.

**One consequence the arithmetic above omits, named here because no spec's totals include it:**
restoring the manager's "Invite to book" button means a redirected walk-in now receives three texts
(code, join, redirect) — and the redirect body opens "We're fully booked today" a minute after the
join text said she was in the line. The button is still worth restoring; the wording is a product
decision with its own msgid and is not made here.

## The three features

Ordered by dependency. W1 is shippable alone; W2 and W3 each assume it.

| # | Feature | What it closes | Spec |
|---|---|---|---|
| W1 | A code between the form and the ticket | The hijack, outright. Flooding's rows. Amplification bounded per number | [`walkin-1-phone-verification.md`](../specs/walkin-1-phone-verification.md) |
| W2 | The staff can open the door | The QR sign was the only entrance; no staff page linked to the form | [`walkin-2-staff-checkin.md`](../specs/walkin-2-staff-checkin.md) |
| W3 | She is told she is in the line | The silence between the scan and the desk; the pending gate; the button that left with it | [`walkin-3-queue-comms.md`](../specs/walkin-3-queue-comms.md) |

W1 must land first and must not anticipate the other two: it writes its guarantees against row
**count**, never row **state**, so every one of its checks holds unchanged when W3 flips the
default. W2 edits the single redirect line W1 ships. W3 owns the state machine and both new SMS
bodies, and carries the collateral repair — dropping the gate hides the arrivals panel
(`floor_board.xml:62`), and the manager's only "Invite to book" button lives inside it
(`floor_board.xml:86`).

## What this epic deliberately does not do

- **No `purpose` column on `modryn.otp.code`.** Login and check-in now share the code pool and the
  three-per-hour budget, so a login code will verify a check-in — and a login code requested
  *after* a check-in code makes the check-in code answer `wrong_code`, because `verify` takes the
  newest unused row. Both prove the same fact: she is holding the phone. Sloppy, not open.
  **Revisit when** a bride is actually rate-limited by using both flows within an hour, or a third
  flow needs codes.
- **No `phone_verified` field on the entry.** Under D1 every creation path runs through the code,
  so the column would be constant-true and would document nothing. **Revisit the day** a bypass is
  introduced.
- **No deletion of the arrivals panel, `modryn_accept`, or `/floor/accept`.** Dead for new entries,
  and still the only way to clear legacy `pending` rows. **Revisit when** no tenant holds one —
  which is sooner than the in-tree comment claiming "bella and noga hold live rows in it" suggests:
  noga holds none today, and bella's two are in `OPEN_STATES`, so the closing cron rewrites them to
  `expired`. The right reason to keep `pending` in the Selection is the five readers that still name
  it, not the rows.
- **No dedupe short-circuit before the code.** Answering "this number is already queued, here is its
  ticket" at submit time is precisely the hijack this epic closes, wearing a helpful face. A
  re-scan therefore costs one code — bounded by the same three-per-hour budget. **Revisit: never.**
  If this reappears as a performance or friction fix, it is the bug coming back.
- **No staff bypass of the code.** D1, taken explicitly over a staff-only branch. At the desk she
  reads the six digits aloud. A bypass would be the only path into the queue that does not prove
  the number, and every guarantee above would have to be restated as "unless staff did it".

## How this gets verified

**noga only.** bella holds live `modryn.twilio.*` credentials and would actually send; noga has
none, so `_send_now` falls through to `_logger.info('[modryn.sms] (no Twilio configured) …')`
(`sms.py:160`) — which is also where the six digits are read during a manual run. `qa/lib/guard.js`
enforces this for the browser suite by refusing any tenant carrying those parameters.

**Restart, not upgrade.** New routes and new Python mean a full server restart; registry
signalling does not re-import Python. XML and views alone go with `-u <module> --stop-after-init`.
Only one server can hold 8069 and the shared databases, so this work is strictly serial.

**Translations are exported, never written.** Trap #9: a QWeb unit is the inner HTML of the block,
markup included. Export the POT with Odoo, re-key with `scripts/sync_translations.py` — then
`git diff --stat -- '*.po'` and `git checkout --` every addon you did not touch, because that
script rewrites all eight.

**Done means:** `MODRYN_DEMO_PASSWORD=modryn2026 ./scripts/verify.sh` reports **≥328 passed,
0 failed, 2 skipped** and the count has not silently dropped; each spec's acceptance table runs
green on noga; and `select count(*) from modryn_queue_entry` is unchanged after a submit that never
reaches the code step — with its control, because an absence passes for free on a broken build.
