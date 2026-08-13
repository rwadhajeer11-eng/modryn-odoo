# W3 — she is told she is in the line

_The acceptance gate goes, and the queue gains the one message it never had. Reverses two
settled decisions in `.memory/decisions.md`. W1 owns the code, W2 owns the staff door; this
spec owns what the queue says, and when._

## The problem

Between the scan and the desk, the queue says nothing to her.

`modryn_check_in` (`queue_entry.py:90-116`) creates a row, `create()` pushes it to the floor board
(`:60-66`), and that is the end of the conversation. The first text she can receive is
`_notify_next_in_line` (`:182-192`), and that only runs when someone **ahead** of her is called
(`:211`) or finished (`:220`) — or when a manager taps "Add to the line" (`modryn_accept`,
`:118-123`). With two people in front of her, that is however long those two take, in silence,
after she has handed over her phone number. Her ticket page says "We'll be with you soon"
(`templates.xml:72`); her phone says nothing, and the phone is the thing she is holding.

The gate that produced the silence is going anyway. W1 makes her prove she holds the number before
any row exists, so the judgement the `pending` panel encoded — is this a real person, really here —
is now made by an SMS she has to read, before the row exists at all. What is left of the accept
step is a manager tapping a button to move a row from a state nobody sees to a state nobody sees.

So the row is created in the line, and the moment it is created she is told.

## Two recorded decisions, reversed

Both are settled decisions in `.memory/decisions.md`. Both are being broken. Neither reversal is
free, and the cost of each is something a boutique will notice.

### `.memory/decisions.md:75-77`

> **The acceptance gate is invisible.** Staff accept an arrival into the line or suggest booking
> instead. She is never told she was turned away — her page simply becomes a warm invitation to
> book. Rejection is never surfaced.

**What it bought:** a hold. A manager could see an arrival land and keep her out of the line while
deciding, without her ever knowing there was a decision.

**What is lost:** exactly that. After this change there is no state between arriving and being in
the line. A manager who wants to send someone to `/book` must now do it from the queue itself,
after that person is already counted in it and already holds a ticket that told her she is in the
queue. The window in which a boutique could think about it, unobserved, is gone.

**What survives, and matters more:** the second sentence, intact. She is still never told she was
turned away. `modryn_redirect` (`:125-133`) already accepts a `waiting` row (`:127`), and the ticket
template renders `redirected` and `expired` through the same warm exit (`templates.xml:84-93`).
Rejection stays invisible; only the moment at which it can happen moves.

### `.memory/decisions.md:79-81`

> **Two SMS per walk-in, maximum:** one at you're-next, one at your-turn naming her stylist. Both
> idempotent via notified-at fields. Checking in twice with the same number resumes the same
> ticket rather than issuing a second.

**What it bought:** a hard ceiling that could be checked by counting. Two per walk-in, whatever
happened on the floor.

**What is lost:** the number two. Idempotence survives — both stamps stay, the de-dupe stays, and
the join text is folded rather than added when she is already first — but the ceiling is now three
or four.

## The arithmetic, done out loud

**Before** — per walk-in, maximum 2:

| # | Text | Fired by |
|---|---|---|
| 1 | "You're next — we'll be with you in a moment." | `_notify_next_in_line` (`:182-192`) |
| 2 | "We're ready for you — %(stylist)s is waiting." | `modryn_call` (`:194-212`) |

**After, solo bride** — she scans into an empty line. **3 total:**

| # | Text | Fired by |
|---|---|---|
| 1 | the 6-digit code | `otp.issue` → blocking `send` (`otp.py:72`) |
| 2 | "You're in the queue — and you're next. We'll be with you in a moment. Your ticket: …" | `_notify_joined`, **folded** (`:170-175`) |
| 3 | "We're ready for you — Dana is waiting." | `modryn_call` |

The separate you're-next never fires: the fold stamps `next_notified_at` (`:175`), and
`_notify_next_in_line`'s once-ever guard (`:190`) reads that stamp and declines.

**After, busy shop** — at least one person waiting ahead of her. **4 total:**

| # | Text | Fired by |
|---|---|---|
| 1 | the 6-digit code | `otp.issue` |
| 2 | "You're in the queue. We'll text you when you're next. Your ticket: …" | `_notify_joined`, plain (`:176-180`) |
| 3 | "You're next — we'll be with you in a moment." | `_notify_next_in_line`, when the one ahead is called (`:211`) or finished (`:220`) |
| 4 | "We're ready for you…" | `modryn_call` |

So: **2 → 3 or 4.** One of those is the code, which is not a queue text at all — it is the price of
not handing a stranger her live ticket. The honest statement of the new ceiling is *three queue
texts plus the code, folded to two-plus-the-code when the shop is empty*, and anyone who wants the
number two back has to give back either the verification or the confirmation.

## The change

### The default flips; `pending` stays in the selection

`state` default `pending` → `waiting` (`queue_entry.py:40-52`, `:49`). The value `pending` **stays
in the selection**, and the reason is not the one that first suggests itself.

The tempting reason is "the tenants hold live rows in it". Measured:

```
$ for db in bella noga; do psql -d $db -tAc \
    "select state, count(*) from modryn_queue_entry group by state order by 1"; done
bella:  called|1  done|2  expired|3  pending|2
noga:   expired|1  redirected|1
```

bella holds two. **noga holds none.** And bella's two are open rows, so the closing cron
(`data/ir_cron_data.xml`, daily, verified active with `nextcall 2026-08-13 16:30`) rewrites them to
`expired` tonight. That reason has a shelf life measured in hours — which matters, because it is
currently written into the source as settled fact (`queue_entry.py:37-39`, "bella and noga hold
live rows in it"). Half of it is already false and the other half expires at closing time. Correct
the comment to the reason that does not rot.

The durable reason is the code that still reads the value, none of which this spec touches:

| Reader | Where |
|---|---|
| `OPEN_STATES` — the de-dupe and the closing sweep | `queue_entry.py:18`, `:106`, `:230` |
| `modryn_redirect`'s filter | `queue_entry.py:127` |
| the board's arrivals search, and its payload key | `floor.py:51`, `:127` |
| `verify.sh` asserting that key exists | `scripts/verify.sh:436` |
| four branches of the ticket page | `templates.xml:56, 71, 95, 106` |

Removing a value from a `Selection` while any row or any branch still names it buys nothing and
costs a write error the first time a legacy row is touched. Keeping it costs one line.

### `modryn_check_in` creates into the line and speaks

The de-dupe is unchanged: an existing open ticket is returned at `:104-109` and **sends nothing**.
The create branch gains one call — `entry._notify_joined()` (`:115`) — and `_notify_joined`
(`:156-180`) makes the whole decision in one place:

```python
first = self.sudo().search([('state', '=', 'waiting')], order='create_date asc', limit=1)
if first == self:
    self._send(<"…and you're next…" + ticket link>)
    self.next_notified_at = fields.Datetime.now()
else:
    self._send(<"…we'll text you when you're next." + ticket link>)
```

Four things about that, each of which is why it is written this way rather than the obvious way:

**The fold exists because `_notify_next_in_line` must now fire on create.** Accept no longer
happens, and `modryn_accept:122` was one of only three call sites; the other two (`:211`, `:220`)
both mean *someone ahead moved*, which by definition never applies to the first person in an empty
shop. Leave it alone and the solo bride gets a join text and then nothing at all until her turn.
Call `_notify_next_in_line()` after `create` instead and she gets two texts one second apart saying
nearly the same sentence. The fold is the third option: decide once, say it once.

**The stamp is what makes the fold safe.** Without `next_notified_at` at `:175`, the folded text
tells her she is next while the column stays NULL — so the next `action_done` (`:218-220`) or
`modryn_call` (`:194-212`) on **any** row re-selects her at `:189`, reads an empty stamp at `:190`,
and sends "You're next — we'll be with you in a moment." for real. One move reaches it: she is
alone and waiting, a staff member closes a walk-in left over from the morning, and her phone buzzes
with a sentence she read four minutes ago.

**The fold's search must stay character-identical to `:189`.** Same domain, same order, same
`sudo`. If those two ever disagree about who is first, the disagreement is silent — the module
would hold two opinions of the word "first", and the stamp lands under one of them.

**The ticket link is in both branches, always.** She may never have seen it: in staff mode (W2) the
redirect goes to `/floor` and she never lands on `/q/<token>` at all. The SMS is the only copy she
gets.

The wording deliberately reuses her ticket page's own sentences — "we'll be with you in a moment"
(`templates.xml:68`) — so the text and the page do not describe one state in two voices. Her page
now agrees with the text on arrival for the first time: created at `waiting` and first in line
makes `is_next` true on the very first render (`main.py:178-182`), where a `pending` row used to
show the generic wait.

### Which door each text goes through

The join text goes through `_send` (`:143-154`) like the other three, which means `send_async`
(`sms.py:123-141`) → `modryn_sms_outbox` → the drain cron. Not the blocking sender: the request it
rides on is **hers**, submitted at `/queue/verify` with her thumb still on the screen, and
`SEND_TIMEOUT = 10` (`sms.py:11`) is a long time to hold a check-in open for a message she is not
waiting for.

The code keeps the blocking `send` (`otp.py:72`), for the opposite reason, and one `verify.sh`
already guards (`:768-771`): the code has to exist before the form that asks for it renders.

`_send`'s own comment (`:147-148`) enumerates three texts — "redirect, you're next, and your turn".
It is now four. Fix it, or it becomes an inventory that quietly lies.

## The de-dupe now costs a code

Before, a re-scan was free: `modryn_check_in` found the open row, handed back the same ticket, sent
nothing. After, the code is issued at `/queue/checkin/submit` (`main.py:91`) — before any row is
looked at — so a re-scan costs exactly one SMS. The join text stays behind the create branch
(`:115`), so the early return at `:108-109` still sends nothing. **One code, no queue text, same
ticket.**

That is the correct trade, not a regression to fix later. Short-circuiting the de-dupe before the
code — "this number is already queued, here is its ticket" — *is* the hijack the verification exists
to close: a stranger typing her number would get her live ticket back without ever touching her
phone. `main.py:88-90` says so in place.

The cost is bounded by the budget the codes already carry: `MAX_SENDS_PER_HOUR = 3` (`otp.py:13`,
enforced `:55-60`), per number. The fourth re-scan inside an hour is answered with "Too many codes
sent to that number" and nothing leaves the box. That budget is now **shared with portal login**,
which is a real consequence and is listed below as deliberately skipped.

## The button that leaves with the panel

The arrivals panel self-hides on `t-if="state.pending.length"` (`floor_board.xml:62`). With no new
row ever entering `pending`, it is empty on every board that has cleared its legacy rows — and it
takes two buttons with it. "Add to the line" (`:82`) is the one being removed on purpose.
**"Invite to book" (`:86`) is not**, and it exists nowhere else: `redirectPending` has exactly one
call site (`floor_board.js:405-407`). Dropping the gate would silently remove the manager's only
way to say *not today*.

The backend never needed the panel. `modryn_redirect` filters on `('pending', 'waiting')` (`:127`)
and `/floor/redirect` (`floor.py:246-256`) browses whatever id it is handed. So the restoration is
one button on the waiting card's existing manager-only actions row (`floor_board.xml:144`), wired
to the handler that already exists:

```xml
<button class="btn btn-sm btn-outline-dark"
        t-on-click="() => this.redirectPending(entry.id)">
    Invite to book
</button>
```

No Python changes. `modryn_accept` and `/floor/accept` (`floor.py:231-244`) are left exactly as they
are: dead for new arrivals, and still the only way to clear a legacy `pending` row.

## Translation

Two new msgids, both `_()` on a literal in `queue_entry.py`, so the extractor sees them directly —
this is not the QWeb inner-HTML case (`.memory/odoo-traps.md` #9). The rule survives anyway:
**never hand-write the msgid.** Export the POT with Odoo, re-key with
`scripts/sync_translations.py`, then run `git diff --stat -- '*.po'` and `git checkout --` every
addon you did not touch, because that script rewrites all eight. `he.po` and `ar.po` already carry
the sibling these two sit next to (the Python `You're next — we'll be with you in a moment.` term
at `he.po:268`).

## Acceptance

Every run below is on **noga**. bella holds live `modryn.twilio.*` credentials and would actually
send; noga has none, so `_send_now` falls through to the log (`sms.py:158-161`) — which is also
where the six digits are read during a manual run. New Python means a **full server restart**, not
`-u … --stop-after-init`.

| # | Check | Required |
|---|---|---|
| 1 | `grep -c "default='waiting'" addons/modryn_queue_poc/models/queue_entry.py` | `1` |
| 2 | `grep -c "('pending', 'Pending')" addons/modryn_queue_poc/models/queue_entry.py` | `1` — the value stayed |
| 3 | `grep -c "modryn.sms'\].send(" addons/modryn_queue_poc/models/queue_entry.py` | `0` — no queue text reaches the blocking sender |
| 4 | Check a fresh number into an **empty** line, then `psql -d noga -tAc "select state, next_notified_at is not null from modryn_queue_entry order by id desc limit 1"` | `waiting\|t` — created into the line, folded and stamped |
| 5 | Same run: `psql -d noga -tAc "select count(*) from modryn_sms_outbox where create_date > (now() at time zone 'utc') - interval '2 minutes'"` | `1` — one join text. The code never touches the outbox; `send` is not `send_async` |
| 6 | Same run: `psql -d noga -tAc "select body from modryn_sms_outbox order by id desc limit 1"` | contains `/q/` **and** "and you're next" |
| 7 | Now `action_done` an unrelated older walk-in and re-count outbox rows for her number | **unchanged** — the stamp held. A second you're-next is the exact failure this check exists for |
| 8 | Check in B behind A: `psql -d noga -tAc "select next_notified_at is null from modryn_queue_entry order by id desc limit 1"` | `t`, and B's body reads "We'll text you when you're next", not the folded form |
| 9 | Call A, then re-check B | B gains exactly one outbox row and a non-null stamp |
| 10 | Re-scan with the same number: complete the code, land on the same `/q/<token>` | `select count(*) from modryn_queue_entry where phone='…'` is still `1`, and the outbox count for that number is **unchanged** — one code, no second join text |
| 11 | `grep -c "Invite to book" addons/modryn_staff/static/src/floor/floor_board.xml` | `2` — panel and waiting card |
| 12 | A manager on `/floor` sees "Invite to book" on a waiting card (browser, not curl — the board is OWL) | present, and clicking it moves that entry to `redirected` |
| 13 | `MODRYN_DEMO_PASSWORD=modryn2026 ./scripts/verify.sh` | ≥ 328 passed / 0 failed / 2 skipped. `verify.sh:436` stays green: the `pending` key survives an empty list (`floor.py:127`) |

## What this does not do

- **It does not delete the arrivals panel or `/floor/accept`.** bella held two `pending` rows when
  this was written. Deleting the only door while someone is still behind it.
- **It does not give `modryn.otp.code` a `purpose` column.** The 3-per-hour budget is now shared
  between portal login and check-in, so a code issued at `/my/login` will verify at `/queue/verify`.
  Both prove the same fact — she is holding the phone — so this is sloppy, not a hole. Add
  `purpose` plus two domain leaves when a bride is actually rate-limited by using both flows in one
  hour, or when a third flow needs codes.
- **It does not make queue texts language-aware.** Bookings render in the language she booked in
  (`modryn_lang`, `booking_comms.py:79-82`); `modryn.queue.entry` has no such column, so every queue
  text renders in the language of whatever request triggered it. For the new join text that is the
  language she just filled the form in — right, by accident. For you're-next and your-turn it is
  the **staff member's** language, which is wrong, and was wrong before this change. Add a column
  when a boutique runs a mixed-language floor and someone says so.
- **It does not mark a staff-mode check-in differently.** She gets the same join text with the same
  link, which is the point: she may never have seen `/q/<token>`, so the link is always in the body.
- **It does not touch the two live bugs in the check-in form** — the phone error that is set but
  never rendered, and the `client_type` select that forgets its value on a re-render. Both belong
  with the form, which W1 and W2 own.
- **It does not change the closing cron.** `_modryn_expire_open_tickets` (`:223-232`) sweeps
  `OPEN_STATES`, which still includes `pending`, so legacy rows keep expiring on the same schedule.
