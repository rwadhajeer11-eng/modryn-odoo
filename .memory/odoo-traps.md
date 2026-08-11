# Odoo 19 traps that fail with no error and no log line

Every entry cost real time on this project. What they share is silence: the code looks right,
the server starts, the page renders, and the thing simply does not work. Assume more exist.

---

## 1. `'category': 'Theme/*'` voids every asset in the module

**Symptom.** A module's SCSS and JS never load. No error, no log line, no 404 — the bundle is
simply built without them.

**Cause.** `website/models/ir_asset.py` discards assets from any module whose manifest
`category` starts with `Theme/`, unless that module is the website's *selected* `theme_id`.

**Fix.** Use `'category': 'Website'`. Ours are all `'Website'` for this reason.

---

## 2. LibSass kills the whole bundle on modern colour syntax

**Symptom.** The entire storefront renders unstyled — not just the offending rule, everything.

**Cause.** Odoo compiles SCSS with **LibSass**, which predates CSS Color Level 4.
`rgb(43 33 24 / 0.1)` raises `Function rgb is missing argument $green` and aborts the bundle.

**Fix.** Always `rgba(43, 33, 24, 0.1)`. Every `.scss` in this repo carries a comment saying so
because it is not obvious from the failure.

**Detection.** `verify.sh` asserts the compiled bundle is over 200 KB — a tiny bundle means a
SCSS error ate it.

---

## 3. `mapped(callable)` on an empty recordset calls it with the recordset

**Symptom.** A page 500s only when there is no data — i.e. for every brand-new tenant.

**Cause.** `recordset.mapped(lambda ev: ev.start)` on an **empty** recordset invokes the
callable once with the recordset itself. `empty_recordset.start` is `False`, not a datetime, so
the next operation explodes.

**Fix.** Use a comprehension: `{ev.start for ev in bookings}`. See
`addons/modryn_booking/controllers/main.py`, where this made `/book` a guaranteed 500 on a
fresh boutique.

---

## 4. `_sql_constraints` was removed in Odoo 19

**Symptom.** A uniqueness constraint you declared does not exist. Duplicates save cleanly.

**Cause.** Odoo 19 dropped `_sql_constraints`. The attribute is ignored and **no index is
created**. Odoo *does* log a warning at registry build naming the attribute
(`odoo/orm/model_classes.py:162`), but it scrolls past in the boot log with no connection to the
duplicate you later find in the data — which is why this still reads as a silent failure.

**Fix.**

```python
_name_uniq = models.Constraint('unique(name)', "That already exists.")
```

A duplicate staff role sailed through here before this was caught.

---

## 5. `translate=True` makes the column jsonb, and that is contagious

**Symptom.** Uniqueness silently fails; later, every write raises `InvalidTextRepresentation`;
`WHERE name = 'מוכרת'` fails with `invalid input syntax for type json`.

**Cause.** A translatable field is stored as **jsonb**. `unique(name)` then compares whole JSON
objects, so `{"en_US": "x"}` and `{"en_US": "x", "he_IL": "x"}` are distinct. Switching the
field back to non-translatable does **not** migrate the column, so the ORM and the schema
disagree from then on.

**Fix.** For a tenant's own data (roles, pieces, rooms, shifts) use a plain `Char` and enforce
uniqueness in Python with `@api.constrains`. Reserve `translate=True` for UI chrome.

**Gotcha for scripts.** Querying such a column from psql needs `name->>'en_US'`, not `name =`.

---

## 6. CSRF 400 on a visitor's very first request

**Symptom.** A public form returns a bare `400 Bad Request` — but only for people whose first
ever page load is that form.

**Cause — UNCONFIRMED. The explanation originally recorded here was wrong.** It claimed Odoo
only sends the session cookie when the session is dirty. Checked against the vendored 19.0
source, that is false: `http.py` sets the cookie on `if sess.is_dirty or cookie_sid != sess.sid`,
and a first-time visitor has no cookie, so `None != sid` and the cookie **is** sent. The
`is_dirty` gate gates *persistence to the store*, not cookie emission. An unpersisted sid also
survives the round trip (`session.sid = sid  # in case the session was not persisted`), and the
token is HMACed over only the stable `sid[:STORED_SESSION_BYTES]` prefix.

What is certain: the 400 was real and observed, and adding `session.touch()` fixed it. What
causes it is not established. The most likely remaining explanation is a client that does not
return the cookie between the GET and the POST — a `curl` without a shared cookie jar behaves
exactly this way — in which case the POST is issued under a freshly minted sid and the HMAC
cannot match.

**Fix.** `request.session.touch()` when rendering the form
(`addons/modryn_portal/controllers/booking_link.py`). Harmless and it worked. If you have a
reproduction, finish the derivation and replace this entry — do not re-tell the old story.

---

## 7. `sudo()` raises privileges but does not change `env.user`

**Symptom.** Every booking created from the public site was "organised by" the anonymous
`public` user. Nothing errored.

**Cause.** `sudo()` elevates **access rights**. It does not change identity, and
`calendar.event.user_id` defaults to `lambda self: self.env.user`.

**Fix.** Resolve the intended owner explicitly. `_organizer()` in
`addons/modryn_booking/controllers/main.py` looks up the boutique's internal user by group.

---

## 8. `useDraggable` suppresses pointer-events page-wide

**Symptom.** Drag-and-drop looks perfect and every drop silently misses.

**Cause.** During a drag, Odoo's hook disables pointer-events across the page — that is how it
runs its own hit-testing. `elementFromPoint` and `elementsFromPoint` therefore return only
`<html>`. A probe confirmed exactly that.

**Fix.** Hit-test **geometrically**: iterate `[data-drop-target]`, compare bounding rects, take
the innermost box containing the pointer. See `zoneAt()` in
`addons/modryn_staff/static/src/floor/floor_board.js`.

---

## 9. A QWeb translation term includes its inline markup

**Symptom.** A page renders in English despite a complete, correct-looking `.po` file. No
warning that a msgid failed to match.

**Cause.** The translation unit is the **inner HTML of the block**, markup included — the term
is `<span>Price on request</span>`, or an entire `<select>`, not the visible words.

**Fix.** Never hand-write msgids. Export the POT with Odoo itself, then re-key translations onto
the exported msgids by comparing tag-stripped text. That is exactly what
`scripts/sync_translations.py` does.

**Two sub-traps it also encodes.** Normalisation must **preserve case** — Odoo extracts
`"Available"` and `"available"` as different terms with different Hebrew forms, and lowercasing
merged them, dropping one. And `_()` around a dictionary *lookup* hides the literals from the
extractor entirely; use `LazyTranslate` (`_lt`) on the values.

**Related.** Writing `res.users.lang` does **not** change what a website page renders — the
URL's language prefix wins. A staff language toggle must redirect through `/en` and set the
`frontend_lang` cookie.

---

## 10. Catching a `ValidationError` does not undo the write

**Symptom.** The UI correctly reported *"Room 1 is already being used by …"* and then committed
**both** customers into Room 1 anyway.

**Cause.** The constraint fires on flush and raises. Catching it only stops Odoo's HTTP handler
from rolling the request back — it does **not** revert the write that provoked it. The request
then commits normally, invalid value included.

**Fix.** Wrap any write whose constraint failure you intend to *report* rather than *raise*:

```python
try:
    with request.env.cr.savepoint():
        record.write({'modryn_room_id': room_id})
except ValidationError as exc:
    return dict(self._board(), error=exc.args[0])
```

See `set_room()` in `addons/modryn_staff/controllers/floor.py`. This is the most dangerous entry
in this file: the system tells the user it refused, and does it anyway.

---

## 11. Short-interval crons are permanently overdue

**Symptom.** A cron whose `nextcall` is in the past, apparently never firing.

**Cause.** Odoo's threaded scheduler makes one pass per database roughly every **60 seconds**.
Any cron with an interval near that sits overdue between firings as a matter of course —
measured here at 1m for a 1-minute cron and 1m45s for a 10-minute cron.

**Fix.** Never assert `nextcall > now()` for a short-interval cron; assert it exists and is
active. Keep the stricter assertion only where firing early would be destructive — the
daily closing cron, which would expire every live ticket on the floor.

---

## 12. `t-key` is an OWL directive, not a QWeb one

**Symptom.** `WARNING … Unknown directives or unused attributes: {'t-key'}`, once per row, on
every single render.

**Cause.** `t-key` belongs to OWL component templates. Server-rendered QWeb has no such
directive.

**Fix.** Keep `t-key` only in files that are genuinely OWL templates
(`floor_board.xml`). Remove it from server-side views.

---

## 13. `createdb -T` clones `database.secret`, and every signed thing with it

Found 2026-08-11, while building something unrelated.

`scripts/new_boutique.sh` provisions a tenant with `createdb -T modryn_template`, a
Postgres-level copy. It duplicates `ir_config_parameter` wholesale, and that table holds
**`database.secret`** — the HMAC key behind Odoo's CSRF tokens, its session tokens, our OTP
hashes in `otp.py` and our booking token in `booking_comms.py`.

The script already regenerated `database.uuid` with the comment *"a duplicated database.uuid
makes two tenants look like one instance"*. The identical argument applies to the secret and it
was simply missed for months.

Consequence, verified rather than reasoned: ids restart at 1 in every database, and
`_modryn_token()` signed only `"booking:<id>"`, so **bella's token for booking N was
byte-identical to noga's**. Bella's reminder link returned 200 on noga and rendered another
boutique's customer. Ten of noga's live bookings had a colliding bella token. Since the token
*is* the auth for `/b/<token>`, and CSRF keys off the same secret, the POST cancel was reachable
too — cancelling a stranger's fitting and firing that boutique's day-waitlist.

Two lessons, not one:

- **Rotate every per-instance secret on clone, not just the ones you happened to think of.**
  Anything in `ir_config_parameter` that is supposed to be unique per instance is copied by
  `createdb -T`. `database.uuid` and `database.secret` both are.
- **Do not let DB-per-tenant stand in for a signature's scope.** Under DB-per-tenant the key
  alone *looks* like enough context. It is not, the moment two databases share a key. Put the
  tenant in the signed message: `"booking:<db>:<id>"`. It costs nothing and it fails safe.

The suite had 263 green checks and none of them noticed, because **every check asked one tenant
about itself**. `verify.sh` §1 now asserts the secrets are pairwise distinct and that one
tenant's booking token 404s in the other — with an own-tenant control, so a broken token builder
cannot make the probe pass for the wrong reason.

---

## Renamed or moved in Odoo 19

| Was | Now |
|---|---|
| `res.users.groups_id` | `res.users.group_ids` |
| `res.groups.category_id` | `res.groups.privilege_id` (new `res.groups.privilege` model) |
| `_sql_constraints` | `models.Constraint(...)` — see trap 4 |

## Environment (macOS, no Docker)

- A shell alias shadows the venv: always call **`python3`**, never `python`.
- `db_host` must be **empty**, not `False`, to use the Postgres unix socket.
- QR rendering needs `rlPyCairo`, which is **absent from Odoo's `requirements.txt`** and itself
  needs `pkg-config` + `cairo`.
- Module data is only re-read on upgrade — `-u <module> --stop-after-init`. Editing an XML data
  file and restarting changes nothing.
- `db_name = modryn_template,bella,noga` in `odoo.conf` is **load-bearing**: `dbfilter` only
  routes HTTP. Without `db_name`, the cron scheduler enumerates every database on the Postgres
  server — including MODRYN's own `f*_test` databases — and errors against each.
