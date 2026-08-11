# Spec 0: Palette consolidation + slug-normalisation fix

**Epic**: bridal-availability · **Plan**: `../plans/bridal-availability.md` §F0 · **Status**: building
**Branch**: `feature/tenancy-slug-and-ics`

## Problem

Two things, both small, done before the expensive availability work starts.

**The palette is declared in three places.** `primary_variables.scss`, `floor.scss`, and
`roster.scss` (which redeclares four of them defensively — they were already in scope from
`floor.scss` in the same bundle). They agree today; nothing makes them agree tomorrow.
`docs/design-system.md` documented the drift rather than hiding it.

**The slug guard shipped in `56cfa46` is stricter than Odoo's own canonicalisation.** It
compares the raw URL segment against `cls._slug(record)`, but `_slugify` lowercases, strips
combining marks and collapses duplicate dashes. So a URL that differs from canonical only in
case or accent — `/shop/Aurora-Gown-7`, `/shop/Café-Blanc-7` — now returns a hard 404 on the
boutique's own dress, where core used to 301 it onto the canonical URL. Reproduced live:
`/shop/change_pricelist/ברירת-מחדל-USD-1` → 404, `…-usd-1` → 303. An autocapitalising phone
keyboard or a press-kit link produces exactly that, which is the same broken-shared-link harm
the tenancy fix was written to prevent.

**Baseline**: `verify.sh` 263/0/0 measured 2026-08-11. Recorded with the caveat that it was run
from the main checkout's suite against this worktree's addons — this branch's own `verify.sh`
carries ~199 lines more, so its true count is higher and is re-established by this feature's run.

## Requirements

1. The nine `$modryn-*` variables are declared once, in `primary_variables.scss`; the
   declaration blocks leave `floor.scss` and `roster.scss`.
2. **No new file and no `@import`.** `web.assets_frontend` includes `web._assets_helpers`
   includes `web._assets_primary_variables`, compiled as one SCSS unit, so bundle membership
   *is* the import. A local `@import` is not inert: `AssetsBundle.compile_css`'s `sanitize()`
   (`assetsbundle.py:618-627`) rejects it, pushes *"Local import … is forbidden"* onto
   `css_errors`, and emits the error in place of the stylesheet — the whole bundle dies.
3. That include chain also reaches `web.assets_backend`, so `primary_variables.scss` holds
   **variables only**. A CSS rule there would be emitted into every bundle in the product.
4. `rgba()` only, never `rgb(a b c / d)` — LibSass kills the entire bundle silently
   (`.memory/odoo-traps.md` trap 2). `verify.sh` asserts the compiled bundle exceeds 200 KB
   precisely to catch this.
5. The slug guard slugifies the requested half before comparing:
   `cls._slug(base.with_context(lang=c)) == cls._slugify(requested)`. `_slug` output is already
   slugified, so this is idempotent for the canonical case and still rejects a foreign tenant's
   name, which differs after slugification too.
6. The rename trade-off is spelled out in `ir_http.py`: the 404 also catches a record's **own**
   former name after a rename. Nothing in a URL separates that from another tenant's name; the
   backlog chose the 404 and MODRYN behaves the same. A dead marketing link after a rename is a
   decision, not a regression.
7. `docs/design-system.md` and `docs/scorecard.md` reflect the new arrangement, including the
   corrected line count for `primary_variables.scss` (72 → 97).
8. Backlog item 3 (design tokens) is removed and the list renumbered.

## Acceptance criteria

- Full `verify.sh` green, no section regressed, plus the new normalisation assertion.
- A slug that is not canonical but slugifies to canonical **301s**, not 404s. Hebrew has no
  case, so the test doubles a dash — same normalisation path, catalogue-independent, and a slug
  always carries its `-<id>` tail so a dash is always present.
- Cross-tenant slugs still 404 in both directions; the bare-id URL still 301s; `/en/` and `/ar/`
  still 200.
- Compiled frontend bundle still exceeds 200 KB — proves LibSass did not die on the moved
  variables.
- Storefront, `/floor` and `/roster` render with no colour, font or radius shift.

## Out of scope

Retokenising the raw `#FFFFFF` literals and the hand-expanded `rgba()` tints (6 gold, 5 ink, 2
danger). They are expanded from the hex by hand today, so a palette change still means editing
triplets — recorded rather than fixed, and not worth its own feature.

## Notes

Two findings from the adversarial review were left alone deliberately: the multi-language loop
accepts a slug matching in *any* published language (weaker as a tenancy check, but it prevents
false 404s the day a dress name is translated), and the two-argument
`/shop/<category>/<product>` route has a narrow enumeration oracle that is unreachable today
because neither tenant has a `product.public.category`.
