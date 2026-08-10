# Bugs that shipped here, and what actually caused them

Real defects found in this repo, with root causes and the commit that fixed each. The generic
Odoo failure modes live in [`odoo-traps.md`](odoo-traps.md); this file is the specific
history — useful mostly because several of these were **invisible until hunted**, and the
same shapes will recur.

---

## Behaviour users would have seen

### Every booking was owned by the anonymous website user

`calendar.event.user_id` came out as login `public` for every public booking. `sudo()` had been
used, which raises privileges but leaves `env.user` alone — and that default is
`lambda self: self.env.user`.

Fixed by resolving the boutique's internal owner explicitly (`_organizer()`,
`addons/modryn_booking/controllers/main.py`). `verify.sh` now asserts no booking is organised
by `public`, because nothing about this is visible from the UI.

### A room collision was reported *and* committed — commit `13bdba8`

The board said *"Room 1 is already being used by HTTPRoom A"* and the database then showed both
customers in Room 1. Catching the `ValidationError` prevented Odoo's handler from rolling the
request back but did not undo the write. Fixed with an explicit savepoint. Full explanation in
[`odoo-traps.md` §10](odoo-traps.md).

**This is the worst class of bug in the project:** the system correctly detects a violation,
tells the user it refused, and saves it regardless.

### `/book` was a guaranteed 500 for every new boutique

`recordset.mapped(lambda ev: ev.start)` on an empty recordset. Any boutique with zero bookings —
i.e. every new one — hit it on their first page load. See [`odoo-traps.md` §3](odoo-traps.md).

### A freed slot was offered and then refused

The `/book` slot list excluded cancelled bookings but the submit-time collision guard did not.
A customer could pick a slot the page showed as free and be told it was "just taken". Left
unfixed it would also have silently broken the refill loop shipped two commits later (`ab942c9`),
whose whole purpose is re-selling cancelled slots. Fixed in `85b9056` by adding the same
`modryn_cancelled_at` filter to the submit-time guard, so both places agree.

### She was texted "you're next" twice for a state change that never happened

Observed while `/floor` was 500ing. The SMS was sent inside a transaction that then rolled back
— the message left the building, the state change did not. The 500 was fixed, but the ordering
hazard is real and unfixed in general: **treat a send inside a transaction as a bug**, because
a rollback cannot recall a text message.

---

## Structural bugs — commit `5928a95`

### Odoo's cron scheduler roamed into MODRYN's databases

`dbfilter` routes HTTP only. Cron enumerated **every** database on the Postgres server,
including MODRYN's `f*_test` databases, and errored against each (`ir_module_module does not
exist`). Fixed by adding `db_name = modryn_template,bella,noga` to `odoo.conf`.

### Helper promotion picked whoever was alphabetically first

When a primary left a customer card, the "longest-serving helper" was promoted — except the
helpers were a plain many2many, which reads in the comodel's `_order`, i.e. by employee name.
The board was making a staffing decision based on the alphabet.

Fixed by introducing `modryn.floor.helper`, an explicit through-model ordered by `create_date`.
A migration moves the old rows and drops the two m2m tables
(`addons/modryn_staff/migrations/19.0.1.1.0/post-migrate.py`).

**Follow-on bug this caused.** The replacement `modryn_helper_ids` is a *non-stored* compute
over the through-model. Putting a non-stored field in a search domain raises
`Cannot convert ... to SQL because it is not stored` — which took `/floor` down with a 500 for
every signed-in staff member while every anonymous check still passed. Query the through-model
directly instead; see `_compute_modryn_is_occupied` in
`addons/modryn_staff/models/hr_employee.py`.

---

## Silent-data and tooling bugs

### Twilio credentials appeared to save and did not

`configure_twilio.py` printed a success message from a transaction that was never committed.
Odoo's shell does not autocommit. Fixed with an explicit `env.cr.commit()` — and the lesson
generalises: **a success message proves the code path ran, never that the data persisted.**

### A duplicate staff role saved cleanly

`_sql_constraints` no longer exists in Odoo 19, so the declared uniqueness index was never
created. See [`odoo-traps.md` §4](odoo-traps.md).

### Two Hebrew words collapsed into one

`sync_translations.py` lowercased msgids when matching. Odoo extracts `"Available"` (the badge,
פנויה) and `"available"` (the suffix in "3 available", פנויות) as separate terms; lowercasing
merged them and dropped one, so the board rendered English for that word with nothing to
explain why. Fixed by making normalisation case-preserving.

### Offer SMS went out in the server's language

`_make_offer()` is called by a **cron**, whose language is the server's, not the customer's — so
a Hebrew customer would have received an English text. Fixed in `ab942c9` by recording `lang` on
the waitlist row at join time and restoring it with `with_context(lang=…)` before composing.

**The general shape:** any message composed outside a request has no user language. Capture it
at the point of human contact and replay it at send time.

---

## Bugs in the verification, not the code

Worth their own file — see [`verification-lessons.md`](verification-lessons.md). Two are worth
repeating here because they produced *false confidence* rather than false alarms:

- **Anonymous checks proved the gate, never the page.** `/floor` returning 303 to a logged-out
  visitor says nothing about whether it renders for staff. It was 500ing for every signed-in
  manager while the whole suite stayed green. `verify.sh` §10a now signs in and asserts the real
  page.
- **A cron check compared a naive-UTC column to local `now()`**, reporting every cron three
  hours overdue. Fixing that revealed a *second*, genuine issue underneath — short-interval
  crons really are overdue between firings. Two defects, one symptom; the first fix made the
  second visible.
