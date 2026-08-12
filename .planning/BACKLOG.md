# What to do next

Ranked. Top item first — a fresh session can start at #1 without asking anything, except where
an item is marked **blocked on you**.

Sizes: **S** ≤ half a day · **M** ≤ 2 days · **L** ≤ a week · **XL** more.

---

## 1. Prove SMS actually reaches a phone — **blocked on you** · S

Everything in the comms engine is written and integrated; nothing has been **delivered**. A live
Twilio call returned error `21266` ("'To' and 'From' cannot be the same"), which proves the
credentials, the adapter and the error handling all work — and proves nothing about delivery.

**Needs from you:** a destination mobile number that is **not** the Twilio sender — sending to
the sender is what produced error 21266. The sender is `TWILIO_FROM_NUMBER` in the gitignored
`.env`; it is deliberately not repeated here, because git history is forever.

**Then:** book on `bella`, confirm the confirmation SMS arrives, open the `/b/<token>` link from
it, cancel, and confirm the claim link reaches the next person on that day's waitlist. That one
run exercises the whole chain.

Until this is done, the scorecard and `STATE.md` must keep saying delivery is unproven. They
currently do.

---

## 2. Rotate the Twilio credentials — **blocked on you** · S

The API key SID, secret and phone SID were pasted into a chat transcript on 2026-08-10. They
live in the gitignored `.env` and have never been committed, but a transcript is not a secret
store.

Rotate in the Twilio console, update `.env`, re-run `scripts/configure_twilio.py`, and confirm
with a send. Do this whenever the PoC ends, sooner if the transcript is shared.

---

## 3. Make the roster actually mean something · M

Publishing a week currently changes nothing outside the roster page. It does not restrict who can
be assigned on the floor that day, and it does not feed the booking grid.

That gap is the difference between a rota and a spreadsheet. The smallest honest version: the
floor board's staff list shows who is rostered today, and assigning someone who is not warns
rather than blocks — blocking would be wrong on a day when someone covers a sick colleague.

---

## 4. Availability engine · L (was XL)

**Opening hours are done** — `modryn.opening.hours`, editable at `/manage/hours`, read by both
the booking grid and the waitlist claim page, seeded so the old Sunday–Thursday 10:00–18:00
lattice survives unchanged. That also closed a latent bug: the waitlist copy of the grid had no
weekday filter at all, so a claim link could offer a Friday.

**Blackout dates and per-window capacity are done too.** The partial unique index now keys on
`(start, modryn_slot_seat)`, so a window can take more than one fitting an hour and Postgres —
not Python — still decides who gets the last seat.

Still missing: **per-appointment-type duration**, and **per-staff calendars**. Duration is the
harder half and was deliberately not attempted: a 90-minute fitting overlapping an adjacent
60-minute slot cannot be expressed by any unique index. It needs a `tstzrange` EXCLUDE constraint
and `btree_gist`, plus a non-uniform grid — a different feature wearing the same name.

Still the largest piece of remaining work and the one Odoo Enterprise's `appointment` module
would most plausibly halve.

---

## 5. Israeli payment provider and deposits · M each

No PSP exists in Odoo for Grow, Meshulam or Tranzila — each is a custom `payment.provider`.
Deferred deliberately: it needs a real merchant account, which is a business decision, not a
technical one. Per-tenant credentials are free under DB-per-tenant, which is a genuine advantage
here.

---

## 6. WhatsApp · blocked externally

Odoo's WhatsApp module is Enterprise-only, and the Business API needs Meta verification the
business has not started. SMS via Twilio is the working channel. Do not start this without the
verification in hand.

---

## Not doing, on purpose

- **Migrating MODRYN onto Odoo.** The verdict is settled; see
  [`../.memory/decisions.md`](../.memory/decisions.md).
- **Anything requiring Enterprise.** Community only.
- **Visible queue numbers or wait-time estimates.** A researched product decision, not an
  omission.
- **A cross-tenant admin console.** Real work (`L`) and the visible cost of DB-per-tenant, but
  pointless unless the verdict reverses.

---

## Housekeeping worth knowing

- `.memory/` and `.planning/` are **committed**. They existed as untracked scratch earlier and
  vanished between two commands on the same day.
- `docs/screenshots/*.yml` and `*.log` are gitignored — Playwright writes several hundred
  transient dumps per session, and ~300 of them once got swept into a commit.
- `DON'T_DELETE.md` at the repo root is your own verbatim brief for the dispatch board. It is
  kept as written, untracked, and should not be tidied.
