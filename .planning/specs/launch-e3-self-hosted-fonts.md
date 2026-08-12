# E3 — self-hosted type

_Closes launch gate 6: "no request to `fonts.gstatic.com` on any page."_

## The problem, which is three problems

The storefront pulls Frank Ruhl Libre and Assistant from Google's CDN. That is a third-party
dependency on every cold page load, a privacy exposure under EU and Israeli law, and it makes a
load campaign measure Google's latency instead of ours. `docs/scorecard.md` records it as a
deliberate PoC shortcut; gate 6 is where it stops being acceptable.

It arrives by **three separate mechanisms**, and fixing one leaves the gate failing.

**1. A CSS `@import`, not a `<link>`.** `odoo/addons/website/static/src/scss/website.scss:5-18`
loops the selected font aliases and compiles

```scss
@import url("https://fonts.googleapis.com/css?family=#{unquote($-url)}&display=swap");
```

**into the stylesheet.** Measured on the live dev server, `web.assets_frontend.rtl.min.css`
contains exactly two such lines. There is nothing in `website/models/website.py` to change, and a
grep of the page HTML for `<link.*font` finds nothing and proves nothing.

**2. A preconnect nobody counted.** `odoo/addons/website/views/website_templates.xml:175`, inside
`website.layout`, unconditionally emits

```xml
<link rel="preconnect" href="https://fonts.gstatic.com/" crossorigin=""/>
```

Odoo's own comment above it is a TODO admitting it should be conditional. A preconnect is not a
hint the browser may ignore — Chrome and Firefox both open TCP and TLS to `fonts.gstatic.com` on
parse. With the `@import`s gone that connection carries no request, still resolves Google's DNS,
still exposes the visitor's IP, and **still fails gate 6 as written**.

**3. Eight more fonts, in edit mode.** `primary_variables.scss:46` uses `map-merge`, so
`$o-theme-font-configs` keeps Odoo's eight base Google families, each carrying a `'url'`.
`builder.fonts.scss` loops over *all* of them and imports every one for anyone in website edit
mode — and offers them in the font picker, so a boutique owner can put the third party back with
two clicks and no code change to review.

## What changes

| Where | What | Why there |
|---|---|---|
| `scripts/fetch_fonts.sh` (new) | Pull Google's own `&subset=hebrew,latin` woff2 bytes to `addons/modryn_theme/static/src/fonts/` as six stable names. **Run once, output committed.** | A build step that reaches the network is a build step that fails the day Google rate-limits the box. The v1 API with an explicit `&subset=` returns one face per weight instead of v2's per-unicode-range split (18 files for 6 weights). The hebrew subset carries **U+20AA (₪)** and the storefront prints shekel prices — `&subset=` is not optional. |
| `modryn.scss` | Six `@font-face` blocks, absolute `/modryn_theme/...` paths, `font-display: swap` | `primary_variables.scss:20-22` says VARIABLES ONLY, because the same include chain reaches `web.assets_backend` and a CSS rule there is emitted into **every bundle in the product**. `modryn.scss` is already in `web.assets_frontend`, so **the manifest needs no `assets` change.** |
| `primary_variables.scss:46-58` | `map-merge` → **bare assignment**, `'family'` only, no `'url'` | This is the fix for problem 3, not a tidy-up. `secondary_variables.scss:225` re-merges `$o-base-theme-font-configs`, so SYSTEM_FONTS survives and the picker still offers honest choices. |
| `views/website_templates.xml` (new) | `<xpath expr="//link[@rel='preconnect'][@href='https://fonts.gstatic.com/']" position="replace"/>` | Problem 2. Matched on the **href**, not position — the surrounding block gains and loses `<link>`s across point releases. |

Removing the `'url'` key **is** sufficient for the `@import`: the `@else` branch only fires when
`'attachment'` is set, and we set neither, so the loop emits nothing. `'family'` still drives
`$o-theme-font` and the CSS stacks, which are already correct.

Both families are SIL OFL 1.1 — self-hosting is explicitly permitted, no UI attribution required.
Ship `OFL.txt` beside the bytes.

## The explicit non-goal: Arabic

Neither family has Arabic glyphs. Odoo appends `'Odoo Unicode Support Noto'` to every family
stack, and there is **no `@font-face` for it anywhere in the frontend bundle** — the only two faces
in `web.assets_frontend` are FontAwesome and `odoo_ui_icons`. So `/ar/shop` renders in the OS
default sans-serif today, and will still render in the OS default sans-serif afterwards. Adding an
Arabic face is a design decision about how the Arabic storefront should look; it is not part of
removing a third party, and bundling it here would hide it.

## Verification — three numbers, because two can both be zero for the wrong reason

A bundle that failed to compile contains no `googleapis` either. LibSass dies **silently** on one
Color-Level-4 `rgb(a b c / d)` and takes the entire frontend bundle rather than the offending rule
(`primary_variables.scss:24`). So the check carries a positive control: `theme_marker` counts the
gold `#C5A059` in the same stylesheet, proving the file under inspection is really MODRYN's.

```bash
for path in /shop /book /en/shop /ar/shop; do
  html=$(curl -sk "$BASE$path")
  css=$(printf '%s' "$html" | grep -oE '/web/assets/[^"]*\.css' | head -1)
  body=$(curl -sk "$BASE$css")
  # html_3p  css_3p  faces  theme_marker
done
# required: 0  0  >=2  >0   on EVERY row
```

`/shop` and `/en/shop` compile to **different bundles** — `.rtl.min.css` and `.min.css`, 995,393
and 994,804 bytes — each carrying its own copy of the imports. Checking one is not checking both,
which is why the loop includes an LTR path.

Measured before the change, all four rows read `html_3p=1 css_3p=2 faces=0 theme_marker=28`. That
is the proof the check has teeth.

**Edit mode is checked separately, signed in:** `/odoo` → website → Edit → network tab shows zero
`fonts.googleapis.com`. That check only becomes possible because of the bare-assignment change; it
is unreachable by removing the `'url'` keys alone.

## Acceptance

| # | Check | Required |
|---|---|---|
| 1 | the four-path probe | `html_3p=0 css_3p=0 faces>=2 theme_marker>0` every row |
| 2 | `curl -I /modryn_theme/static/src/fonts/assistant-400.woff2` | 200 |
| 3 | `./scripts/verify.sh` | 0 failed; §2's Frank Ruhl Libre and `#C5A059` assertions still pass |
| 4 | `-u modryn_theme` on one tenant | no "Element cannot be located" — a failed xpath must be loud, not a no-op view |
| 5 | `/ar/shop` | still renders; no Arabic face added |
| 6 | `OFL.txt` present beside the woff2 files | yes |
