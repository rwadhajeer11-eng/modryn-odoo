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

## 3. Per-appointment-type duration · M

The last piece of the availability engine, and the only one deliberately not attempted.

A 90-minute fitting overlapping the following 60-minute slot **cannot be expressed by a unique
index**. It needs a `tstzrange` EXCLUDE constraint and the `btree_gist` extension, plus a grid
that is no longer uniform — a different feature wearing the same name. A half-built version that
offers 11:00 while a fitting runs through it is worse than not shipping one.

Everything else in that engine landed: opening hours, closures, per-window capacity enforced by
Postgres, and the published rota capping what a day can sell.

---

## 4. Israeli payment provider and deposits · M each

No PSP exists in Odoo for Grow, Meshulam or Tranzila — each is a custom `payment.provider`.
Deferred deliberately: it needs a real merchant account, which is a business decision, not a
technical one. Per-tenant credentials are free under DB-per-tenant, which is a genuine advantage
here.

---

## 5. WhatsApp · blocked externally

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

## Left open by the walk-in verification build (2026-08-13) · S each

Three things that build deliberately did not decide. None is a bug; each is a call somebody has
to make. Full context in [`epics/walkin-verification.md`](epics/walkin-verification.md).

- **A redirected walk-in now reads a contradiction.** She gets *"you're in the queue"* on
  check-in, then — if a manager taps **Invite to book** — *"We're fully booked today"* minutes
  later. Before the gate was dropped, redirect happened from `pending`, before any join text
  existed. The button is worth keeping; the second body needs wording that acknowledges the
  first. It is customer-facing copy in he + ar, so it is a product decision, not a patch.
- **Three codes per hour is a real ceiling at the desk.** `modryn.otp.code` counts per phone
  across *all* flows (`otp.py:13`), so a bride who mistypes her way through three codes is
  locked out for an hour with a staff member standing in front of her. Recovery below that
  works — restarting from `/queue/checkin` issues a fresh code and it is accepted. The fix is a
  `purpose` column plus two domain leaves; it was skipped on purpose and this is its trigger.
- **`scripts/verify.sh` asserts nothing about the check-in flow.** Its 328/0/2 was unchanged
  across the whole build, which is a *control*, not evidence. Coverage today is the specs'
  manual acceptance tables plus `qa/` act 6 in a browser. Since `deploy.sh` gates rollback on
  `verify.sh`, the flow that now stands between a walk-in and the queue has no deploy-time
  check. Worth a §-block: submit creates no row, a wrong code creates no row, the right one
  creates exactly one at `waiting`.

## Housekeeping worth knowing

- `.memory/` and `.planning/` are **committed**. They existed as untracked scratch earlier and
  vanished between two commands on the same day.
- `docs/screenshots/*.yml` and `*.log` are gitignored — Playwright writes several hundred
  transient dumps per session, and ~300 of them once got swept into a commit.
- `DON'T_DELETE.md` at the repo root is your own verbatim brief for the dispatch board. It is
  kept as written, untracked, and should not be tidied.
