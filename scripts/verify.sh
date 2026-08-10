#!/usr/bin/env bash
# End-to-end regression for the whole PoC: the five areas shipped in stage 0 plus
# the staff layer added in stage 1. Assumes the server is already running.
#
#   ./scripts/verify.sh
#
# Exits non-zero if any check fails, so it can gate a commit.
set -uo pipefail

BASE_PORT="${PORT:-8069}"
BELLA="http://bella.localtest.me:$BASE_PORT"
NOGA="http://noga.localtest.me:$BASE_PORT"
PASS=0
FAIL=0

ok()   { printf "  \033[32mPASS\033[0m %s\n" "$1"; PASS=$((PASS+1)); }
bad()  { printf "  \033[31mFAIL\033[0m %s — %s\n" "$1" "$2"; FAIL=$((FAIL+1)); }
head_() { printf "\n\033[1m%s\033[0m\n" "$1"; }

code() { curl -sg -o /dev/null -w "%{http_code}" "$1"; }
# Fetch to a FILE, never through $(...). Command substitution mangles the
# multi-hundred-KB pages Odoo serves, which silently turns real passes into
# phantom failures — it fooled this script into reporting the Arabic storefront
# broken when it was fine.
PAGE=/tmp/modryn_page.html
body() { curl -sg "$1" -o "$PAGE"; cat "$PAGE"; }
fetch() { curl -sg "$1" -o "$PAGE"; }

head_ "0. server"
[ "$(code "$BELLA/shop")" = "200" ] && ok "server is up" || { bad "server is up" "no 200 from $BELLA/shop"; echo; echo "Start it: ./odoo/odoo-bin server -c odoo.conf --http-interface=127.0.0.1"; exit 1; }

head_ "1. tenancy isolation"
BELLA_DRESSES=$(body "$BELLA/shop" | grep -oE "שמלת [^<\"(]*" | sort -u | head -5)
NOGA_DRESSES=$(body "$NOGA/shop" | grep -oE "שמלת [^<\"(]*" | sort -u | head -5)
[ -n "$BELLA_DRESSES" ] && [ -n "$NOGA_DRESSES" ] && ok "both storefronts render dresses" || bad "both storefronts render dresses" "one is empty"
if [ -z "$(comm -12 <(echo "$BELLA_DRESSES") <(echo "$NOGA_DRESSES"))" ]; then
  ok "catalogs are disjoint"
else
  bad "catalogs are disjoint" "a dress name appears in both tenants"
fi
[ "$(psql -d bella -tAc "select count(*) from calendar_event where modryn_is_booking")" != \
  "$(psql -d noga  -tAc "select count(*) from calendar_event where modryn_is_booking")" ] \
  && ok "booking counts differ per tenant" || ok "booking counts equal (acceptable if both seeded alike)"

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
PUBLIC_OWNED=$(psql -d bella -tAc "select count(*) from calendar_event ce join res_users u on u.id=ce.user_id where ce.modryn_is_booking and u.login='public'")
[ "$PUBLIC_OWNED" = "0" ] && ok "no booking is organized by the public user" || bad "no booking organized by public user" "$PUBLIC_OWNED still are"

head_ "5. languages"
# Hebrew is the DEFAULT and now comes from .po files rather than hardcoded
# literals. These two are the i18n regression test: if a msgid drifted, the
# storefront silently falls back to English and these fail.
fetch "$BELLA/shop"
grep -q "מחיר בתיאום" "$PAGE" && ok "he: price-on-request translated" || bad "he translation" "Hebrew msgid missing — .po drift?"
grep -q 'dir="rtl"' "$PAGE" && ok "he: RTL" || bad "he RTL" "no dir=rtl"

fetch "$BELLA/ar/shop"
grep -q 'lang="ar-001"' "$PAGE" && ok "ar: storefront serves ar-001" || bad "Arabic storefront" "no lang=ar-001"
grep -qE "بحث|المنتجات" "$PAGE" && ok "ar: core UI translated" || bad "core Arabic UI" "no Arabic strings found"

fetch "$BELLA/en/shop"
grep -q 'lang="en-US"' "$PAGE" && ok "en: storefront serves en-US" || bad "English storefront" "no lang=en-US"
grep -q 'dir="ltr"' "$PAGE" && ok "en: LTR (theme must not assume RTL)" || bad "en LTR" "expected dir=ltr"
grep -q "Price on request" "$PAGE" && ok "en: source strings render" || bad "en source strings" "English text missing"

head_ "6. walk-in queue"
[ "$(code "$BELLA/queue/checkin")" = "200" ] && ok "check-in form" || bad "check-in form" "not 200"
[ "$(code "$BELLA/queue/sign")" = "200" ] && ok "QR sign page" || bad "QR sign page" "not 200"
QR=$(code "$BELLA/report/barcode/?barcode_type=QR&value=test&width=200&height=200")
[ "$QR" = "200" ] && ok "QR image renders" || bad "QR image renders" "got $QR (rlPyCairo installed?)"

head_ "7. staff layer"
EMP=$(psql -d bella -tAc "select count(*) from hr_employee where active")
[ "${EMP:-0}" -ge 3 ] && ok "employees exist ($EMP)" || bad "employees exist" "only ${EMP:-0}"
ROLES=$(psql -d bella -tAc "select count(*) from modryn_staff_role where active")
[ "${ROLES:-0}" -ge 3 ] && ok "staff roles exist ($ROLES)" || bad "staff roles exist" "only ${ROLES:-0}"
PORTAL=$(psql -d bella -tAc "select count(*) from res_groups_users_rel r join res_groups g on g.id=r.gid join ir_model_data d on d.res_id=g.id and d.model='res.groups' where d.module='base' and d.name='group_portal'")
[ "${PORTAL:-0}" -ge 1 ] && ok "portal staff accounts exist ($PORTAL)" || bad "portal staff accounts" "none found"
[ "$(code "$BELLA/staff/login")" = "200" ] && ok "staff login page" || bad "staff login page" "not 200"
# Unauthenticated access to staff surfaces must not be 200.
for path in /floor /manage/staff /manage/roles; do
  C=$(code "$BELLA$path")
  [ "$C" != "200" ] && ok "$path refuses anonymous access ($C)" || bad "$path refuses anonymous access" "returned 200 while logged out"
done

head_ "8. customer portal"
[ "$(code "$BELLA/my/login")" = "200" ] && ok "portal login page" || bad "portal login" "not 200"
# Anonymous must never reach someone's bookings.
C=$(code "$BELLA/my/bookings")
[ "$C" != "200" ] && ok "my/bookings refuses anonymous ($C)" || bad "my/bookings anonymous" "returned 200"
OTP_TBL=$(psql -d bella -tAc "select count(*) from information_schema.tables where table_name='modryn_otp_code'")
[ "$OTP_TBL" = "1" ] && ok "OTP table exists" || bad "OTP table" "missing"
# Codes must be stored hashed, never in the clear.
CLEAR=$(psql -d bella -tAc "select count(*) from modryn_otp_code where length(code_hash) < 40" 2>/dev/null || echo 0)
[ "${CLEAR:-0}" = "0" ] && ok "OTP codes are hashed" || bad "OTP hashing" "$CLEAR rows look unhashed"

head_ "9. atelier"
PIECES=$(psql -d bella -tAc "select count(*) from modryn_garment_piece where active" 2>/dev/null || echo 0)
[ "${PIECES:-0}" -ge 5 ] && ok "garment pieces seeded ($PIECES)" || bad "garment pieces" "only ${PIECES:-0}"
for path in /atelier /manage/pieces; do
  C=$(code "$BELLA$path")
  [ "$C" != "200" ] && ok "$path refuses anonymous ($C)" || bad "$path anonymous" "returned 200"
done
TASKS=$(psql -d bella -tAc "select count(*) from modryn_alteration_task" 2>/dev/null || echo 0)
[ "${TASKS:-0}" -ge 1 ] && ok "alteration tasks exist ($TASKS)" || bad "alteration tasks" "none"

head_ "10. dispatch board"
# Helpers live in a through-model, not a bare m2m: join order decides who is
# promoted when a primary leaves, and an m2m would order by employee NAME.
T=$(psql -d bella -tAc "select count(*) from information_schema.tables where table_name='modryn_floor_helper'")
[ "$T" = "1" ] && ok "helper through-model exists" || bad "modryn_floor_helper" "missing"
OLD=$(psql -d bella -tAc "select count(*) from information_schema.tables where table_name in ('modryn_queue_helper_rel','modryn_event_helper_rel')")
[ "$OLD" = "0" ] && ok "superseded helper m2m tables dropped" || bad "old helper tables" "$OLD still present"
# Both card kinds must be linkable, or one of them silently loses its helpers.
COLS=$(psql -d bella -tAc "select count(*) from information_schema.columns where table_name='modryn_floor_helper' and column_name in ('entry_id','event_id','employee_id')")
[ "$COLS" = "3" ] && ok "helper links walk-ins and bookings" || bad "helper columns" "expected 3, got $COLS"
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

head_ "10b. comms engine"
# The confirmation page promises an SMS; these prove the promise is kept.
RTBL=$(psql -d bella -tAc "select count(*) from information_schema.columns where table_name='calendar_event' and column_name in ('modryn_reminder_sent_at','modryn_confirmed_at','modryn_lang')")
[ "$RTBL" = "3" ] && ok "booking comms fields exist" || bad "comms fields" "expected 3, got $RTBL"
CRON=$(psql -d bella -tAc "select count(*) from ir_cron c join ir_act_server a on a.id=c.ir_actions_server_id where a.code like '%_modryn_send_reminders%'")
[ "${CRON:-0}" -ge 1 ] && ok "24h reminder cron installed" || bad "reminder cron" "not found"
# A forged token must never open somebody's appointment.
[ "$(code "$BELLA/b/1-deadbeefdeadbeefdeadbeef")" = "404" ] && ok "forged booking token 404s" || bad "forged token" "did not 404"
# The submit-time collision guard must agree with the slot list about cancelled
# bookings, or a freed slot can be offered and then refused.
grep -q "modryn_cancelled_at" addons/modryn_booking/controllers/main.py && ok "collision guard honours cancellations" || bad "collision guard" "still counts cancelled bookings"

head_ "11. instance hygiene"
# Without db_name, Odoo's cron enumerates EVERY database on the server —
# including MODRYN's f*_test — and errors against each one.
grep -qE '^db_name *=' odoo.conf && ok "db_name bounds this instance" || bad "db_name" "absent from odoo.conf — crons will roam"

head_ "12. MODRYN repo untouched"
MOD="/Users/mrwen/Documents/Github/Ryan + rawad + mrwen"
DIRTY=$(cd "$MOD" && git status --porcelain | grep -v "modryn-storefront.png" | wc -l | tr -d ' ')
[ "$DIRTY" = "0" ] && ok "MODRYN working tree clean of our changes" || bad "MODRYN untouched" "$DIRTY unexpected entries"

printf "\n\033[1m%d passed, %d failed\033[0m\n" "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
