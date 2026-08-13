# W1 — a code between the form and the ticket

_Closes the ticket hijack outright. Bounds the other two abuse paths on a second axis rather than
closing them, and says which is which._

## The door that is open

`/queue/checkin` is `auth='public'` (`controllers/main.py:9`), which is correct — she arrives by
scanning a sign and there is nobody to log in as. What is not correct is what happens next.
`modryn_check_in` looks up the phone number and, if an open ticket already exists for it, **returns
that ticket** (`models/queue_entry.py:95-101`). The controller then redirects to
`/q/<access_token>` (`main.py:38`).

So the form does not only create. It also *retrieves*, keyed on a nine-digit string anyone can
type. Enter a stranger's number and the 303 hands you her live ticket URL, and the page behind it
carries her name, her state, her stylist's name, and a "book a fitting" link pre-filled with her
name and phone (`main.py:57-62`). No session, no code, no cookie. One POST.

The dedupe is not a bug. It is load-bearing: without it a second scan puts her in the line twice
and she loses her real place. The bug is that nothing between the form and the dedupe establishes
that the person typing the number is holding the phone.

## Three abuse paths, and what a code actually does to each

| | Open today | After W1 |
|---|---|---|
| **Ticket hijack** | Type any number, receive her ticket URL, name and state | **Closed.** The dedupe is never consulted until a code sent to that handset comes back |
| **Queue flooding** | One POST per fake number, one row each, no per-number cap in Python | **Rows closed, sends bounded.** Zero rows are written before verification; issuance is capped per number |
| **SMS amplification** | Every accepted entry is worth up to two texts through `_send` (`queue_entry.py:133-144`), for free | **Bounded, not closed** — and it gets a new, cheaper message of its own |

Each of those deserves a sentence that does not flatter the change.

**The hijack is genuinely closed, and by a mechanism worth naming.** The code is not a check on the
ticket. It is a check on the *number*, run before `modryn_check_in` is called at all. The dedupe
survives untouched and still hands back her existing ticket — but only to whoever received the six
digits on that handset. Nothing about the queue's fairness or its re-scan behaviour changes; the
only thing that changes is who gets to ask.

**Flooding stops producing rows, which is the part that mattered.** Today a script with two hundred
made-up numbers produces two hundred `waiting` rows and a floor board nobody can read. After W1 it
produces zero, because the row is created in the verify handler and a code cannot be read off a
handset that does not exist. What it still produces is two hundred outbound messages, which is a
real cost — it has simply moved from the boutique's floor board to a metered bill.

**Amplification is the one to be honest about**, and the arithmetic is smaller than it first looks:
`_send` has three call sites, but `modryn_redirect` only fires for `pending`/`waiting`
(`queue_entry.py:117`) and `modryn_call` only after `called`, so no single entry can reach all three
— two texts is the ceiling. The OTP is itself an SMS, sent on the *unverified* path, so the cheapest
possible attack gets marginally cheaper per request. Two bounds stand against
that, and neither existed on this route before in the form that matters: `otp.issue` refuses a
fourth code to the same number within an hour (`models/otp.py:55-60`, `MAX_SENDS_PER_HOUR = 3`), and
nginx already limits `/queue/checkin/submit` to `modryn_post` 10r/m burst 5 per client IP
(`deploy/nginx/modryn-site.conf:177-178`). The second of those is worth stating plainly because it
is easy to describe this module as having "no rate limiting anywhere" — true of the Python, false in
production. What W1 adds is the **per-number** axis, which no IP limit can supply: one number, three
messages an hour, whatever the source address.

## The flow

The check-in flow's two routes become four. `/q/<token>`, `/queue/sign` and `/queue/channel` are
untouched.

| Route | Method | Does |
|---|---|---|
| `/queue/checkin` | GET | Renders the form. `session.touch()`. Unchanged from `main.py:10-14` |
| `/queue/checkin/submit` | POST | Validates name + phone, `otp.issue(phone)`, stashes the validated values in session, redirects to `/queue/verify`. **Writes no queue row** |
| `/queue/verify` | GET | Redirects to `/queue/checkin` if the session key is absent. `session.touch()`. Renders the six-digit form |
| `/queue/verify` | POST | `otp.verify(phone, code)`; on success pops the session key, calls `modryn_check_in`, redirects to `/q/<token>` |

**The session carries one key, not three.** `modryn_queue_pending` holds a dict of the already
validated `{name, phone_e164, client_type}`. Three separate keys means three pops on the success
path and a half-cleared session the first time someone forgets one; a single key is popped or it is
not. The phone stored is the **normalized E.164 form** returned by `otp.issue` — the verify handler
must not re-parse whatever she originally typed, for the same reason `send_async` stores the
normalized number rather than the raw one (`models/sms.py:138-140`).

**Nothing is trusted from the second POST except the code.** The name and the client type were
validated at submit and are read back out of the session, never out of `post`. A verify handler that
accepted a fresh name would let the code prove one number and the row carry another.

**Popping on success is what makes the guarantee durable.** The dedupe already makes a replayed
verify idempotent, so the pop is not what prevents a duplicate row — it is what makes "abandoning
after the code step leaves nothing behind" true for the back button as well as for the closed tab.

## The error copy, and why it is built inside the handler

`otp.issue` and `otp.verify` return an error *key*, never a sentence (`otp.py:50`, `otp.py:78`).
Seven keys can reach this flow: `invalid_number`, `rate_limited`, `send_failed` from issue;
`no_code`, `too_many_attempts`, `expired`, `wrong_code` from verify. Plus a fallback, because a key
this file has not heard of must still render something.

The mapping is built **inside the handler** with `_()`, not at module level. `portal.py:11-16`
records why the portal's identical dict uses `LazyTranslate` instead: a module-level `_()` runs at
import time with no request language bound, and the obvious repair — `_(ERRORS[key])` — wraps the
*lookup*, which hides every literal from the extractor so the strings never reach a `.po` and stay
English forever. A dict constructed per request sidesteps both: it evaluates with the request
language already bound, and its values are literal arguments to `_()`, which is exactly what the
extractor is looking for.

The copy is deliberately **duplicated** rather than imported from `portal.py:22-31`. Those sentences
are written for a woman alone with her phone ("Please request a code first"); a check-in desk with a
staff member reading the screen is a different room. Sharing the strings would save eight lines and
silently couple two pieces of customer-facing copy that should be free to diverge.

## Two bugs already shipping

Both are in `views/templates.xml` and both get worse the moment re-renders become routine, which is
what this change makes them.

- **The phone error is computed and never displayed.** `main.py:29` sets `errors['phone']`, and the
  phone field (`templates.xml:22-26`) has no `t-if="errors.get('phone')"` block — the name field
  three lines above it does (`templates.xml:18-19`). A bad number today re-renders the form with no
  visible reason. She retypes the same number.
- **The client type does not survive a re-render.** The `<select>` (`templates.xml:30-33`) hardcodes
  both `<option>`s with no `t-att-selected` against `values.get('client_type')`, so any validation
  failure silently resets an evening client to bride. The room selector on the floor board already
  does this correctly (`modryn_staff/static/src/floor/floor_board.xml:130-132`) and is the shape to
  copy.

## The verify template

New template, mirroring `modryn_portal/views/portal_templates.xml:34-64` — the same page for the
same job, and it already carries the details that are easy to omit: `inputmode="numeric"` so phones
show a keypad, `autocomplete="one-time-code"` so iOS and Android offer the SMS autofill,
`maxlength="6"`, `dir="ltr"` on a field that is digits in every language, and a way back for someone
who typed the wrong number.

It `t-call`s `website.layout`, matching `checkin_form` (`templates.xml:6`). W2 replaces the layout
call on both templates; W1 must not anticipate it.

`session.touch()` runs on both GET handlers. She reaches `/queue/verify` by redirect and therefore
already has a session, so the trap-6 reasoning does not obviously apply to the second page — but
trap #6 records that its own cause is **unconfirmed** and that the fix is cheap and empirically
worked, and `portal.py:121` already applies it on the structurally identical `/my/verify` GET.
Matching the portal is better than reasoning about which of the two pages really needs it.

## What this records

**D1 — everyone enters the code, staff included.** No bypass, no staff-only branch in the submit
handler. At the desk she reads the six digits aloud. A bypass was considered and rejected: it would
be the only path into the queue that does not prove the number, and every guarantee below would have
to be restated as "unless staff did it". The cost is real and should be said out loud — the flow now
takes a round trip through her handset in the one situation where both people are standing at the
same counter.

**D2 belongs to W3, and W1 must not be read as having forgotten it.** The pending gate is being
dropped (`.memory/decisions.md:75-77` reversed), but the state default stays `pending`
(`queue_entry.py:46`) until W3 flips it together with the join text — they are one event and one
message and splitting them would ship a queue that silently confirms nothing. W1's guarantees are
therefore written against row **count**, never row **state**, so every check below holds unchanged
across that flip.

## Deliberately skipped

- **A `purpose` column on `modryn.otp.code`.** Codes and the three-per-hour budget are now shared
  between portal login and check-in, and `verify` takes the newest unused row for the number
  (`otp.py:83-85`). Two consequences, and the second is the likelier one to be seen: a login code
  will be accepted at check-in (sloppy — both prove she holds the phone), and a woman who requests a
  login code *after* requesting a check-in code will find the check-in code now answers
  `wrong_code`, because the newer row is the one that gets compared. Revisit when someone is
  actually rate-limited by using both flows in one hour, or when a third flow needs codes.
- **A `phone_verified` field on the entry.** Under D1 every creation path runs through the code, so
  the column would be constant-true and would document nothing. It becomes worth adding the day a
  bypass is introduced.
- **A `limit_req` block for `/queue/verify`.** The new POST matches no location in
  `modryn-site.conf` and falls through to `location /` (`:310`) unlimited, where `/my/verify` gets
  `modryn_otp` 5r/m burst 3 (`:193-194`). Flagged rather than silently added, and flagged with the
  right reason: it is **not** what stands between an attacker and a code. Guessing is capped by the
  model at five attempts per code (`otp.py:88`, counted before the compare at `otp.py:95`) and three
  codes per hour, so fifteen guesses an hour against a million. What the missing block leaves
  unbounded is HTTP workers, same as any unlimited POST.
- **Short-circuiting the dedupe before the code.** Asking "is this number already in the queue?" at
  submit time and redirecting her straight to her ticket is precisely the hijack this spec closes,
  wearing a helpful face. The code is required even for a number the boutique already knows.

## Acceptance

Run on **noga**. bella holds live Twilio credentials and would actually send; noga has none, so
`_send_now` falls through to `_logger.info('[modryn.sms] (no Twilio configured) …')`
(`models/sms.py:160`) — which is also where the six digits are read during a manual run. New routes
and new Python mean a full server restart; a `-u` upgrade does not re-import Python.

| # | Check | Required |
|---|---|---|
| 1 | POST a valid name + phone to `/queue/checkin/submit`, then `select count(*) from modryn_queue_entry where phone='+972500000123'` | **0** |
| 2 | Same submit, then `select count(*) from modryn_otp_code where phone='+972500000123'` | **1** |
| 3 | Control for 1 — complete the flow with the code from the log, re-run the same query | **1**. Without this, check 1 passes on a module that never creates anything |
| 4 | Five wrong codes to `/queue/verify`, then the entry count query, and `select attempts from modryn_otp_code where phone='+972500000123'` | **0 rows, attempts = 5** |
| 5 | A sixth POST after those five | renders the `too_many_attempts` copy, still **0 rows** |
| 6 | Submit, then abandon — no verify POST at all | **0 rows**, and no row appears later |
| 7 | Four submits for one number inside an hour | the fourth renders the `rate_limited` copy; `select count(*) from modryn_otp_code where phone=…` stays **3** |
| 8 | Seed an open entry in noga, submit **its** phone from a clean cookie jar, stop at the code step | the response body contains **no** `access_token` of that entry, and the `Location` header is `/queue/verify` |
| 9 | Control for 8 — `GET /q/<that same token>` | **200**. Otherwise check 8 passes because the token is unreachable, not because it is withheld |
| 10 | `GET /queue/verify` with no cookie jar | **303** to `/queue/checkin` |
| 11 | Submit with a malformed phone | the re-rendered form contains the phone error text — today it contains nothing |
| 12 | Submit `client_type=evening` with a blank name | the re-rendered `<select>` carries `selected` on the evening option |
| 13 | `MODRYN_DEMO_PASSWORD=modryn2026 ./scripts/verify.sh` | **≥328 passed, 0 failed**; §6 (`verify.sh:324-327`) still green |
| 14 | After `scripts/sync_translations.py`, `git diff --stat -- '*.po'` | only `modryn_queue_poc` — the script rewrites all eight addons, so `git checkout --` the rest |

Checks 3 and 9 exist because 1 and 8 are assertions that something is **absent**, and an absence
passes for free on a broken build. Neither is optional.

## What this does not do

- **It does not touch the state machine.** `modryn_accept`, `/floor/accept` and the `pending`
  default all stand. Dropping the gate, folding the first-in-line case and restoring the manager's
  "Invite to book" button — which today exists only inside the pending panel
  (`floor_board.xml:79-87`) and vanishes with it — are W3.
- **It does not add a staff entry point.** `staff_mode`, the dynamic layout call and the `/floor`
  redirect target are W2. W1 ships the redirect exactly as it stands today
  (`request.redirect('/q/%s' % entry.access_token)`, `main.py:38`) and W2 edits that one line. No
  seam is introduced for it; a one-line change does not need an extension point.
- **It does not change any SMS body.** The join / next / turn wording, and the folding that keeps an
  empty boutique from firing two texts one second apart, are W3. The only new message here is the
  code itself, whose body already exists at `otp.py:69-71` and stays on the blocking `send()`
  (`otp.py:72`) because she is watching the screen for it.
- **It does not close SMS amplification**, and the table above says so rather than implying
  otherwise. It bounds it per number, where before it was bounded only per source address.
