-- =========================================================================
-- B2 Stage D-4: b2_market_observation_revisions
--
-- OPERATOR-RUN. Paste into the Supabase SQL editor and execute by hand.
-- The application NEVER executes DDL: tests/test_b2_stage_d_storage.py
-- ::test_no_ddl_verb_appears_anywhere asserts that no schema verb appears in
-- apex/b2_validation_bridge.py or in any pure validation module, and that
-- guard is what keeps "this application never creates, alters or drops a
-- table" a structural fact rather than a promise.
--
-- WHY THIS TABLE EXISTS
-- b2_market_observations is append-only. When a vendor re-reports a bar we
-- already stored with different values, the store correctly refuses the write
-- and reports a conflict -- and the knowledge that the vendor changed its mind
-- then survives only in a capture report nobody keeps. This table keeps it,
-- as a SEPARATE point-in-time fact. The original observation is never touched,
-- never updated and never overwritten by anything here.
--
-- The live case: Yahoo publishes a provisional volume for the most recently
-- closed daily bar and corrects it later. On the 2026-08-31 capture, GC=F,
-- CL=F and NQ=F each carried a provisional volume for their 2026-08-28 bar.
-- Open, high, low and close were bit-identical across both captures.
--
-- RUN ORDER: this file must be executed BEFORE the code that writes to it is
-- deployed. The capture path fails open if the table is absent -- the capture
-- result is unaffected and the failure is reported separately -- but there is
-- no reason to rely on that.
-- =========================================================================

create table if not exists public.b2_market_observation_revisions (
    -- PHYSICAL identity of the revision: sha256("rev"|observation_id|
    -- revised_content_hash) truncated to 32 hex, the same construction and
    -- width as observation_id and content_hash. Deliberately a function of the
    -- observation and the revised CONTENT only -- no clock, no kind, no
    -- ordinal -- so that re-seeing a revision deduplicates onto this row while
    -- a later, DIFFERENT revision appends beside it.
    revision_id            text primary key,

    -- The observation this revises. RESTRICT on both update and delete: the
    -- referenced table is append-only, so neither can legitimately happen, and
    -- a cascade would be a way to lose history quietly.
    observation_id         text not null
        references public.b2_market_observations (observation_id)
        on update restrict on delete restrict,

    -- The stored observation's content hash, denormalised. This is the
    -- AUTHORITATIVE integrity witness, copied from the content_hash COLUMN --
    -- never recomputed from PostgREST-returned floats, which come back at 15
    -- significant digits and do not round-trip.
    original_content_hash  text not null,
    revised_content_hash   text not null,

    -- volume_only is proven bit-exactly by hash probe; price is a genuine
    -- open/high/low/close change; other is a real difference that could not be
    -- attributed, escalated rather than downgraded.
    revision_kind          text not null,

    -- Identity basis of the observation, denormalised: a revision row must be
    -- auditable and its ids re-derivable without trusting a join, and a future
    -- change to the identity basis must be visible here rather than silent.
    symbol                 text not null,
    granularity            text not null,
    bar_time               timestamptz not null,
    price_source           text not null,

    -- The REVISED values, as the vendor reported them at first_seen_at.
    -- The ORIGINAL values are NOT copied here: b2_market_observations is
    -- append-only, so the join can never go stale, and a copy could only ever
    -- disagree with the row it claims to mirror.
    "open"                 double precision not null,
    high                   double precision not null,
    low                    double precision not null,
    close                  double precision not null,
    volume                 double precision,

    -- Which measured fields moved. At least one, always.
    changed_fields         text[] not null,

    -- first_seen_at is the DATABASE's own record of when this payload was
    -- first observed. The client never sends it, so it cannot be backdated,
    -- and ON CONFLICT DO NOTHING means a re-capture never moves it.
    first_seen_at          timestamptz not null default now(),
    -- captured_at is the capture RUN's reference clock. Both are kept so a
    -- clock disagreement stays visible instead of being averaged away.
    captured_at            timestamptz not null,
    resolver_version       text not null,
    meta                   jsonb not null default '{}'::jsonb,

    constraint b2_mor_kind_ck
        check (revision_kind in ('volume_only', 'price', 'other')),

    -- An identical payload is a duplicate, not a revision. Enforced here so
    -- the distinction cannot be blurred by a future client bug.
    constraint b2_mor_hash_differs_ck
        check (revised_content_hash <> original_content_hash),

    constraint b2_mor_id_shape_ck
        check (revision_id ~ '^[0-9a-f]{32}$'),
    constraint b2_mor_hash_shape_ck
        check (revised_content_hash ~ '^[0-9a-f]{32}$'
               and original_content_hash ~ '^[0-9a-f]{32}$'),
    constraint b2_mor_granularity_ck
        check (granularity in ('1d', '5m')),

    -- The same OHLC invariants MarketBar.__post_init__ enforces before a row
    -- is ever built. Asserted again here because a constraint that lives only
    -- in the application is a convention, not a guarantee.
    constraint b2_mor_ohlc_ck
        check ("open" > 0 and high > 0 and low > 0 and close > 0
               and high >= greatest("open", close)
               and low <= least("open", close)),
    constraint b2_mor_volume_ck
        check (volume is null or volume >= 0),
    constraint b2_mor_changed_ck
        check (coalesce(array_length(changed_fields, 1), 0) >= 1),

    -- The natural key, asserted for the same reason b2_market_observations
    -- asserts its own: if revision_id derivation is ever broken by a bug,
    -- inserts fail loudly instead of silently storing one revision twice under
    -- two different ids.
    constraint b2_mor_natural_key_uq
        unique (observation_id, revised_content_hash)
);

-- "every revision of this bar, oldest first" -- the read a researcher does.
create index if not exists b2_mor_observation_idx
    on public.b2_market_observation_revisions (observation_id, first_seen_at);

-- "which bars in this series were ever revised" -- the read an auditor does.
create index if not exists b2_mor_series_idx
    on public.b2_market_observation_revisions (symbol, granularity, bar_time);

-- "show me every price/other revision, newest first" -- the read an operator
-- does, because a volume revision on a futures symbol is expected and a price
-- revision is not.
create index if not exists b2_mor_kind_idx
    on public.b2_market_observation_revisions (revision_kind, first_seen_at desc);


-- =========================================================================
-- RLS / SERVICE-ROLE POSTURE
--
-- Identical to b2_market_observations: RLS enabled, no policies. RLS with zero
-- policies denies anon and authenticated outright. service_role has BYPASSRLS
-- and is the only credential this path uses, server-side only, never copied,
-- logged or widened.
-- =========================================================================

alter table public.b2_market_observation_revisions enable row level security;

revoke all on public.b2_market_observation_revisions from anon;
revoke all on public.b2_market_observation_revisions from authenticated;

-- Append-only enforced by the DATABASE rather than by convention. The capture
-- path only ever INSERTs and SELECTs, so this costs the application nothing
-- and makes silently rewriting history impossible even from a future code bug.
--
-- The friction is intentional: correcting a row later requires re-granting as
-- the postgres role, which is a deliberate, visible act.
--
-- VERIFY THIS STUCK. Supabase's default-privilege automation can re-grant on
-- new tables; re-check after creating the table that service_role holds
-- INSERT and SELECT but not UPDATE or DELETE:
--
--   select privilege_type from information_schema.role_table_grants
--    where table_name = 'b2_market_observation_revisions'
--      and grantee = 'service_role';
revoke update, delete, truncate on public.b2_market_observation_revisions from service_role;


-- =========================================================================
-- POST-DEPLOY VERIFICATION (read-only, safe to run repeatedly)
--
--   select count(*) from public.b2_market_observation_revisions;          -- 0
--
-- After the first Gold-only capture, expect exactly one row, kind
-- 'volume_only', for the GC=F 2026-08-28 bar:
--
--   select observation_id, revision_kind, changed_fields, volume,
--          original_content_hash, revised_content_hash, first_seen_at
--     from public.b2_market_observation_revisions
--    order by first_seen_at;
--
-- Re-running that capture immediately must add NO row: the revision is
-- already known, and its first_seen_at must be unchanged.
-- =========================================================================
