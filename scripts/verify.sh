#!/usr/bin/env bash
# End-to-end regression for the whole PoC: the five areas shipped in stage 0 plus
# the staff layer added in stage 1. Assumes the server is already running.
#
#   ./scripts/verify.sh
#
# Exits non-zero if any check fails, so it can gate a commit.
set -uo pipefail

# ~30 checks grep files under addons/ with relative paths, and sections 11 and 13
# read odoo.conf relatively. deploy/scripts/deploy.sh invokes this script by
# ABSOLUTE path and never cd's, so every one of those was already reading from
# whatever directory root happened to be in when they typed the sudo line.
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || exit 1

# Where this suite points. The defaults are the developer laptop, so an unset
# environment behaves exactly as it did before this block existed.
#
#   (dev)   ./scripts/verify.sh
#   (prod)  BASE_HOST=example.com BASE_SCHEME=https ODOO_CONF=/etc/odoo/odoo.conf \
#           MODRYN_DEMO_PASSWORD=... /opt/modryn/scripts/verify.sh
#
# BASE_HOST IS AN ON-BOX CONTRACT. Section 10a signs in with a real
# POST /staff/login, which through nginx is rate-limited (modryn_otp, 5r/m
# burst=3) and watched by fail2ban (maxretry=6). The geo block in
# deploy/nginx/modryn-http.conf exempts 127.0.0.1/32 for exactly this reason,
# and that exemption only applies when the suite runs ON the box, because
# $binary_remote_addr is nginx's peer. Driven from a laptop against production
# it is limit-counted, and three debugging runs later 10a reports "sara could
# not sign in" with entirely the wrong explanation.
#
# BASE_SCHEME drives the PORT default, because the port is a development
# artifact: dev talks to Odoo on 8069 directly, production talks to nginx on 443
# and a ":443" in the URL is at best noise and at worst a Host header that does
# not match server_name. Still overridable for the case that turns up anyway.
BASE_SCHEME="${BASE_SCHEME:-http}"
BASE_HOST="${BASE_HOST:-localtest.me}"
if [ "$BASE_SCHEME" = https ]; then BASE_PORT="${PORT:-}"; else BASE_PORT="${PORT:-8069}"; fi
turl() { printf '%s://%s.%s%s' "$BASE_SCHEME" "$1" "$BASE_HOST" "${BASE_PORT:+:$BASE_PORT}"; }

# NO -k ANYWHERE IN THIS FILE. Pointed at https:// with -k, an expired
# certificate, a wrong-name certificate and the self-signed placeholder
# provision.sh installs at bootstrap all look identical to a healthy one. A
# certificate fault must present as section 0 failing, loudly, rather than as
# 364 quiet passes. deploy/scripts/verify_edge.sh inspects the certificate on
# purpose, which is why it is the only file here that may pass -k.
BELLA="$(turl bella)"
NOGA="$(turl noga)"

# odoo.conf is NOT the same file in the two modes, and this is the trap. Both
# /opt/modryn and this repository are checkouts of the same tree, so
# ./odoo.conf EXISTS on the box too — and it is the DEVELOPER's config, listing
# bella,noga,modryn_template with absolute paths to a laptop. Reading it there
# would resolve a tenant list that has nothing to do with what the server runs,
# and every per-tenant loop below would assert against the wrong set.
ODOO_CONF="${ODOO_CONF:-odoo.conf}"

PASS=0
FAIL=0
SKIP=0

ok()   { printf "  \033[32mPASS\033[0m %s\n" "$1"; PASS=$((PASS+1)); }
bad()  { printf "  \033[31mFAIL\033[0m %s — %s\n" "$1" "$2"; FAIL=$((FAIL+1)); }
# Neither a pass nor a failure: a check that cannot run HERE. Counted separately
# so a green line never means "verified" when it means "not looked at" — section
# 12 is machine-specific and must not fail the suite on the staging server.
skip() { printf "  \033[33mSKIP\033[0m %s — %s\n" "$1" "$2"; SKIP=$((SKIP+1)); }
# Looked, found something, cannot judge it. Distinct from skip(): skip means the
# check could not run, note means it ran and the result is not ours to grade.
# Counted in neither column so it can never move the exit status — section 12
# reports on a repository a parallel session is actively writing, where the same
# query legitimately answers differently twice in one afternoon.
note() { printf "  \033[36mNOTE\033[0m %s — %s\n" "$1" "$2"; }
head_() { printf "\n\033[1m%s\033[0m\n" "$1"; }

# Prove a monitor has teeth: seed the exact condition it hunts, inside a
# transaction that is rolled back, and require the monitor to SEE it.
#
# Every count-based assertion in this suite that queries a table which happens to
# be empty returns 0 whether the code works or was deleted outright — six of them
# printed green for exactly that reason. A monitor with no subject is not evidence.
# $1 db, $2 label, $3 SQL that seeds the condition, $4 the monitor's own query.
detects() {
  local db="$1" label="$2" seed="$3" query="$4" saw
  saw=$(psql -d "$db" -v ON_ERROR_STOP=1 -tAq <<SQL 2>/dev/null
BEGIN;
$seed
$query
ROLLBACK;
SQL
)
  # >0 means the monitor fired on a planted row. Empty means the seed itself
  # failed (a renamed column), which is a broken check, not a passing one.
  [ -n "$saw" ] && [ "$saw" != "0" ] && ok "$db: $label — monitor detects a planted row ($saw)" \
    || bad "$db: $label monitor" "planting the condition produced '${saw:-<seed failed>}', not a detection — this check cannot fail and proves nothing"
}

# A booking's public token, derived exactly the way booking_comms.py derives it:
# the id, then the first 24 hex of HMAC-SHA256("booking:<db>:<id>") under that
# database's own secret. The db name is inside the signed message, not merely
# implied by the key — see the comment on _modryn_token.
bk_token() {
  local db="$1" id="$2" secret
  secret=$(psql -d "$db" -tAc "select value from ir_config_parameter where key='database.secret'")
  printf '%s-%s' "$id" \
    "$(printf 'booking:%s:%s' "$db" "$id" | openssl dgst -sha256 -hmac "$secret" -r | cut -c1-24)"
}

code() { curl -sg -o /dev/null -w "%{http_code}" "$1"; }
# Fetch to a FILE, never through $(...). Command substitution mangles the
# multi-hundred-KB pages Odoo serves, which silently turns real passes into
# phantom failures — it fooled this script into reporting the Arabic storefront
# broken when it was fine.
PAGE=/tmp/modryn_page.html
body() { curl -sg "$1" -o "$PAGE"; cat "$PAGE"; }
fetch() { curl -sg "$1" -o "$PAGE"; }

head_ "0. server"
[ "$(code "$BELLA/shop")" = "200" ] && ok "server is up" || { bad "server is up" "no 200 from $BELLA/shop"; echo; echo "Start it:  ./odoo/odoo-bin server -c odoo.conf --http-interface=127.0.0.1"; echo "On the box: BASE_HOST=\$DOMAIN BASE_SCHEME=https ODOO_CONF=/etc/odoo/odoo.conf $0"; exit 1; }

# THE tenant list, derived once. Every per-tenant assertion below loops over it.
# Sections used to write `for db in bella noga` inline, and one of them (10i) had
# quietly settled on bella alone — so a table missing on noga, or a drain cron
# deactivated on noga only, printed green.
#
# Source is odoo.conf's db_name, the same list that bounds the crons (section 11):
# a tenant added to the server is therefore verified by construction, and this can
# never wander into MODRYN's f*_test databases the way an unfiltered psql -l would.
# Filtered to databases where modryn_portal is actually INSTALLED, so a database
# without the product is not asserted against on columns it does not have.
#
# modryn_template is EXCLUDED even though it now carries the modules. It is a
# golden database, not a boutique: it deliberately holds no employees, no dresses
# and no bookings, because each tenant seeds its own after cloning. Every data
# assertion below would fail against it for the correct reason, which is the worst
# kind of red. Its SCHEMA is still checked — section 17 adds it back explicitly,
# which is where a missing index in the template genuinely does matter, since every
# clone inherits it.
TENANTS=""
for db in $(grep -E '^db_name *=' "$ODOO_CONF" | cut -d= -f2- | tr ',' ' '); do
  [ "$db" = "modryn_template" ] && continue
  INST=$(psql -d "$db" -tAc "select count(*) from ir_module_module where name='modryn_portal' and state='installed'" 2>/dev/null || echo 0)
  [ "${INST:-0}" = "1" ] && TENANTS="$TENANTS $db"
done
TENANTS="${TENANTS# }"
# An empty list would make every per-tenant loop below a no-op and print nothing —
# the one failure mode that looks exactly like success.
[ -n "$TENANTS" ] && ok "tenant list resolved from $ODOO_CONF db_name:$(printf ' %s' $TENANTS)" \
  || bad "tenant list" "no database in $ODOO_CONF db_name has modryn_portal installed — nothing below is actually checked"

# $BELLA and $NOGA are not "two tenants" — they are THESE two tenants, by name,
# and the asymmetry between them is load-bearing in fourteen psql call sites.
# Section 5 reads modryn_alteration_task on bella because noga holds zero BY
# DESIGN; section 16 reads modryn_shift_slot on bella for the same reason;
# sections 1, 2, 6 and 17 compare bella's catalog against noga's.
#
# Point this at a production box whose boutiques are called something else and
# every one of those becomes `psql -d bella` against a database that does not
# exist. psql writes to stderr, the `2>/dev/null || echo 0` idiom swallows it,
# and the assertion reads a legitimate 0 — green, on a check with no subject.
# Meanwhile $NOGA/shop hits nginx's catch-all and 404s, and section 0 would not
# have fired because section 0 only ever probes $BELLA.
#
# So both names must be IN the resolved list, and the suite refuses to continue
# otherwise. NOT skip() — a suite that cannot see its own subjects has verified
# nothing, and 364 green lines under those conditions is the single most
# dangerous output this file can produce.
#
# DELIBERATELY NOT DERIVED POSITIONALLY. `BELLA=$(nth 1)` / `NOGA=$(nth 2)`
# would silently swap the pair the day someone reorders db_name, and the "noga
# legitimately holds zero" assertions would then run against the tenant that has
# data — red, for a reason nobody could find. With a third boutique, positional
# derivation quietly ignores it. The names are the contract.
for t in bella noga; do
  case " $TENANTS " in
    *" $t "*) ;;
    *) bad "tenant '$t' is not on this server" \
         "this suite's cross-tenant sections are written against the pair (bella, noga) BY NAME — 14 psql calls and 68 URLs. On a server without both, they would query databases that do not exist and read the resulting empty output as a legitimate zero. Resolved list:$(printf ' %s' $TENANTS)"
       printf "\n\033[1m%d passed, %d failed, %d skipped\033[0m\n" "$PASS" "$FAIL" "$SKIP"
       exit 1 ;;
  esac
done
ok "the pair this suite is written against (bella, noga) is present"

head_ "1. tenancy isolation"
BELLA_DRESSES=$(body "$BELLA/shop" | grep -oE "שמלת [^<\"(]*" | sort -u | head -5)
NOGA_DRESSES=$(body "$NOGA/shop" | grep -oE "שמלת [^<\"(]*" | sort -u | head -5)
[ -n "$BELLA_DRESSES" ] && [ -n "$NOGA_DRESSES" ] && ok "both storefronts render dresses" || bad "both storefronts render dresses" "one is empty"
if [ -z "$(comm -12 <(echo "$BELLA_DRESSES") <(echo "$NOGA_DRESSES"))" ]; then
  ok "catalogs are disjoint"
else
  bad "catalogs are disjoint" "a dress name appears in both tenants"
fi
# DELETED: a booking-count comparison whose two branches both called ok(). It
# could not fail under any input — including both tenants reading zero, which is
# what a broken isolation boundary collapsing to one empty database looks like.
# The disjoint-catalogs check above is the real isolation proof and does fail.
#
# The URL half of isolation, which the catalog comparison above cannot see. A
# <model(...)> route matches on the id and throws the name away, so bella's
# /shop/<her dress>-2 opened on noga used to 301 onto NOGA's dress 2 and serve
# it. Probed on an id published in BOTH tenants — a 404 for an id noga simply
# does not have would prove nothing.
SHARED_ID=$(comm -12 <(psql -d bella -tAc "select id from product_template where is_published order by id" | sort) \
                     <(psql -d noga  -tAc "select id from product_template where is_published order by id" | sort) | head -1)
# Ask each tenant for its own canonical slug — the Location of the bare-id 301 —
# rather than re-implementing _slugify in bash. website overrides _slug to prefer
# seo_name, so a hand-rolled slug would probe a URL that 404s for the wrong reason.
BSLUG=$(curl -sg -o /dev/null -w '%{redirect_url}' "$BELLA/shop/$SHARED_ID"); BSLUG="${BSLUG#$BELLA}"
NSLUG=$(curl -sg -o /dev/null -w '%{redirect_url}' "$NOGA/shop/$SHARED_ID");  NSLUG="${NSLUG#$NOGA}"
if [ -z "$SHARED_ID" ] || [ -z "$BSLUG" ] || [ -z "$NSLUG" ]; then
  bad "cross-tenant product URL 404s" "could not build a slugged URL (id '$SHARED_ID', bella '$BSLUG', noga '$NSLUG') — if python-slugify ever gets installed it strips Hebrew, every slug collapses to a bare id, and this boundary quietly stops existing"
else
  # Positive control. Without it a broken URL builder makes the two 404 assertions
  # below pass for the wrong reason.
  [ "$(code "$BELLA$BSLUG")" = "200" ] && [ "$(code "$NOGA$NSLUG")" = "200" ] \
    && ok "each tenant serves its own slugged product URL" \
    || bad "each tenant serves its own slugged product URL" "the probe URLs are not even valid at home — bella $(code "$BELLA$BSLUG"), noga $(code "$NOGA$NSLUG")"
  [ "$(code "$NOGA$BSLUG")" = "404" ] && ok "bella's product URL 404s on noga" \
    || bad "bella's product URL 404s on noga" "got $(code "$NOGA$BSLUG") — noga answered for a name that is not hers"
  [ "$(code "$BELLA$NSLUG")" = "404" ] && ok "noga's product URL 404s on bella" \
    || bad "noga's product URL 404s on bella" "got $(code "$BELLA$NSLUG")"
  # The rule must stay narrow. A bare id is not a wrong name, and Odoo's canonical
  # 301 onto the slugged URL is load-bearing for SEO — if this flips to 404 the fix
  # has over-reached and every /shop/<id> link in the wild breaks.
  [ "$(code "$BELLA/shop/$SHARED_ID")" = "301" ] && ok "bare-id product URL still 301s to its canonical slug" \
    || bad "bare-id canonical 301" "expected 301, got $(code "$BELLA/shop/$SHARED_ID")"
  # ...and a slug that is not canonical but SLUGIFIES to canonical is still this
  # record's URL. _slugify lowercases, drops combining marks and collapses
  # duplicate dashes, so /shop/Aurora-Gown-7 and /shop/Café-Blanc-7 are the same
  # dress as their canonical form — core 301'd them onto it. The guard compared
  # the raw segment once and turned all of those into dead 404s, which is exactly
  # the broken shared link this whole section exists to prevent (a press-kit URL,
  # an autocapitalising phone keyboard). Hebrew has no case, so the variant here
  # doubles a dash: same normalisation path, and it works whatever the catalogue
  # is named. A slug always carries its "-<id>" tail, so there is always a dash.
  VARSLUG="${BSLUG/-/--}"
  [ "$(code "$BELLA$VARSLUG")" = "301" ] && ok "non-canonical slug that slugifies to canonical still 301s" \
    || bad "slug normalisation" "expected 301 for $VARSLUG, got $(code "$BELLA$VARSLUG") — the guard is stricter than Odoo's own canonicalisation"
  # The guard compares the requested slug against the record's canonical one, and
  # display_name is TRANSLATABLE. A real browser sending Accept-Language: en-US is
  # 303'd to the /en/ form before the guard runs, so the boutique's own canonical
  # link arrives to be compared in English. These must stay 200 — the day a dress
  # name is translated, a single-language comparison would 404 the canonical link
  # for every en-defaulting first-time visitor. (Plain curl cannot see this: its
  # UA trips is_a_bot() in http_routing, which pins request.lang to the default.)
  for p in /en /ar; do
    [ "$(code "$BELLA$p$BSLUG")" = "200" ] && ok "product URL still resolves under $p" \
      || bad "localized product URL $p" "answered $(code "$BELLA$p$BSLUG") — the slug guard is comparing one language only"
  done
fi

# database.secret is the HMAC key behind CSRF tokens, session tokens, the OTP
# hashes in otp.py and the booking token in booking_comms.py. new_boutique.sh
# clones with `createdb -T`, which copies it — and until 2026-08-11 nothing
# rotated it, so every tenant shared one key. Ids restart at 1 per database, so
# bella's token for booking 7 was byte-identical to noga's: one boutique's
# reminder link opened, confirmed and CANCELLED another boutique's appointment.
# Nothing in this suite noticed, because every check asked one tenant about
# itself.
SECRETS=$(for db in $TENANTS modryn_template; do
  psql -d "$db" -tAc "select value from ir_config_parameter where key='database.secret'" 2>/dev/null
done)
N_SECRET=$(printf '%s\n' "$SECRETS" | grep -cve '^\s*$')
N_UNIQ=$(printf '%s\n' "$SECRETS" | grep -ve '^\s*$' | sort -u | wc -l | tr -d ' ')
[ "$N_SECRET" -gt 1 ] && [ "$N_SECRET" = "$N_UNIQ" ] \
  && ok "every database has its own database.secret ($N_UNIQ distinct)" \
  || bad "database.secret is shared" "$N_SECRET databases, only $N_UNIQ distinct key(s) — a signed token from one tenant verifies in another"
# The rotation above is the root fix; this is the behaviour it protects, asserted
# end to end so it fails even if some future clone path forgets to rotate.
XB=$(psql -d bella -tAc "select id from calendar_event where modryn_is_booking and active limit 1")
if [ -n "$XB" ] && [ -n "$(psql -d noga -tAc "select id from calendar_event where id=$XB and modryn_is_booking")" ]; then
  XTOK=$(bk_token bella "$XB")
  [ "$(code "$BELLA/b/$XTOK")" = "200" ] \
    && ok "a booking token works in its own tenant (control)" \
    || bad "booking token control" "bella's own token did not open bella's booking $XB — the probe below would pass for the wrong reason"
  [ "$(code "$NOGA/b/$XTOK")" = "404" ] \
    && ok "bella's booking token 404s on noga" \
    || bad "cross-tenant booking token" "got $(code "$NOGA/b/$XTOK") — bella's link opened noga's booking $XB, and the same token can cancel it"
else
  skip "cross-tenant booking token" "no booking id exists in both tenants to probe with"
fi

head_ "2. theme + RTL"
body "$BELLA/shop" | grep -q 'dir="rtl"' && ok "storefront is RTL" || bad "storefront is RTL" "no dir=rtl"
CSS=$(body "$BELLA/shop" | grep -oE '/web/assets/[^"]*\.css' | head -1)
curl -sg "$BELLA$CSS" -o /tmp/modryn_bundle.css
grep -qF "#C5A059" /tmp/modryn_bundle.css && ok "gold token compiled into CSS" || bad "gold token compiled into CSS" "missing #C5A059"
grep -qF "Frank Ruhl Libre" /tmp/modryn_bundle.css && ok "display font present" || bad "display font present" "missing Frank Ruhl Libre"
# LibSass dies on modern rgb() and silently kills the bundle; a tiny bundle means it broke.
[ "$(wc -c < /tmp/modryn_bundle.css)" -gt 200000 ] && ok "frontend bundle compiled fully" || bad "frontend bundle compiled fully" "bundle suspiciously small — SCSS error?"

head_ "3. catalog"
body "$BELLA/shop" | grep -q "מחיר בתיאום" && ok "price-visibility toggle hides a price" || bad "price-visibility toggle" "no 'מחיר בתיאום' on the grid"
body "$BELLA/book/dress/2" | grep -q "אזל המלאי" && ok "out-of-stock size marked in size picker" || bad "out-of-stock size marked" "no 'אזל המלאי'"

head_ "4. booking"
[ "$(code "$BELLA/book")" = "200" ] && ok "standalone booking page" || bad "standalone booking page" "not 200"
[ "$(code "$BELLA/book/dress/2")" = "200" ] && ok "dress-bound booking page" || bad "dress-bound booking page" "not 200"
[ "$(code "$BELLA/book/dress/99999")" = "404" ] && ok "unknown dress 404s" || bad "unknown dress 404s" "expected 404"
# THE BUG THIS STAGE FIXES: bookings must never be organized by the public user.
for db in $TENANTS; do
  PUBLIC_OWNED=$(psql -d $db -tAc "select count(*) from calendar_event ce join res_users u on u.id=ce.user_id where ce.modryn_is_booking and u.login='public'")
  [ "${PUBLIC_OWNED:-1}" = "0" ] && ok "$db: no booking is organized by the public user" || bad "$db public-owned bookings" "$PUBLIC_OWNED still are"
done

head_ "5. languages"
# Hebrew is the DEFAULT and now comes from .po files rather than hardcoded
# literals. These two are the i18n regression test: if a msgid drifted, the
# storefront silently falls back to English and these fail.
fetch "$BELLA/shop"
grep -q "מחיר בתיאום" "$PAGE" && ok "he: price-on-request translated" || bad "he translation" "Hebrew msgid missing — .po drift?"
grep -q 'dir="rtl"' "$PAGE" && ok "he: RTL" || bad "he RTL" "no dir=rtl"

# Every shipped catalogue, every entry, both languages. The boutique screens
# are Hebrew-first with an Arabic toggle, so ONE blank msgstr is one word of
# English on an otherwise Hebrew page - and a blank entry is invisible in
# review because the file looks complete either way.
#
# What this CANNOT see: an English string that was never added to the .po at
# all, or one added without naming the view it appears in. Both need Odoo's own
# extractor. That used to be too expensive to run here; one combined export is
# four seconds, so the check below this one now does it.
# Three outcomes, not two. 0 clean, 1 blank entries found, 2 could not run -
# because a check that could not run has NOT passed, and the first version of
# this exited 0 when polib was missing, printing PASS over a check that never
# opened a file. That is the exact shape this suite exists to hunt.
.venv/bin/python - <<'PY'
import glob, os, sys
try:
    import polib
except ImportError:
    sys.exit(2)          # could not run - reported as a skip, never as a pass
blank = []
for path in sorted(glob.glob('addons/*/i18n/*.po')):
    for entry in polib.pofile(path):
        if entry.msgid and not entry.msgstr:
            blank.append('%s: %r' % (path, entry.msgid[:40]))
for line in blank[:8]:
    print(line)
sys.exit(1 if blank else 0)
PY
case $? in
  0) ok "every shipped .po entry is translated" ;;
  2) skip "translation coverage" "polib is not installed in .venv — this check did not run" ;;
  *) bad "translation coverage" "blank msgstr found — that term renders English" ;;
esac

# The other half, and the half that has actually cost time. It asks Odoo what
# strings EXIST and compares that with what the files carry, so it can see two
# things the check above cannot:
#
#   a string that was never added to the .po at all — which is every string a
#   brand-new screen introduces, and is how a whole screen shipped in English
#   while this suite reported green;
#
#   and a string that is translated but not BOUND to the view it appears in. A
#   model_terms translation binds to the view named in its reference lines, not
#   to the word, so "Bride" was translated, read as translated, and rendered in
#   English on the one screen that had just started using it.
#
# Four seconds, against the qa tenant. Same three outcomes as above: a check
# that could not run has not passed.
.venv/bin/python scripts/i18n_audit.py --db qa >/tmp/modryn_i18n.txt 2>&1
case $? in
  0) ok "every source string is translated, and bound to the view it is used in" ;;
  2) skip "translation completeness" "$(head -1 /tmp/modryn_i18n.txt)" ;;
  *) bad "translation completeness" "$(head -3 /tmp/modryn_i18n.txt | tr '\n' ' ')" ;;
esac

fetch "$BELLA/ar/shop"
grep -q 'lang="ar-001"' "$PAGE" && ok "ar: storefront serves ar-001" || bad "Arabic storefront" "no lang=ar-001"
# Markers from CORE Odoo's Arabic, not ours: "الصفحة الرئيسية" is the website
# module's Home menu and "الأعلى إلى الأقل" is website_sale's price sort. Both
# were verified present on a rendered /ar/shop.
#
# This used to grep for "بحث|المنتجات" and had been failing on a correctly
# translated page: Odoo 19 simply does not render either word here, so the
# check was reporting a translation outage that did not exist (the page
# carries 46 Arabic runs and the database holds 855 translated field labels).
# Deliberately NOT relaxed to "contains any Arabic character" — our own addons
# ship ar.po, so that would pass with core Odoo entirely untranslated, which is
# the exact failure this line exists to catch.
grep -qE "الصفحة الرئيسية|الأعلى إلى الأقل" "$PAGE" && ok "ar: core UI translated" || bad "core Arabic UI" "no core-Odoo Arabic strings found"

fetch "$BELLA/en/shop"
grep -q 'lang="en-US"' "$PAGE" && ok "en: storefront serves en-US" || bad "English storefront" "no lang=en-US"
grep -q 'dir="ltr"' "$PAGE" && ok "en: LTR (theme must not assume RTL)" || bad "en LTR" "expected dir=ltr"
grep -q "Price on request" "$PAGE" && ok "en: source strings render" || bad "en source strings" "English text missing"

head_ "6. walk-in queue"
[ "$(code "$BELLA/queue/checkin")" = "200" ] && ok "check-in form" || bad "check-in form" "not 200"
[ "$(code "$BELLA/queue/sign")" = "200" ] && ok "QR sign page" || bad "QR sign page" "not 200"
QR=$(code "$BELLA/report/barcode/?barcode_type=QR&value=test&width=200&height=200")
[ "$QR" = "200" ] && ok "QR image renders" || bad "QR image renders" "got $QR (rlPyCairo installed?)"

head_ "6-bis. the line cannot hold one number twice"
# The whole check-in flow, driven end to end, with row counts as the truth —
# deploy.sh gates rollback on this suite, and until this section existed the
# flow between a walk-in and the queue had no deploy-time check at all.
#
# noga ONLY: modryn.otp.code.issue() texts the code SYNCHRONOUSLY, and bella
# holds live Twilio credentials — this section must never cost money. noga has
# none, so _send_now falls through to the log. The phone is random per run so
# two consecutive runs stay under the 3-codes-per-hour budget, and every row
# this section creates is deleted at the end.
if ! echo " $TENANTS " | grep -q " noga "; then
  skip "6-bis" "noga is not among the served tenants here"
else
  QPHONE="+9725088$(printf '%05d' $((RANDOM % 100000)))"
  QROWS() { psql -d noga -tAc "select count(*) from modryn_queue_entry where phone='$QPHONE'"; }
  QJAR=$(mktemp)
  CT=$(curl -sg -c "$QJAR" "$NOGA/queue/checkin" | grep -oE 'name="csrf_token" value="[^"]*"' | sed 's/.*value="//;s/"//')
  SUBMIT=$(curl -sg -b "$QJAR" -c "$QJAR" -o /dev/null -w '%{http_code}' -X POST "$NOGA/queue/checkin/submit" \
    --data-urlencode "name=Verify Dupe" --data-urlencode "phone=$QPHONE" \
    --data-urlencode "client_type=bride" --data-urlencode "csrf_token=$CT")
  [ "$SUBMIT" = "303" ] && ok "submit answers 303 to the code step" || bad "submit" "got $SUBMIT, not 303"
  [ "$(QROWS)" = "0" ] && ok "submit alone creates no row" || bad "submit created a row" "$(QROWS) row(s) before any code was typed"

  CT2=$(curl -sg -b "$QJAR" -c "$QJAR" "$NOGA/queue/verify" | grep -oE 'name="csrf_token" value="[^"]*"' | sed 's/.*value="//;s/"//')
  CODE=$(./scripts/otp_code.sh noga "$QPHONE" 2>/dev/null)
  if [ -z "$CODE" ]; then
    bad "6-bis code recovery" "otp_code.sh found no live code for $QPHONE — did issue() fail?"
  else
    WRONG=$(printf '%06d' $(( (10#$CODE + 1) % 1000000 )))
    curl -sg -b "$QJAR" -c "$QJAR" -o /dev/null -X POST "$NOGA/queue/verify" \
      --data-urlencode "code=$WRONG" --data-urlencode "csrf_token=$CT2"
    [ "$(QROWS)" = "0" ] && ok "a wrong code creates no row" || bad "wrong code" "$(QROWS) row(s) after a wrong code"
    REDIR1=$(curl -sg -b "$QJAR" -c "$QJAR" -o /dev/null -w '%{redirect_url}' -X POST "$NOGA/queue/verify" \
      --data-urlencode "code=$CODE" --data-urlencode "csrf_token=$CT2")
    TOKEN1="${REDIR1##*/q/}"
    [ "$(QROWS)" = "1" ] && ok "the right code creates exactly one row" || bad "right code" "$(QROWS) row(s), not 1"
    STATE1=$(psql -d noga -tAc "select state from modryn_queue_entry where phone='$QPHONE'")
    [ "$STATE1" = "waiting" ] && ok "and it lands at waiting" || bad "landing state" "got '$STATE1', not waiting"
    case "$REDIR1" in */q/*) ok "verify redirects to her ticket" ;; *) bad "verify redirect" "got '$REDIR1'" ;; esac

    # The de-dupe, driven the way a real re-scan drives it: the entire flow
    # again with the same number. One row, same ticket, one code spent.
    CT3=$(curl -sg -b "$QJAR" -c "$QJAR" "$NOGA/queue/checkin" | grep -oE 'name="csrf_token" value="[^"]*"' | sed 's/.*value="//;s/"//')
    curl -sg -b "$QJAR" -c "$QJAR" -o /dev/null -X POST "$NOGA/queue/checkin/submit" \
      --data-urlencode "name=Verify Dupe Again" --data-urlencode "phone=$QPHONE" \
      --data-urlencode "client_type=bride" --data-urlencode "csrf_token=$CT3"
    CT4=$(curl -sg -b "$QJAR" -c "$QJAR" "$NOGA/queue/verify" | grep -oE 'name="csrf_token" value="[^"]*"' | sed 's/.*value="//;s/"//')
    CODE2=$(./scripts/otp_code.sh noga "$QPHONE" 2>/dev/null)
    REDIR2=$(curl -sg -b "$QJAR" -c "$QJAR" -o /dev/null -w '%{redirect_url}' -X POST "$NOGA/queue/verify" \
      --data-urlencode "code=$CODE2" --data-urlencode "csrf_token=$CT4")
    TOKEN2="${REDIR2##*/q/}"
    [ "$(QROWS)" = "1" ] && ok "a re-check-in adds no second row" || bad "re-check-in" "$(QROWS) row(s) for one number"
    [ -n "$TOKEN1" ] && [ "$TOKEN1" = "$TOKEN2" ] && ok "and she gets the SAME ticket back" \
      || bad "ticket identity" "first flow gave '$TOKEN1', second gave '$TOKEN2'"
  fi

  # The referee itself, poked directly: a raw INSERT must be refused BY NAME,
  # with an own-tenant control so this cannot pass because inserts are broken
  # generally.
  QIDX="modryn_queue_entry_modryn_open_phone_uniq"
  DUPERR=$(psql -d noga -c "insert into modryn_queue_entry (name, phone, client_type, state, create_uid, write_uid, create_date, write_date)
    values ('dupe probe', '$QPHONE', 'bride', 'waiting', 1, 1, now(), now())" 2>&1)
  echo "$DUPERR" | grep -q "$QIDX" && ok "Postgres refuses a second open row, naming $QIDX" \
    || bad "duplicate INSERT went through" "no $QIDX in: $(echo "$DUPERR" | head -1)"
  CPHONE="+9725077$(printf '%05d' $((RANDOM % 100000)))"
  psql -d noga -qc "insert into modryn_queue_entry (name, phone, client_type, state, create_uid, write_uid, create_date, write_date)
    values ('dupe control', '$CPHONE', 'bride', 'waiting', 1, 1, now(), now())" 2>/dev/null \
    && ok "control: a fresh number inserts cleanly" \
    || bad "control insert failed" "the refusal above may be a broken table, not the index"

  # Leave the tenant as found: entries, codes and the outbox rows the join
  # text queued. SQL on purpose — the ORM path would text the day-waitlist.
  psql -d noga -qc "delete from modryn_queue_entry where phone in ('$QPHONE', '$CPHONE');
    delete from modryn_otp_code where phone='$QPHONE';
    delete from modryn_sms_outbox where phone='$QPHONE';"
  rm -f "$QJAR"

  # Install-and-upgrade wiring, same trap §18/§19 guard as modryn_portal.
  grep -q "'pre_init_hook': 'pre_init_hook'" addons/modryn_queue_poc/__manifest__.py \
    && grep -q "'post_init_hook': 'post_init_hook'" addons/modryn_queue_poc/__manifest__.py \
    && ok "queue manifest declares both install hooks" \
    || bad "queue install hooks" "only migrations/ is wired — every cloned boutique would skip the dedupe and the index check"
  grep -q "from .schema_guard import post_init_hook, pre_init_hook" addons/modryn_queue_poc/__init__.py \
    && ok "queue hooks are attributes of the package, where getattr() looks" \
    || bad "queue hook export" "hooks not re-exported from __init__.py"
  QMANIFEST_V=$(grep -oE "19\.0\.[0-9.]+" addons/modryn_queue_poc/__manifest__.py | tail -1)
  QMIG_V=$(basename "$(ls -d addons/modryn_queue_poc/migrations/19.0.* | sort -V | tail -1)")
  [ "$QMANIFEST_V" = "$QMIG_V" ] && ok "queue manifest $QMANIFEST_V matches migrations/$QMIG_V" \
    || bad "queue migration version" "manifest is $QMANIFEST_V but the newest migration dir is $QMIG_V"
  for db in $TENANTS; do
    QREC=$(psql -d "$db" -tAc "select latest_version from ir_module_module where name='modryn_queue_poc'")
    QNEWEST=$(printf '%s\n%s\n' "$QREC" "$QMIG_V" | sort -V | tail -1)
    if [ "$QREC" = "$QMIG_V" ]; then
      ok "$db: recorded $QREC — queue migrations/$QMIG_V has been applied"
    elif [ "$QNEWEST" = "$QMIG_V" ]; then
      ok "$db: recorded $QREC — queue migrations/$QMIG_V is pending and will run"
    else
      bad "$db queue migration can never run" "ir_module_module records $QREC, already past migrations/$QMIG_V"
    fi
  done
fi

head_ "7. staff layer"
# Per tenant, not bella alone: a boutique whose staff never seeded has no one who
# can open the floor board, and that is invisible from the other tenant's data.
for db in $TENANTS; do
  EMP=$(psql -d $db -tAc "select count(*) from hr_employee where active")
  [ "${EMP:-0}" -ge 3 ] && ok "$db: employees exist ($EMP)" || bad "$db employees" "only ${EMP:-0}"
  ROLES=$(psql -d $db -tAc "select count(*) from modryn_staff_role where active")
  [ "${ROLES:-0}" -ge 3 ] && ok "$db: staff roles exist ($ROLES)" || bad "$db staff roles" "only ${ROLES:-0}"
  PORTAL=$(psql -d $db -tAc "select count(*) from res_groups_users_rel r join res_groups g on g.id=r.gid join ir_model_data d on d.res_id=g.id and d.model='res.groups' where d.module='base' and d.name='group_portal'")
  [ "${PORTAL:-0}" -ge 1 ] && ok "$db: portal staff accounts exist ($PORTAL)" || bad "$db portal staff accounts" "none found"
  # Roles are a MANY-to-many since 19.0.1.7.0, and an empty many-to-many is not
  # an error - it is just empty. A woman with no role keeps her home and her
  # profile and silently loses every other page, so a whole team can be locked
  # out with nothing in any log. This counts the ones with none, which is the
  # shape both ways it could happen take: a migration that saved and restored
  # nothing, and a write to the old single-value name being discarded.
  # Administrator is excluded - it is Odoo's own row, has no boutique job, and
  # is role-less in the template every boutique is cloned from.
  NOROLE=$(psql -d $db -tAc "select count(*) from hr_employee e
    where e.active and e.name <> 'Administrator'
      and not exists (select 1 from hr_employee_modryn_staff_role_rel x
                       where x.hr_employee_id = e.id)")
  [ "${NOROLE:-1}" = "0" ] && ok "$db: every employee carries at least one role"     || bad "$db employee roles" "${NOROLE:-?} employee(s) with none — they can open only their home and profile"
done
[ "$(code "$BELLA/staff/login")" = "200" ] && ok "staff login page" || bad "staff login page" "not 200"
# Unauthenticated access to staff surfaces must not be 200.
for path in /floor /staff/home /manage/staff /manage/roles; do
  C=$(code "$BELLA$path")
  [ "$C" != "200" ] && ok "$path refuses anonymous access ($C)" || bad "$path refuses anonymous access" "returned 200 while logged out"
done

# --- the role→page matrix -------------------------------------------------
# The grant table must exist everywhere, and on the template — the one
# database no owner ever hand-edits — every role must carry its seeded
# defaults, because every future boutique is a clone of exactly that state.
for db in $TENANTS modryn_template; do
  RP=$(psql -d "$db" -tAc "select count(*) from information_schema.tables where table_name='modryn_role_page'")
  [ "$RP" = "1" ] && ok "$db: role→page table exists" || bad "$db role→page table" "missing — the matrix has nowhere to live"
done
UNGRANTED=$(psql -d modryn_template -tAc "select count(*) from modryn_staff_role r
  where not exists (select 1 from modryn_role_page p where p.role_id = r.id)" 2>/dev/null)
[ "${UNGRANTED:-1}" = "0" ] && ok "template: every role carries page grants" \
  || bad "template role grants" "${UNGRANTED:-?} role(s) with no rows — cloned boutiques would strand that role on its home page"
# Page routes ask the matrix, not just a group — greppable teeth, the slot-
# snapshot style. If one of these disappears, the matrix silently stops
# meaning anything for that page.
grep -q "access.can_view('floor')" addons/modryn_staff/controllers/floor.py \
  && grep -q "access.can_view('roster')" addons/modryn_roster/controllers/roster.py \
  && grep -q "access.can_view('atelier')" addons/modryn_atelier/controllers/atelier.py \
  && grep -q "access.can_view('reports')" addons/modryn_ops/controllers/reports.py \
  && ok "all four matrix-gated pages ask can_view()" \
  || bad "matrix gates" "a page route no longer consults the matrix"
# The three xpath nav injections are gone FROM THE DATABASE, not merely from
# the source — a forgotten -u leaves the stale inherit view pointing at an
# anchor that no longer exists, and every staff page dies at view load.
# modryn_template is in the loop for the same reason it is in §17: a stale
# view there is invisible today and inherited by every boutique cloned
# tomorrow, and new_boutique.sh's post-clone gate checks only indexes.
for db in $TENANTS modryn_template; do
  STALE=$(psql -d "$db" -tAc "select count(*) from ir_ui_view where key in
    ('modryn_ops.manage_nav_audit','modryn_ops.staff_nav_reports','modryn_roster.manage_nav_shifts')")
  [ "$STALE" = "0" ] && ok "$db: no stale nav-injection views" \
    || bad "$db stale nav views" "$STALE inherit view(s) still target anchors the unified nav removed — run -u modryn_ops,modryn_roster"
done

head_ "8. customer portal"
[ "$(code "$BELLA/my/login")" = "200" ] && ok "portal login page" || bad "portal login" "not 200"
# Anonymous must never reach someone's bookings.
C=$(code "$BELLA/my/bookings")
[ "$C" != "200" ] && ok "my/bookings refuses anonymous ($C)" || bad "my/bookings anonymous" "returned 200"
for db in $TENANTS; do
  OTP_TBL=$(psql -d $db -tAc "select count(*) from information_schema.tables where table_name='modryn_otp_code'")
  [ "$OTP_TBL" = "1" ] && ok "$db: OTP table exists" || bad "$db OTP table" "missing"
  # Codes must be stored hashed, never in the clear. The teeth test first: an
  # empty modryn_otp_code answers 0 whether hashing works or was ripped out.
  detects "$db" "OTP hashing" \
    "INSERT INTO modryn_otp_code (phone, code_hash, expires_at, create_uid, write_uid, create_date, write_date) VALUES ('+972500000000','1234', now() + interval '5 minutes',1,1,now(),now());" \
    "SELECT count(*) FROM modryn_otp_code WHERE length(code_hash) < 40;"
  CLEAR=$(psql -d $db -tAc "select count(*) from modryn_otp_code where length(code_hash) < 40" 2>/dev/null || echo 0)
  [ "${CLEAR:-0}" = "0" ] && ok "$db: OTP codes are hashed" || bad "$db OTP hashing" "$CLEAR rows look unhashed"
done

head_ "9. atelier"
for db in $TENANTS; do
  PIECES=$(psql -d $db -tAc "select count(*) from modryn_garment_piece where active" 2>/dev/null || echo 0)
  [ "${PIECES:-0}" -ge 5 ] && ok "$db: garment pieces seeded ($PIECES)" || bad "$db garment pieces" "only ${PIECES:-0}"
done
for path in /atelier /manage/pieces; do
  C=$(code "$BELLA$path")
  [ "$C" != "200" ] && ok "$path refuses anonymous ($C)" || bad "$path anonymous" "returned 200"
done
# bella ONLY, deliberately: alteration tasks are demo workload, not structure.
# noga holds zero by design (it is the tenant kept bare to prove isolation), so a
# $TENANTS loop here would fail on a database that is behaving correctly.
TASKS=$(psql -d bella -tAc "select count(*) from modryn_alteration_task" 2>/dev/null || echo 0)
[ "${TASKS:-0}" -ge 1 ] && ok "bella: alteration tasks exist ($TASKS)" || bad "alteration tasks" "none"

# --- the workshop queue engine ---------------------------------------------
# Schema everywhere (template included — clones inherit it), and greppable
# teeth on the three behaviors the engine cannot afford to lose silently:
# the required-fields door, the SKIP LOCKED pull, and the outbox-only SMS.
# The contract change these guard already broke one consumer once — the k6
# manager scenario posted priority-less creates and went red on a healthy
# server — so removing any of them must fail this suite by name.
for db in $TENANTS modryn_template; do
  PCOL=$(psql -d "$db" -tAc "select count(*) from information_schema.columns
    where table_name='modryn_alteration_task' and column_name='priority'")
  WCOL=$(psql -d "$db" -tAc "select count(*) from information_schema.columns
    where table_name='modryn_staff_role' and column_name='is_workshop'")
  [ "$PCOL" = "1" ] && [ "$WCOL" = "1" ] && ok "$db: priority + is_workshop columns exist" \
    || bad "$db workshop schema" "priority=$PCOL is_workshop=$WCOL — the queue engine has nothing to order by"
done
grep -q "FOR UPDATE SKIP LOCKED" addons/modryn_atelier/models/alteration_task.py \
  && ok "pull-next takes its row under FOR UPDATE SKIP LOCKED" \
  || bad "pull-next lock" "two simultaneous finishers can be handed the same task"
grep -q "missing_priority" addons/modryn_atelier/controllers/atelier.py \
  && grep -q "missing_due" addons/modryn_atelier/controllers/atelier.py \
  && ok "the create door still requires priority and due date" \
  || bad "create door" "a task without urgency would sit wherever the defaults drop it"
grep -q "send_async" addons/modryn_staff/models/notify.py \
  && ! grep -qE "\.send\(" addons/modryn_staff/models/notify.py \
  && ok "assignment SMS goes through the outbox, never the blocking door" \
  || bad "notify send path" "blocking send() is reserved for the OTP and the 24h reminder"

head_ "10. dispatch board"
# Helpers live in a through-model, not a bare m2m: join order decides who is
# promoted when a primary leaves, and an m2m would order by employee NAME.
for db in $TENANTS; do
  T=$(psql -d $db -tAc "select count(*) from information_schema.tables where table_name='modryn_floor_helper'")
  [ "$T" = "1" ] && ok "$db: helper through-model exists" || bad "$db modryn_floor_helper" "missing"
  OLD=$(psql -d $db -tAc "select count(*) from information_schema.tables where table_name in ('modryn_queue_helper_rel','modryn_event_helper_rel')")
  [ "$OLD" = "0" ] && ok "$db: superseded helper m2m tables dropped" || bad "$db old helper tables" "$OLD still present"
  # Both card kinds must be linkable, or one of them silently loses its helpers.
  COLS=$(psql -d $db -tAc "select count(*) from information_schema.columns where table_name='modryn_floor_helper' and column_name in ('entry_id','event_id','employee_id')")
  [ "$COLS" = "3" ] && ok "$db: helper links walk-ins and bookings" || bad "$db helper columns" "expected 3, got $COLS"
done
# jsonrpc action routes must refuse a session-less caller (Odoo answers with a
# SessionExpired error payload, never a result).
ASSIGN=$(curl -sg -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"call","params":{"target":"queue","target_id":1,"employee_id":1}}' \
  "$BELLA/floor/assign" | grep -c '"result"')
[ "$ASSIGN" = "0" ] && ok "/floor/assign refuses anonymous" || bad "/floor/assign anonymous" "returned a result"
# English staff surface: the language toggle's target must exist and be LTR.
fetch "$BELLA/en/floor"
C=$(code "$BELLA/en/floor")
[ "$C" != "200" ] && ok "/en/floor refuses anonymous ($C)" || bad "/en/floor anonymous" "returned 200"

head_ "10a. authenticated surfaces actually render"
# Anonymous 303s prove the GATE, not the PAGE. A non-stored field used in a
# search domain took /floor down with a 500 while every anonymous check still
# passed — so sign in and look at the real thing.
#
# The password comes from the environment, same variable seed_staff.py reads.
# It used to be the burned demo literal right here, which meant this suite went
# green only for as long as nobody rotated the demo credential — and kept a
# burned secret alive in the repo that the seeder had already stopped using.
STAFF_PW="${MODRYN_DEMO_PASSWORD:-}"
if [ -z "$STAFF_PW" ]; then
  bad "10a. staff sign-in" "MODRYN_DEMO_PASSWORD unset — export the password you seeded with"
else
JAR=$(mktemp); TOKEN_URL="$BELLA/staff/login"
CT=$(curl -sg -c "$JAR" "$TOKEN_URL" | grep -oE 'name="csrf_token" value="[^"]*"' | sed 's/.*value="//;s/"//')
curl -sg -b "$JAR" -c "$JAR" -o /dev/null -X POST "$TOKEN_URL" \
  --data-urlencode "username=sara" --data-urlencode "password=$STAFF_PW" --data-urlencode "csrf_token=$CT"
# Diagnose the sign-in ONCE, before asserting on pages. A wrong password produces
# a session-less jar, and every page below then answers 303 — four mysterious
# redirect failures that read like broken routes and send you into the controllers.
# The password these databases hold predates the credential-hygiene change, so a
# mismatch between MODRYN_DEMO_PASSWORD and the seeded value is the LIKELY cause,
# not a code fault. Say that instead of making someone deduce it.
SESSION=$(curl -sg -b "$JAR" -o /dev/null -w "%{http_code}" "$BELLA/floor")
if [ "$SESSION" = "200" ]; then
  ok "staff sign-in succeeded"
  # The availability grid is seven days by three parts of the day, ALWAYS -
  # it is computed from the calendar, not from however many shift templates the
  # boutique happens to have. This is the one assertion in the suite that can
  # actually go red if it regresses to being template-driven: the older checks
  # ("at least 5 templates", "at least 5 slots") pass identically at 5 and at 21.
  CELLS=$(curl -sg -b "$JAR" "$BELLA/roster" | grep -o 'modryn_avail_cell' | wc -l)
  [ "${CELLS:-0}" -eq 21 ] && ok "the week grid offers all 21 cells" \
    || bad "roster grid" "rendered ${CELLS:-0} cells, wanted 21 (7 days x morning/midday/evening)"
  # /manage/shifts is deliberately NOT in this list. It is gated on
  # _is_owner(), and sara is a shift MANAGER — so 404 is the correct answer and
  # asserting 200 would have demanded that the owner-only gate be broken. It is
  # asserted the other way round, immediately below.
  SHIFTS_MGR=$(curl -sg -b "$JAR" -o /dev/null -w "%{http_code}" "$BELLA/manage/shifts")
  [ "$SHIFTS_MGR" = "404" ] && ok "/manage/shifts stays owner-only (a manager gets 404)" \
    || bad "/manage/shifts owner gate" "a shift manager got $SHIFTS_MGR, wanted 404"
  # The window belongs to the manager. Asserted with a real signed-in session,
  # because the anonymous check above can only ever reach Odoo's login redirect
  # — it never touches _is_manager() at all.
  WR=$(curl -sg -b "$JAR" -o /dev/null -w "%{http_code}" -X POST "$BELLA/roster/window/rule" \
    --data-urlencode "week=0")
  [ "$WR" != "404" ] && ok "a manager may set the submission window ($WR)" \
    || bad "window rule for a manager" "got 404 — the manager gate is refusing the manager"
  # ...but NOT for a week her team is already standing. Asserted on the DATA
  # and not on the status code: both routes answer a refusal with the same 303
  # they answer a success with, so a status check stays green over exactly the
  # defect this exists to catch. That defect was real: the guard was pasted
  # twice into one route and left out of the other entirely, and nothing in the
  # suite noticed.
  CUR=$(psql -d bella -tAc "select to_char((now() at time zone 'Asia/Jerusalem')::date - ((extract(dow from (now() at time zone 'Asia/Jerusalem')::date))::int), 'YYYY-MM-DD')")
  psql -d bella -qc "insert into modryn_roster_week (week_start) values ('$CUR') on conflict (week_start) do nothing;" >/dev/null 2>&1
  psql -d bella -qc "update modryn_roster_week set opens_at=null, closes_at=null where week_start='$CUR';" >/dev/null 2>&1
  curl -sg -b "$JAR" -o /dev/null -X POST "$BELLA/roster/window/week" \
    --data-urlencode "csrf_token=$CT" --data-urlencode "week=-1" \
    --data-urlencode "opens_date=$CUR" --data-urlencode "opens_time=09:00" \
    --data-urlencode "closes_date=$CUR" --data-urlencode "closes_time=21:00"
  LEAK=$(psql -d bella -tAc "select count(*) from modryn_roster_week where week_start='$CUR' and opens_at is not null")
  [ "${LEAK:-1}" = "0" ] && ok "the window refuses a week already being worked" \
    || bad "window on a worked week" "a POST with week=-1 wrote onto $CUR - the guard is missing from /roster/window/week"
  for path in /floor /atelier /roster; do
    C=$(curl -sg -b "$JAR" -o /dev/null -w "%{http_code}" "$BELLA$path")
    [ "$C" = "200" ] && ok "$path renders for a manager" || bad "$path for a manager" "got $C"
  done
  BOARD=$(curl -sg -b "$JAR" -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","method":"call","params":{}}' "$BELLA/floor/data")
  echo "$BOARD" | grep -q '"result"' && ok "/floor/data returns a board" || bad "/floor/data" "no result — server error?"
  echo "$BOARD" | grep -q '"pending"' && ok "board carries the arrivals gate" || bad "board pending panel" "key missing"
  # No assertion here on the queue rows' own keys. They exist only while
  # somebody is standing in the line, and bella's is empty in its seeded state -
  # a check that can only run when a stranger happens to have walked in fails
  # over nothing, which is the same fault as passing over nothing. The browser
  # suite asserts the with-the-team panel with a customer actually in it.
  SUP=$(curl -sg -b "$JAR" -o /dev/null -w "%{http_code}" "$BELLA/shift-supervisor")
  [ "$SUP" = "200" ] && ok "/shift-supervisor renders for a manager"     || bad "/shift-supervisor for a manager" "got $SUP"
  # TRANSLATED, not merely present. A new screen ships every one of its strings
  # in English, and the .po check a few hundred lines up says in its own comment
  # that it cannot see a string that was never added to the file at all. This is
  # the check that can: it reads the rendered page.
  curl -sg -b "$JAR" "$BELLA/shift-supervisor" | grep -q 'אחראי משמרת'     && ok "he: the supervisor screen is translated"     || bad "supervisor translation" "the Hebrew heading is missing - the screen is shipping in English"
else
  bad "10a. staff sign-in" "sara could not sign in (/floor answered $SESSION, not 200). The exported MODRYN_DEMO_PASSWORD does not match the value these databases were seeded with — they predate the credential-hygiene change. Fix: export MODRYN_DEMO_PASSWORD as the seeded value, or re-seed with 'MODRYN_DEMO_PASSWORD=... .venv/bin/python scripts/seed_staff.py' (which rotates the developer's own manual login too). Every 10a assertion below is skipped, not passed."
fi
rm -f "$JAR"
fi

head_ "10b. comms engine"
# The confirmation page promises an SMS; these prove the promise is kept.
for db in $TENANTS; do
  RTBL=$(psql -d $db -tAc "select count(*) from information_schema.columns where table_name='calendar_event' and column_name in ('modryn_reminder_sent_at','modryn_confirmed_at','modryn_lang')")
  [ "$RTBL" = "3" ] && ok "$db: booking comms fields exist" || bad "$db comms fields" "expected 3, got $RTBL"
  CRON=$(psql -d $db -tAc "select count(*) from ir_cron c join ir_act_server a on a.id=c.ir_actions_server_id where a.code like '%_modryn_send_reminders%'")
  [ "${CRON:-0}" -ge 1 ] && ok "$db: 24h reminder cron installed" || bad "$db reminder cron" "not found"
done
# A forged token must never open somebody's appointment.
[ "$(code "$BELLA/b/1-deadbeefdeadbeefdeadbeef")" = "404" ] && ok "forged booking token 404s" || bad "forged token" "did not 404"
# The submit-time collision guard must agree with the slot list about cancelled
# bookings, or a freed slot can be offered and then refused.
grep -q "modryn_cancelled_at" addons/modryn_booking/controllers/main.py && ok "collision guard honours cancellations" || bad "collision guard" "still counts cancelled bookings"

head_ "10b-bis. add to calendar (.ics)"
# A bare 200 here would pass on an empty file. The assertions with teeth are the
# DTSTART — which must equal the exact UTC start Postgres holds, so this fails if
# timezone handling drifts, the wrong booking is exported, or the body is a stub —
# and the UID, which must be derived from the booking rather than vobject's clock.
for db in $TENANTS; do
  case "$db" in bella) BASE="$BELLA" ;; noga) BASE="$NOGA" ;; *) continue ;; esac
  ROW=$(psql -d "$db" -tAc "select id || '|' || to_char(start, 'YYYYMMDD\"T\"HH24MISS\"Z\"')
        from calendar_event
        where modryn_is_booking and modryn_cancelled_at is null and active
        order by start desc limit 1")
  if [ -z "$ROW" ]; then
    skip "$db: .ics export" "no live booking to export — nothing to assert against"
    continue
  fi
  EID="${ROW%%|*}"; DTSTART="${ROW##*|}"
  TOK=$(bk_token "$db" "$EID")
  HDR=$(curl -sg -D- -o "$PAGE" "$BASE/b/$TOK/ics")
  printf '%s' "$HDR" | grep -qi '^HTTP/1.1 200' \
    && ok "$db: /b/<token>/ics answers 200" \
    || bad "$db .ics status" "$(printf '%s' "$HDR" | head -1 | tr -d '\r')"
  printf '%s' "$HDR" | grep -qi '^Content-Type: text/calendar' \
    && ok "$db: served as text/calendar" \
    || bad "$db .ics content-type" "not text/calendar — a phone would save a blob instead of handing it to the calendar app"
  grep -q '^BEGIN:VCALENDAR' "$PAGE" \
    && ok "$db: body is a VCALENDAR" || bad "$db .ics body" "no BEGIN:VCALENDAR"
  grep -q "^DTSTART:$DTSTART" "$PAGE" \
    && ok "$db: DTSTART is the booking's real start ($DTSTART)" \
    || bad "$db .ics DTSTART" "expected $DTSTART, file has '$(grep '^DTSTART' "$PAGE" | tr -d '\r')'"
  grep -q "^UID:modryn-booking-$EID@" "$PAGE" \
    && ok "$db: UID is derived from the booking, not the clock" \
    || bad "$db .ics UID" "vobject's invented UID survived — every download would be a NEW event in her calendar, and it carries the server hostname"
  # A bare ATTENDEE:MAILTO: (every partner here is phone-only) makes Outlook offer
  # accept/decline on what is a personal appointment. ORGANIZER is the other half
  # of that trigger, and it is NOT symmetric across tenants: bella's organizer
  # partner has no email so stock omits the line, noga's resolves to OdooBot and
  # ships one. Asserting only on bella would have missed it — this loop runs both.
  grep -q '^ATTENDEE' "$PAGE" \
    && bad "$db .ics attendee" "an empty ATTENDEE:MAILTO: line came back" \
    || ok "$db: no empty attendee line"
  grep -q '^ORGANIZER' "$PAGE" \
    && bad "$db .ics organizer" "$(grep '^ORGANIZER' "$PAGE" | tr -d '\r') — Outlook will offer accept/decline on a personal appointment" \
    || ok "$db: no organizer line"
  # The token can cancel and never expires. A DESCRIPTION syncs to every calendar
  # the file is shared into, which for a bridal fitting means the mother and the
  # bridesmaids. It must not be in there.
  grep -q "$TOK" "$PAGE" \
    && bad "$db .ics leaks the booking token" "the cancel credential is in the file, which syncs to every shared calendar and never expires" \
    || ok "$db: .ics carries no booking token"
  # She cancels through our own page; the fitting must not sit in her calendar
  # afterwards looking live. Same UID + STATUS:CANCELLED is what retracts it.
  CID=$(psql -d "$db" -tAc "select id from calendar_event
        where modryn_is_booking and modryn_cancelled_at is not null and active limit 1")
  if [ -z "$CID" ]; then
    skip "$db: cancelled booking .ics" "no cancelled booking to export"
  else
    fetch "$BASE/b/$(bk_token "$db" "$CID")/ics"
    grep -q '^STATUS:CANCELLED' "$PAGE" \
      && ok "$db: a cancelled fitting exports as STATUS:CANCELLED" \
      || bad "$db cancelled .ics" "the file still looks live — she cancels through our own page and the appointment stays in her calendar forever"
  fi
done
# A forged token must not hand out somebody's appointment as a file either.
[ "$(code "$BELLA/b/1-deadbeefdeadbeefdeadbeef/ics")" = "404" ] \
  && ok "forged token cannot download an .ics" || bad "forged .ics token" "did not 404"
# The suffix form is a different URL: <string:token> swallows ".ics", so the HMAC
# compare fails. Asserted so nobody 'helpfully' switches the route to /b/<token>.ics.
[ "$(code "$BELLA/b/$(bk_token bella 1).ics")" = "404" ] \
  && ok "token-with-suffix form 404s" || bad "suffix form" "did not 404"
# A route nobody can reach is not a feature. Both pages must actually link to it,
# and the link only belongs on a booking that is still ahead of her.
# `start` is `timestamp without time zone` holding UTC, and psql's session TZ here
# is Asia/Jerusalem — so a bare now() compares UTC data against local wall-clock and
# is wrong by the offset. It silently picked the wrong booking, and for three hours
# a day picked none at all and skipped. Same idiom as the outbox checks below.
FUT=$(psql -d bella -tAc "select id from calendar_event where modryn_is_booking and active and modryn_cancelled_at is null and start > (now() at time zone 'utc') order by start limit 1")
if [ -z "$FUT" ]; then
  skip "'add to calendar' link renders" "no future booking on bella — the link is deliberately hidden on past ones"
else
  FTOK=$(bk_token bella "$FUT")
  fetch "$BELLA/b/$FTOK"
  grep -qF "/b/$FTOK/ics" "$PAGE" \
    && ok "reminder page offers 'add to calendar'" \
    || bad "reminder page .ics link" "the route exists but the page does not link to it"
  fetch "$BELLA/book/confirmed/$FTOK"
  grep -qF "/b/$FTOK/ics" "$PAGE" \
    && ok "confirmation page offers 'add to calendar'" \
    || bad "confirmation page .ics link" "the inherit into modryn_booking.booking_confirmed did not apply"
  # THE POINT of token-addressing that page. It prints her phone number, and
  # since it gained the .ics link it prints her cancel token too — so while it
  # answered to <int:event_id>, `seq 1 500` harvested both for every booking in
  # the boutique. Fetching it by id above would have locked that in.
  [ "$(code "$BELLA/book/confirmed/$FUT")" = "404" ] \
    && ok "the id-addressed confirmation page is gone" \
    || bad "confirmation page still enumerable" "/book/confirmed/$FUT answered $(code "$BELLA/book/confirmed/$FUT") — walking ids hands out every booking's phone number and cancel token"
fi
# Past bookings must NOT offer it — there is nothing to add. Cancelled ones MUST,
# because that tap is what removes the dead fitting from her calendar.
#
# These two sit OUTSIDE the future-booking branch on purpose. They were nested
# inside it, so the day bella's last future booking aged into the past, three
# assertions that never needed one stopped running and the suite reported a
# single skip in their place — fewer checks, same green line. A fixture guard
# must gate only the checks that actually need that fixture.
PAST=$(psql -d bella -tAc "select id from calendar_event where modryn_is_booking and active and start < (now() at time zone 'utc') order by start limit 1")
if [ -z "$PAST" ]; then
  skip "past booking hides the .ics link" "no past booking on bella"
else
  fetch "$BELLA/b/$(bk_token bella "$PAST")"
  grep -q "/ics" "$PAGE" \
    && bad "past booking hides the .ics link" "it is still offered on an appointment that has already happened" \
    || ok "past booking hides the .ics link"
fi
CANC=$(psql -d bella -tAc "select id from calendar_event where modryn_is_booking and active and modryn_cancelled_at is not null and start > (now() at time zone 'utc') limit 1")
if [ -z "$CANC" ]; then
  skip "cancelled booking offers 'remove from calendar'" "no cancelled future booking on bella"
else
  fetch "$BELLA/b/$(bk_token bella "$CANC")"
  grep -q "/ics" "$PAGE" \
    && ok "cancelled booking offers 'remove from calendar'" \
    || bad "cancelled booking .ics link" "she cancelled through this page and has no way to clear the fitting from her calendar"
fi

head_ "10c. premium waitlist"
for db in $TENANTS; do
  QCOLS=$(psql -d $db -tAc "select count(*) from information_schema.columns where table_name='modryn_queue_entry' and column_name in ('access_token','next_notified_at','turn_notified_at')")
  [ "$QCOLS" = "3" ] && ok "$db: ticket + notification fields exist" || bad "$db queue fields" "expected 3, got $QCOLS"
done
# Her private page must not be guessable and must not leak on a bad token.
[ "$(code "$BELLA/q/garbagegarbagegarbage")" = "404" ] && ok "unknown ticket token 404s" || bad "ticket token" "did not 404"
# The gate is staff-only; a customer must never be able to accept herself.
ACC=$(curl -sg -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","method":"call","params":{"entry_id":1}}' "$BELLA/floor/accept" | grep -c '"result"')
[ "$ACC" = "0" ] && ok "/floor/accept refuses anonymous" || bad "/floor/accept anonymous" "returned a result"
# The closing cron must be scheduled ahead, never "now" — firing on install
# would expire every live ticket on the floor.
for db in $TENANTS; do
  FUT=$(psql -d $db -tAc "select count(*) from ir_cron c join ir_act_server a on a.id=c.ir_actions_server_id where a.code like '%_modryn_expire_open_tickets%' and c.nextcall > (now() at time zone 'utc')")
  [ "${FUT:-0}" = "1" ] && ok "$db: closing cron scheduled in the future" || bad "$db closing cron" "missing or due immediately"
done

head_ "10d. advance refill loop"
for db in $TENANTS; do
  WCOLS=$(psql -d $db -tAc "select count(*) from information_schema.columns where table_name='modryn_day_waitlist' and column_name in ('phone','day','state','offer_token','offer_expires_at','lang')")
  [ "$WCOLS" = "6" ] && ok "$db: day waitlist table shaped" || bad "$db day waitlist columns" "expected 6, got $WCOLS"
  # One row per phone per day, or a cancellation offers the same woman twice.
  # Shape, not name: section 17 checks the name. This one catches an index that
  # kept its name while losing UNIQUE or losing a column from the key.
  UNIQ=$(psql -d $db -tAc "select count(*) from pg_indexes where tablename='modryn_day_waitlist' and indexdef like '%UNIQUE%phone%day%'")
  [ "${UNIQ:-0}" -ge 1 ] && ok "$db: one waitlist row per phone per day" || bad "$db phone+day uniqueness" "no unique index"
done
# Existence + active only, NOT nextcall > now(): Odoo's threaded scheduler makes
# one pass per database about every 60s, so any cron with an interval near that
# is routinely a minute overdue between firings — measured, not assumed. The
# closing cron above keeps the stricter check because firing IT early would
# expire live tickets, whereas this one only lapses windows that really passed.
for db in $TENANTS; do
  OCRON=$(psql -d $db -tAc "select count(*) from ir_cron c join ir_act_server a on a.id=c.ir_actions_server_id where a.code like '%_modryn_expire_offers%' and c.active")
  [ "${OCRON:-0}" = "1" ] && ok "$db: offer-expiry cron installed and active" || bad "$db offer expiry cron" "missing or inactive"
done
# A stale or forged claim link must land on the warm page, never a booking form.
CL=$(fetch "$BELLA/claim/notarealtoken")
echo "$CL" | grep -q "Take this place" && bad "forged claim token" "rendered a bookable form" || ok "forged claim link offers nothing"
# A fully-booked day has to stay visible, otherwise she never learns she could
# have been first in line.
grep -q "full_days" addons/modryn_booking/views/templates.xml && ok "/book invites her onto the waitlist" || bad "waitlist form" "absent from /book"
grep -q "modryn_offer_next" addons/modryn_portal/models/calendar_event.py && ok "every cancellation path offers the slot on" || bad "refill hook" "modryn_cancel does not offer"
# The offer text is composed by a cron, so her language has to be recorded.
grep -q "with_context(lang=" addons/modryn_portal/models/day_waitlist.py && ok "offer SMS speaks her language" || bad "offer language" "cron composes in server language"

head_ "10e. fitting rooms + calls for help"
for db in $TENANTS; do
  ROOMS=$(psql -d $db -tAc "select count(*) from modryn_fitting_room where active")
  [ "${ROOMS:-0}" -ge 3 ] && ok "$db: fitting rooms seeded ($ROOMS)" || bad "$db fitting rooms" "only ${ROOMS:-0}"
  RCOLS=$(psql -d $db -tAc "select count(*) from information_schema.columns where table_name in ('modryn_queue_entry','calendar_event') and column_name='modryn_room_id'")
  [ "$RCOLS" = "2" ] && ok "$db: both card kinds can hold a room" || bad "$db room columns" "expected 2, got $RCOLS"
  # The one thing a room registry must never do: put two women in one room.
  DOUBLE=$(psql -d $db -tAc "select count(*) from (select modryn_room_id from modryn_queue_entry where modryn_room_id is not null and state in ('waiting','called') group by modryn_room_id having count(*) > 1) x")
  [ "${DOUBLE:-0}" = "0" ] && ok "$db: no room holds two live walk-ins" || bad "$db room collision" "$DOUBLE rooms double-booked"
done
# Catching the ValidationError does NOT undo the write it rejected — without a
# savepoint the board refused the move and committed it anyway.
grep -q "cr.savepoint()" addons/modryn_staff/controllers/floor.py && ok "rejected room move is rolled back" || bad "room rollback" "no savepoint around the write"
for db in $TENANTS; do
  SCRON=$(psql -d $db -tAc "select count(*) from ir_cron c join ir_act_server a on a.id=c.ir_actions_server_id where a.code like '%_modryn_escalate_unanswered%' and c.active")
  [ "${SCRON:-0}" = "1" ] && ok "$db: escalation cron installed" || bad "$db escalation cron" "missing or inactive"
done
# Every SOS route is staff-only; a customer must never page the floor.
for route in /floor/sos /floor/sos/ack /floor/sos/resolve /floor/room; do
  R=$(curl -sg -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","method":"call","params":{}}' "$BELLA$route" | grep -c '"result"')
  [ "$R" = "0" ] && ok "$route refuses anonymous" || bad "$route anonymous" "returned a result"
done
[ "$(code "$BELLA/manage/rooms")" != "200" ] && ok "/manage/rooms refuses anonymous" || bad "/manage/rooms anonymous" "returned 200"

head_ "10f. weekly roster"
for db in $TENANTS; do
  TPLS=$(psql -d $db -tAc "select count(*) from modryn_shift_template where active")
  [ "${TPLS:-0}" -ge 5 ] && ok "$db: shift templates seeded ($TPLS)" || bad "$db shift templates" "only ${TPLS:-0}"
  # +3640 days = exactly 520 weeks, so the planted row stays a MONDAY (which is
  # what makes the monitor fire) while landing a decade out, where it cannot hit
  # UNIQUE (template_id, day). Planting on THIS week collided with a real slot on
  # any tenant that already had a rota — bella failed while noga passed, and the
  # error was swallowed by the helper's 2>/dev/null, so it read as "seed failed".
  # The Israeli week starts Sunday. Python weekday(): Sun=6. Vacuously true on a
  # tenant with no slots, so the teeth test plants a Monday one and requires a catch.
  detects "$db" "week start" \
    "INSERT INTO modryn_shift_slot (name, week_start, day, start_hour, end_hour, template_id, create_uid, write_uid, create_date, write_date) SELECT 'planted', date_trunc('week', now())::date + 3640, date_trunc('week', now())::date + 3640, 10, 18, id, 1, 1, now(), now() FROM modryn_shift_template LIMIT 1;" \
    "SELECT count(*) FROM modryn_shift_slot WHERE extract(dow from week_start) <> 0;"
  SUN=$(psql -d $db -tAc "select count(*) from modryn_shift_slot where extract(dow from week_start) <> 0")
  [ "${SUN:-1}" = "0" ] && ok "$db: weeks start on Sunday" || bad "$db week start" "$SUN slots start mid-week"
done
# bella ONLY: slots materialise lazily when someone opens /roster, so an untouched
# tenant legitimately holds zero. Looping this would fail noga for not being visited.
SLOTS=$(psql -d bella -tAc "select count(*) from modryn_shift_slot")
[ "${SLOTS:-0}" -ge 5 ] && ok "bella: next week materialised ($SLOTS slots)" || bad "shift slots" "only ${SLOTS:-0}"
# Hours are snapshots: editing a template must not rewrite a week people agreed to.
grep -q "'start_hour': template.start_hour" addons/modryn_roster/models/shift_slot.py && ok "slots snapshot their hours" || bad "hour snapshot" "slots read hours from the template"
# Grouped on the key availability ACTUALLY has now - (day, shift_type,
# employee) - and defaulting to the FAILING value, which is the idiom nine
# lines above. It used to read slot_id and default to 0, the PASSING value: the
# moment that column stopped existing, psql would error to a stderr nobody sees
# inside four hundred lines of output, DUP would come back empty, and this would
# have printed green for both tenants over a query that never ran. It is the
# suite's only availability assertion, so that green would have meant nothing.
for db in $TENANTS; do
  DUP=$(psql -d $db -tAc "select count(*) from (select day, shift_type, employee_id from modryn_availability group by 1,2,3 having count(*) > 1) x")
  [ "${DUP:-1}" = "0" ] && ok "$db: no duplicate availability rows" || bad "$db availability duplicates" "${DUP:-unreadable} found"
done
# Every roster route is staff-only, and publishing is manager-only.
for route in /roster/available /roster/assign /roster/publish; do
  R=$(curl -sg -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","method":"call","params":{}}' "$BELLA$route" | grep -c '"result"')
  [ "$R" = "0" ] && ok "$route refuses anonymous" || bad "$route anonymous" "returned a result"
done
for path in /roster /manage/shifts; do
  [ "$(code "$BELLA$path")" != "200" ] && ok "$path refuses anonymous" || bad "$path anonymous" "returned 200"
done
# The week she is STANDING in is not hers to fill: its rota went out days ago.
# Greppable teeth on the guard itself, in the slot-snapshot style used above.
# The disabled attribute on the buttons is only a suggestion — /roster/available
# is reachable without one, and this is the line that says the server refuses it
# too. If the constant or the comparison is edited away the page keeps looking
# right and the rule quietly stops existing.
grep -q "if int(week) < self.PLANNABLE_FROM" addons/modryn_roster/controllers/roster.py   && ok "the toggle refuses a week already being worked"   || bad "roster past-week guard" "the check on PLANNABLE_FROM is gone from /roster/available"
# The three refusals a woman can actually PROVOKE by pressing a cell — a shut
# window, a published week, the week already being worked — each have to carry a
# sentence as well as a code. A press that answers with nothing to read is
# indistinguishable from a press that did nothing at all, which is exactly how a
# working deadline got reported as a broken page.
#
# Scoped to those three deliberately. `forbidden` and `not_found` on the same
# file are machine answers to a request no button can produce, and demanding
# prose for them would be a check nobody could keep green.
for code in window_closed published past_week; do
  N=$(grep -c "'error': '$code', *\$" addons/modryn_roster/controllers/roster.py)
  B=$(grep -c "return {'error': '$code'}\$" addons/modryn_roster/controllers/roster.py)
  [ "${B:-1}" = "0" ] && [ "${N:-0}" -ge 1 ]     && ok "roster: $code answers with a sentence, not just a code"     || bad "roster $code message" "answered with a bare code — she would see a press that did nothing"
done
# The window forms are asserted on the EXACT status, not on "anything but 200":
# a route that has been DELETED answers 404, which is also "not 200", so the
# loose form keeps printing green over a control that no longer exists.
#
# The exact status is 303, not 404. These carry auth='user', and Odoo bounces a
# signed-out visitor to /web/login before the handler — and therefore before
# _is_manager() — ever runs. 404 is what a signed-in NON-manager gets, which is
# a different rule and is asserted separately in section 10a.
for path in /roster/window/rule /roster/window/week; do
  W=$(curl -sg -o /dev/null -w "%{http_code}" -X POST "$BELLA$path")
  [ "$W" = "303" ] && ok "$path sends anonymous to sign in ($W)" \
    || bad "$path anonymous" "got $W, wanted 303"
done
# The recurring window lives in two config rows and nothing asserted they were
# ever writable, let alone well-formed. A malformed value is invisible at
# runtime: _parse_window answers anything it cannot read with the shipped
# default and says nothing at all.
for db in $TENANTS; do
  for key in modryn.roster.window_open modryn.roster.window_close; do
    V=$(psql -d $db -tAc "select value from ir_config_parameter where key='$key'")
    if [ -z "$V" ]; then
      ok "$db: $key unset — the shipped default applies"
    elif printf '%s' "$V" | grep -qE '^[0-6]:[0-9]+(\.[0-9]+)?$'; then
      ok "$db: $key is '$V'"
    else
      bad "$db $key" "'$V' cannot be parsed — the window silently falls back to Thursday 09:00"
    fi
  done
done

head_ "10g. one bride per slot"
# Existence moved to section 17, which checks all three unique indexes across the
# tenants AND the template in one place. What stays here is the half section 17
# deliberately does not do — the predicate — and it subsumes existence anyway:
# a missing index reports "got: none" and fails.
#
# Partial on all THREE columns, or it governs the wrong rows, and the predicate has
# to be checked per tenant: an upgrade that failed to rebuild it on one database
# leaves the other one's correct definition standing as false comfort.
#
# Drop modryn_is_booking and two staff meetings at 14:00 become an error; flip the
# cancelled test to IS NOT NULL and it indexes exactly the rows that hold nothing —
# a cancelled 14:00 could never be rebooked, silently killing the waitlist refill.
# `active IS TRUE` is the third: search() defaults to active_test=True, so an
# ARCHIVED booking is invisible to /book, to both pre-checks and to the floor board
# while still sitting in the index — poisoning that hour forever, offered to every
# bride and refused for every one of them by a row nobody can see.
for db in $TENANTS; do
  PRED=$(psql -d $db -tAc "select indexdef from pg_indexes where indexname='calendar_event_modryn_one_live_booking_per_slot'")
  case "$PRED" in
    *UNIQUE*modryn_is_booking*IS\ TRUE*modryn_cancelled_at*IS\ NULL*active*IS\ TRUE*) ok "$db: slot index is partial over live, visible bookings only" ;;
    *) bad "$db slot index predicate" "expected UNIQUE ... WHERE modryn_is_booking IS TRUE AND modryn_cancelled_at IS NULL AND active IS TRUE, got: ${PRED:-none}" ;;
  esac
done
# Belt and braces: the data the index promises. A non-zero here means the index is
# absent AND the race has already fired. The WHERE must match the index predicate
# character for character — including `active is true` — or this reports conflicts
# the index does not police and misses the ones it does.
#
# GROUPED BY (start, modryn_slot_seat), because that is what the index keys on
# now. Grouping by start alone would call the second bride of a capacity-2 hour a
# double-booking and fail the suite the first time a boutique used the feature —
# the check would be policing a rule the product deliberately dropped.
for db in $TENANTS; do
  DBL=$(psql -d $db -tAc "select count(*) from (select start, modryn_slot_seat from calendar_event where modryn_is_booking is true and modryn_cancelled_at is null and active is true group by start, modryn_slot_seat having count(*) > 1) x")
  [ "${DBL:-0}" = "0" ] && ok "$db: no seat holds two live bookings" || bad "$db double-booking" "$DBL seats hold two brides"
done
# The index NAME is the discriminator three separate `except UniqueViolation`
# handlers compare against. If a copy drifts from what Postgres actually reports,
# every real race re-raises and the losing bride gets a 500 — the exact failure the
# handlers exist to prevent, and one no source-only grep would catch.
for f in addons/modryn_portal/models/calendar_event.py addons/modryn_booking/controllers/main.py addons/modryn_portal/controllers/waitlist.py; do
  grep -q "'calendar_event_modryn_one_live_booking_per_slot'" "$f" \
    && ok "$(basename $f) names the slot index exactly" \
    || bad "slot index constant" "$f does not carry the literal index name — its UniqueViolation catch cannot match"
done
# Catching the UniqueViolation stops the 500 but does NOT undo the rejected INSERT;
# the poisoned transaction then kills the _slots() read that re-renders the form.
# Exactly the bug 10e records floor.py already having to fix.
grep -q "cr.savepoint()" addons/modryn_booking/controllers/main.py && ok "lost race is rolled back, not 500" || bad "booking rollback" "no savepoint around create"
grep -q "except UniqueViolation" addons/modryn_booking/controllers/main.py && ok "lost race answers with a sentence" || bad "booking race" "UniqueViolation not handled"
# The pre-check is now a UX affordance, not the guarantee — but deleting it would
# make the common case (form rendered minutes ago) a collision instead of a message.
grep -q "search_count(taken_domain)" addons/modryn_booking/controllers/main.py && ok "friendly pre-check retained" || bad "slot pre-check" "removed along with the race fix"

head_ "10h. /book scan is bounded"
# /book is a load-test page. Unbounded, it reads every booking the boutique will
# ever take and discards all but the 14 days it renders.
grep -q "('start', '<', until)" addons/modryn_booking/controllers/main.py && ok "/book scans only the rendered fortnight" || bad "slot scan" "no upper bound on start"
# The bound must be derived in LOCAL time. utcnow()+DAYS_AHEAD lands up to ~22h short
# of the final day's 17:00 slot, which silently re-offers that day's booked slots.
grep -q "datetime.utcnow() + timedelta(days=DAYS_AHEAD)" addons/modryn_booking/controllers/main.py && bad "slot bound" "naive UTC bound drops the last day" || ok "slot bound derived in Jerusalem local time"
# bella/book is already covered in section 4; noga proves the bound is not
# tenant-specific (the two databases hold different booking horizons).
[ "$(code "$NOGA/book")" = "200" ] && ok "noga /book renders" || bad "noga /book" "not 200"

head_ "10i. sms outbox"
# Every assertion in this section used to run against bella ALONE while 10g looped
# both tenants. A table never created on noga, or a drain cron deactivated on noga
# only, would have shipped green — and noga is the tenant WITHOUT Twilio credentials,
# so nobody would notice by not receiving a text.
#
# The queue must exist, or every non-interactive text silently evaporates.
for db in $TENANTS; do
  [ -n "$(psql -d $db -tAc "select to_regclass('public.modryn_sms_outbox')")" ] \
    && ok "$db: outbox table exists" || bad "$db outbox table" "modryn_sms_outbox missing — module not upgraded?"
  # waitlist_id is load-bearing, not bookkeeping: it is the only path by which a
  # permanently undeliverable offer text hands its day back (see 10k).
  OCOLS=$(psql -d $db -tAc "select count(*) from information_schema.columns where table_name='modryn_sms_outbox' and column_name in ('phone','body','state','attempts','last_error','sent_at','retry_after','waitlist_id')")
  [ "${OCOLS:-0}" = "8" ] && ok "$db: outbox table shaped" || bad "$db outbox columns" "expected 8, got ${OCOLS:-0}"
  # _trigger() is the fast path, but it can only wake a cron that is installed and on.
  DCRON=$(psql -d $db -tAc "select count(*) from ir_cron c join ir_act_server a on a.id=c.ir_actions_server_id where a.code like '%_drain()%' and c.active")
  [ "${DCRON:-0}" -ge 1 ] && ok "$db: outbox drain cron installed and active" || bad "$db drain cron" "missing or inactive"
done
# The whole point of the stage: POST /book/submit must not reach the blocking sender.
grep -q "send_async(event.modryn_customer_phone" addons/modryn_portal/models/booking_comms.py \
  && ok "booking confirmation is queued, not sent inline" || bad "booking confirmation queued" "still calls the synchronous sender"
# The login code is the one text she is actively waiting for; queueing it would
# show her a code-entry form before the code exists.
grep -q "\.send(phone, body)" addons/modryn_portal/models/otp.py \
  && ok "OTP still sends synchronously" || bad "OTP synchronous" "login code was queued — she'd wait on a cron"
# The reminder deliberately stays synchronous: its sent-at stamp IS the retry
# ledger, and stamping on "enqueued" would lose a reminder that never sends.
grep -q "modryn.sms'\].send(event.modryn_customer_phone" addons/modryn_portal/models/booking_comms.py \
  && ok "24h reminder still sends synchronously" || bad "reminder synchronous" "stamp/send coupling broken"
# Nothing should sit pending: _trigger() wakes the drain within about a second,
# and the 5-minute interval catches any notify nobody heard.
#
# Both monitors below query a table that is empty on every tenant today, so both
# answered 0 — and would have answered 0 with the drain deleted. Each is now
# preceded by detects(), which plants exactly the row the monitor hunts inside a
# rolled-back transaction and requires the monitor to see it. The pair is the
# evidence: the monitor CAN fire, and on real data it does not.
for db in $TENANTS; do
  detects "$db" "outbox drains" \
    "INSERT INTO modryn_sms_outbox (phone, body, state, attempts, create_uid, write_uid, create_date, write_date) VALUES ('+972500000000','planted','pending',0,1,1,(now() at time zone 'utc') - interval '2 hours',now());" \
    "SELECT count(*) FROM modryn_sms_outbox WHERE state='pending' AND create_date < (now() at time zone 'utc') - interval '10 minutes';"
  # `at time zone 'utc'`, NOT bare now(). create_date is `timestamp` holding UTC
  # while now() is `timestamptz`; comparing them coerces through the session zone
  # (Asia/Jerusalem, +03), so a row created THIS INSTANT already read as three
  # hours stuck and the check reported a healthy cron asleep.
  STUCK=$(psql -d $db -tAc "select count(*) from modryn_sms_outbox where state='pending' and create_date < (now() at time zone 'utc') - interval '10 minutes'" 2>/dev/null || echo 0)
  [ "${STUCK:-0}" = "0" ] && ok "$db: outbox drains within the notify window" || bad "$db outbox drains" "$STUCK rows pending over 10min — cron worker asleep?"
  # A finished row's body carries her name and a live booking link. Retention is a
  # privacy limit first, a disk one second.
  detects "$db" "outbox reaping" \
    "INSERT INTO modryn_sms_outbox (phone, body, state, attempts, create_uid, write_uid, create_date, write_date) VALUES ('+972500000000','planted','sent',1,1,1,(now() at time zone 'utc') - interval '30 days',now());" \
    "SELECT count(*) FROM modryn_sms_outbox WHERE state IN ('sent','failed') AND create_date < (now() at time zone 'utc') - interval '8 days';"
  AGED=$(psql -d $db -tAc "select count(*) from modryn_sms_outbox where state in ('sent','failed') and create_date < (now() at time zone 'utc') - interval '8 days'" 2>/dev/null || echo 0)
  [ "${AGED:-0}" = "0" ] && ok "$db: finished texts are reaped past retention" \
    || bad "$db outbox reaping" "$AGED finished rows older than retention still hold message bodies"
done
OUTBOX=addons/modryn_portal/models/sms_outbox.py
# The backoff reused a clock captured before the loop. Each row can burn up to
# SEND_TIMEOUT, so under the exact failure this backoff exists for — a timing-out
# Twilio — a full batch takes minutes and every retry from roughly the sixth row on
# was stamped in the past: _wake() fired immediately and attempts 2 and 3 burned
# back-to-back against a still-degraded Twilio. A backoff of zero, precisely when
# it was needed.
grep -q "retry_at = fields.Datetime.now() + timedelta" "$OUTBOX" \
  && ok "retry backoff uses the in-loop clock" || bad "retry backoff" "retry_at not computed from fields.Datetime.now() inside the loop"
grep -qE "retry_at = now \+" "$OUTBOX" \
  && bad "retry backoff" "retry_at computed from the batch-start 'now' — collapses to zero on a slow batch" \
  || ok "retry backoff does not reuse the batch-start clock"
grep -q "^RETENTION_DAYS" "$OUTBOX" \
  && ok "outbox retention is a named constant" || bad "outbox retention" "no RETENTION_DAYS — message bodies live forever"
grep -A4 "def _drain" "$OUTBOX" | grep -q "self._gc()" \
  && ok "the drain reaps finished rows" || bad "outbox reaping" "_drain does not call _gc — sent/failed rows accumulate"
# The docstring promise, enforced. An escaping row is never marked, so _order='id
# asc' re-picks it first on every run and wedges every message behind it — and five
# consecutive cron failures deactivate the drain outright
# (ir_cron.MIN_FAILURE_COUNT_BEFORE_DEACTIVATION), silently ending all SMS.
grep -A12 "for row in pending:" "$OUTBOX" | grep -q "except Exception" \
  && ok "the drain survives a sender that raises" || bad "drain guard" "no per-row except around _send_now — one poison row wedges the queue"
# The failure MODE, not a string: response.json() raises JSONDecodeError — itself a
# RequestException — when an edge answers with a non-JSON body. It used to sit
# OUTSIDE the guard, so it escaped _send_now and wedged the drain.
#
# This assertion USED to require (False, 'transport_error') on a 200/HTML. That
# was wrong in the other direction and it was this suite pinning it: an accepted
# status IS the answer, the sid is only a log handle, and an unreadable body must
# not convert a delivered message into a retry — a retry sends her the same text
# again and there is no undo. So the contract is now: accepted status => sent,
# whatever the body. The parse still may not escape. Runs the REAL function body;
# no server, no Twilio, no network.
.venv/bin/python - <<'PY' && ok "an accepted status counts as SENT even with an unreadable body" || bad "_send_now accepted-status contract" "a 201 Twilio accepted is retried — she gets the same text twice"
import logging, sys
from unittest import mock
import requests
src = open('addons/modryn_portal/models/sms.py').read()
fn = "def _send_now(self, to, body):" + src.split("    def _send_now(self, to, body):")[1].split("\n    @api.model")[0]
ns = {'requests': requests, '_logger': logging.getLogger('v'), 'normalize_il_phone': lambda n: n,
      'TWILIO_BASE': 'https://example.invalid', 'SEND_TIMEOUT': 1}
exec(fn, ns)
class S:
    def _twilio_config(self): return {'account_sid': 'AC', 'key_sid': 'SK', 'key_secret': 's', 'from': '+1'}
r = requests.Response(); r.status_code = 201
r._content = b'<html>502 Bad Gateway</html>'; r.headers['Content-Type'] = 'text/html'
with mock.patch.object(requests, 'post', return_value=r):
    ok_, detail = ns['_send_now'](S(), '+972521234567', 'hi')
sys.exit(0 if ok_ is True else 1)
PY
# The other half of the same contract: a REJECTED status must still never escape.
.venv/bin/python - <<'PY' && ok "a rejected status with a non-JSON body is a handled failure" || bad "_send_now failure contract" "a 500 with an HTML body escapes _send_now and wedges the drain"
import logging, sys
from unittest import mock
import requests
src = open('addons/modryn_portal/models/sms.py').read()
fn = "def _send_now(self, to, body):" + src.split("    def _send_now(self, to, body):")[1].split("\n    @api.model")[0]
ns = {'requests': requests, '_logger': logging.getLogger('v'), 'normalize_il_phone': lambda n: n,
      'TWILIO_BASE': 'https://example.invalid', 'SEND_TIMEOUT': 1}
exec(fn, ns)
class S:
    def _twilio_config(self): return {'account_sid': 'AC', 'key_sid': 'SK', 'key_secret': 's', 'from': '+1'}
r = requests.Response(); r.status_code = 500
r._content = b'<html>502 Bad Gateway</html>'; r.headers['Content-Type'] = 'text/html'
with mock.patch.object(requests, 'post', return_value=r):
    ok_, detail = ns['_send_now'](S(), '+972521234567', 'hi')
# Must be a clean False, and must NOT be classified permanent — a 500 is Twilio's.
sys.exit(0 if ok_ is False and detail else 1)
PY

head_ "10j. claim path is race-safe and date-bounded"
# The SECOND booking-creation path, and the one never tested: modryn_cancel() frees
# a slot and texts a claim link for that same day in the same call, so the link
# holder and any /book visitor are pointed at one hour BY DESIGN. Its create() was
# bare while /book/submit's was guarded.
W=addons/modryn_portal/controllers/waitlist.py
grep -q "with request.env.cr.savepoint():" "$W" \
  && ok "claim create is inside a savepoint" \
  || bad "claim create savepoint" "$W creates a booking with no savepoint — the aborted tx would also break the re-render"
grep -q "except UniqueViolation" "$W" \
  && ok "claim path catches UniqueViolation" || bad "claim UniqueViolation catch" "$W does not catch the slot index violation"
# A bare catch would tell a bride to pick another time when the real failure was an
# unrelated constraint on calendar_attendee or mail_followers — advice she cannot
# act on, in a loop, hiding a real bug.
grep -q "exc.diag.constraint_name != SLOT_INDEX" "$W" \
  && ok "claim catch is scoped to our slot index" || bad "claim catch scope" "$W swallows every unique violation, not just the slot one"
# _free_slots_on had NO date bound at all — it read every booking ever taken, on
# every /claim GET and every failed /claim POST, to decide eight hours.
# The edges are now derived from the boutique's own hours rather than a hardcoded
# 10-18, and the upper one is an INSTANT (last start + one slot) rather than a
# wall-clock hour — a window closing at midnight would otherwise reach hour 24,
# which datetime.replace() rejects, 500ing every /claim that day.
grep -q "('start', '>=', first_start)" "$W" && grep -q "('start', '<', after_last)" "$W" \
  && grep -q "after_last = _utc_at(hours\[-1\]) + timedelta(minutes=SLOT_MINUTES)" "$W" \
  && ok "_free_slots_on is bounded to the rendered day" || bad "_free_slots_on bound" "the taken-set scan has no date window, or its upper edge is back to a wall-clock hour"
# The bound must localise each local hour, not add hours to a UTC value: on Israel's
# spring-forward day local midnight is +02:00 while local 10:00 is +03:00, so
# arithmetic lands an hour out and drops that day's real bookings out of the scan —
# offering an already-taken hour to a second bride.
grep -q "TZ.localize(naive).astimezone(pytz.utc)" "$W" \
  && ok "day window is DST-derived via TZ.localize" || bad "day window DST" "bound looks computed by UTC arithmetic — will drift across a DST flip"

head_ "10k. a waitlist offer is never held hostage by an undeliverable text"
# NOTHING in this suite touched day_waitlist.py, which is how a regression that
# blocks a whole day's waitlist for two hours shipped green. Moving the offer text
# to the outbox moved its failure moment too: a number Twilio ACCEPTS and then
# rejects (landline, unsubscribed, bad To) now answers ok=True at enqueue and fails
# minutes later, inside the drain. Only one offer stands per day at a time, so
# without a way back nobody else on that day is texted until the 2h window lapses.
WAITLIST=addons/modryn_portal/models/day_waitlist.py
grep -q "waitlist_id" "$OUTBOX" \
  && ok "outbox records which waitlist entry a message belongs to" || bad "outbox waitlist link" "no waitlist_id on modryn.sms.outbox — final failure cannot travel back"
grep -q "_release_waitlist()" "$OUTBOX" \
  && ok "final failure calls back into the waitlist" || bad "final-failure hook" "_release_waitlist() not called from the drain"
grep -q "def _modryn_offer_undeliverable" "$WAITLIST" \
  && ok "waitlist can withdraw an undeliverable offer" || bad "waitlist reclaim" "_modryn_offer_undeliverable missing from day_waitlist.py"
grep -A20 "def _modryn_offer_undeliverable" "$WAITLIST" | grep -q "modryn_offer_next" \
  && ok "withdrawing an offer re-offers the day" || bad "waitlist reclaim" "_modryn_offer_undeliverable does not call modryn_offer_next — the day stays blocked"
# The invariant itself, asserted against real rows: no entry may sit in 'offered' —
# holding its whole day — while the text that offer depends on has already given up.
#
# This join reads two tables that are empty or offer-free on every tenant, so it
# answered 0 unconditionally. detects() plants a matched pair first and requires
# the join to find it, which is the only thing that makes the 0 below mean anything.
for db in $TENANTS; do
  detects "$db" "offer held by a dead text" \
    "INSERT INTO modryn_day_waitlist (name, phone, day, state, create_uid, write_uid, create_date, write_date) VALUES ('planted','+972500000000', now()::date + 400, 'offered', 1, 1, now(), now());
     INSERT INTO modryn_sms_outbox (phone, body, state, attempts, waitlist_id, create_uid, write_uid, create_date, write_date) SELECT '+972500000000','planted','failed',3,id,1,1,now(),now() FROM modryn_day_waitlist WHERE phone='+972500000000' AND day = now()::date + 400;" \
    "SELECT count(*) FROM modryn_day_waitlist w JOIN modryn_sms_outbox o ON o.waitlist_id = w.id WHERE w.state='offered' AND o.state='failed';"
  HELD=$(psql -d $db -tAc "select count(*) from modryn_day_waitlist w join modryn_sms_outbox o on o.waitlist_id = w.id where w.state = 'offered' and o.state = 'failed'" 2>/dev/null || echo skip)
  if [ "$HELD" = "skip" ]; then
    bad "$db no offer held by a dead text" "query failed — is waitlist_id deployed?"
  elif [ "$HELD" = "0" ]; then
    ok "$db: no waitlist offer is standing on a failed text"
  else
    bad "$db no offer held by a dead text" "$HELD entries stuck in 'offered' with a failed offer SMS"
  fi
done

head_ "10k-bis. an outage must not eat the waitlist"
# The assertions above grep for _release_waitlist() and _modryn_offer_undeliverable,
# both of which the BUGGY code also had — they were structurally incapable of
# catching the regression that reclaimed a place on any failure at all. This one
# executes the real _release_waitlist body against a fake row per error class and
# checks whether it reached back into the waitlist. Verified to exit 1 against the
# pre-fix code, which reclaimed on 11 of 11 transient failures including twilio_401.
SMS=addons/modryn_portal/models/sms.py
.venv/bin/python - <<'PY' && ok "a transient SMS failure does NOT burn her waitlist place" \
  || bad "waitlist reclaim classification" "_release_waitlist reclaims on a non-permanent failure — one Twilio outage expires the whole day's queue"
import logging, re, sys, contextlib
src = open('addons/modryn_portal/models/sms.py').read()
ns = {'re': re}
exec(src[src.index('def normalize_il_phone'):src.index('class ModrynSms')], ns)
out = open('addons/modryn_portal/models/sms_outbox.py').read()
body = "def _release_waitlist(self):" + out.split("    def _release_waitlist(self):")[1].split("\n    # --")[0]
g = {'_logger': logging.getLogger('v'), 'is_permanent_rejection': ns['is_permanent_rejection']}
exec(body, g)
class Entry:
    id = 1
    def __init__(self): self.reclaimed = False
    def _modryn_offer_undeliverable(self): self.reclaimed = True
class Cr:
    def savepoint(self): return contextlib.nullcontext()
class Env: cr = Cr()
class Row:
    id = 7; env = Env()
    def __init__(self, e): self.last_error = e; self.waitlist_id = Entry()
    def ensure_one(self): pass
# Account-scoped failures fail identically for EVERY recipient; filing one as
# permanent is how a ten-deep list empties itself inside an hour.
transient = ['twilio_401','twilio_401_20003','twilio_403','twilio_404_20404','twilio_429',
             'twilio_500','twilio_503','twilio_400_21606','twilio_400_21408',
             'transport_error','raised']
permanent = ['twilio_400_21211','twilio_400_21214','twilio_400_21217','twilio_400_21610',
             'twilio_400_21612','twilio_400_21614','invalid_number']
fails = []
for e in transient:
    r = Row(e); g['_release_waitlist'](r)
    if r.waitlist_id.reclaimed: fails.append('reclaimed on transient %s' % e)
for e in permanent:
    r = Row(e); g['_release_waitlist'](r)
    if not r.waitlist_id.reclaimed: fails.append('did NOT reclaim on permanent %s' % e)
for f in fails: print(' ', f, file=sys.stderr)
sys.exit(1 if fails else 0)
PY
grep -q "PERMANENT_TWILIO_CODES" "$SMS" \
  && ! grep -A12 "PERMANENT_TWILIO_CODES = frozenset" "$SMS" | grep -q "'429'\|'20429'" \
  && ok "rate limiting is not treated as a dead number" || bad "429 classification" "429/20429 in the permanent set"
# HTTP status alone cannot separate "her number is a landline" (21614) from "our
# From number is misconfigured" (21606) — both are 400.
grep -q "twilio_%s_%s" "$SMS" \
  && ok "twilio error code is carried on the failure detail" || bad "twilio detail" "only the HTTP status is recorded — 400 cannot be disambiguated"

head_ "10k-quinquies. one Twilio account behind every database, and a tenant that can still refuse it"
# Credentials moved out of each database and into the process environment, which
# quietly retired the property four harnesses were built on: qa/lib/guard.js and
# the three loadtest seeders each refuse to run until they have counted ZERO
# modryn.twilio.* parameters, because "this tenant holds none" USED to mean "this
# tenant cannot reach a real handset". Every database inherits the platform
# account now, so that count proves nothing and an explicit
# modryn.twilio.disabled is the only thing standing between a load test and a
# stranger's phone. Nothing else in this suite ever executes _twilio_config, so
# the whole precedence ladder could ship on a grep and a hope.
#
# Below runs the REAL _twilio_config — stubbing it, as the two _send_now checks
# above deliberately do, is exactly what cannot be done here — against a REAL
# tenant database, through the same ir.config_parameter reads Odoo makes.
#
# It PLANTS parameters to do it. They go in inside a transaction that is never
# committed, the trick detects() uses: restoration is not a cleanup step that
# could itself fail on the way out, it is the absence of a commit, so a crash
# anywhere in the middle still leaves the tenant exactly as it was. bella carries
# four live override parameters and altering them would be a real regression.
for db in $TENANTS; do
  MODRYN_VERIFY_DB="$db" .venv/bin/python - <<'PY' && ok "$db: the off switch, then the tenant's own four, then the platform's" \
    || bad "$db twilio precedence" "the sender resolves the wrong credentials, or none — see the failing states above"
import logging, os, re, sys

import psycopg2
import requests

# Obvious fakes. A real credential written here would land in every transcript
# this suite is ever pasted into.
ENVVAR = {'account_sid': 'TWILIO_ACCOUNT_SID', 'key_sid': 'TWILIO_API_KEY_SID',
          'key_secret': 'TWILIO_API_KEY_SECRET', 'from': 'TWILIO_FROM_NUMBER'}
PLATFORM = {'account_sid': 'ACplatform', 'key_sid': 'SKplatform',
            'key_secret': 'splatform', 'from': '+972500000001'}
TENANT = {'account_sid': 'ACtenant', 'key_sid': 'SKtenant',
          'key_secret': 'stenant', 'from': '+972500000002'}

src = open('addons/modryn_portal/models/sms.py').read()
ns = {'os': os, 're': re, 'requests': requests, '_logger': logging.getLogger('v')}
# The module's OWN constants, never re-declared here: a renamed parameter key
# then fails this check instead of being quietly redefined as correct. Both
# slices stop short of `from odoo import`, which a bare interpreter cannot load.
exec(src[src.index('TWILIO_BASE ='):src.index('def normalize_il_phone')], ns)
exec(src[src.index('def normalize_il_phone'):src.index('class ModrynSms')], ns)
# Should a later edit ever post before reading the config, this sends it at a
# name that cannot resolve rather than at Twilio, on a suite that runs unattended.
ns['TWILIO_BASE'] = 'https://example.invalid'
ns['SEND_TIMEOUT'] = 1
for decl in ("def _twilio_config(self):", "def _send_now(self, to, body):"):
    exec(decl + src.split("    " + decl)[1].split("\n    @api.model")[0], ns)

PARAM = {'account_sid': ns['P_ACCOUNT_SID'], 'key_sid': ns['P_KEY_SID'],
         'key_secret': ns['P_KEY_SECRET'], 'from': ns['P_FROM']}


class Icp:
    def __init__(self, cur):
        self.cur = cur

    def sudo(self):
        return self

    # Odoo's own signature: absent parameter means the default, not None, and
    # `all(cfg.values())` reads False and None alike — so a wrong default here
    # would hide a missing credential rather than reveal it.
    def get_param(self, key, default=False):
        self.cur.execute("SELECT value FROM ir_config_parameter WHERE key = %s", (key,))
        row = self.cur.fetchone()
        return row[0] if row else default


class Sms:
    _twilio_config = ns['_twilio_config']
    _send_now = ns['_send_now']

    def __init__(self, cur):
        self.env = {'ir.config_parameter': Icp(cur)}


ORIGIN = dict([(v, 'platform') for v in PLATFORM.values()]
              + [(v, 'tenant') for v in TENANT.values()])


# Never the values, only where each one came from — 'other' means a credential
# this check did not plant, i.e. the tenant's real one, named without printing it.
def shape(cfg):
    if cfg is None:
        return 'None'
    return '{%s}' % ', '.join('%s=%s' % (f, ORIGIN.get(cfg[f], 'other')) for f in sorted(cfg))


# Inside this process only. Exporting these from the shell would hand the
# platform's real credentials' slot to a fake for the rest of the suite.
def platform_env(on):
    for var in ENVVAR.values():
        os.environ.pop(var, None)
    if on:
        for field, var in ENVVAR.items():
            os.environ[var] = PLATFORM[field]


def clear(cur, keys):
    cur.execute("DELETE FROM ir_config_parameter WHERE key = ANY(%s)", (list(keys),))


def put(cur, key, value):
    clear(cur, [key])
    cur.execute("INSERT INTO ir_config_parameter (key, value, create_uid, write_uid,"
                " create_date, write_date) VALUES (%s, %s, 1, 1, now(), now())", (key, value))


fails = []
# The key string is contract, not detail: qa/lib/guard.js and the loadtest
# seeders read it by name from outside Python and cannot follow a rename.
if ns.get('P_DISABLED') != 'modryn.twilio.disabled':
    fails.append("sms.py does not define P_DISABLED = 'modryn.twilio.disabled'")
DISABLED = 'modryn.twilio.disabled'

conn = psycopg2.connect(dbname=os.environ['MODRYN_VERIFY_DB'])
try:
    cur = conn.cursor()
    sms = Sms(cur)

    # 1. THE CONTROL, and the reason it is first: states 3 and 5 both assert
    # against a build that reads no environment at all, and on such a build 3
    # passes for free. This one fixes what "nothing configured" looks like, and
    # state 2 immediately proves this harness CAN produce a config, so the Nones
    # below mean the off switch rather than a dead code path.
    clear(cur, list(PARAM.values()) + [DISABLED])
    platform_env(False)
    cfg = sms._twilio_config()
    if cfg is not None:
        fails.append('1 (control): nothing configured anywhere, resolved %s' % shape(cfg))
    sent = sms._send_now('+972521234567', 'planted')
    if sent != (True, 'logged'):
        fails.append('1 (control): unconfigured send returned %r, not the log fallback' % (sent,))

    # 2. The platform default, which is the whole point of the change.
    platform_env(True)
    cfg = sms._twilio_config()
    if not cfg or cfg.get('from') != PLATFORM['from']:
        fails.append('2: a tenant with no parameters of its own resolved %s, '
                     'not the platform environment' % shape(cfg))

    # 3. The switch that replaced "holds no parameters" as the way to keep a
    # database off the wire. It must beat a fully configured environment.
    put(cur, DISABLED, '1')
    cfg = sms._twilio_config()
    if cfg is not None:
        fails.append('3: %s is set and the platform environment still won (%s)' % (DISABLED, shape(cfg)))
    sent = sms._send_now('+972521234567', 'planted')
    if sent != (True, 'logged'):
        fails.append('3: a disabled tenant returned %r — it just texted someone' % (sent,))
    clear(cur, [DISABLED])

    # 4. The override survives. A boutique that pays its own Twilio bill keeps
    # its own caller ID even once the platform has one.
    for field, key in PARAM.items():
        put(cur, key, TENANT[field])
    cfg = sms._twilio_config()
    if not cfg or cfg.get('from') != TENANT['from']:
        fails.append('4: the tenant\'s own four did not outrank the platform, resolved %s' % shape(cfg))

    # 5. All four or none. A half-filled tenant that borrows the platform's
    # missing pieces sends authenticated as this boutique and arriving from
    # another — and the recipient sees only the second, so her reply goes to the
    # wrong salon. This is the state a field-by-field `or` fallback passes 4 on.
    clear(cur, [PARAM['from']])
    cfg = sms._twilio_config()
    if not cfg or any(cfg.get(field) != PLATFORM[field] for field in PLATFORM):
        fails.append('5: three tenant parameters were mixed with the platform '
                     'environment instead of falling through whole: %s' % shape(cfg))
finally:
    conn.rollback()
    conn.close()

for f in fails:
    print('  ', f, file=sys.stderr)
sys.exit(1 if fails else 0)
PY
done
# Every check above asks one tenant about itself, which is precisely how a shared
# database.secret survived 263 green checks (section 1, and .memory/odoo-traps.md
# §13). "One account behind every database" is not a claim any single tenant can
# make: it is only true if two of them, asked separately, answer the same. A
# per-tenant environment read that accidentally keyed off the database name would
# satisfy all five states above and fail only here.
.venv/bin/python - <<'PY' && ok "bella and noga inherit the same platform sender" \
  || bad "cross-tenant twilio inheritance" "the two tenants resolved different senders from one environment — a per-database credential is back"
import os
import sys

import psycopg2

PLATFORM = {'TWILIO_ACCOUNT_SID': 'ACplatform', 'TWILIO_API_KEY_SID': 'SKplatform',
            'TWILIO_API_KEY_SECRET': 'splatform', 'TWILIO_FROM_NUMBER': '+972500000001'}
os.environ.update(PLATFORM)

src = open('addons/modryn_portal/models/sms.py').read()
ns = {'os': os}
exec(src[src.index('TWILIO_BASE ='):src.index('def normalize_il_phone')], ns)
exec("def _twilio_config(self):" + src.split("    def _twilio_config(self):")[1]
     .split("\n    @api.model")[0], ns)
KEYS = [ns['P_ACCOUNT_SID'], ns['P_KEY_SID'], ns['P_KEY_SECRET'], ns['P_FROM'],
        'modryn.twilio.disabled']


class Icp:
    def __init__(self, cur):
        self.cur = cur

    def sudo(self):
        return self

    def get_param(self, key, default=False):
        self.cur.execute("SELECT value FROM ir_config_parameter WHERE key = %s", (key,))
        row = self.cur.fetchone()
        return row[0] if row else default


class Sms:
    _twilio_config = ns['_twilio_config']

    def __init__(self, cur):
        self.env = {'ir.config_parameter': Icp(cur)}


# bella and noga BY NAME, like every other cross-tenant assertion in this file —
# and bella's four live override parameters are cleared inside the same
# uncommitted transaction, because an override is the one thing that legitimately
# makes two tenants differ and would hide the failure this check exists for.
senders = {}
for db in ('bella', 'noga'):
    conn = psycopg2.connect(dbname=db)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM ir_config_parameter WHERE key = ANY(%s)", (KEYS,))
        cfg = Sms(cur)._twilio_config()
        senders[db] = cfg and cfg.get('from')
    finally:
        conn.rollback()
        conn.close()

# Both None would be "the same" too, and would mean the environment is never read
# — so the platform's own From number is required, not merely agreement.
wrong = {db: v for db, v in senders.items() if v != PLATFORM['TWILIO_FROM_NUMBER']}
for db, v in wrong.items():
    print('  %s resolved %s' % (db, 'nothing' if not v else 'a sender that is not the platform\'s'),
          file=sys.stderr)
sys.exit(1 if wrong else 0)
PY

head_ "10k-ter. one bad number must not end the day's queue"
.venv/bin/python - <<'PY' && ok "normalize_il_phone output is always E.164 and idempotent" \
  || bad "normalize_il_phone" "a branch emits a value it will not re-accept — such a row can never be texted"
import re, itertools, sys
src = open('addons/modryn_portal/models/sms.py').read()
ns = {'re': re}
exec(src[src.index('def normalize_il_phone'):src.index('# Twilio 4xx codes')], ns)
n = ns['normalize_il_phone']; E164 = re.compile(r'\+\d{9,15}')
bad = [(s, n(s)) for L in range(9)
       for s in map(''.join, itertools.product('+0279', repeat=L))
       if n(s) is not None and (not E164.fullmatch(n(s)) or n(n(s)) != n(s))]
for s, r in bad[:5]: print('  %r -> %r -> %r' % (s, r, n(r)), file=sys.stderr)
sys.exit(1 if bad else 0)
PY
grep -q "for candidate in candidates:" "$WAITLIST" \
  && ok "modryn_offer_next walks past a candidate it cannot text" || bad "offer walk" "modryn_offer_next still dead-ends on the first candidate"
grep -A25 "def modryn_offer_next" "$WAITLIST" | grep -q "limit=MAX_OFFER_CANDIDATES" \
  && ok "the walk is bounded" || bad "offer walk bound" "unbounded candidate loop inside a cancellation request"

head_ "10k-quater. one standing offer per day, enforced by the database"
# Existence of the index itself is section 17; this is the code side.
grep -q "_modryn_one_offer_per_day = models.UniqueIndex" "$WAITLIST" \
  && ok "one-offer-per-day is a partial unique index" || bad "offer race" "only a search_count guards it — two workers both offer the same day"
grep -A30 "def modryn_offer_next" "$WAITLIST" | grep -q "ONE_OFFER_PER_DAY_INDEX" \
  && ok "losing the offer race is handled, not a 500" || bad "offer race handling" "UniqueViolation not caught at the call site"
for db in $TENANTS; do
  DUPO=$(psql -d $db -tAc "select coalesce(sum(c),0) from (select count(*)-1 c from modryn_day_waitlist where state='offered' group by day having count(*)>1) x" 2>/dev/null || echo 0)
  [ "${DUPO:-0}" = "0" ] && ok "$db: no day carries two standing offers" || bad "$db duplicate offers" "$DUPO rows must be expired before the index can build"
done

head_ "10k-sexies. a burned entry can rejoin"
grep -A40 "def modryn_join" "$WAITLIST" | grep -q "UniqueViolation" \
  && ok "a racing /waitlist/join is graceful, not a 500" || bad "join race" "modryn_join create() is unguarded against _phone_day_uniq"
grep -A14 "def modryn_join" "$WAITLIST" | grep -q "state', 'in', ('waiting', 'offered')" \
  && bad "expired rejoin" "modryn_join still filters to waiting/offered — an expired customer gets a 500 forever" \
  || ok "modryn_join matches every state, so an expired entry rejoins"

head_ "11. instance hygiene"
# Without db_name, Odoo's cron enumerates EVERY database on the server —
# including MODRYN's f*_test — and errors against each one.
grep -qE '^db_name *=' "$ODOO_CONF" && ok "db_name bounds this instance" || bad "db_name" "absent from $ODOO_CONF — crons will roam"

head_ "12. MODRYN repo (informational — never gates)"
# Only meaningful on a machine that has the sibling design repo checked out. It
# lives at a developer-specific absolute path, so on the staging server it is
# absent — and an absent checkout is nothing to report, not a regression. SKIP
# rather than bad(), because failing here would have made the suite unrunnable
# anywhere but one laptop; and rather than ok(), because "we never looked" must
# never print green. Override the location with MODRYN_REPO.
MOD="${MODRYN_REPO:-/Users/mrwen/Documents/Github/Ryan + rawad + mrwen}"
if [ ! -d "$MOD/.git" ]; then
  skip "MODRYN working tree clean of our changes" "no checkout at $MOD (set MODRYN_REPO to check)"
else
  # NOTE, not bad(): this reads ANOTHER repository's working tree, and that repo
  # has its own live development. It is not a stable gate — the same query passed
  # and then failed inside one run window because a parallel session was mid-edit,
  # and those files were confirmed to be that session's own work, not contamination
  # from here. A gate whose verdict depends on when you happened to look is worse
  # than no gate: it trains everyone to re-run until green.
  #
  # Still printed in full, and still not whitelisted. The information is worth
  # surfacing — a human glancing at the filenames can tell ours from theirs in a
  # second, which is the actual judgement, and no exit code can make it for them.
  ENTRIES=$(cd "$MOD" && git status --porcelain | grep -v "modryn-storefront.png")
  DIRTY=$(printf '%s' "$ENTRIES" | grep -c . | tr -d ' ')
  if [ "${DIRTY:-0}" = "0" ]; then
    note "MODRYN working tree" "clean — nothing to triage"
  else
    note "MODRYN working tree" "$DIRTY entries, listed below (triage by eye: ours = delete it, theirs = leave it)"
    printf '%s\n' "$ENTRIES" | sed 's/^/         /'
  fi
fi

head_ "13. credential hygiene"
# The demo password lived as a literal in seed_staff.py for months and reached git
# history. These guards fail the suite if it ever grows back, because the only
# thing that kept the old literal alive was that nothing ever checked for it.
grep -q "os.environ.get('MODRYN_DEMO_PASSWORD')" scripts/seed_staff.py \
  && ok "seeder takes its password from the environment" || bad "seeder password source" "seed_staff.py no longer reads MODRYN_DEMO_PASSWORD"
# Anchored at column 0: an ASSIGNMENT is the leak. Unanchored, this also matched
# the `MODRYN_DEMO_PASSWORD='pick-your-own'` in the script's own usage hint and
# reported the fix as the bug.
grep -qE "^DEMO_PASSWORD[[:space:]]*=[[:space:]]*['\"]" scripts/seed_staff.py \
  && bad "seeder password source" "DEMO_PASSWORD assigned a string literal — hardcoded password is back" \
  || ok "seeder carries no literal password"
# Scoped to what EXECUTES or instructs a live sign-in. .planning/ is the written
# record of this cleanup and names the burned literal on purpose; forbidding it
# there would mean deleting the evidence to satisfy the guard.
# The needles are split across quotes on purpose: written whole, this line would
# match itself and the guard would report its own source as a leak forever.
if grep -rn -e 'modryn'"2026" -e 'modryn'"poc123" addons scripts docs README.md 2>/dev/null; then
  bad "burned credential absent" "literal present in code/scripts/docs (matches above)"
else
  ok "no burned credential literal in code, scripts or docs"
fi
# The one assertion that proves the failure MODE rather than a string. A silent
# default is exactly how the literal survived, so unset must be fatal. sed
# truncates the script at its first env[...] access, so this never opens a database.
GUARD=$(mktemp); sed '/^Employee = env/,$d' scripts/seed_staff.py > "$GUARD"
if env -u MODRYN_DEMO_PASSWORD .venv/bin/python "$GUARD" >/dev/null 2>&1; then
  bad "seeder refuses to run unconfigured" "exits 0 with MODRYN_DEMO_PASSWORD unset"
else
  ok "seeder refuses to run without MODRYN_DEMO_PASSWORD"
fi
rm -f "$GUARD"

head_ "14. archive is cancel"
# An owner who hits Archive instead of Cancel used to poison that hour forever:
# the row leaves every reader (all four use search() with active_test=True) while
# staying in the slot index, so the hour is offered to every bride and refused for
# every one of them by a row nobody can see.
for db in $TENANTS; do
  # No row may be live-by-app-definition and invisible at the same time.
  detects "$db" "archived-but-live booking" \
    "INSERT INTO calendar_event (name, show_as, start, stop, active, modryn_is_booking, allday, create_uid, write_uid, create_date, write_date) VALUES ('planted','busy', now() + interval '400 days', now() + interval '400 days 1 hour', false, true, false, 1, 1, now(), now());" \
    "SELECT count(*) FROM calendar_event WHERE modryn_is_booking IS TRUE AND modryn_cancelled_at IS NULL AND active IS NOT TRUE;"
  GHOST=$(psql -d "$db" -tAc "select count(*) from calendar_event
    where modryn_is_booking is true and modryn_cancelled_at is null and active is not true")
  [ "${GHOST:-1}" = "0" ] && ok "$db: no archived-but-live booking" \
    || bad "$db archived-but-live booking" "$GHOST row(s) invisible to all four readers yet still 'booked'"
done
# `not vals['active']`, not `is False`: the index predicate is `active IS TRUE`,
# so NULL and 0 hide the row exactly as False does and all three must be caught.
grep -q "if 'active' in vals and not vals\['active'\]" addons/modryn_portal/models/calendar_event.py \
  && ok "archive interception present in calendar_event.write" \
  || bad "archive interception" "write() override missing or narrowed to 'is False' — NULL/0 archive slips through"

head_ "15. no orphan partners"
# _FlushingSavepoint.__init__ calls cr.flush() BEFORE issuing SAVEPOINT, so a
# partner created above the block is already outside the savepoint's reach and
# survives the rollback of the booking it was created for.
# Scoped by create_uid = the PUBLIC user, which is what makes this precise: the
# leak only ever happens on a public web route (sudo() elevates privileges but
# leaves env.user public), while the boutique's own company record and every
# seeded demo contact are written by __system__. An earlier version of this query
# instead filtered `create_date > now() - interval '1 day'` and matched any
# partner at all — it passed only because the seed happened to be 28h old, would
# have false-positived the seeded contacts a day earlier, and let a real orphan
# age quietly out of the window after 24h. Ownership does not expire; a window does.
# EXCLUDING phones that have verified an OTP, and this exclusion is load-bearing
# rather than a loosening. portal.py::verify_submit creates a partner for a
# number it has never booked for, on purpose, so the session has an identity —
# its own comment says "her booking list is simply empty". That is a bride who
# signed in to check her bookings before she has any, which is a legitimate and
# entirely ordinary state.
#
# Without this clause the check reports "savepoint leaking again?" the first
# time a real customer does that on a healthy tenant. It never fired here only
# because nothing had ever exercised the portal-login path against a number with
# no booking — qa/specs/portal.spec.js does, and it went red on its first run.
#
# The discriminator is exact: the savepoint leak creates a partner for a booking
# that then rolled back, and no OTP was ever verified for that number. A portal
# identity always has one. The planted orphan below carries no OTP row either,
# so detects() still has its subject and this cannot pass vacuously.
ORPHAN_SQL="from res_partner p join res_users u on u.id = p.create_uid
  where u.login = 'public' and p.active is true and coalesce(p.phone,'') <> ''
    and not exists (select 1 from calendar_event_res_partner_rel r where r.res_partner_id = p.id)
    and not exists (select 1 from calendar_event e where e.modryn_customer_phone = p.phone)
    and not exists (select 1 from modryn_otp_code o where o.phone = p.phone)"
for db in $TENANTS; do
  detects "$db" "orphan partners" \
    "INSERT INTO res_partner (name, phone, active, company_id, autopost_bills, create_uid, write_uid, create_date, write_date) SELECT 'planted orphan','+972500000000', true, 1, 'ask', u.id, u.id, now(), now() FROM res_users u WHERE u.login='public';" \
    "SELECT count(*) $ORPHAN_SQL;"
  ORPHAN=$(psql -d "$db" -tAc "select count(*) $ORPHAN_SQL" 2>/dev/null || echo ERR)
  [ "${ORPHAN:-ERR}" = "0" ] && ok "$db: no web-created partner without a booking" \
    || bad "$db orphan partners" "$ORPHAN partner(s) created by a public route with a phone and no booking — savepoint leaking again?"
  # Duplicates on one phone are the /claim signature: every claimant for one offer
  # shares offer.phone, so N losers left N copies of the same bride. Public-created
  # only, or a mother and daughter sharing a landline would read as a leak.
  DUPP=$(psql -d "$db" -tAc "select coalesce(max(c),0) from (select count(*) c from res_partner p
    join res_users u on u.id = p.create_uid where u.login='public' and p.active is true
    and coalesce(p.phone,'') <> '' group by p.phone) x")
  [ "${DUPP:-9}" -le 1 ] && ok "$db: no duplicate web-created partner per phone" \
    || bad "$db duplicate partners" "one phone has $DUPP partners — losing racers leaving copies"
done
# -A before the pipe, never `grep -q ... -A 12`: -q exits on first match and
# prints nothing, so the second grep in that pipeline reads an empty stream and
# the assertion passes unconditionally.
grep -A 12 "with request.env.cr.savepoint():" addons/modryn_portal/controllers/waitlist.py \
  | grep -q "Partner.search" && ok "claim: partner lookup inside the savepoint" \
  || bad "claim savepoint scope" "Partner.search/create sits before the savepoint — losers will commit partners"

head_ "16. submitted time is validated"
# Both paths test membership against the set the server itself just offered,
# rather than restating the opening rules — nothing restated, nothing to drift.
for db in $TENANTS; do
  # Every live booking must sit on an offerable hour: Sun-Thu, 10:00-17:00 local,
  # on the hour. AT TIME ZONE is DST-aware, same reason the Python side localises.
  detects "$db" "unoffered booking" \
    "INSERT INTO calendar_event (name, show_as, start, stop, active, modryn_is_booking, allday, create_uid, write_uid, create_date, write_date) VALUES ('planted','busy', (now() + interval '400 days')::date + time '01:17', (now() + interval '400 days')::date + time '02:17', true, true, false, 1, 1, now(), now());" \
    "SELECT count(*) FROM calendar_event WHERE modryn_is_booking IS TRUE AND modryn_cancelled_at IS NULL AND active IS TRUE AND (extract(dow from (start at time zone 'UTC' at time zone 'Asia/Jerusalem')) IN (5,6) OR extract(hour from (start at time zone 'UTC' at time zone 'Asia/Jerusalem')) NOT BETWEEN 10 AND 17 OR extract(minute from start) <> 0 OR extract(second from start) <> 0);"
  BOGUS=$(psql -d "$db" -tAc "select count(*) from calendar_event
    where modryn_is_booking is true and modryn_cancelled_at is null and active is true
      and ( extract(dow from (start at time zone 'UTC' at time zone 'Asia/Jerusalem')) in (5,6)
         or extract(hour from (start at time zone 'UTC' at time zone 'Asia/Jerusalem')) not between 10 and 17
         or extract(minute from start) <> 0
         or extract(second from start) <> 0 )")
  [ "${BOGUS:-1}" = "0" ] && ok "$db: every live booking is on an offerable hour" \
    || bad "$db unoffered booking" "$BOGUS booking(s) on a closed day, closed hour or off-grid minute"
done
grep -q "for d in self._slots() for t in d\['times'\]" addons/modryn_booking/controllers/main.py \
  && ok "/book/submit validates against the offered set" \
  || bad "/book/submit slot validation" "posted slot is not checked against _slots()"
grep -q "for s in self._free_slots_on(offer.day)" addons/modryn_portal/controllers/waitlist.py \
  && ok "/claim validates against the offered set" \
  || bad "/claim slot validation" "posted slot is not checked against _free_slots_on()"

head_ "17. the unique indexes are really there"
# The loudest line in the suite. registry.py DROPS a failed constraint on install
# (_schema.error, :731) and only warns on upgrade (:743) — either way the run exits
# 0, records the version, and ships with no index. This is the only detector.
#
# modryn_template is in the loop ON PURPOSE and is NOT in $TENANTS: a missing index
# there is invisible today and infinite tomorrow, because every boutique provisioned
# from here on is a clone of it.
for db in $TENANTS modryn_template; do
  for idx in calendar_event_modryn_one_live_booking_per_slot \
             modryn_day_waitlist_modryn_one_offer_per_day \
             modryn_day_waitlist_phone_day_uniq \
             modryn_queue_entry_modryn_open_phone_uniq; do
    HAVE=$(psql -d "$db" -tAc "select to_regclass('$idx')" 2>/dev/null)
    [ -n "$HAVE" ] && ok "$db: $idx present" \
      || bad "$db: $idx ABSENT" "the run that should have created it exited 0 — this tenant can sell one fitting room twice"
  done
done

head_ "18. both install entry points are wired"
# migrations/ covers the two hand-built tenants and NOBODY else: modryn_template
# ships the modules uninstalled and new_boutique.sh clones it, so every real
# boutique takes the INSTALL path, which never runs a migration script.
grep -q "'pre_init_hook': 'pre_init_hook'" addons/modryn_portal/__manifest__.py \
  && grep -q "'post_init_hook': 'post_init_hook'" addons/modryn_portal/__manifest__.py \
  && ok "manifest declares both install hooks" \
  || bad "install hooks" "only migrations/ is wired — every cloned boutique would skip the dedupe and the index check"
# loading.py does getattr(sys.modules['odoo.addons.modryn_portal'], name) on the
# PACKAGE; a name defined only in a submodule is invisible to it.
grep -q "from .schema_guard import pre_init_hook, post_init_hook" addons/modryn_portal/__init__.py \
  && ok "hooks are attributes of the package, where getattr() looks" \
  || bad "hook export" "hooks not re-exported from __init__.py — getattr on the package will miss them"
# One copy of the dedupe, or the two paths drift and one gets updated alone.
COPIES=$(grep -rl 'row_number() OVER (PARTITION BY "start"' addons/modryn_portal/ --include='*.py' | wc -l | tr -d ' ')
[ "$COPIES" = "1" ] && ok "the dedupe SQL exists once, in schema_guard" \
  || bad "duplicated dedupe" "$COPIES copies of the partition query — one will be updated and the other will not"
# Follow the manifest rather than a frozen version string. This check was pinned
# to 19.0.1.3.0, so the moment a newer migration was added it kept passing by
# inspecting a directory that no longer runs anywhere — every database already
# records 1.3.0 — while the pair that DOES run went unread.
PORTAL_MIG_V=$(basename "$(ls -d addons/modryn_portal/migrations/19.0.* | sort -V | tail -1)")
for f in pre-migrate post-migrate; do
  grep -q "from odoo.addons.modryn_portal.schema_guard import" \
    addons/modryn_portal/migrations/$PORTAL_MIG_V/$f.py 2>/dev/null \
    && ok "migrations/$PORTAL_MIG_V/$f.py delegates to schema_guard" \
    || bad "$f not shared" "the upgrade path is running its own SQL again"
done

head_ "19. the migration is actually eligible"
MANIFEST_V=$(grep -oE "19\.0\.[0-9.]+" addons/modryn_portal/__manifest__.py | tail -1)
MIG_V=$(basename "$(ls -d addons/modryn_portal/migrations/19.0.* | sort -V | tail -1)")
[ "$MANIFEST_V" = "$MIG_V" ] && ok "manifest $MANIFEST_V matches migrations/$MIG_V" \
  || bad "migration version" "manifest is $MANIFEST_V but the newest migration dir is $MIG_V"
# Odoo runs migrations/<v>/ only when recorded < v <= manifest. Three states, and
# only one of them is wrong:
#   recorded <  MIG_V  → pending, will run on the next -u.
#   recorded == MIG_V  → already applied. Healthy steady state AFTER an upgrade —
#                        section 17 is what proves it actually did its job.
#   recorded >  MIG_V  → this migration can never run on this tenant again. That
#                        is the trap the previous pass fell into: it shipped
#                        migrations/19.0.1.2.0/ while both tenants already
#                        recorded 19.0.1.2.0, so it was a no-op from birth.
# Comparing with sort -V rather than string equality, because "19.0.1.10.0" is
# greater than "19.0.1.9.0" and lexical comparison says the opposite.
for db in $TENANTS; do
  REC=$(psql -d "$db" -tAc "select latest_version from ir_module_module where name='modryn_portal'")
  NEWEST=$(printf '%s\n%s\n' "$REC" "$MIG_V" | sort -V | tail -1)
  if [ "$REC" = "$MIG_V" ]; then
    ok "$db: recorded $REC — migrations/$MIG_V has been applied"
  elif [ "$NEWEST" = "$MIG_V" ]; then
    ok "$db: recorded $REC — migrations/$MIG_V is pending and will run"
  else
    bad "$db migration can never run" "ir_module_module records $REC, which is already past migrations/$MIG_V — Odoo will skip it silently forever"
  fi
done

head_ "20. the golden template ships a working boutique"
# modryn_platform is excluded BY NAME below, and the loop after this check is
# why: it is MODRYN's own register of which boutiques subscribe, and it must
# never be installed in a boutique. Excluded by name rather than by loosening
# the pattern, so a ninth addon added tomorrow is still caught by its absence.
#
# This check only started SEEING modryn_platform once something ran -u against
# the template: a new addon gets its ir_module_module row when the module list
# is refreshed, not when its directory appears. So "it passed yesterday" was
# never evidence the module was absent - only that nobody had looked yet.
MISSING=$(psql -d modryn_template -tAc "select string_agg(name, ',') from ir_module_module
  where name like 'modryn%' and name <> 'modryn_platform' and state <> 'installed'" 2>/dev/null)
[ -z "$MISSING" ] && ok "modryn_template has every boutique module installed" \
  || bad "template ships no product" "uninstalled in the template: $MISSING — every tenant cloned from it 404s on /book, /floor and /my"

# The other half, and the one that actually protects a customer: no boutique
# may carry the platform register. It lists every OTHER shop that subscribes -
# names, cities, partners, what each one pays for - so a boutique with it
# installed would put its owner one URL away from her competitors' details.
# Asserted on the template AND on every tenant: the template is what future
# boutiques get cloned from, the tenants are the ones that exist today.
for db in modryn_template $TENANTS; do
  STATE=$(psql -d "$db" -tAc "select state from ir_module_module
    where name='modryn_platform'" 2>/dev/null)
  # No row at all is a correct answer too: it means this database has not had
  # its module list refreshed since the addon appeared, which is exactly as
  # not-installed as 'uninstalled' is.
  if [ -z "$STATE" ] || [ "$STATE" = "uninstalled" ]; then
    ok "$db does not carry the platform register"
  else
    bad "$db can see other boutiques" "modryn_platform is '$STATE' in $db - its owner can open /platform/boutiques and read every subscribing shop's details"
  fi
done
grep -q "^  -i modryn_theme,modryn_booking" scripts/build_template.sh \
  && ok "build_template.sh installs the modryn addons" \
  || bad "build_template.sh" "installs core only; the addons never reach the template"
grep -q "to_regclass" scripts/new_boutique.sh \
  && ok "new_boutique.sh refuses a tenant cloned from a template with no index" \
  || bad "new_boutique.sh" "hands over a tenant without checking it inherited the indexes"

head_ "21. outcomes end a booking (modryn_ops)"
# A booking with no ending starves every downstream number — conversion, ATV,
# no-show rate are all made of outcomes. These prove the ending exists, that
# nobody can quietly skip or rewrite it, and that the audit trail has teeth.
for db in $TENANTS modryn_template; do
  OSTATE=$(psql -d $db -tAc "select state from ir_module_module where name='modryn_ops'")
  [ "$OSTATE" = "installed" ] && ok "$db: modryn_ops installed" || bad "$db modryn_ops" "state '$OSTATE', not installed"
  OCOLS=$(psql -d $db -tAc "select count(*) from information_schema.columns where table_name='calendar_event' and column_name in ('modryn_outcome','modryn_outcome_at','modryn_outcome_by_id','modryn_sale_amount','modryn_feedback_sent_at')")
  [ "$OCOLS" = "5" ] && ok "$db: outcome fields exist" || bad "$db outcome fields" "expected 5, got $OCOLS"
  AUD=$(psql -d $db -tAc "select (to_regclass('modryn_audit_log') is not null)::int")
  [ "$AUD" = "1" ] && ok "$db: audit table exists" || bad "$db audit table" "modryn_audit_log missing"
done
# The manager's nag must see a past booking that never got an outcome — that
# is the number that keeps conversion honest. Seeded off-grid two days back so
# it cannot collide with a real slot's unique index.
for db in $TENANTS; do
  detects "$db" "unclosed past booking" \
    "INSERT INTO calendar_event (name, show_as, start, stop, active, modryn_is_booking, allday, create_uid, write_uid, create_date, write_date) VALUES ('planted-unclosed','busy', now() - interval '2 days', now() - interval '2 days' + interval '1 hour', true, true, false, 1, 1, now(), now());" \
    "SELECT count(*) FROM calendar_event WHERE modryn_is_booking IS TRUE AND modryn_cancelled_at IS NULL AND active IS TRUE AND modryn_outcome IS NULL AND start < now() AND start >= now() - interval '14 days';"
done
# Closing is for signed-in staff; changing a recorded outcome is for managers.
FINB=$(curl -sg -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","method":"call","params":{"event_id":1,"outcome":"sold"}}' "$BELLA/floor/finish/booking" | grep -c '"result"')
[ "$FINB" = "0" ] && ok "/floor/finish/booking refuses anonymous" || bad "/floor/finish/booking anonymous" "returned a result"
# The walk-in half of the same door, and the one that MOVES STOCK: a hole here
# is a stranger taking dresses off a boutique's rail from the open internet.
FINW=$(curl -sg -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","method":"call","params":{"entry_id":1,"outcome":"sold","variant_id":1}}' "$BELLA/floor/walkin/outcome" | grep -c '"result"')
[ "$FINW" = "0" ] && ok "/floor/walkin/outcome refuses anonymous" || bad "/floor/walkin/outcome anonymous" "returned a result"
grep -q "if force or not me or event.modryn_employee_id != me" addons/modryn_ops/controllers/floor_ops.py \
  && ok "staff cannot force, nor close another stylist's booking" \
  || bad "staff close gate" "the stylist-or-manager guard is gone from floor_ops.py"
grep -q "if previous and not force" addons/modryn_ops/models/calendar_event.py \
  && ok "a recorded outcome cannot be silently overwritten" \
  || bad "overwrite gate" "modryn_set_outcome no longer refuses ungated rewrites"
grep -q "self.modryn_feedback_sent_at = fields.Datetime.now()" addons/modryn_ops/models/calendar_event.py \
  && ok "the not-sold feedback text is stamped for the manager to see" \
  || bad "feedback stamp" "modryn_feedback_sent_at is never written"
# The audit page is the owner's alone — managers appear IN it.
AUDC=$(code "$BELLA/manage/audit")
[ "$AUDC" != "200" ] && ok "/manage/audit walled from the public ($AUDC)" || bad "/manage/audit" "renders for an anonymous visitor"
# The bride's record: relationship data staff may edit, money data they may
# not even see. The budget key's absence from the staff payload IS the ACL.
for db in $TENANTS modryn_template; do
  CCOLS=$(psql -d $db -tAc "select count(*) from information_schema.columns where table_name='res_partner' and column_name in ('modryn_wedding_date','modryn_budget','modryn_party_notes','modryn_measurements','modryn_notes','modryn_category')")
  [ "$CCOLS" = "6" ] && ok "$db: customer CRM fields exist" || bad "$db CRM fields" "expected 6, got $CCOLS"
done
grep -q "if self._is_manager():" addons/modryn_ops/controllers/floor_ops.py \
  && grep -q "data\['budget'\]" addons/modryn_ops/controllers/floor_ops.py \
  && ok "budget enters the payload only for managers" \
  || bad "budget gate (read)" "the manager-only budget key is gone from _customer_payload"
grep -q "if budget is not None and not self._is_manager():" addons/modryn_ops/controllers/floor_ops.py \
  && ok "budget writes from non-managers are refused server-side" \
  || bad "budget gate (write)" "customer_save no longer refuses staff budget writes"
# Derived, not ratcheted: her category is recomputed from her whole outcome
# history, so another sold booking keeps her 'purchased' through a later
# not-sold browse, while correcting her ONLY sale genuinely downgrades her.
grep -q "_modryn_refresh_partner_category" addons/modryn_ops/models/calendar_event.py \
  && grep -q "modryn_outcome', '=', 'sold'" addons/modryn_ops/models/calendar_event.py \
  && ok "bride category is derived from her whole outcome history" \
  || bad "category derivation" "_modryn_refresh_partner_category no longer recomputes from outcomes"
# A correction must not re-text the bride: side effects fire only when the
# outcome actually changed.
grep -q "if previous != outcome:" addons/modryn_ops/models/calendar_event.py \
  && ok "a detail-only re-save fires no SMS and resets no task clock" \
  || bad "re-save side effects" "the previous != outcome gate is gone — every force re-save re-texts the customer"
CPRO=$(curl -sg -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","method":"call","params":{"phone":"052-0000000"}}' "$BELLA/floor/customer" | grep -c '"result"')
[ "$CPRO" = "0" ] && ok "/floor/customer refuses anonymous" || bad "/floor/customer anonymous" "returned a result"
# An outcome edit must leave a trail. The write-diff runs in Python, so the
# proof is structural: the write override wires every audited field through
# modryn_log, and the log model is append-only for every non-system group.
grep -q "AUDITED_FIELDS" addons/modryn_ops/models/calendar_event.py \
  && ok "outcome edits route through the audit diff" \
  || bad "audit diff" "calendar_event.write no longer diffs audited fields"
AUDW=$(psql -d bella -tAc "select count(*) from ir_model_access a join ir_model m on m.id=a.model_id where m.model='modryn.audit.log' and (a.perm_write or a.perm_unlink)")
[ "${AUDW:-1}" = "0" ] && ok "audit log is append-only (no group may edit or delete)" || bad "audit log mutability" "$AUDW ACL rows grant write/unlink"

head_ "22. tasks, checklists and escalation (modryn_ops)"
# The follow-up work outcomes create, and the daily open/close routine. The
# index is what stops two boards minting duplicate checklists; the cron is
# what stops a forgotten task staying forgotten.
for db in $TENANTS modryn_template; do
  TIDX=$(psql -d $db -tAc "select count(*) from pg_indexes where indexname='modryn_task_modryn_one_instance_per_day'")
  [ "$TIDX" = "1" ] && ok "$db: modryn_task_modryn_one_instance_per_day present" || bad "$db task index" "missing — duplicate checklists every morning"
done
# The template ships EMPTY checklists by the owner's decision: each boutique
# defines its own routine at /manage/checklists.
TPLN=$(psql -d modryn_template -tAc "select count(*) from modryn_task_template")
[ "$TPLN" = "0" ] && ok "modryn_template ships no checklist templates" || bad "template checklists" "$TPLN seeded rows — the decision was to ship empty"
# Install-and-upgrade wiring, same trap §18/§19 guard as modryn_portal.
grep -q "'pre_init_hook': 'pre_init_hook'" addons/modryn_ops/__manifest__.py \
  && grep -q "'post_init_hook': 'post_init_hook'" addons/modryn_ops/__manifest__.py \
  && ok "modryn_ops manifest declares both install hooks" || bad "modryn_ops hooks" "not declared in the manifest"
grep -q "from .schema_guard import post_init_hook, pre_init_hook" addons/modryn_ops/__init__.py \
  && ok "modryn_ops hooks are attributes of the package" || bad "modryn_ops hook wiring" "__init__.py does not re-export them"
OPS_MIG=$(ls addons/modryn_ops/migrations/ 2>/dev/null | sort -V | tail -1)
OPS_MAN=$(grep -oE "19\.0\.[0-9.]+" addons/modryn_ops/__manifest__.py | head -1)
[ "$OPS_MIG" = "$OPS_MAN" ] && ok "modryn_ops manifest $OPS_MAN matches migrations/$OPS_MIG" \
  || bad "modryn_ops migration eligibility" "manifest $OPS_MAN vs migrations/$OPS_MIG — the newer pair will never run"
for db in $TENANTS; do
  # Escalation must SEE an overdue-unescalated task. Existence+active only for
  # the cron itself: short-interval crons sit permanently overdue by design
  # (.memory/odoo-traps.md §11).
  detects "$db" "overdue unescalated task" \
    "INSERT INTO modryn_task (name, task_type, state, due_at, create_uid, write_uid, create_date, write_date) VALUES ('planted-overdue','adhoc','open', now() - interval '45 minutes', 1, 1, now(), now());" \
    "SELECT count(*) FROM modryn_task WHERE state='open' AND escalated_at IS NULL AND due_at IS NOT NULL AND due_at < now() - interval '30 minutes';"
  ECRON=$(psql -d $db -tAc "select count(*) from ir_cron c join ir_act_server a on a.id=c.ir_actions_server_id where a.code like '%_modryn_escalate_overdue%' and c.active")
  [ "${ECRON:-0}" -ge 1 ] && ok "$db: escalation cron installed and active" || bad "$db escalation cron" "not found or inactive"
done
# Walls: the checklist registry is the owner's; ticking needs a signed-in
# member of staff.
CHK=$(code "$BELLA/manage/checklists")
[ "$CHK" != "200" ] && ok "/manage/checklists walled from the public ($CHK)" || bad "/manage/checklists" "renders for an anonymous visitor"
TDN=$(curl -sg -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","method":"call","params":{"task_id":1}}' "$BELLA/tasks/done" | grep -c '"result"')
[ "$TDN" = "0" ] && ok "/tasks/done refuses anonymous" || bad "/tasks/done anonymous" "returned a result"
# An overwritten outcome must drop the follow-up work the old outcome created,
# or the completion KPI counts tasks nobody was ever meant to do.
grep -q "_modryn_outcome_tasks_cancel" addons/modryn_ops/models/calendar_event.py \
  && ok "outcome overwrite unlinks the old outcome's open tasks" \
  || bad "overwrite task cleanup" "_modryn_outcome_tasks_cancel is gone"

head_ "23. reports read what outcomes wrote (modryn_ops)"
# The KPI page is only ever as honest as the rows beneath it. Plant a sold
# outcome inside a rolled-back transaction and require the conversion
# numerator — the exact SQL /manage/reports runs — to count it.
for db in $TENANTS; do
  detects "$db" "sold outcome" \
    "INSERT INTO calendar_event (name, show_as, start, stop, active, modryn_is_booking, allday, modryn_outcome, modryn_outcome_at, modryn_sale_amount, modryn_customer_phone, create_uid, write_uid, create_date, write_date) VALUES ('planted-sold','busy', now() - interval '3 hours', now() - interval '2 hours', true, true, false, 'sold', now(), 5000, '052-0000001', 1, 1, now(), now());" \
    "SELECT count(*) FILTER (WHERE modryn_outcome = 'sold') FROM calendar_event WHERE modryn_is_booking IS TRUE AND modryn_cancelled_at IS NULL AND modryn_outcome IS NOT NULL AND start >= date_trunc('month', now()) AND start < now() + interval '1 day';"
done
# Walls: numbers for managers and up; a stylist gets exactly her own.
RPT=$(code "$BELLA/manage/reports")
[ "$RPT" != "200" ] && ok "/manage/reports walled from the public ($RPT)" || bad "/manage/reports" "renders for an anonymous visitor"
MYS=$(curl -sg -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","method":"call","params":{}}' "$BELLA/floor/my/stats" | grep -c '"result"')
[ "$MYS" = "0" ] && ok "/floor/my/stats refuses anonymous" || bad "/floor/my/stats anonymous" "returned a result"
# The self-stats route must be structurally incapable of reading a colleague:
# the employee comes from the session, never from a parameter.
grep -q "def my_stats(self):" addons/modryn_ops/controllers/reports.py \
  && ok "/floor/my/stats takes no employee parameter (self-scoped by construction)" \
  || bad "my_stats scoping" "the route signature grew a parameter — a stylist could ask for a colleague"
grep -q "employee_id=me.id" addons/modryn_ops/controllers/reports.py \
  && ok "my-stats query is scoped to the session's own employee" \
  || bad "my_stats scoping" "the employee filter is gone"

head_ "24. opening hours are a table, not a constant (modryn_booking)"
# The Sun-Thu 10:00-18:00 lattice was hardcoded TWICE — modryn_booking's
# controller and modryn_portal/controllers/waitlist.py — and the second copy
# carried no weekday filter at all, so a claim link cheerfully offered a Friday.
# One table now feeds both. This section is what stops it drifting back.
#
# modryn_template is in the loop ON PURPOSE and is NOT in $TENANTS, for section
# 17's reason: a seed that misses the golden database misses every boutique
# cloned from it from here on, and no per-tenant check would ever notice.
for db in $TENANTS modryn_template; do
  if [ -z "$(psql -d "$db" -tAc "select to_regclass('public.modryn_opening_hours')" 2>/dev/null)" ]; then
    bad "$db: modryn_opening_hours table" "missing — the model never reached this database, so /book is still drawing a constant"
    continue
  fi
  SEED=$(psql -d "$db" -tAc "select count(*)||'|'||count(*) filter (where start_hour=10 and end_hour=18 and weekday in ('6','0','1','2','3'))||'|'||count(*) filter (where weekday in ('4','5')) from modryn_opening_hours where active" 2>/dev/null)
  # Positive control, and it has to come first: an empty table answers "no
  # Friday row" exactly as a correctly seeded one does, so the shape assertion
  # below would print green against no rows at all — the failure mode section 8
  # and section 21 both had to grow detects() for.
  if [ "${SEED%%|*}" = "0" ]; then
    bad "$db: opening hours seeded" "the table exists and is EMPTY — /book offers nothing, and the shape check below would pass on nothing"
    continue
  fi
  # Exactly the lattice that used to be the constant: Sunday('6') through
  # Thursday('3'), 10.0->18.0, no Friday('4') or Saturday('5') row. This is what
  # lets every booking assertion above stay green without being edited — a red
  # line here means the seed CHANGED behaviour rather than merely relocating it.
  [ "$SEED" = "5|5|0" ] && ok "$db: seeded Sun-Thu 10:00-18:00, no Friday or Saturday" \
    || bad "$db opening-hours seed" "rows|Sun-Thu 10-18|Fri-Sat reads '$SEED', want '5|5|0' — this boutique's week is not the one the rest of this suite asserts"
done
# Blackout dates. The weekly grid can say the shop opens on Thursdays; it cannot
# say it is shut on THIS Thursday, so Yom Kippur, a wedding and stocktaking were
# all unsayable and /book sold slots on every one of them. modryn_template is in
# the loop for the reason it is above: a model that never reached the golden
# database never reaches a single boutique cloned from it afterwards.
for db in $TENANTS modryn_template; do
  [ -n "$(psql -d "$db" -tAc "select to_regclass('public.modryn_closure')" 2>/dev/null)" ] \
    && ok "$db: modryn_closure table present" \
    || bad "$db: modryn_closure table" "missing — this boutique cannot close for a holiday, so /book will sell Yom Kippur"
done
# EMPTY on the TEMPLATE only, and deliberately not per tenant. Holidays are data
# the owner types, never a computed Hebrew calendar, so a clone inheriting
# somebody else's Yom Kippur is a bug — while a real boutique accumulating real
# closed days is the feature working. Asserting emptiness on bella and noga
# would turn this suite red the first time anyone uses it.
CLON=$(psql -d modryn_template -tAc "select count(*) from modryn_closure" 2>/dev/null)
[ "$CLON" = "0" ] && ok "modryn_template ships no closures" \
  || bad "template closures" "${CLON:-<no table>} seeded rows — every boutique cloned from here inherits a holiday it never chose"
# ...and that emptiness is precisely why the grid check below cannot stand alone:
# with no closure anywhere, "no closed date was rendered" passes over nothing.
# Plant one inside a rolled-back transaction and require the range test
# modryn_closed_dates() runs to find tomorrow in it — inclusive on BOTH ends,
# which is the half an off-by-one breaks silently.
for db in $TENANTS; do
  detects "$db" "a closure covering tomorrow" \
    "INSERT INTO modryn_closure (name, date_from, date_to, active, create_uid, write_uid, create_date, write_date) VALUES ('planted-closure', (now() at time zone 'Asia/Jerusalem')::date + 1, (now() at time zone 'Asia/Jerusalem')::date + 1, true, 1, 1, now(), now());" \
    "SELECT count(*) FROM modryn_closure WHERE active AND ((now() at time zone 'Asia/Jerusalem')::date + 1) BETWEEN date_from AND date_to;"
done
# The two hand-built tenants take the UPGRADE path; a freshly cloned boutique
# takes the INSTALL path. Odoo runs migrations/<v>/ only while recorded < v <=
# manifest, so a bumped manifest with no matching directory — or a directory the
# manifest has already sailed past — seeds new boutiques and silently leaves
# bella and noga on no hours at all. Same trap as sections 19 and 22.
BK_MAN=$(grep -E "^ *'version'" addons/modryn_booking/__manifest__.py | grep -oE "19\.0\.[0-9.]+" | head -1)
BK_MIG=$(ls addons/modryn_booking/migrations/ 2>/dev/null | sort -V | tail -1)
[ -n "$BK_MIG" ] && [ "$BK_MAN" = "$BK_MIG" ] \
  && ok "modryn_booking manifest $BK_MAN matches migrations/$BK_MIG" \
  || bad "modryn_booking migration eligibility" "manifest '${BK_MAN:-<none>}' vs migrations/'${BK_MIG:-<none>}' — the upgrade path never runs, so only cloned boutiques get hours"
# 303 exactly, not merely "not 200": a 404 is also not 200, and would mean the
# page does not exist rather than that it is walled — which is how a wall check
# passes for a page nobody ever built.
HRS=$(code "$BELLA/manage/hours")
[ "$HRS" = "303" ] && ok "/manage/hours exists and redirects an anonymous visitor ($HRS)" \
  || bad "/manage/hours" "answered $HRS — 200 means the public can rewrite the boutique's week, anything else means the page is not there"
# ...and the page must actually follow the table. Both halves below are derived
# from the rows rather than restated here, so an owner who edits her hours moves
# the expectation with her instead of turning this section red.
fetch "$BELLA/book"
OPEN_LBL=$(psql -d bella -tAc "select to_char(time '00:00' + start_hour * interval '1 hour', 'HH24:MI') from modryn_opening_hours where active order by start_hour limit 1" 2>/dev/null)
if [ -z "$OPEN_LBL" ]; then
  # An empty label would leave the regex below as `> *<`, which matches nearly
  # every tag on the page: the check would pass hardest exactly when the table
  # is gone.
  bad "/book offers the table's first hour" "no active opening-hours row on bella to derive the label from"
else
  # The label, not the option's value: the value is UTC, so the 10:00 the bride
  # reads is 07:00 in the attribute for half the year.
  tr '\n' ' ' < "$PAGE" | grep -qE "> *$OPEN_LBL *<" \
    && ok "/book offers a $OPEN_LBL slot, the earliest hour the table opens" \
    || bad "/book first slot" "no $OPEN_LBL option on the page — the picker is not built from modryn_opening_hours"
fi
# A closed day is skipped whole, so its date reaches NEITHER the picker's
# optgroups NOR the "day you wanted is full" waitlist list — it must not appear
# in any form. An open day always reaches one of the two, booked out or not.
# That asymmetry IS the weekday filter, and it is the thing waitlist.py lacked.
#
# "Closed" now means two things and the query below derives BOTH from the data:
# a weekday with no window, and a date a modryn_closure covers. A blackout date
# must vanish exactly as Saturday does — rendering it as a FULL day instead would
# invite her onto a waitlist for a day nobody is coming in, which is a product
# regression dressed as a smaller diff.
SEEN=0; MISSING=""; OFFERED=""
while IFS='|' read -r DAY IS_OPEN; do
  [ -z "$DAY" ] && continue
  SEEN=$((SEEN+1))
  if grep -qF "$DAY" "$PAGE"; then
    [ "$IS_OPEN" = "t" ] || OFFERED="$OFFERED $DAY"
  elif [ "$IS_OPEN" = "t" ]; then
    MISSING="$MISSING $DAY"
  fi
done <<SQL
$(psql -d bella -tAq -c "SELECT to_char(d, 'DD.MM.YYYY'), (extract(isodow from d)::int - 1)::text in (select weekday from modryn_opening_hours where active) AND NOT EXISTS (select 1 from modryn_closure where active and d::date between date_from and date_to) FROM generate_series((now() at time zone 'Asia/Jerusalem')::date + 1, (now() at time zone 'Asia/Jerusalem')::date + 14, interval '1 day') d" 2>/dev/null)
SQL
# Jerusalem local dates, not the shell's: _slots() counts its fortnight from
# datetime.now(TZ), and a psql session on a UTC host would slide the window by a
# day for three hours every evening (.memory/odoo-traps.md §14).
#
# SEEN is this check's positive control. With the table absent the query errors,
# the loop reads nothing, and both lists stay empty — a silent green over zero
# days examined.
[ "$SEEN" = "14" ] && [ -z "$MISSING$OFFERED" ] \
  && ok "/book renders exactly the 14 days the table opens and no closure shuts" \
  || bad "/book grid does not follow the tables" "$SEEN of 14 days derived; open days missing from the page:${MISSING:- none}; days shut by weekday or by closure and offered anyway:${OFFERED:- none}"

# ---- more than one fitting in the same hour ---------------------------------
# Capacity is a column on the opening-hours WINDOW, spent through a seat number
# on the booking, and the unique index moves from (start) to (start, seat).
# DURATION is deliberately not part of this: a 90-minute fitting overlapping the
# next hour cannot be expressed by ANY unique index — that needs a tstzrange
# EXCLUDE constraint, btree_gist, and a non-uniform grid. Capacity is the piece
# the index actually blocks, so capacity is the piece that ships.
#
# modryn_template is in the loop for section 17's reason: an index that never
# changed in the golden database never changes in any boutique cloned from it.
for db in $TENANTS modryn_template; do
  SEAT=$(psql -d "$db" -tAc "select count(*) from information_schema.columns where table_name='calendar_event' and column_name='modryn_slot_seat'" 2>/dev/null)
  [ "${SEAT:-0}" = "1" ] && ok "$db: calendar_event.modryn_slot_seat exists" \
    || bad "$db: modryn_slot_seat column" "absent — the unique index has nothing to seat a second bride on, so this boutique is still one fitting an hour"
  # THE check the happy path cannot make. Section 17 asserts this index by NAME,
  # and an index left on (start) alone passes that, passes every booking test in
  # this suite, and pins capacity at 1 while /manage/hours says two. Read the
  # definition. `start` first, so a seat-only index is caught too.
  DEF=$(psql -d "$db" -tAc "select indexdef from pg_indexes where indexname='calendar_event_modryn_one_live_booking_per_slot'" 2>/dev/null)
  printf '%s' "$DEF" | grep -qE 'btree \("?start"?, *modryn_slot_seat\)' \
    && ok "$db: the one-booking index is on (start, modryn_slot_seat)" \
    || bad "$db: one-booking index definition" "reads '${DEF:-<no such index>}' — it never gained the seat column, so every window is capped at one fitting whatever the owner typed"
  # Every window still takes one at a time, which is why every booking assertion
  # above stays green unedited. `is distinct from` so a NULL — i.e. the column
  # added without a default — is counted rather than silently skipped.
  CAPS=$(psql -d "$db" -tAc "select count(*) from modryn_opening_hours where capacity is distinct from 1" 2>/dev/null)
  [ "${CAPS:-x}" = "0" ] && ok "$db: every seeded window takes one fitting at a time" \
    || bad "$db: window capacity default" "${CAPS:-<no capacity column>} window(s) are not the default 1 — the week the rest of this suite asserts is not the week /book now draws"
done
# ...and none of the three checks above proves the index still REFUSES anything.
# A unique index that stopped enforcing reads identically in pg_indexes. Plant the
# clash, inside a rolled-back transaction, and require the database to have
# refused it — and in the same breath require a DIFFERENT seat at that same
# instant to be ACCEPTED, which is the half that catches an old (start)-only
# index surviving the migration under another name. Both halves, or nothing:
# `count(*) = 2` so one of the two passing alone cannot print green.
#
# ON CONFLICT DO NOTHING rather than an exception handler: a raw unique violation
# aborts the transaction and detects() would read the abort as a failed seed.
# Untargeted, so it arbitrates against whatever unique indexes the table really
# has — which is exactly the question being asked.
SEAT_ROW="INSERT INTO calendar_event (name, show_as, start, stop, active, modryn_is_booking, modryn_slot_seat, allday, create_uid, write_uid, create_date, write_date) VALUES"
for db in $TENANTS; do
  detects "$db" "a second bride on a seat that is already taken" \
    "$SEAT_ROW ('planted-seat-0','busy', now() - interval '2 days', now() - interval '2 days' + interval '1 hour', true, true, 0, false, 1, 1, now(), now());
     CREATE TEMP TABLE probe (label text, inserted int);
     WITH again AS ($SEAT_ROW ('planted-seat-0-again','busy', now() - interval '2 days', now() - interval '2 days' + interval '1 hour', true, true, 0, false, 1, 1, now(), now()) ON CONFLICT DO NOTHING RETURNING 1)
     INSERT INTO probe SELECT 'same_seat', count(*)::int FROM again;
     WITH other AS ($SEAT_ROW ('planted-seat-1','busy', now() - interval '2 days', now() - interval '2 days' + interval '1 hour', true, true, 1, false, 1, 1, now(), now()) ON CONFLICT DO NOTHING RETURNING 1)
     INSERT INTO probe SELECT 'other_seat', count(*)::int FROM other;" \
    "SELECT (count(*) = 2)::int FROM probe WHERE (label = 'same_seat' AND inserted = 0) OR (label = 'other_seat' AND inserted = 1);"
done
# modryn_portal's manifest-vs-migrations eligibility is NOT re-checked here. It is
# already section 19, which additionally compares each tenant's RECORDED version —
# the state that decides whether the migration can ever run. A second, weaker copy
# of that check in this section would be one more thing to keep in step, and the
# suite has a comment further up about exactly that kind of check.

# ---- the rota caps the grid, and a silent rota caps nothing ------------------
# A day cannot sell more concurrent fittings than it has stylists on the floor,
# so modryn_roster overrides modryn_daily_caps() and trims each hour by the
# people its PUBLISHED rota puts on that date. The risk is entirely in the
# fallback: a date the rota says nothing about must come back UNCAPPED, never
# capped at zero, or the boutique's whole booking grid empties with no error
# anywhere. The three checks below are that fallback, from both sides.
#
# noga has never opened /roster: every one of its shift slots is unpublished, so
# modryn_rostered_on() answers None for all fourteen days and the cap must be
# completely silent there. Its grid is asserted whole — the day count AND the
# first hour — because "the page still rendered" is not the same as "it still
# sells what it sold yesterday". This is THE regression guard for the empty-grid
# disaster; bella's own grid is already asserted in full above.
fetch "$NOGA/book"
N_LBL=$(psql -d noga -tAc "select to_char(time '00:00' + start_hour * interval '1 hour', 'HH24:MI') from modryn_opening_hours where active order by start_hour limit 1" 2>/dev/null)
if [ -z "$N_LBL" ]; then
  # An empty label leaves the regex as `> *<`, which matches nearly every tag on
  # the page — the check would pass hardest exactly when the table is gone.
  bad "noga /book offers the table's first hour" "no active opening-hours row on noga to derive the label from"
else
  # The label, not the option's value: the value is UTC, so the hour she reads is
  # an hour or two off it depending on the season.
  tr '\n' ' ' < "$PAGE" | grep -qE "> *$N_LBL *<" \
    && ok "noga: /book still offers a $N_LBL slot with no published rota" \
    || bad "noga /book first slot" "no $N_LBL option on the page — the rota cap is trimming a boutique whose rota says nothing, so this grid is empty"
fi
N_SEEN=0; N_MISSING=""; N_OFFERED=""
while IFS='|' read -r DAY IS_OPEN; do
  [ -z "$DAY" ] && continue
  N_SEEN=$((N_SEEN+1))
  # label="..." is the OPTGROUP attribute, and templates.xml emits an optgroup
  # only for a day that is `not full`. A bare grep for the date matches the
  # waitlist <select> too, which a FULL day also prints — so it would report a
  # day the cap had emptied as present, and this check exists to catch exactly
  # that. Trade-off worth naming: a legitimately fully-booked day emits no
  # optgroup either, so this reads "still bookable" rather than "still listed".
  # Both tenants hold zero future bookings, so a full day cannot arise here; if
  # one ever does, this points at it and the answer is to look at why.
  if grep -qF "label=\"$DAY\"" "$PAGE"; then
    [ "$IS_OPEN" = "t" ] || N_OFFERED="$N_OFFERED $DAY"
  elif [ "$IS_OPEN" = "t" ]; then
    N_MISSING="$N_MISSING $DAY"
  fi
done <<SQL
$(psql -d noga -tAq -c "SELECT to_char(d, 'DD.MM.YYYY'), (extract(isodow from d)::int - 1)::text in (select weekday from modryn_opening_hours where active) AND NOT EXISTS (select 1 from modryn_closure where active and d::date between date_from and date_to) FROM generate_series((now() at time zone 'Asia/Jerusalem')::date + 1, (now() at time zone 'Asia/Jerusalem')::date + 14, interval '1 day') d" 2>/dev/null)
SQL
# N_SEEN is the positive control: with the table absent the query errors, the
# loop reads nothing, and both lists stay empty — a silent green over zero days.
[ "$N_SEEN" = "14" ] && [ -z "$N_MISSING$N_OFFERED" ] \
  && ok "noga: /book renders the same 14 days the tables alone would give" \
  || bad "noga /book grid changed under the rota cap" "$N_SEEN of 14 days derived; open days missing from the page:${N_MISSING:- none}; days shut and offered anyway:${N_OFFERED:- none}"
# ...and a silent cap proves nothing about a cap that WORKS.
#
# What was here first was a detects() that planted a published shift in SQL and
# then read it back with SQL that re-derived the same predicate by hand. It was
# a tautology: it passed with modryn_roster/models/opening_hours.py DELETED,
# because it never touched the override at all. It tested Postgres.
#
# This suite cannot start an odoo-bin shell, so it cannot call modryn_daily_caps
# and cannot honestly claim to have exercised it. What it CAN do is assert the
# wiring, which is what actually disappears if the feature is removed or
# half-merged — the same grep-of-the-source tool section 18 uses for the dedupe
# and section 16 for the scan bound. The behaviour itself is proved by hand
# against a running server and recorded in .planning/specs/avail-6-*.md.
grep -q "from . import opening_hours" addons/modryn_roster/models/__init__.py \
  && ok "modryn_roster loads its opening-hours override" \
  || bad "rota cap not loaded" "models/__init__.py does not import opening_hours — Odoo loads Python through __init__, so the override silently does not exist and every date stays uncapped"
grep -q "_inherit = 'modryn.opening.hours'" addons/modryn_roster/models/opening_hours.py \
  && grep -q "def modryn_daily_caps" addons/modryn_roster/models/opening_hours.py \
  && ok "the override extends modryn.opening.hours and defines modryn_daily_caps" \
  || bad "rota cap override missing" "modryn_roster does not override modryn_daily_caps — the booking grid would keep the base {} and the rota would cap nothing"
grep -q "super().modryn_daily_caps" addons/modryn_roster/models/opening_hours.py \
  && ok "the override calls super(), so a third module could cap too" \
  || bad "rota cap does not chain" "the override replaces the base answer instead of extending it"
# Both grids must ASK. An override nothing calls is the same as no override.
grep -q "modryn_daily_caps" addons/modryn_booking/controllers/main.py \
  && grep -q "modryn_daily_caps" addons/modryn_portal/controllers/waitlist.py \
  && ok "both grids ask for the daily cap" \
  || bad "a grid ignores the rota cap" "/book and /claim must both consult it, or the picker and the claim page disagree about what the boutique can staff"
# And the direction stays one-way: modryn_booking may not learn the rota exists.
# Matched on the two things that would actually BE a reference — an import of the
# module, or a lookup of its model — rather than on the module's name, which
# appears legitimately in several comments explaining why this rule exists.
grep -rEn "from odoo\.addons\.modryn_roster|import modryn_roster|\[['\"]modryn\.shift\.slot['\"]\]" \
  addons/modryn_booking/ --include='*.py' > /dev/null \
  && bad "dependency inverted" "modryn_booking imports the roster or reads modryn.shift.slot — the dependency runs modryn_roster -> modryn_staff -> modryn_booking, so this is a load cycle waiting to happen" \
  || ok "modryn_booking still knows nothing about the roster"
# The zero that must never be computed, checked against the live data that would
# compute it. Publishing is week-wide, so a manager who fills Sunday and hits
# Publish leaves the rest of the week published and naming NOBODY — bella has
# four such days next week right now. A day rostered only by the owner counts the
# same way: rostered, and nobody on the floor. Both are "the rota has nothing to
# say", and both must still be sold. If one of these dates vanishes from /book,
# the cap emitted 0 for it.
for db in $TENANTS; do
  fetch "$(turl "$db")/book"
  Z_SEEN=0; Z_LOST=""
  while read -r DAY; do
    [ -z "$DAY" ] && continue
    Z_SEEN=$((Z_SEEN+1))
    # Same reason as above, and here it is the whole point: a cap of 0 empties
    # the day's times, which makes it FULL, which still prints the date in the
    # waitlist <select>. Matching the bare date would pass on precisely the
    # failure this loop is named after.
    grep -qF "label=\"$DAY\"" "$PAGE" || Z_LOST="$Z_LOST $DAY"
  done <<SQL
$(psql -d "$db" -tAq -c "SELECT to_char(s.day,'DD.MM.YYYY') FROM modryn_shift_slot s LEFT JOIN hr_employee_modryn_shift_slot_rel r ON r.modryn_shift_slot_id = s.id LEFT JOIN hr_employee e ON e.id = r.hr_employee_id AND e.active AND e.modryn_level IN ('manager','staff') WHERE s.published AND s.day BETWEEN (now() at time zone 'Asia/Jerusalem')::date + 1 AND (now() at time zone 'Asia/Jerusalem')::date + 14 AND (extract(isodow from s.day)::int - 1)::text IN (SELECT weekday FROM modryn_opening_hours WHERE active) AND NOT EXISTS (SELECT 1 FROM modryn_closure c WHERE c.active AND s.day BETWEEN c.date_from AND c.date_to) GROUP BY s.day HAVING count(e.id) = 0 ORDER BY s.day" 2>/dev/null)
SQL
  if [ "$Z_SEEN" = "0" ]; then
    # No subject on this tenant — noga publishes nothing at all. Not a pass:
    # this check saw no day that could have produced a zero.
    note "$db: no published-but-unstaffed day in the fortnight" "the never-zero guard had nothing to test here"
  else
    [ -z "$Z_LOST" ] && ok "$db: all $Z_SEEN published-but-unstaffed days are still on /book" \
      || bad "$db: a rota cap of zero reached /book" "days published with nobody on the floor and now missing from the page:$Z_LOST — an unstaffed day is uncapped, never capped at 0"
  fi
done

head_ "25. demo web presence"
# The fresh-tenant demo taught this the hard way: Odoo's stock homepage is an
# empty div, the stock footer is yourcompany.example.com boilerplate, and /book
# was never in the nav. These checks pin the replacements ON THE SERVED PAGE —
# a template that exists but stopped propagating through the COW tree would
# pass a psql check and still greet visitors with the empty div.
for db in $TENANTS; do
  fetch "$(turl "$db")/"
  grep -q "modryn_home" "$PAGE" && ok "$db: homepage hero is served" \
    || bad "$db homepage" "modryn_home marker missing from / — the hero did not reach this website's COW tree"
  grep -q 'href="/book"' "$PAGE" && ok "$db: /book is in the page" \
    || bad "$db nav" "no /book link on / — the menu record did not fan out to this website"
  grep -q "o_brand_promotion" "$PAGE" \
    && bad "$db brand promotion" "'Powered by Odoo' is back on the page" \
    || ok "$db: no Odoo brand promotion"
  grep -qiE "disruptive products|yourcompany\.example\.com|555-555-5556|Copyright (&amp;copy;|©) Company name" "$PAGE" \
    && bad "$db footer" "stock Odoo placeholder chrome is served (footer copy, placeholder phone, or 'Company name' copyright)" \
    || ok "$db: stock placeholder chrome is gone"
  # Both website.menu rows (generic + this website's copy) must exist, and the
  # copy must carry Hebrew — the .po only reaches the generic record.
  BOOKMENUS=$(psql -d "$db" -tAc "select count(*) from website_menu where url='/book'")
  [ "${BOOKMENUS:-0}" -ge 2 ] && ok "$db: /book menu rows exist ($BOOKMENUS)" \
    || bad "$db /book menu" "expected generic + per-website rows, found ${BOOKMENUS:-0}"
  HEMENU=$(psql -d "$db" -tAc "select count(*) from website_menu where url='/book' and website_id is not null and name->>'he_IL' is not null")
  [ "${HEMENU:-0}" -ge 1 ] && ok "$db: the website's /book menu copy is translated" \
    || bad "$db /book menu he" "the per-website copy has no he_IL — seed_demo_web.py has not run here"
done
# OTP demo mode is a per-tenant opt-in for credential-less demos ONLY. Neither
# dev tenant may carry it, and the gate must remain the send()'s own 'logged'
# no-provider branch — the one condition that cannot hold when Twilio sent.
for db in $TENANTS; do
  DEMO=$(psql -d "$db" -tAc "select count(*) from ir_config_parameter where key='modryn.sms_demo'")
  [ "${DEMO:-0}" = "0" ] && ok "$db: modryn.sms_demo is not set" \
    || bad "$db sms demo" "modryn.sms_demo is set on a tenant that can text real phones"
done
grep -q "detail == 'logged'" addons/modryn_portal/models/otp.py \
  && ok "demo-code gate reads send()'s 'logged' branch" \
  || bad "demo-code gate" "otp.issue no longer keys on detail == 'logged' — a configured tenant could leak a real code"
# The per-IP dimension: with real Twilio behind anonymous forms, the per-phone
# cap alone leaves an SMS-bomb relay (rotate numbers, same IP). Structural,
# plus the column that must exist for it to count anything.
grep -q "IP_MAX_SENDS_PER_HOUR" addons/modryn_portal/models/otp.py \
  && grep -q "recent_ip" addons/modryn_portal/models/otp.py \
  && ok "OTP issue carries a per-IP cap" \
  || bad "OTP per-IP cap" "IP_MAX_SENDS_PER_HOUR gate missing from otp.issue"
for db in $TENANTS; do
  psql -d "$db" -tAc "select 1 from information_schema.columns where table_name='modryn_otp_code' and column_name='ip'" | grep -q 1 \
    && ok "$db: modryn_otp_code.ip column exists" \
    || bad "$db otp ip column" "the per-IP cap counts a column that is not there"
done
# The workshop's own creation door: manager-gated server-side, closed to the
# anonymous world. 303 (to login) and 403/404 both count as refused; 200 means
# the gate is gone.
ATELIER_ANON=$(curl -sg -o /dev/null -w '%{http_code}' -X POST "$(turl bella)/atelier/task/new" -d "customer_name=x")
[ "$ATELIER_ANON" != "200" ] && ok "anonymous POST /atelier/task/new refused ($ATELIER_ANON)" \
  || bad "atelier task/new gate" "anonymous POST returned 200"
ASSIGN_ANON=$(curl -sg -o /dev/null -w '%{http_code}' -X POST "$(turl bella)/atelier/assign" -d "task_id=1")
[ "$ASSIGN_ANON" != "200" ] && ok "anonymous POST /atelier/assign refused ($ASSIGN_ANON)" \
  || bad "atelier assign gate" "anonymous POST returned 200"
# -A30: each method opens with a docstring; the guard is the first statement
# after it, well within thirty lines but far past two.
#
# task_new used to require _is_manager. It now requires _is_staff, deliberately:
# a seamstress must be able to write down a garment that arrives in her hands.
# The escalation this check really guards against moved rather than vanished —
# it is now task_create() refusing to let a non-manager set seamstress_id, so a
# seamstress cannot hand work to somebody else. All three lines below are
# required together; dropping any one of them re-opens a real hole.
grep -A30 "def task_new" addons/modryn_atelier/controllers/atelier.py | grep -q "_is_staff" \
  && grep -A30 "def assign" addons/modryn_atelier/controllers/atelier.py | grep -q "_is_manager" \
  && grep -A30 "def task_create" addons/modryn_atelier/controllers/atelier.py | grep -q "_is_manager" \
  && ok "atelier routes re-check their group server-side (task_new staff, assign + task_create manager)" \
  || bad "atelier group re-check" "a route lost its group guard — hiding the panel is not a permission"
# The auto-assign pool must be non-empty by construction on a seeded tenant:
# seed_staff.py sets is_workshop on the seamstress role, and the template ships
# it for fresh installs.
for db in $TENANTS; do
  POOL=$(psql -d "$db" -tAc "select count(*) from modryn_staff_role where is_workshop")
  [ "${POOL:-0}" -ge 1 ] && ok "$db: a workshop role exists (pool is live)" \
    || bad "$db workshop pool" "no role has is_workshop — auto-assignment is dead again"
done
psql -d modryn_template -tAc "select count(*) from modryn_staff_role where is_workshop" | grep -q "^[1-9]" \
  && ok "template ships a workshop role" \
  || bad "template workshop role" "modryn_template's seamstress lost is_workshop — fresh boutiques start with a dead pool"

printf "\n\033[1m%d passed, %d failed, %d skipped\033[0m\n" "$PASS" "$FAIL" "$SKIP"
[ "$FAIL" -eq 0 ] || exit 1
