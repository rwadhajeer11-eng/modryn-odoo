# W2 — the staff can open the door

_Depends on W1 (the OTP gate). Everything below assumes a check-in already costs a 6-digit code;
this spec adds no way in that skips it._

## The problem

`/queue/checkin` has exactly one entrance: the QR sign at `/queue/sign`, whose barcode encodes
`host_url + '/queue/checkin'` (`controllers/main.py:71-74`). No staff page links to the form.
`grep -rn 'queue/checkin' addons/` outside `modryn_queue_poc` returns nothing.

So a bride whose phone is dead, whose camera app fights the lounge lighting, or who simply hands
her details to the woman at the desk cannot be put in the line at all. The staff member's only
recourse is to read the URL off the printed sign into her own browser — which is a workaround
dressed as a feature, and it lands her on the storefront-shelled public form with no way back to
the board.

## One flag, and it is recomputed every request

```python
group = request.env.ref('modryn_staff.group_boutique_staff', raise_if_not_found=False)
staff_mode = bool(group) and request.env.user.has_group('modryn_staff.group_boutique_staff')
```

Not stored in the session. A session-stored flag survives sign-out, survives a shared tablet
being handed to a customer, and survives the account being demoted — three states where the
terminal would keep offering the staff shell to someone who is no longer staff. Recomputing costs
one cached group lookup per request and cannot go stale.

`staff_mode` drives exactly two things and nothing else:

| | In staff mode | Otherwise |
|---|---|---|
| Shell | `modryn_staff.staff_layout` | `website.layout` |
| Redirect after a verified code | `/floor` | `/q/<access_token>` |

It grants no permission. It does not skip the code (D1: everyone enters the code, staff included —
at the desk she reads the six digits aloud). It does not change what `modryn_check_in` does. A
staff member who mistypes gets the same refusal a bride does.

The `bool(group) and` guard is a belt on top of braces: `has_group` resolves the xmlid through
`_get_group_definitions().get_id()`, which **returns `None` for an unknown xmlid rather than
raising** (`odoo/odoo/tools/set_expression.py:138-143`, reached from
`res_users.py:1094`). On a database without `modryn_staff` the bare `has_group` call already
answers `False`. Keep the guard or drop it — but do not keep it believing it is what prevents an
exception.

## The shell swap is a dynamic `t-call`, not a second template

`checkin_form` currently hardcodes `<t t-call="website.layout">` (`views/templates.xml:6`). It
becomes:

```xml
<t t-call="{{ layout }}">
```

with the controller passing `'modryn_staff.staff_layout'` or `'website.layout'`.

This is not a trick. Server-side QWeb runs the `t-call` value through `_compile_format`
(`odoo/odoo/addons/base/models/ir_qweb.py:2631`), whose docstring at `:240` reads
"**Values**: format string expression for template name", and `{{ … }}` is one of the two
interpolation forms that `FORMAT_REGEX` recognises (`odoo/odoo/tools/translate.py:127`). Every
existing `t-call="{{ … }}"` in the vendored tree is an OWL template, so this is the repo's first
server-side use — but it is the documented behaviour of the directive, not an accident.

Zero new layout templates. The staff terminal wears the real MODRYN nav, the language toggle, the
signed-in name and a way back to `/floor`, all of which already exist and stay in one place.

`staff_layout` is only reachable when `modryn_staff` is installed, which is the same condition
that makes `staff_mode` true — the swap cannot name a template that is not there. And an
undefined `active_tab` is already exercised in production: `/atelier` renders
`modryn_staff.manage_layout` with no `active_tab` in its render context
(`modryn_atelier/controllers/atelier.py:100-105`), and the layout reads `active_tab == 'staff'`
regardless (`manage_templates.xml:36`). QWeb compiles a bare name to `values.get(…)` unless
`raise_on_missing` is set (`ir_qweb.py:1577-1589`), so a missing key is `None`, not a traceback.

## The one nav link, and why it goes at line 26

One `<a>` in `addons/modryn_staff/views/floor_templates.xml`, inserted **before**
`<t t-call="modryn_staff.lang_toggle"/>` at line 26:

```xml
<a href="/queue/checkin"
   t-attf-class="modryn_admin_link #{'is_active' if active_tab == 'checkin' else ''}">Check someone in</a>
```

`.modryn_admin_nav` is `display: flex` (`floor.scss:60-61`) and `.modryn_admin_who` carries
`margin-inline-start: auto` (`floor.scss:88-89`). That one declaration splits the nav into two
clusters: page links before it, identity and sign-out flung to the far end after it. A link placed
anywhere below the `.modryn_admin_who` span at line 27 lands in the sign-out cluster, next to
"Sign out", where nobody looks for a page. Line 26 is the last position still inside the link
cluster.

**No `t-if`.** The whole shell renders only for staff: its four callers are `/floor`, `/roster`,
`/manage/reports` and now the check-in form, and each one refuses a non-staff user before
rendering (`floor.py:30`, `roster.py:52-53`, `reports.py:155-156`, and `staff_mode` itself).
Gating the link again would be a second copy of a rule the shell already enforces.

Contrast the sibling two lines above: `/atelier` **is** wrapped in
`t-if="…has_group('modryn_staff.group_shift_manager')"` (`floor_templates.xml:22-23`). That gate
is not about being staff — it is about being a *manager*, a narrower set than the shell's own
audience, and `/atelier` returns `not_found()` to everyone else
(`modryn_atelier/controllers/atelier.py:97`). Check-in has no such narrowing: every staff member
standing at the desk should be able to open the door. The absence of a `t-if` here and the
presence of one there are the same rule applied to two different audiences, not an inconsistency.

## Why this is an edit and not an inheritance

`modryn_ops` adds nav items without touching `modryn_staff` — `staff_nav_reports` inherits
`modryn_staff.staff_layout` by xpath (`ops_templates.xml:17-23`). That is the right pattern and it
does not apply here, for one hard reason: **`modryn_staff` depends on `modryn_queue_poc`**
(`modryn_staff/__manifest__.py:31`), not the reverse. `modryn_ops` inherits downhill
(`modryn_ops/__manifest__.py:21` depends on `modryn_staff`); `modryn_queue_poc` would have to
inherit uphill, and adding `modryn_staff` to its `depends` closes a cycle.

So the `<a>` is edited into `floor_templates.xml` directly, and `modryn_staff` gains a hardcoded
reference to a route `modryn_queue_poc` owns. Two weaker versions of that already ship in the same
nav: `/roster` (line 20) and `/atelier` (line 22) both point into modules that depend on
`modryn_staff` and may therefore be *absent*. `/queue/checkin` is the strong case — its module is a
declared dependency, so the route is guaranteed to exist wherever the shell renders.

## The second shell, honestly

`modryn_staff.manage_layout` (`manage_templates.xml:25-55`) is a copy-paste sibling of
`staff_layout` with its own nav, and it is what every `/manage/*` page and `/atelier` wear
(ten `t-call` sites across four addons). **An owner sitting on `/manage/staff` will not see the
check-in link.**

That is accepted, not overlooked. The desk terminal is `/floor`; `/manage/*` is where the boutique
is configured, not where someone stands with a bride in front of her. `manage_layout` already
carries an ungated "Floor board" link (`manage_templates.xml:46`), so the owner is one click from
the shell that has the door.

It is a restraint, not a limit: adding the same `<a>` to `manage_layout` is one more line and
reuses the same msgid, exactly as "Floor board" and "Workshop" already appear in both shells.
Revisit when an owner is observed actually working the desk out of `/manage`.

## The collateral D2 leaves behind: "Invite to book" comes back

Dropping the pending gate (D2) means new entries are created at `waiting`, so
`state.pending.length` is 0 and the whole "Just arrived" panel self-hides
(`floor_board.xml:62`). The **only** copy of the manager's "Invite to book" button lives inside
that panel (`floor_board.xml:84-87`), so it disappears with it. Nothing in the spec that dropped
the gate says so, which is exactly why it is written here.

The backend needs no change: `modryn_redirect` already accepts `waiting`
(`queue_entry.py:117`). Restoring the button is one `<button t-if="state.canAssign">` added to the
waiting card's existing `t-if="state.canAssign"` actions row (`floor_board.xml:144-156`, beside
"Done"), wired to the handler that already exists (`floor_board.js:405-407`). `state.canAssign` is
`can_assign` is `_is_manager()` (`floor.py:134`), which is the same check `/floor/redirect`
performs server-side (`floor.py:250`) — the drawn gate and the enforced gate stay identical.

**What it now costs her, which the SMS accounting for D2 does not mention.** Under the old flow
she was redirected out of `pending`, having received nothing — one text, the invitation. Under D2
she is in `waiting` and has already been told "you're in the queue", so a redirect now sends her a
second queue text that opens "We're fully booked today" a minute after the first said she was in
the line. Three texts for a walk-in who never gets seen (OTP, join, redirect), and two of them
disagree. The button is still worth restoring — the alternative is a manager with no way to turn
anyone away — but the redirect body should be reworded for a bride who is already in the line, and
that wording is a W3 decision, not something to invent here.

Free of translation cost: the msgid "Invite to book" is keyed to
`code:addons/modryn_staff/static/src/floor/floor_board.xml:0` — line **zero**
(`modryn_staff/i18n/he.po:286-288`). Moving the button inside the same file changes no `.po` line.

The "Just arrived" panel, `modryn_accept`, and `/floor/accept` all stay. They are dead for new
entries and are still the only way to clear the pending rows bella and noga hold today.

## Translation

One new server-side term, `"Check someone in"`. Trap #9 says the unit is the inner HTML of the
block — but the nav's existing terms are per-link leaves, not one nav-sized blob:
`msgid "Floor board"`, `"Workshop"`, `"Manage staff"`, `"Sign out"` each stand alone
(`modryn_staff/i18n/he.po:249, 682, 308, 544`), while the brand is
`"<span class=\"modryn_admin_brand\">MODRYN</span>"` (`:34`) — markup included, because that is
the leaf. **Inserting an `<a>` therefore adds one msgid and invalidates none of the four.**

Still: export the POT with Odoo and re-key with `scripts/sync_translations.py`. Never hand-write
the msgid. That script rewrites all eight addons' `.po` files, so `git diff --stat -- '*.po'`
afterwards and `git checkout --` the ones this change never touched.

## Deployment note

New Python in `modryn_queue_poc/controllers/main.py` means a **full server restart**; registry
signalling does not re-import Python. The nav `<a>` alone would be `-u modryn_staff
--stop-after-init`, but it never ships alone. Only one server can hold 8069 and the shared
databases, so this work is serial.

## Acceptance

Every row is a command whose output someone reads. Live checks run against **noga** — bella holds
real Twilio credentials (`.memory`: `qa/lib/guard.js` refuses any tenant carrying `modryn.twilio.*`),
and on noga the code is readable from the `[modryn.sms] (no Twilio configured)` log line.

| # | Check | Required |
|---|---|---|
| 1 | `awk '/queue\/checkin/{c=NR} /lang_toggle/{t=NR} END{print (c&&c<t)?"ok":"FAIL"}' addons/modryn_staff/views/floor_templates.xml` | `ok` — the link is above the toggle, inside the link cluster |
| 2 | `grep -A2 'href="/queue/checkin"' addons/modryn_staff/views/floor_templates.xml \| grep -c 't-if'` | `0` — no redundant gate |
| 3 | anonymous: `curl -s http://noga.localtest.me:8069/queue/checkin \| grep -c modryn_admin_nav` | `0`, and the page is still 200 — the public form keeps the storefront shell |
| 4 | signed in as **dana** (noga staff, not a manager): same URL, same grep | `≥1` — she gets the staff shell |
| 5 | same response: `grep -c 'href="/atelier"'` | `0` — the manager-gated sibling is still gated, proving row 4 is not the shell rendering unconditionally |
| 6 | same response: `grep -c 'href="/queue/checkin"'` | `≥1` — a staff-level user sees the link with no `t-if` |
| 7 | as dana, complete a check-in with the code from the log; read the response headers | `Location: /floor` |
| 8 | anonymous, same flow, different number | `Location: /q/<token>` |
| 9 | `grep -n 'redirectPending\|Walk-in queue' addons/modryn_staff/static/src/floor/floor_board.xml` | a `redirectPending` hit appears **after** the "Walk-in queue" line — the button is on the waiting card, not only in the hidden panel |
| 10 | `psql -d noga -tAc "select count(*) from modryn_queue_entry where state='pending' and create_date > now() - interval '1 hour'"` after rows 7 and 8 | `0` — D2 holds; a verified check-in lands in `waiting` |
| 11 | `git diff -- addons/modryn_staff/i18n/he.po \| grep -c 'Invite to book'` | `0` — moving the button touched no translation |
| 12 | `git diff --stat -- '*.po'` | only `modryn_staff` (and whatever the new msgid genuinely needs) — everything else `git checkout --`'d |
| 13 | `MODRYN_DEMO_PASSWORD=modryn2026 ./scripts/verify.sh` | ≥328 passed, **0 failed**, 2 skipped — the count has not silently dropped |

Rows 3–6 are the whole flag: two shells, one URL, one recomputed boolean, and a control (row 5)
proving the staff shell is not simply rendering for everybody.

## What this does not do

- **No staff bypass of the code.** D1. A staff member types the six digits she was read at the
  desk. There is no route, flag or group that skips `otp.verify`.
- **No second controller and no second layout template.** One route, one dynamic `t-call`, one new
  `<a>`.
- **No check-in link on `manage_layout`.** Argued above; the trigger to revisit is an owner
  observed working the desk from `/manage/*`.
- **No deletion of the pending panel, `modryn_accept`, or `/floor/accept`.** Dead for new entries;
  still the only way to clear the legacy rows on bella and noga.
- **No `phone_verified` column on the entry.** Every creation path now runs through OTP, so the
  column would be constant-true. Add it the day a bypass is introduced.
- **No `purpose` column on `modryn.otp.code`.** Login and check-in now share the code pool and the
  3-per-hour budget, so a login code will verify a check-in. Both prove she holds the phone, so it
  is sloppy rather than open. Add `purpose` plus two domain leaves when a bride is actually
  rate-limited by using both flows within an hour, or when a third flow needs codes.
- **No dedupe short-circuit before the code.** Answering "this number is already queued" at submit
  time and redirecting to her ticket is precisely the hijack W1 exists to close.
- **No rewording of the redirect SMS.** Named above as a real cost of restoring the button after
  D2; changing the body is a product decision with its own msgid and belongs in its own spec.
- **No `active_tab` plumbing beyond the one key.** If W1's render context omits `active_tab`, the
  link renders without `is_active` and nothing breaks — `/atelier` proves that path today.
