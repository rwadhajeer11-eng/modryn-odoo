# Decisions already settled

Each of these was argued through and chosen deliberately. Reaching the opposite conclusion means
re-running an argument that already happened — so if one looks wrong, say why it is wrong rather
than quietly building the other thing.

---

## Scope

**Odoo is a separate evaluation project. MODRYN is never touched.**
`/Users/mrwen/Documents/Github/Ryan + rawad + mrwen` is read-only — other sessions commit to it
while this one runs, so its HEAD and mtimes move on their own. That is expected, not corruption.
`verify.sh` §12 asserts its working tree carries only its own pre-existing untracked files.

**Community edition only.** Appointment, Planning and WhatsApp are Enterprise-only, and hosting
Enterprise on behalf of customers additionally requires an Odoo partner agreement — a commercial
negotiation, not a purchase. Enterprise stays deferred; nothing here may depend on it.

**Odoo core is never edited.** It is a shallow, gitignored clone at `odoo/`. Everything lives in
`addons/`.

---

## People and permissions

**Three levels: owner, shift manager, staff.**

**Owner is an internal user; manager and staff are portal users.** Portal accounts are free even
under Enterprise, so a ten-person boutique costs one seat rather than ten — and they are
*structurally* unable to reach `/odoo`, which is the real point. A saleswoman cannot wander into
the raw back office.

**Consequence to remember:** portal users have no ORM access to `hr.employee` (Odoo restricts it
to `hr.group_hr_user` and it carries private HR fields). Every staff-facing controller therefore
checks its group itself and reads through `sudo()`, handing templates plain dicts. Never pass a
recordset to a portal-facing template.

**Staff roles are owner-created data, not a Selection field.** Boutiques invent their own roles;
a developer should not be in that loop. Same pattern for garment pieces, fitting rooms and shift
templates — four instances of the same decision.

**Every route re-checks its group server-side.** Hiding a button is not a permission.

---

## Floor model

**Occupancy is derived, never stored.** `modryn_is_occupied` is computed from live assignments
plus the clock, with no `@api.depends` and no storage. A stored field would need invalidating
every minute and be wrong the rest of the time; a manual זמין/תפוס toggle drifts the moment
someone forgets to press it. Same reasoning frees a fitting room when a fitting ends.

**One accountable primary, any number of helpers.** The bridal-floor reality is a saleswoman
styling while a seamstress pins. Helpers come and go without changing who owns the customer.
Join order is load-bearing (the longest-serving helper is promoted), which is why helpers are an
explicit through-model rather than a many2many.

**Alteration work does not make a seamstress "busy".** She is sewing in the back and is still
callable; her workshop load shows on the atelier dashboard instead.

**All staff-facing UI is custom-themed** — `/staff/login`, `/manage`, `/floor`, `/roster`,
`/atelier`. Staff never meet Odoo's vocabulary. Odoo's own "edit this content" corner widget is
hidden for the same reason.

---

## Customer experience

**The queue shows no numbers and no position.** Researched against how premium retail actually
handles waiting (the Waitwhile pattern): three warm states — checked in, you're next, your turn —
and the shop absorbs the waiting rather than displaying it. "You are 7th" is a supermarket deli
counter.

**The acceptance gate is invisible.** Staff accept an arrival into the line or suggest booking
instead. She is never told she was turned away — her page simply becomes a warm invitation to
book. Rejection is never surfaced.

**Two SMS per walk-in, maximum:** one at you're-next, one at your-turn naming her stylist. Both
idempotent via notified-at fields. Checking in twice with the same number resumes the same
ticket rather than issuing a second.

**The advance waitlist is per DAY, not per slot.** Most cancellations will not match anyone's
exact requested hour, so a per-slot list would almost never fire. She said "Tuesday works"; the
boutique honours that.

**One live claim offer per day, two-hour window.** Two people holding claim links for one slot is
a race the boutique loses in public. Long enough that she can be at work and still answer, short
enough that a freed Saturday slot is not dead all afternoon.

**A fully-booked day stays visible on `/book`,** carrying a waitlist form instead of a time
picker. Hiding it means she never learns she could have been first in line.

**An offer whose SMS fails is expired immediately** rather than holding the slot hostage for two
hours.

---

## Paging and rooms

**An SOS carries context, not just a name.** "Dana needs help" sends a colleague across the
boutique asking questions. "Dana needs help with Michal in Room 2" sends her straight there.
Room when one is assigned, customer name otherwise — so a boutique that never configures rooms
still gets something useful.

**The overlay is full-screen and interrupting.** A woman holding a dress with both hands will
miss a corner toast, and being missed is the only failure mode that matters here.

**Repeat taps reuse the open call.** Three jabs at the button is one cry for help, not three
overlays.

**Escalation works by clearing the target.** After 30 seconds unanswered, the named colleague is
removed, which *is* the broadcast — every manager's board then matches it. Acked calls never
escalate; already-broadcast calls never escalate twice.

**A board only renders calls it should answer** — mine to answer, mine to watch, or a broadcast
when I am a manager. An uninvolved colleague sees nothing.

---

## Roster

**Shifts are owner-defined templates.** One boutique opens late on Thursdays, another runs a
Saturday-night bridal evening. No two keep the same hours.

**Coverage targets are per shift *and* per role.** Two saleswomen and no seamstress is not the
same shift as one of each, though both are "two people".

**Availability and assignment are separate models.** Collapsing them would mean ticking a box put
you on the rota.

**Shortages count who is *rostered*, never who is *available*.** A shift with six volunteers and
nobody assigned is not covered.

**Slots snapshot their template's hours at generation.** A manager moving "Thursday late" an hour
later next month must not silently rewrite a week people already agreed to work.

**Publishing freezes the week,** and refuses availability edits with a message naming who to ask
rather than failing silently.

---

## Language and time

**Customers get he/ar/en; staff get he/en.** Hebrew is the default and the fallback.

**English is the source language; translations live in `.po` files.** Msgids are derived from
Odoo's own POT export — never hand-written. See [`odoo-traps.md` §9](odoo-traps.md).

**Everything is UTC in the database and `Instant`-equivalent in code;** local time exists only
for display and for constructs that are genuinely local. Israeli weeks run Sunday–Saturday, and
"today" is computed in `Asia/Jerusalem` rather than UTC — otherwise the boutique and the server
disagree about the date for three hours every evening.

---

## Verdict

**Do not rebuild MODRYN on Odoo.** Odoo is strong on catalog, storefront and back office, and
gives Hebrew + Arabic UI, RTL compilation, the Israeli week, ILS with agorot and product variants
for free. Every PRD differentiator is custom work on Odoo exactly as it was on FastAPI — and all
of it has now been built here, which confirmed the verdict rather than changing it.

The one place Odoo genuinely beat MODRYN: **`bus.bus` gives true websocket realtime in about
fifty lines**, on themed frontend pages as well as the back office, against MODRYN's shipped
five-second polling.

The defensible use of Odoo is as a *per-boutique back office* — stock, purchasing, invoicing,
payroll — alongside MODRYN, not as a replacement for the storefront and floor tools.

Full reasoning and evidence: [`../docs/scorecard.md`](../docs/scorecard.md).
