#!/usr/bin/env bash
# The nginx layer. Everything scripts/verify.sh structurally cannot see.
#
#   sudo /opt/modryn/deploy/scripts/verify_edge.sh                     # on the box
#   DOMAIN=example.com TENANT=bella ./verify_edge.sh --remote-only     # from anywhere
#
# verify.sh proves Odoo is correct for hostnames that EXIST. This file proves
# nginx is correct for the ones that must NOT: the catch-all, the database
# manager under a language prefix, the rate limits standing between an attacker
# and the Twilio bill, and the certificate. Neither is a superset of the other
# and neither is optional — deploy/README.md §11 gates 2, 3, 3b, 3c and 4 all
# live here, and gate 10 depends on E4 below for its control.
#
# ---------------------------------------------------------------------------
# SAFE AGAINST A LIVE BOX, with exactly two exceptions, both bounded:
#
#   E4  spends THIS CLIENT's modryn_post rate-limit bucket for ~1 minute,
#       shared across /book/submit, /waitlist/join, /claim/* and /my/cancel/*.
#       Customers are unaffected — the key is $binary_remote_addr.
#       ZERO rows written and ZERO SMS: no csrf_token is sent, so Odoo rejects
#       at 400 before any handler runs. If anyone ever "improves" this by
#       scraping a token, every accepted POST becomes a real message.
#
#   E11 may generate ONE failed login: 1 of maxretry=6, toward a one-hour ban
#       of this IP on ports 80/443 (SSH is unaffected — the jail is
#       port = http,https). Never loop it. It is self-limiting: the line it
#       writes makes the next run take the other branch.
#
# Everything else is a GET or a socket probe.
# ---------------------------------------------------------------------------
set -uo pipefail

PASS=0; FAIL=0; SKIP=0
ok()   { printf "  \033[32mPASS\033[0m %s\n" "$1"; PASS=$((PASS+1)); }
bad()  { printf "  \033[31mFAIL\033[0m %s — %s\n" "$1" "$2"; FAIL=$((FAIL+1)); }
# A check that cannot run HERE. Counted separately so a green --remote-only run
# can never be mistaken for a full pass.
skip() { printf "  \033[33mSKIP\033[0m %s — %s\n" "$1" "$2"; SKIP=$((SKIP+1)); }
# Ran, found something, cannot judge it. Moves neither column.
note() { printf "  \033[36mNOTE\033[0m %s — %s\n" "$1" "$2"; }
head_(){ printf "\n\033[1m%s\033[0m\n" "$1"; }

REMOTE_ONLY=0
[ "${1:-}" = "--remote-only" ] && { REMOTE_ONLY=1; shift; }

ENV_FILE="${ENV_FILE:-/etc/modryn/deploy.env}"
# shellcheck disable=SC1090
[ -r "$ENV_FILE" ] && { set -a; . "$ENV_FILE"; set +a; }
DOMAIN="${1:-${DOMAIN:-}}"
[ -n "$DOMAIN" ] || { echo "!! DOMAIN unset and $ENV_FILE unreadable — pass it: verify_edge.sh example.com" >&2; exit 1; }

NGINX_LOG=/var/log/nginx/modryn.log
ON_BOX=0
[ "$REMOTE_ONLY" = 0 ] && [ "$(id -u)" = 0 ] && [ -r "$NGINX_LOG" ] && ON_BOX=1

# -k ON PURPOSE, HERE AND NOWHERE ELSE. E9 inspects the certificate
# deliberately, so the other eleven checks must keep working on a box whose
# certificate is bad — otherwise a single expired cert turns twelve independent
# results into one useless error and you learn nothing about the twelve.
# scripts/verify.sh passes no -k for the opposite reason: there, a bad cert
# MUST look like a failure.
c()   { curl -sk -o /dev/null -w '%{http_code}' --max-time 15 "$@"; }
cpost(){ curl -sk -o /dev/null -w '%{http_code}' --max-time 15 -X POST "$@"; }
hdr() { curl -skI --max-time 15 "$@"; }

head_ "E0. the subject"
# THE TENANT, derived and never assumed. An unknown host 404s at the catch-all,
# so a hardcoded 'bella' on a box that does not have one makes E2, E4, E7 and E8
# assert against the CATCH-ALL and pass for entirely the wrong reason.
if [ "$ON_BOX" = 1 ]; then
  TENANT="${TENANT:-$(awk -F= '/^[[:space:]]*db_name[[:space:]]*=/{gsub(/[[:space:]]/,"",$2); split($2,a,","); print a[1]; exit}' /etc/odoo/odoo.conf)}"
  [ -n "$TENANT" ] || { echo "!! db_name is empty in /etc/odoo/odoo.conf — no boutique exists yet" >&2; exit 1; }
else
  TENANT="${TENANT:?set TENANT=<slug> in --remote-only mode: /etc/odoo/odoo.conf is not readable from here, and guessing a hostname would make every tenant check below assert against the catch-all}"
fi
T="https://$TENANT.$DOMAIN"
UNKNOWN="nope-$$"
U="https://$UNKNOWN.$DOMAIN"

if [ "$(c "$T/shop")" = 200 ]; then
  ok "tenant host $TENANT.$DOMAIN serves /shop"
else
  bad "tenant host" "$T/shop did not answer 200 — every check below would be measuring the catch-all instead of a boutique"
  printf "\n\033[1m%d passed, %d failed, %d skipped\033[0m\n" "$PASS" "$FAIL" "$SKIP"
  exit 1
fi

head_ "E1. an unknown subdomain dies at nginx"
# The runbook's original version of this greps the ODOO journal for the hostname
# and expects nothing. IT CANNOT FAIL: odoo.conf.prod sets log_level = warn and
# the only handler override is res_users:INFO, so production Odoo logs no
# request lines at all. The failure this check exists to catch — the
# 303 -> /odoo -> /web/database/selector chain — raises nothing and would print
# nothing either. Absence of evidence from a logger that is switched off is not
# evidence of absence.
#
# The nginx access log is where the answer actually is: modryn_load carries
# ua_status=$upstream_status, which is "-" when no upstream was contacted and a
# number when one was. That is a POSITIVE fact about the request.
PROBE="/__edge_probe_$$"
CODE=$(c "$U$PROBE")
[ "$CODE" = 404 ] && ok "$U$PROBE -> 404" \
  || bad "unknown subdomain" "answered $CODE, not 404 — the catch-all is not default_server, or a proxy_pass got into it"

# C9 says 404 with no 3xx. A 301->404 would satisfy a naive final-status check
# while still telling an attacker the host was interesting enough to redirect.
RED=$(curl -sk -o /dev/null -w '%{num_redirects}' --max-time 15 -L --max-redirs 5 "$U/" 2>/dev/null)
[ "${RED:-0}" = 0 ] && ok "and no redirect chain" \
  || bad "unknown subdomain redirects" "$RED hop(s) before the final status"

if [ "$ON_BOX" = 1 ]; then
  sleep 1   # nginx buffers the access log
  LINE=$(grep -F "uri=$PROBE" "$NGINX_LOG" | tail -1)
  if [ -z "$LINE" ]; then
    bad "E1 evidence" "no line for $PROBE in $NGINX_LOG — the access log is not recording, so nothing below it can be believed either"
  elif printf '%s' "$LINE" | grep -q 'ua_status=-'; then
    ok "nginx recorded ua_status=- : no upstream was contacted"
  else
    bad "unknown subdomain reached Odoo" "$LINE"
  fi
  # THE CONTROL. ua_status=- is also what a broken, renamed or removed log field
  # produces. Prove the same field is populated for a request that DID reach
  # Odoo, in the same log, seconds apart.
  CPROBE="/__edge_control_$$"
  curl -sk -o /dev/null --max-time 15 "$T$CPROBE"; sleep 1
  grep -F "uri=$CPROBE" "$NGINX_LOG" | grep -qE 'ua_status=[0-9]' \
    && ok "control: a proxied request in the same log records a numeric ua_status" \
    || bad "E1 control" "a request that certainly reached Odoo also logged ua_status=- — the field proves nothing and the check above is decoration"
else
  skip "E1 upstream evidence" "needs $NGINX_LOG — run on the box. The 404 above proves the STATUS, not that Python was never reached"
fi

head_ "E2. the database manager, on every host and under every prefix"
# `location ^~ /web/database/` covers the bare form and short-circuits regex
# evaluation; a second REGEX location covers /<lang>/web/database/. Both,
# deliberately, so deleting either still leaves the prefix closed.
# /en/web/database/manager was served BYTE-IDENTICALLY to the bare form until
# 2026-08-11 (verified with curl + cmp: two 46,329-byte responses), which is why
# a spot check of the unprefixed path proves nothing.
for h in "$T" "$U"; do
  for p in /web/database/manager /web/database/selector \
           /en/web/database/manager /he/web/database/manager /ar/web/database/selector; do
    C=$(c "$h$p")
    [ "$C" = 404 ] && ok "$h$p -> 404" || bad "$h$p" "answered $C, not 404"
  done
done

head_ "E3. the prefixed routes EXIST (so E4 is not measuring a 404)"
# A 429 on a path nginx would 404 anyway proves nothing about the rate limit,
# and a language-prefixed path is exactly the kind that quietly stops existing.
# A registered route rejects a CSRF-less POST with 400; an absent one 404s.
#
# THIS RUNS BEFORE E4, NOT AFTER. modryn_post is rate=10r/m keyed on client IP;
# once E4 has spent the bucket this control would itself answer 429 and there
# would be no way left to establish the route was ever there.
for p in /waitlist/join /en/waitlist/join /book/submit /en/book/submit; do
  C=$(cpost "$T$p")
  [ "$C" = 400 ] && ok "POST $p -> 400 (route registered, CSRF refused)" \
    || bad "positive control $p" "answered $C; a 404 means the route is gone and E4 below would be meaningless"
done

head_ "E4. rate limits fire, bare AND prefixed"
# Every MODRYN route is website=True, so Odoo answers at /path AND /<lang>/path
# and RENDERS the prefixed form into its own <form action>. The old
# `location = /waitlist/join` matched the bare form only — and /waitlist/join
# and /book/submit each send an SMS. The application's only throttle is
# per-phone-number, which does not bound an attacker rotating numbers, so
# limit_req was the ENTIRE defence for the Twilio bill and the prefixed form
# bypassed all of it. (deploy/README.md "Where the spec was wrong" #11.)
for p in /waitlist/join /en/waitlist/join; do
  CODES=""
  for _ in $(seq 1 12); do CODES="$CODES $(cpost "$T$p")"; done
  case "$CODES" in
    *429*) ok "POST $p burst produced 429:$CODES" ;;
    *)     bad "no rate limit on $p" "twelve POSTs and not one 429:$CODES" ;;
  esac
done

head_ "E5. Odoo is not reachable except through nginx"
if [ "$ON_BOX" = 0 ]; then
  IP=$(getent hosts "$TENANT.$DOMAIN" 2>/dev/null | awk '{print $1; exit}')
  [ -n "$IP" ] || IP=$(dig +short "$TENANT.$DOMAIN" 2>/dev/null | tail -1)
  if [ -z "$IP" ]; then
    skip "E5" "could not resolve $TENANT.$DOMAIN to an address"
  elif curl -s -o /dev/null --max-time 3 "http://$IP:8069/" 2>/dev/null; then
    bad "port 8069 is open to the internet" "http://$IP:8069/ answered — http_interface is not 127.0.0.1, or ufw is wrong"
  else
    ok "port 8069 refused from outside"
  fi
else
  # NOT the same claim. This proves nothing is BOUND to a public address; it
  # does not prove a firewall, because there is nothing between a process and
  # itself.
  if ss -ltn 'sport = :8069' 2>/dev/null | grep -qE '127\.0\.0\.1:8069|\[::1\]:8069'; then
    note "8069 binds loopback only" "the REAL test is from outside — run --remote-only from the operator's laptop before the launch"
  else
    bad "8069 binding" "$(ss -ltn 'sport = :8069' 2>/dev/null | tail -n +2)"
  fi
fi

head_ "E6. no connection to modryn_template"
if [ "$ON_BOX" = 1 ]; then
  N=$(sudo -u postgres psql -d postgres -tAc "select count(*) from pg_stat_activity where datname='modryn_template'" 2>/dev/null)
  [ "${N:-x}" = 0 ] && ok "0 connections to modryn_template — createdb -T works without downtime" \
    || bad "modryn_template held open" "${N:-<query failed>} connection(s): someone put the template back into db_name, and adding a boutique now needs a restart"
else
  skip "E6" "needs psql on the box"
fi

head_ "E7. compression"
# Odoo does not compress; ~1 MB of CSS depends on nginx doing it. Checking the
# HTML alone is not enough — nginx gzips text/html almost by default, so the
# bundle is the one that actually matters.
hdr -H 'Accept-Encoding: gzip' "$T/shop" | grep -qi 'content-encoding: *gzip' \
  && ok "gzip on /shop" || bad "gzip on /shop" "no content-encoding: gzip"
CSS=$(curl -sk --max-time 20 "$T/shop" | grep -oE '/web/assets/[^"]*\.css' | head -1)
if [ -n "$CSS" ]; then
  hdr -H 'Accept-Encoding: gzip' "$T$CSS" | grep -qi 'content-encoding: *gzip' \
    && ok "gzip on the CSS bundle" || bad "gzip on the CSS bundle" "$CSS served uncompressed — this is the ~1 MB one"
else
  bad "E7 control" "no /web/assets/*.css in /shop — the page did not render a bundle, so the gzip result above describes nothing"
fi

head_ "E8. the filestore is served by nginx, not a Python worker"
C=$(c "$T/web/filestore/$TENANT/aa/aaaa")
[ "$C" = 404 ] && ok "/web/filestore is internal (404 from outside)" \
  || bad "/web/filestore" "answered $C — the location lost its 'internal' directive and the filestore is world-readable by path"

head_ "E9. TLS"
CERT=$(echo | openssl s_client -connect "$TENANT.$DOMAIN:443" -servername "$TENANT.$DOMAIN" 2>/dev/null \
       | openssl x509 -noout -dates -checkend $((30*86400)) 2>/dev/null)
printf '%s' "$CERT" | grep -q 'Certificate will not expire' \
  && ok "certificate valid for at least 30 more days" \
  || bad "certificate expiry" "$(printf '%s' "$CERT" | grep notAfter) — certbot's renew timer is not doing its job, or provision.sh still points snippets/modryn-tls.conf at the SELF-SIGNED placeholder"
# The placeholder is a self-signed CN=$DOMAIN and would satisfy a naive
# "does 443 answer" check forever. Name it explicitly.
echo | openssl s_client -connect "$TENANT.$DOMAIN:443" -servername "$TENANT.$DOMAIN" \
     -verify_return_error >/dev/null 2>&1 \
  && ok "chain verifies against the system trust store" \
  || bad "certificate chain" "does not verify — the self-signed placeholder is still installed"

head_ "E10. security headers"
H=$(hdr "$T/shop")
printf '%s' "$H" | grep -qi 'strict-transport-security: *max-age=31536000; *includeSubDomains' \
  && ok "HSTS, one year, includeSubDomains" \
  || bad "HSTS" "absent or weakened. This one is a COMMITMENT: every browser that has already seen it refuses plain HTTP to every subdomain for a year regardless, so removing it does not undo it — it only leaves new visitors unprotected"
printf '%s' "$H" | grep -qi 'x-content-type-options: *nosniff' \
  && ok "nosniff" \
  || bad "nosniff" "absent — add_header does not accumulate across levels, so check whether a location added one header and silently dropped the inherited set"

head_ "E11. the fail2ban filter matches a REAL line"
if [ "$ON_BOX" = 1 ]; then
  CLIENT="${CLIENT_IP:-${SSH_CLIENT%% *}}"
  [ -n "$CLIENT" ] || CLIENT=$(who am i 2>/dev/null | sed -n 's/.*(\(.*\))/\1/p')
  LINE=$(journalctl -t modryn --no-pager -o cat 2>/dev/null | grep -F 'Login failed for login:' | tail -1)
  if [ -z "$LINE" ]; then
    # EXACTLY ONE attempt. See the safety header at the top of this file.
    note "seeding one failed login" "1 of maxretry=6 — do NOT loop this"
    curl -sk -o /dev/null --max-time 15 -d 'login=verify-edge-probe&password=wrong' "$T/staff/login"
    sleep 2
    LINE=$(journalctl -t modryn --no-pager -o cat 2>/dev/null | grep -F 'Login failed for login:' | tail -1)
  fi
  if [ -z "$LINE" ]; then
    bad "no login-failure line in the journal" "log_handler = odoo.addons.base.models.res_users:INFO is missing from odoo.conf — log_level = warn suppresses that line on its own, and the jail then bans nobody, silently, forever"
  else
    OUT=$(fail2ban-regex "$LINE" /etc/fail2ban/filter.d/modryn-odoo.conf 2>&1)
    printf '%s' "$OUT" | grep -qE '1 matched' \
      && ok "fail2ban-regex: 1 matched" \
      || bad "the fail2ban filter is decorative" "$(printf '%s' "$OUT" | grep -E 'matched|missed' | tr '\n' ' ')"
    # The second half of the same fact, and the one the runbook left as prose.
    # If the captured host is 127.0.0.1 while the request came from elsewhere,
    # nginx is not forwarding the client address and the jail will ban NGINX on
    # the sixth wrong password — taking the whole platform down.
    FOUND=$(printf '%s' "$OUT" | sed -n '/Addresses found/,$p' | grep -oE '[0-9]{1,3}(\.[0-9]{1,3}){3}' | tail -1)
    if [ -n "$CLIENT" ] && [ "$FOUND" = "$CLIENT" ]; then
      ok "captured host $FOUND is the real client — the forwarded address survives the proxy"
    elif [ "$FOUND" = "127.0.0.1" ]; then
      bad "the jail would ban nginx" "the filter captured 127.0.0.1 while the request came from ${CLIENT:-a remote address}: X-Forwarded-For/Host is missing, ProxyFix is inert, and the sixth wrong password takes the platform offline"
    else
      note "captured host ${FOUND:-none} vs client ${CLIENT:-unknown}" "could not confirm the client address; set CLIENT_IP= and re-run"
    fi
  fi
else
  skip "E11" "needs root, the systemd journal and fail2ban-regex"
fi

head_ "E12. plain HTTP and the ACME path"
R=$(curl -sI --max-time 15 -o /dev/null -w '%{http_code}' "http://$TENANT.$DOMAIN/" 2>/dev/null)
case "$R" in
  301|308) ok "http:// -> $R (redirects to https)" ;;
  *)       bad "http:// redirect" "answered $R, expected 301" ;;
esac
# Served from /var/www/acme, NOT redirected to https — a redirect here breaks
# HTTP-01 renewal for anyone who ever falls back to it.
A=$(c "http://$TENANT.$DOMAIN/.well-known/acme-challenge/__probe_$$")
[ "$A" = 404 ] && ok "ACME challenge path 404s from disk, not a redirect" \
  || bad "ACME challenge path" "answered $A — a 301 here breaks HTTP-01 renewal"

printf "\n\033[1m%d passed, %d failed, %d skipped\033[0m\n" "$PASS" "$FAIL" "$SKIP"
[ "$SKIP" -gt 0 ] && printf "\033[33m%d check(s) did not run here — this is NOT a full pass.\033[0m\n" "$SKIP"
[ "$FAIL" -eq 0 ] || exit 1
