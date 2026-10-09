-- =====================================================================
-- Lisan AI -- public API, phase 1 tables  (run in: Supabase dashboard > SQL Editor)
--
-- WHAT: the tables the API key system and the admin API settings need.
-- Nothing here is read by the browser: like every other table, only the
-- server (secret service key) can touch them. SAFE TO RUN MORE THAN ONCE.
--
-- Later phases (uploads, quotes, jobs, charges) add their own tables; they
-- are NOT in this file on purpose, so phase 1 can be tested on its own.
-- =====================================================================

-- 1. The admin page's API settings: one row, one JSON object.
create table if not exists api_settings (
  id          integer primary key check (id = 1),
  settings    jsonb not null default '{}'::jsonb,
  updated_at  timestamptz not null default now()
);

-- 2. The owner's acceptance of the API terms (one row per account and terms version).
create table if not exists api_owner_acceptances (
  id              uuid primary key default gen_random_uuid(),
  uid             uuid not null,
  terms_version   text not null,
  rights_confirmed boolean not null default false,
  accepted_at     timestamptz not null default now(),
  unique (uid, terms_version)
);

-- 3. The keys. The secret itself is never stored: only an HMAC of it (key_hash),
--    the first characters for the dashboard (prefix) and which server secret
--    version made the hash (pepper_version).
create table if not exists api_keys (
  id               uuid primary key default gen_random_uuid(),
  uid              uuid not null,
  name             text not null,
  prefix           text not null,
  pepper_version   integer not null default 1,
  key_hash         text not null unique,
  scopes           jsonb not null default '["read","account"]'::jsonb,
  state            text not null default 'active' check (state in ('active','revoked')),
  daily_credit_cap integer not null check (daily_credit_cap > 0),
  created_at       timestamptz not null default now(),
  expires_at       timestamptz,
  last_used_at     timestamptz,
  revoked_at       timestamptz
);
create index if not exists api_keys_uid_idx on api_keys (uid);

-- 4. Lock all three down exactly like the other tables (server-only).
alter table api_settings          enable row level security;
alter table api_owner_acceptances enable row level security;
alter table api_keys              enable row level security;
revoke all on api_settings          from anon, authenticated;
revoke all on api_owner_acceptances from anon, authenticated;
revoke all on api_keys              from anon, authenticated;

-- 5. Check (every row must show rls_on = true and all four can_* = false):
select c.relname as table_name, c.relrowsecurity as rls_on,
       has_table_privilege('anon', c.oid, 'SELECT')          as anon_can_read,
       has_table_privilege('anon', c.oid, 'INSERT')          as anon_can_insert,
       has_table_privilege('authenticated', c.oid, 'SELECT') as authed_can_read,
       has_table_privilege('authenticated', c.oid, 'UPDATE') as authed_can_update
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relname in ('api_settings','api_owner_acceptances','api_keys');

-- UNDO (only if you want to remove phase 1 completely):
--   drop table if exists api_keys; drop table if exists api_owner_acceptances; drop table if exists api_settings;
