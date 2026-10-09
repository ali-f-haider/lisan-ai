-- =====================================================================
-- Lisan AI -- public API, phase 2 tables  (run in: Supabase dashboard > SQL Editor)
-- Run api_phase1.sql first.
--
-- WHAT: the tables the paid dubbing endpoints need: the price quotes a
-- customer approves, the "one approval per step" record, the daily credit
-- reservations of each key, and the saved answers that make a retry safe.
-- Server-only like every other table. SAFE TO RUN MORE THAN ONCE.
-- =====================================================================

-- 1. Quotes: the exact numbers shown to the customer, kept until they expire.
create table if not exists api_quotes (
  id          uuid primary key,
  job_id      text not null,
  key_id      uuid not null,
  uid         uuid not null,
  step        text not null,
  quote       jsonb not null,
  expires_ts  double precision not null,
  created_at  timestamptz not null default now()
);
create index if not exists api_quotes_job_idx on api_quotes (job_id);

-- 2. One approval per step of a job. The two unique rules are the money safety:
--    the same quote can be approved once, and the same step of the same job
--    (same text revision and same choices) can be approved once, whatever key,
--    quote or retry is used.
create table if not exists api_operations (
  id               uuid primary key,
  quote_id         uuid not null unique,
  job_id           text not null,
  key_id           uuid not null,
  uid              uuid not null,
  step             text not null,
  revision         integer not null default 0,
  selection        text not null,
  maximum_credits  integer not null check (maximum_credits >= 0),
  state            text not null default 'accepted',
  created_at       timestamptz not null default now(),
  unique (job_id, step, revision, selection)
);

-- 3. What each key has reserved today (the whole maximum of every approved step;
--    refunds do not give a stolen key its allowance back). One row per key, job and step.
create table if not exists api_spend (
  id          uuid primary key default gen_random_uuid(),
  key_id      uuid not null,
  day         date not null,
  job_id      text not null,
  step        text not null,
  amount      integer not null check (amount >= 0),
  created_at  timestamptz not null default now(),
  unique (key_id, job_id, step)
);
create index if not exists api_spend_key_day_idx on api_spend (key_id, day);

-- 4. Saved answers for the Idempotency-Key header (kept 24 hours by the server).
create table if not exists api_requests (
  id           uuid primary key default gen_random_uuid(),
  key_id       uuid not null,
  idem_key     text not null,
  fingerprint  text not null,
  state        text not null default 'in_progress' check (state in ('in_progress','complete')),
  status       integer,
  response     jsonb,
  created_ts   double precision not null,
  created_at   timestamptz not null default now(),
  unique (key_id, idem_key)
);

-- 5. Lock all four down exactly like the other tables (server-only).
alter table api_quotes     enable row level security;
alter table api_operations enable row level security;
alter table api_spend      enable row level security;
alter table api_requests   enable row level security;
revoke all on api_quotes     from anon, authenticated;
revoke all on api_operations from anon, authenticated;
revoke all on api_spend      from anon, authenticated;
revoke all on api_requests   from anon, authenticated;

-- 6. Check (every row must show rls_on = true and all four can_* = false):
select c.relname as table_name, c.relrowsecurity as rls_on,
       has_table_privilege('anon', c.oid, 'SELECT')          as anon_can_read,
       has_table_privilege('anon', c.oid, 'INSERT')          as anon_can_insert,
       has_table_privilege('authenticated', c.oid, 'SELECT') as authed_can_read,
       has_table_privilege('authenticated', c.oid, 'UPDATE') as authed_can_update
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relname in ('api_quotes','api_operations','api_spend','api_requests');

-- OPTIONAL housekeeping, run now and then (never touch api_operations: it is the permanent record of what was approved):
--   delete from api_quotes   where created_at < now() - interval '3 days';
--   delete from api_requests where created_at < now() - interval '3 days';
--   delete from api_spend    where day < current_date - 60;

-- UNDO (only if you want to remove phase 2 completely):
--   drop table if exists api_requests; drop table if exists api_spend; drop table if exists api_operations; drop table if exists api_quotes;
