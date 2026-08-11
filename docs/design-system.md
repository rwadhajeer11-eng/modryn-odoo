# Design system — as built

The tokens this PoC actually uses, read from the files that define them. Source of truth for the
values is MODRYN's own `Frontend/packages/ui/src/theme.css`; this document records what landed
here and, honestly, where it has already drifted.

## Where tokens live

| File | Scope |
|---|---|
| `addons/modryn_theme/static/src/scss/primary_variables.scss` | The `$modryn-*` palette, declared once, **and** the **customer** storefront expressed in Odoo's native theming slots |
| `addons/modryn_theme/static/src/scss/modryn.scss` | Overrides that undo Odoo eCommerce chrome and enforce contrast |
| `addons/modryn_staff/static/src/floor/floor.scss` | All **staff** chrome — login, `/manage`, `/floor`, cards, chips, modals, SOS |
| `addons/modryn_roster/static/src/roster.scss` | The roster grid |

The nine `$modryn-*` variables are declared in `primary_variables.scss` and nowhere else;
`floor.scss` and `roster.scss` consume them. There is no `@import`, and adding one is worse
than useless: `AssetsBundle.compile_css`'s `sanitize()` rejects a local import outright
(`odoo/odoo/addons/base/models/assetsbundle.py:618-627`, *"Local import … is forbidden for
security reasons"*), pushes the message onto `css_errors` and emits the error in place of the
stylesheet — so the whole bundle dies, exactly as in rule 2 below. `web.assets_frontend` includes `web._assets_helpers`, which includes
`web._assets_primary_variables`, and the three files compile as one SCSS unit, so bundle
membership *is* the import. That include chain also reaches `web.assets_backend`, which is why
`primary_variables.scss` holds variables only: a CSS rule in it would be emitted into every
bundle in the product.

**Still not clean.** Nothing *stops* a future file redeclaring a token — the guard is convention,
not a check. And this only deduplicated the variables: `#FFFFFF` is still written as a literal in
both consumer files, and the tinted fills are hand-expanded `rgba(197, 160, 89, …)` rather than
derived from `$modryn-gold`. Retokenizing those was out of scope.

## Palette

| Role | Hex | Used for |
|---|---|---|
| Gold | `#C5A059` | Accents, CTA surfaces, primary. Borders and fills **only** |
| Gold-text | `#7F612B` | The **only** gold permitted to carry text |
| Ink | `#2B2118` | Body text, dark anchors, secondary |
| Ink-muted | `#6B5D4F` | Secondary text, captions, meta |
| Cream | `#FDFBF7` | Page background |
| Surface | `#F6F0E6` | Raised panels, table headers, footer, published states |
| White | `#FFFFFF` | Cards |
| Border | `#E4DACA` | Every hairline |
| Success | `#2E6B4F` | Available, met targets |
| Danger | `#A03232` | Errors, SOS, overdue |
| Warning | `#8A5A1E` | Storefront warnings |

Odoo's website palette maps them as `o-color-1`…`o-color-5` = gold, surface, cream, white, ink,
which is what its snippets, headers and footers paint themselves with.

## Typography

| Face | Stack | Role |
|---|---|---|
| Frank Ruhl Libre | `'Frank Ruhl Libre', 'David Libre', serif` | Display and headings |
| Assistant | `'Assistant', 'Heebo', sans-serif` | Body and UI |

Registered by merging into `$o-theme-font-configs` — `map-merge`, not assignment, so Odoo's own
font list survives and only gains ours.

**Two honest gaps.** The PoC loads both from the Google Fonts CDN because that is Odoo's native
path; MODRYN self-hosts via `@fontsource`, so production parity means shipping the woff2 files as
addon assets. And **Frank Ruhl Libre has no Arabic glyphs** — the Arabic storefront renders in a
fallback, and a real Arabic display face is still an open choice.

## Three rules that are load-bearing

These are not stylistic preferences. Breaking any of them breaks something real.

**1 · `#7F612B` is the only gold that may carry text.**
Odoo's auto-contrast puts white on `#C5A059`, which is **2.46:1** and fails WCAG AA outright.
`modryn.scss` overrides it explicitly. Gold at `#C5A059` is for borders and fills only.

**2 · `rgba()`, never `rgb(a b c / d)`.**
Odoo compiles SCSS with LibSass, which predates CSS Color Level 4. One modern colour function
raises `Function rgb is missing argument $green` and kills the **entire** frontend bundle — not
the rule, the bundle. Every `.scss` here carries a comment saying so, and `verify.sh` asserts the
compiled bundle exceeds 200 KB because a tiny bundle is the symptom.

**3 · Colour is never the only signal.**
Every status is also stated in words. A busy staff card says "Busy"; a short-staffed shift shows
`1/2` next to the role name; an amber roster card is reinforcement, not information. This holds
for greyscale screens and colour-blind readers, and it is why the roster shows counts rather than
just a tint.

## Layout conventions

- **Radius** 8px on cards and panels; 12px on the SOS overlay card; `9999px` on chips and badges.
  Inputs inherit Bootstrap's default — we never set one, so do not assume 8px there.
- **Logical properties** throughout — `margin-inline-start`, `border-block-end`, `padding-inline`.
  Staff screens flip between Hebrew RTL and English LTR per user, so physical `left`/`right`
  would break one of them.
- **Direction** is set from the user's language at the layout level:
  `dir="ltr"` when the language starts with `en`, otherwise `rtl`.
- **Grids** are `repeat(auto-fill, minmax(…, 1fr))` rather than fixed columns — the floor terminal
  is a tablet, a laptop and occasionally a wall display.

## Components

| Class | What it is |
|---|---|
| `.modryn_admin` / `.modryn_admin_nav` | Staff page shell and its nav, from `modryn_staff.staff_layout` |
| `.modryn_customer_card` | One customer on the floor — walk-in or booking |
| `.modryn_chip` | A staff member. `.is_primary` gold-filled, `.is_helper` dashed. Draggable |
| `.modryn_primary_slot` | The drop zone that makes someone primary |
| `.modryn_badge` | Status pill — `.is_free` `.is_busy` `.is_wait` `.is_muted` |
| `.modryn_bench` | The right-hand staff list; drag home to unassign |
| `.modryn_modal` | Finish-fitting handoff, SOS compose |
| `.modryn_sos_overlay` | Full-screen page. Interrupting by design |
| `.modryn_sos_mine` | The quiet strip for a call I raised myself |
| `.modryn_shift_card` | One roster shift. `.is_short` amber, `.is_published` surface |
| `.modryn_table` | Admin tables in `/manage/*` |

## Drag and drop

Pointer-based via Odoo's `useDraggable`, not HTML5 drag-and-drop — because the thing at a
boutique's front desk is a tablet.

Chips carry `data-employee`; drop zones carry `data-drop-target`, `data-drop-id` and
`data-drop-primary`. The hovered zone gets `.modryn_drop_hover` (gold ring); while a drag is
active the root carries `.is_dragging`, which outlines every legal zone so the manager can see
where a chip may land.

**Hit-testing is geometric, not DOM-based** — during a drag Odoo suppresses pointer-events
page-wide, so `elementFromPoint` returns only `<html>`. The innermost bounding rect containing
the pointer wins, which is what makes the primary slot beat the card it sits inside. See
[`../.memory/odoo-traps.md` §8](../.memory/odoo-traps.md).

## Writing UI copy

The voice is a boutique's, not a system's. Warm, direct, never mechanical.

- "We'll be with you shortly" — not "Position 7 in queue".
- "A place has opened up — it's yours if you'd like it" — not "Slot available".
- "Nobody rostered yet" — not "No records found".
- Empty states read as designed, not as a bug.
- Nothing ever tells a customer she was refused. A rejected walk-in's page becomes a warm
  invitation to book.

English is the source language; Hebrew and Arabic live in `.po` files whose msgids are derived
from Odoo's own POT export, never hand-written — see
[`../.memory/odoo-traps.md` §9](../.memory/odoo-traps.md).

## What this is not

Not a component library, and not MODRYN's design system. It is the subset needed to prove a
boutique-grade surface is reachable inside Odoo. `docs/scorecard.md` row 1 is candid about the
ceiling: the palette, fonts, radii and RTL all land through Odoo's *native* variable slots with
no `!important` and no specificity war (`primary_variables.scss`, 97 lines) — but `modryn.scss`
needed a further 112 lines to undo eCommerce chrome, and the header logo, footer boilerplate and
Odoo branding remain unstyled.
