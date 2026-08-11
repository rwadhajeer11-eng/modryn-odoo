#!/usr/bin/env bash
# Sample the host, PostgreSQL and every tenant's scheduler into CSV for the
# length of one run. Start it BEFORE k6 and stop it (Ctrl-C or SIGTERM) AFTER,
# so the ramp-out tail is captured — that is where a leak shows.
#
#   ./loadtest/observe/observe.sh loadtest/results/2026-08-11T09-14_ramp-350
#   INTERVAL=2 CRON_EVERY=5 ./loadtest/observe/observe.sh <outdir>
#
# Writes, appending, never buffering: a run that is killed still leaves the
# samples up to the moment it died, which is the only interesting part of a run
# that had to be killed.
set -euo pipefail

OUTDIR="${1:?usage: observe.sh <outdir> [slug...]}"
shift || true

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TENANTS_JSON="$REPO/loadtest/config/tenants.json"

INTERVAL="${INTERVAL:-5}"        # seconds between host/pg samples
CRON_EVERY="${CRON_EVERY:-4}"    # cron+app sample every Nth tick
SQL_EVERY="${SQL_EVERY:-60}"     # pg_stat_statements top-N every Nth tick
TOP_N="${TOP_N:-25}"

if [ $# -gt 0 ]; then
  SLUGS=("$@")
elif [ -f "$TENANTS_JSON" ]; then
  read -r -a SLUGS <<<"$(python3 -c "
import json,sys
print(' '.join(t['slug'] for t in json.load(open(sys.argv[1]))['tenants']))" "$TENANTS_JSON")"
else
  echo "!! no tenants given and no $TENANTS_JSON"
  exit 1
fi

mkdir -p "$OUTDIR"
PG_CSV="$OUTDIR/pg.csv"
PROC_CSV="$OUTDIR/proc.csv"
CRON_CSV="$OUTDIR/cron.csv"
SQL_CSV="$OUTDIR/topsql.csv"

# The per-tenant queries are themselves load. Sampling 30 databases every second
# would be a measurable fraction of the run, so they run at 1/CRON_EVERY of the
# host cadence — 20 s by default, which is fine for a metric whose own floor is a
# 60 s scheduler pass.
echo "observe: ${#SLUGS[@]} tenant(s), host every ${INTERVAL}s, cron every $((INTERVAL * CRON_EVERY))s"
echo "         -> $OUTDIR"

# --------------------------------------------------------------- page size
# vm_stat counts PAGES, and the page is 16 KiB on Apple Silicon and 4 KiB on
# Intel. Hardcoding either turns every memory number into a factor-of-four lie.
PAGE=$(vm_stat | sed -n '1s/.*page size of \([0-9]*\) bytes.*/\1/p')
PAGE=${PAGE:-4096}

HAVE_PGSS=$(psql -d postgres -tAc \
  "select count(*) from pg_extension where extname='pg_stat_statements'" 2>/dev/null || echo 0)

[ -s "$PG_CSV" ] || echo "ts,backends,active,idle,idle_in_txn,waiting,d_commit,d_rollback,d_tup_returned,d_tup_fetched,d_blks_hit,d_blks_read,d_deadlocks,d_temp_bytes" > "$PG_CSV"
[ -s "$PROC_CSV" ] || echo "ts,load1,load5,mem_free_mb,mem_active_mb,odoo_procs,odoo_rss_mb,odoo_cpu_pct,odoo_threads" > "$PROC_CSV"
[ -s "$CRON_CSV" ] || echo "ts,slug,overdue,max_lag_s,drain_lag_s,triggers,failed,outbox_pending,queue_open,bookings_live" > "$CRON_CSV"

# ------------------------------------------------------ pg_stat_statements
# The extension is NOT installed on this host, and a header-only topsql.csv is
# indistinguishable from "the run produced no slow queries". This campaign exists
# largely to make query-level claims about the board rebuild and the slot scan;
# without pg_stat_statements it has no query-level evidence at all, so say so in
# the terminal AND leave the reason inside the results directory, where whoever
# reads the CSVs a week later is actually looking.
if [ "$HAVE_PGSS" = "1" ]; then
  [ -s "$SQL_CSV" ] || echo "ts,calls,total_ms,mean_ms,rows,query" > "$SQL_CSV"
else
  MISSING_NOTE="$OUTDIR/topsql.MISSING.txt"
  cat > "$MISSING_NOTE" <<'NOTE'
pg_stat_statements is NOT installed on this PostgreSQL server, so no topsql.csv
was written. This run carries NO query-level evidence: any claim it makes about
which statements dominate (board rebuild, slot scan, variant search) rests on
wall-clock timings only.

To enable it — it needs a preload and therefore a restart, it cannot be turned
on for one session:

  1. add to postgresql.conf:   shared_preload_libraries = 'pg_stat_statements'
     (deploy/postgresql/tuning.conf already sets this for production; this is
      about the local box, whose conf is `psql -d postgres -tAc "show config_file"`)
  2. restart postgres:         brew services restart postgresql@16
  3. create it once:           psql -d postgres -c "CREATE EXTENSION pg_stat_statements"
  4. reset before each run:    psql -d postgres -c "SELECT pg_stat_statements_reset()"

Step 4 matters: the view is cumulative since the last reset, so without it a
run's top statements are diluted by every seed, verify and prior stage.
NOTE
  echo
  echo "!! pg_stat_statements is NOT installed — NO topsql.csv, no query-level"
  echo "   evidence for this run. See $MISSING_NOTE"
  echo
fi

# The IN list for the pg_stat_database aggregate. Only the tenants under test:
# folding in every database on the server would mix another project's traffic
# into the deltas this run is judged on.
DBLIST=$(printf "'%s'," "${SLUGS[@]}"); DBLIST="${DBLIST%,}"

RUNNING=1
trap 'RUNNING=0' INT TERM

prev=""
prev_cpusec=""
tick=0

while [ "$RUNNING" = 1 ]; do
  TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  # ------------------------------------------------------------------ pg
  # Backend states and the cumulative counters in one round trip; a second
  # connection per sample would show up in the very number being sampled.
  row=$(psql -d postgres -tAF, <<SQL || true
select
  (select count(*) from pg_stat_activity where datname in ($DBLIST)),
  (select count(*) from pg_stat_activity where datname in ($DBLIST) and state='active'),
  (select count(*) from pg_stat_activity where datname in ($DBLIST) and state='idle'),
  (select count(*) from pg_stat_activity where datname in ($DBLIST) and state='idle in transaction'),
  (select count(*) from pg_stat_activity where datname in ($DBLIST) and wait_event_type is not null and state='active'),
  coalesce(sum(xact_commit),0), coalesce(sum(xact_rollback),0),
  coalesce(sum(tup_returned),0), coalesce(sum(tup_fetched),0),
  coalesce(sum(blks_hit),0), coalesce(sum(blks_read),0),
  coalesce(sum(deadlocks),0), coalesce(sum(temp_bytes),0)
from pg_stat_database where datname in ($DBLIST);
SQL
)
  if [ -n "$row" ]; then
    if [ -n "$prev" ]; then
      # Deltas for the counter columns, gauges passed through. Emitted as deltas
      # rather than raw totals because a stats reset mid-run would otherwise
      # produce one enormous negative step that silently poisons every average.
      echo "$TS,$row" | awk -F, -v p="$prev" 'BEGIN{split(p,q,",")}
        {printf "%s,%s,%s,%s,%s,%s", $1,$2,$3,$4,$5,$6
         for(i=7;i<=14;i++){d=$i-q[i-1]; printf ",%d", (d<0?0:d)}
         printf "\n"}' >> "$PG_CSV"
    fi
    prev="$row"
  fi

  # ---------------------------------------------------------------- host
  read -r L1 L5 <<<"$(sysctl -n vm.loadavg | tr -d '{}' | awk '{print $1, $2}')"
  read -r FREE ACTIVE <<<"$(vm_stat | awk -v p="$PAGE" '
    /Pages free/      {gsub(/\./,"");f=$3}
    /Pages active/    {gsub(/\./,"");a=$3}
    END{printf "%d %d", f*p/1048576, a*p/1048576}')"

  # pgrep -f, and the pattern excludes this script's own children: an `odoo-bin
  # shell` left open in another terminal is not a worker and must not be summed
  # into the server's RSS.
  PIDS=$(pgrep -f 'odoo-bin server' | tr '\n' ' ' || true)
  if [ -n "${PIDS// /}" ]; then
    # CPU seconds consumed so far, summed over the server's processes. NOT
    # `ps -o %cpu`: on macOS that column is the average since the process
    # started, so a server that has been up an hour reads ~0 % through an entire
    # ramp and the run finds no CPU ceiling because it never looked at one.
    # Differencing cumulative CPU time against wall time is the real utilisation.
    # shellcheck disable=SC2086
    read -r NPROC RSS CPUSEC <<<"$(ps -o rss=,time= -p ${PIDS} | awk '
      {n++; r+=$1
       split($2,t,":")
       s = (length(t)==3) ? t[1]*3600+t[2]*60+t[3] : t[1]*60+t[2]
       c+=s}
      END{printf "%d %d %.2f", n, r/1024, c}')"
    if [ -n "$prev_cpusec" ]; then
      CPU=$(awk -v a="$CPUSEC" -v b="$prev_cpusec" -v i="$INTERVAL" \
        'BEGIN{d=(a-b)/i*100; printf "%.1f", (d<0?0:d)}')
    else
      CPU=0
    fi
    prev_cpusec="$CPUSEC"
    # shellcheck disable=SC2086
    THREADS=$(ps -M -p ${PIDS} 2>/dev/null | grep -vc USER || echo 0)
  else
    NPROC=0; RSS=0; CPU=0; THREADS=0; prev_cpusec=""
  fi
  echo "$TS,$L1,$L5,$FREE,$ACTIVE,$NPROC,$RSS,$CPU,$THREADS" >> "$PROC_CSV"

  # ---------------------------------------------------------- cron + app
  if [ $((tick % CRON_EVERY)) = 0 ]; then
    for slug in "${SLUGS[@]}"; do
      lag=$(psql -d "$slug" -tAF, -f "$REPO/loadtest/observe/cron_lag.sql" 2>/dev/null | tr -d ' ' || true)
      [ -n "$lag" ] || continue
      app=$(psql -d "$slug" -tAF, -c "
        select (select count(*) from modryn_queue_entry
                 where state in ('pending','waiting','called')),
               (select count(*) from calendar_event
                 where modryn_is_booking is true and modryn_cancelled_at is null
                   and active is true)" 2>/dev/null | tr -d ' ' || echo "0,0")
      echo "$TS,$slug,$lag,$app" >> "$CRON_CSV"
    done
  fi

  # ------------------------------------------------------------- top sql
  if [ "$HAVE_PGSS" = "1" ] && [ $((tick % SQL_EVERY)) = 0 ]; then
    # One regexp_replace, not replace(): stripping commas alone left the NEWLINES
    # in. Every statement Odoo's ORM emits is multi-line, so a 200-char prefix
    # carried several of them and each row exploded into three or four malformed
    # rows — the query column of row 1 truncated mid-token, rows 2..n with no
    # timestamp and no counters at all. The character class folds commas, CR, LF,
    # tabs and runs of spaces into single spaces, which is also what makes the
    # prefixes comparable between samples.
    psql -d postgres -tAF, -c "
      select round(total_exec_time)::bigint, calls, round(mean_exec_time::numeric,2),
             rows, regexp_replace(left(query, 200), '[,[:space:]]+', ' ', 'g')
      from pg_stat_statements
      order by total_exec_time desc limit $TOP_N" 2>/dev/null \
      | awk -F, -v ts="$TS" '{printf "%s,%s,%s,%s,%s,%s\n", ts,$2,$1,$3,$4,$5}' >> "$SQL_CSV" || true
  fi

  tick=$((tick + 1))
  # Backgrounded sleep + wait, not a bare sleep: bash runs a trap only between
  # commands, so a plain `sleep 5` swallows Ctrl-C for up to five seconds and the
  # operator presses it again — which kills the shell before the summary below.
  sleep "$INTERVAL" &
  wait $! 2>/dev/null || true
done

echo
echo "observe: stopped after $tick sample(s). Files:"
wc -l "$PG_CSV" "$PROC_CSV" "$CRON_CSV"
# Named separately, not folded into the wc above: without pg_stat_statements the
# file does not exist, and `wc` on a missing path exits 1 under `set -e` — which
# would end every run on this host with a failure the operator has to explain.
if [ -f "$SQL_CSV" ]; then
  wc -l "$SQL_CSV"
else
  echo "  no topsql.csv — pg_stat_statements absent, see $OUTDIR/topsql.MISSING.txt"
fi
