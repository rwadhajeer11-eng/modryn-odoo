#!/usr/bin/env bash
# Remote smoke suite for a deployed SINGLE-TENANT instance (the Railway demo).
# Pure HTTP from anywhere — no psql, no filesystem asserts. The full verify.sh
# cannot run against a PaaS: it makes ~85 peer-auth psql calls and hardcodes
# the bella/noga subdomain contract. This is the deployed-surface subset.
#
# Usage:
#   MODRYN_DEMO_PASSWORD=... ./scripts/smoke_remote.sh https://<host> [staff_login]
#
# staff_login defaults to `tal` (the te demo owner). Section 4 SKIPs when
# MODRYN_DEMO_PASSWORD is unset. Content checks (catalog, images) SKIP on an
# empty tenant rather than fail — the demo tenant ships deliberately empty.
#
# NO -k ANYWHERE (same policy as verify.sh:41): a certificate fault must fail
# section 0 loudly, not hide behind quiet passes.
set -uo pipefail

BASE="${1:?usage: smoke_remote.sh https://host [staff_login]}"
BASE="${BASE%/}"
STAFF_LOGIN="${2:-tal}"
HOST="${BASE#*://}"

PASS=0; FAIL=0; SKIP=0
ok()   { printf "  \033[32mPASS\033[0m %s\n" "$1"; PASS=$((PASS+1)); }
bad()  { printf "  \033[31mFAIL\033[0m %s — %s\n" "$1" "$2"; FAIL=$((FAIL+1)); }
skip() { printf "  \033[33mSKIP\033[0m %s — %s\n" "$1" "$2"; SKIP=$((SKIP+1)); }
head_() { printf "\n\033[1m%s\033[0m\n" "$1"; }

code() { curl -sg -o /dev/null -w "%{http_code}" "$1"; }
# Fetch to a FILE, never through $(...): command substitution mangles Odoo's
# multi-hundred-KB pages (verify.sh learned this the hard way).
PAGE=$(mktemp -t smoke_page)
fetch() { curl -sg "$1" -o "$PAGE"; }

head_ "0. health & routing"
fetch "$BASE/web/health"
grep -q '"pass"' "$PAGE" && ok "/web/health answers pass" || bad "/web/health" "no pass in $(head -c120 "$PAGE")"
fetch "$BASE/web/health?db_server_status=1"
grep -Eq '"db_server_status": ?true' "$PAGE" && ok "Postgres reachable from the container" || bad "db_server_status" "$(head -c120 "$PAGE")"
C=$(code "$BASE/websocket/health")
[ "$C" = "200" ] && ok "/websocket/health on the single port (threaded mode)" || bad "/websocket/health" "got $C"
C=$(code "$BASE/")
[ "$C" = "200" ] && ok "GET / is 200 — monodb serves te on this host" || bad "GET /" "got $C — host->tenant routing broken"
C=$(curl -sg -o /dev/null -w "%{http_code}" "http://$HOST/")
case "$C" in 301|302|307|308) ok "http:// redirects to https ($C)";; *) bad "http->https" "got $C";; esac
# proxy_mode is gated on X-Forwarded-Host; if Railway's edge omits it, Odoo
# thinks the scheme is http and redirect Locations regress to http://.
LOC=$(curl -sgI "$BASE/my/bookings" | tr -d '\r' | grep -i '^location:' | head -1 | cut -d' ' -f2-)
case "$LOC" in
  http://*) bad "redirect scheme" "Location downgraded to $LOC (ProxyFix no-op?)";;
  *)        ok "redirect Location keeps scheme (${LOC:-none})";;
esac

head_ "1. storefront"
C=$(code "$BASE/shop"); [ "$C" = "200" ] && ok "/shop renders" || bad "/shop" "got $C"
fetch "$BASE/shop"
# Product pages are website_sale slugs under /shop/<slug> (Hebrew slugs are
# fine in curl URLs); cart/wishlist/category are chrome, not catalog.
DRESS=$(grep -oE 'href="/shop/[^"]+"' "$PAGE" | grep -vE '/shop/(cart|wishlist|category)' | head -1 | sed 's/^href="//;s/"$//')
if [ -n "$DRESS" ]; then
  ok "catalog links present"
  C=$(code "$BASE$DRESS"); [ "$C" = "200" ] && ok "product page renders" || bad "product page" "$DRESS got $C"
else
  skip "catalog links" "fresh tenant, catalog empty by design"
  skip "product page" "no catalog to click into"
fi
fetch "$BASE/book"
C=$(code "$BASE/book"); [ "$C" = "200" ] && ok "/book renders" || bad "/book" "got $C"
grep -qE 'name="csrf_token"[^>]*value="' "$PAGE" && ok "/book carries a csrf token" || bad "/book csrf" "token missing"
C=$(code "$BASE/queue/checkin"); [ "$C" = "200" ] && ok "/queue/checkin renders" || bad "/queue/checkin" "got $C"
C=$(code "$BASE/my/login"); [ "$C" = "200" ] && ok "/my/login (portal OTP) renders" || bad "/my/login" "got $C"
fetch "$BASE/"
grep -q 'dir="rtl"' "$PAGE" && ok "storefront is RTL (Hebrew default)" || bad "RTL" 'no dir="rtl" on /'
# The demo-web build: a real homepage, /book in the nav, and none of Odoo's
# placeholder chrome. Checked on the SERVED page — a view that exists but
# stopped propagating through the COW tree passes every psql check and still
# greets visitors with the empty div.
grep -q "modryn_home" "$PAGE" && ok "homepage hero is served" \
  || bad "homepage" "modryn_home marker missing — the empty-div homepage is back"
grep -q 'href="/book"' "$PAGE" && ok "/book is in the nav" \
  || bad "nav /book" "no /book link on /"
grep -q "o_brand_promotion" "$PAGE" \
  && bad "brand promotion" "'Powered by Odoo' is served" \
  || ok "no Odoo brand promotion"
grep -qiE "yourcompany\.example\.com|disruptive products|555-555-5556|Copyright (&amp;copy;|©) Company name" "$PAGE" \
  && bad "stock chrome" "placeholder boilerplate is served (footer copy, header phone, or 'Company name' copyright)" \
  || ok "stock placeholder chrome is gone"
C=$(code "$BASE/en"); [ "$C" = "200" ] && ok "/en language toggle target" || bad "/en" "got $C"

head_ "2. assets & filestore"
SZ=$(curl -sg "$BASE/modryn_theme/static/src/fonts/assistant-hebrew-400.woff2" -o /dev/null -w "%{size_download}")
[ "${SZ:-0}" -gt 1024 ] && ok "committed Hebrew font serves ($SZ bytes)" || bad "font woff2" "size $SZ"
fetch "$BASE/"
ASSET=$(grep -oE '(href|src)="[^"]*/web/assets/[^"]*"' "$PAGE" | head -1 | sed 's/^[a-z]*="//;s/"$//')
if [ -n "$ASSET" ]; then
  C=$(code "$BASE$ASSET"); [ "$C" = "200" ] && ok "asset bundle compiles and serves" || bad "asset bundle" "$ASSET got $C"
  case "$ASSET" in *rtl*) ok "bundle is the RTL variant (rtlcss ran in the image)";; *) bad "rtl bundle" "Hebrew page served non-rtl bundle $ASSET";; esac
else
  bad "asset scrape" "no /web/assets/ link found on /"
fi
fetch "$BASE/shop"
IMG=$(grep -oE 'src="[^"]*/web/image/[^"]*"' "$PAGE" | head -1 | sed 's/^src="//;s/"$//')
if [ -n "$IMG" ]; then
  C=$(code "$BASE$IMG"); [ "$C" = "200" ] && ok "filestore image serves (volume seeded)" || bad "filestore image" "$IMG got $C"
else
  skip "filestore image" "no /web/image on empty catalog"
fi

head_ "3. security posture"
# Without nginx these pages cannot 404 (that was nginx's job on the VPS);
# list_db=False guards the OPERATIONS and the listing. Assert no enumeration.
for p in selector manager; do
  fetch "$BASE/web/database/$p"
  if grep -qE 'bella|noga|modryn_template' "$PAGE"; then
    bad "/web/database/$p" "enumerates other databases"
  else
    ok "/web/database/$p leaks no database names"
  fi
done
RESP=$(curl -sg -X POST "$BASE/web/database/create" \
  -F master_pwd=definitely-wrong -F name=hack -F login=a@b.c -F password=x -F lang=en_US -F phone= )
echo "$RESP" | grep -qiE 'access denied|error' && ok "db create refused with a bogus master password" || bad "db create" "no refusal in response"
C=$(code "$BASE/robots.txt"); [ "$C" = "200" ] && ok "/robots.txt" || bad "/robots.txt" "got $C"
C=$(code "$BASE/definitely-not-a-page-xyz"); [ "$C" = "404" ] && ok "unknown path is 404" || bad "404 behavior" "got $C"
C=$(code "$BASE/floor"); [ "$C" != "200" ] && ok "anonymous /floor is gated ($C)" || bad "anonymous /floor" "answered 200"
RPC=$(curl -sg -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","method":"call","params":{}}' "$BASE/floor/data")
echo "$RPC" | grep -q '"result"' && bad "anonymous /floor/data" "returned a result payload" || ok "anonymous JSON-RPC gets no board"

head_ "4. staff surfaces (authenticated)"
STAFF_PW="${MODRYN_DEMO_PASSWORD:-}"
if [ -z "$STAFF_PW" ]; then
  skip "staff section" "MODRYN_DEMO_PASSWORD unset — export the rotated demo password"
else
  JAR=$(mktemp); TOKEN_URL="$BASE/staff/login"
  fetch "$TOKEN_URL"
  C=$(code "$TOKEN_URL"); [ "$C" = "200" ] && ok "/staff/login renders" || bad "/staff/login" "got $C"
  CT=$(curl -sg -c "$JAR" "$TOKEN_URL" | grep -oE 'name="csrf_token"[^>]*value="[^"]*"' | sed 's/.*value="//;s/"//')
  curl -sg -b "$JAR" -c "$JAR" -o /dev/null -X POST "$TOKEN_URL" \
    --data-urlencode "username=$STAFF_LOGIN" --data-urlencode "password=$STAFF_PW" --data-urlencode "csrf_token=$CT"
  SESSION=$(curl -sg -b "$JAR" -o /dev/null -w "%{http_code}" "$BASE/floor")
  if [ "$SESSION" = "200" ]; then
    ok "staff sign-in as $STAFF_LOGIN succeeded"
    for path in /atelier /staff/home /en/floor; do
      C=$(curl -sg -b "$JAR" -o /dev/null -w "%{http_code}" "$BASE$path")
      [ "$C" = "200" ] && ok "$path renders signed-in" || bad "$path" "got $C"
    done
    BOARD=$(curl -sg -b "$JAR" -H "Content-Type: application/json" \
      -d '{"jsonrpc":"2.0","method":"call","params":{}}' "$BASE/floor/data")
    echo "$BOARD" | grep -q '"result"' && ok "/floor/data returns a board" || bad "/floor/data" "no result"
    echo "$BOARD" | grep -q '"pending"' && ok "board carries the arrivals gate" || bad "board pending panel" "key missing"
  else
    bad "staff sign-in" "$STAFF_LOGIN could not sign in (/floor answered $SESSION). Wrong MODRYN_DEMO_PASSWORD for this deployment? Remaining staff checks not run."
  fi
  rm -f "$JAR"
fi

head_ "5. base-url integrity"
fetch "$BASE/"
grep -q "$HOST" "$PAGE" && ok "page carries its own host (web.base.url fixup landed)" || bad "web.base.url" "$HOST absent from /"
C=$(code "$BASE/b/1-000000000000000000000000")
[ "$C" != "500" ] && ok "garbage booking token degrades sanely ($C)" || bad "booking token route" "500 on garbage token"

printf "\n\033[1mRESULT\033[0m PASS=%d FAIL=%d SKIP=%d\n" "$PASS" "$FAIL" "$SKIP"
rm -f "$PAGE"
[ "$FAIL" = "0" ]
