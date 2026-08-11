-- How far behind is this database's scheduler?
--
-- Run per tenant:  psql -d lt07 -tAF, -f loadtest/observe/cron_lag.sql
--
-- This is a first-class metric, not a curiosity. Odoo's cron thread makes about
-- one pass per database per 60 s and walks databases SERIALLY, so with
-- max_cron_threads = 2 the whole fleet shares two workers. At 30 tenants the
-- floor on any one tenant's scheduling interval is roughly 15 x 60 s / 2 — and
-- the SMS outbox drain, the 24 h reminder and the waitlist expiry all live on
-- that clock. The queue looks slow long before anything looks broken.
--
-- `now() at time zone 'UTC'`, NOT now(): ir_cron.nextcall is `timestamp without
-- time zone` holding UTC, so on this host (Asia/Jerusalem, +03) a bare now()
-- both fails to type-check and, coerced, would report every cron as three hours
-- overdue. Every lag number in the run would be fiction in the same direction.
--
-- Columns (one row):
--   overdue        active crons whose nextcall is already past
--   max_lag_s      the worst of those, in seconds — the number that matters
--   drain_lag_s    seconds the MODRYN outbox drain is overdue; 0 when it is not
--                  yet due, -1 when the cron row is missing entirely
--   triggers       rows in ir_cron_trigger not yet consumed. _trigger() writes
--                  one per enqueued text, so a rising count is a backlog that
--                  the nextcall interval alone does not show
--   failed         crons carrying a failure_count. Five consecutive failures
--                  deactivate a cron outright, which ends all SMS silently
--   outbox_pending queued texts not yet sent
select
  count(*) filter (where c.active and c.nextcall < (now() at time zone 'UTC'))  as overdue,
  coalesce(max(extract(epoch from (now() at time zone 'UTC') - c.nextcall))
           filter (where c.active and c.nextcall < (now() at time zone 'UTC')), 0)::int
                                                                               as max_lag_s,
  coalesce(greatest(0, max(extract(epoch from (now() at time zone 'UTC') - c.nextcall))
           filter (where c.active and c.cron_name = 'MODRYN: send queued texts'))::int, -1)
                                                                               as drain_lag_s,
  (select count(*) from ir_cron_trigger)                                       as triggers,
  count(*) filter (where c.failure_count > 0)                                  as failed,
  (select count(*) from modryn_sms_outbox where state = 'pending')             as outbox_pending
from ir_cron c;
