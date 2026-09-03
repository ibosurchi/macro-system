-- =========================================================================
-- B2 H8: b2_ops_job_health
--
-- OPERATOR-RUN. Paste into the Supabase SQL editor and execute by hand.
-- The application NEVER executes DDL: tests/test_b2_stage_d_storage.py
-- ::test_no_ddl_verb_appears_anywhere asserts that no schema verb appears in
-- apex/b2_validation_bridge.py, in any pure validation module, or in the H8
-- operational package apex/ops -- and that guard is what keeps "this
-- application never creates, alters or drops a table" a structural fact rather
-- than a promise.
--
-- WHY THIS TABLE EXISTS
-- B2 evidence capture used to run inside a daemon thread owned by the
-- Streamlit server process. That thread dies with the process -- on redeploy,
-- on restart, and on Streamlit Community Cloud hibernation -- and its only
-- health signal was a module-level dict that died with it. Capture stopped
-- silently and nothing recorded that it had. Correct analytical code did not
-- prove continuous evidence capture.
--
-- This table is the durable answer to the one question that could not be
-- answered at all: WHEN DID EACH JOB LAST ACTUALLY SUCCEED? It is readable
-- with the Streamlit application stopped, and it survives process death.
--
-- ---------------------------------------------------------------------------
-- THIS IS THE FIRST DELIBERATELY MUTABLE B2 TABLE. READ THIS BEFORE COPYING IT.
-- ---------------------------------------------------------------------------
-- b2_market_observations, b2_market_observation_revisions and
-- b2_validation_outcomes all revoke UPDATE from service_role, because each one
-- holds HISTORY and history must not be rewritable.
--
-- This table holds CURRENT STATE, not history. There is exactly one row per
-- job and every run overwrites it, so UPDATE is retained for service_role on
-- purpose. That is a genuine exception to the append-only convention, and it
-- is written down here because an unexplained exception is how a convention
-- quietly dies.
--
-- The consequence is accepted knowingly: this table cannot answer "what
-- happened three runs ago". It is not meant to. Run HISTORY belongs to the
-- scheduler, which already retains it, and duplicating that here would create
-- a second source of truth that can disagree with the first.
--
-- NOTHING HERE TOUCHES EVIDENCE. This file creates one new operational table
-- and does not create, alter, drop or write to b2_shadow_records,
-- b2_market_observations, b2_market_observation_revisions or
-- b2_validation_outcomes.
--
-- RUN ORDER: execute this file BEFORE enabling any scheduled H8 job. A job
-- whose heartbeat write fails still completes its evidence work correctly and
-- reports the health failure separately -- health is never allowed to change
-- an evidence result -- but there is no reason to rely on that.
-- =========================================================================

create table if not exists public.b2_ops_job_health (
    -- One row per operational job. These three keys are declared in
    -- apex/ops/__init__.py and are the values an operator queries by:
    --   capture_shadow        -- hourly Tactical + Execution B2 observation
    --   capture_market_bars   -- daily closed-bar capture
    --   evaluate_outcomes     -- daily matured TACTICAL outcome evaluation
    job_key                text primary key,

    -- ATTEMPT vs SUCCESS are two independent axes and must never be collapsed.
    -- A job that runs every hour and fails every hour is alive on attempts and
    -- stale on success; reading only one of them hides exactly that case, and
    -- it is the case most likely to occur.
    last_attempt_at        timestamptz,
    last_success_at        timestamptz,
    last_failure_at        timestamptz,

    -- One of: success | failure | config_unavailable | lease_not_acquired |
    -- non_durable. Mirrors the H8 exit-code contract in apex/ops/__init__.py.
    last_status            text,
    last_run_id            text,

    -- The intended logical window this run spoke for: a UTC hour
    -- ('2026-09-03T14') for shadow capture, a UTC date for the daily jobs.
    -- Recorded so a gap is visible as a MISSING BUCKET rather than only as an
    -- old timestamp.
    last_logical_bucket    text,
    last_records_written   integer not null default 0,

    -- FALSE when the run completed only through non-durable local storage.
    -- On an ephemeral host that evidence disappears at the next redeploy, so it
    -- must never be counted as clean corpus capture. last_success_at is
    -- deliberately NOT advanced for such a run.
    last_durable           boolean not null default false,

    -- Redacted and length-capped by apex/ops/logging.py before it ever reaches
    -- this column. Never a traceback: a traceback carries local variables and
    -- request URLs, and this value is read long after the context that would
    -- justify keeping it.
    last_error_class       text,
    last_error_summary     text,

    code_version           text,
    schema_version         integer,

    -- ---------------------------------------------------------------------
    -- LEASE COLUMNS (shadow capture only)
    --
    -- Co-located with health rather than given their own table: it is the same
    -- subject and one row per job either way, so a second table would be one
    -- more object to create, grant and keep in step for no extra guarantee.
    --
    -- Acquisition is a SINGLE conditional update -- see apex/ops/lease.py --
    -- so concurrent runs serialise on this row's lock and exactly one wins.
    -- The lease prevents duplicate upstream WORK; it is not what protects
    -- correctness. Correctness is already guaranteed by the primary keys and
    -- natural-key uniques on the evidence tables.
    -- ---------------------------------------------------------------------
    lease_owner            text,
    lease_acquired_at      timestamptz,
    lease_expires_at       timestamptz,

    updated_at             timestamptz not null default now(),

    constraint b2_ojh_status_ck
        check (last_status is null or last_status in (
            'success', 'failure', 'config_unavailable',
            'lease_not_acquired', 'non_durable'
        )),

    -- A durable success must carry the timestamp that proves it. Without this
    -- a client bug could report health as fresh while leaving the one column an
    -- operator actually reads empty.
    constraint b2_ojh_success_ck
        check (last_status <> 'success' or last_success_at is not null),

    constraint b2_ojh_records_ck
        check (last_records_written >= 0),

    -- A held lease has both an owner and an expiry, or neither. A half-set
    -- lease would be indistinguishable from a free one to the acquire filter.
    constraint b2_ojh_lease_ck
        check ((lease_owner is null and lease_expires_at is null)
               or (lease_owner is not null and lease_expires_at is not null))
);

-- "which jobs are stale" -- the read an operator does, and the read a future
-- staleness evaluator will do. Ordered by success, because success is the
-- freshness axis that matters.
create index if not exists b2_ojh_success_idx
    on public.b2_ops_job_health (last_success_at desc nulls first);


-- =========================================================================
-- RLS / SERVICE-ROLE POSTURE
--
-- Identical to b2_market_observations, b2_market_observation_revisions and
-- b2_validation_outcomes in every respect EXCEPT the retained UPDATE, which is
-- explained in the header above. RLS enabled, no policies. RLS with zero
-- policies denies anon and authenticated outright. service_role has BYPASSRLS
-- and is the only credential this path uses, server-side only, never copied,
-- logged or widened.
-- =========================================================================

alter table public.b2_ops_job_health enable row level security;

revoke all on public.b2_ops_job_health from anon;
revoke all on public.b2_ops_job_health from authenticated;

-- DELETE and TRUNCATE stay revoked: losing health state is how a stale capture
-- becomes invisible again, which is the exact failure this table exists to end.
-- UPDATE is INTENTIONALLY NOT revoked here. See the header.
revoke delete, truncate on public.b2_ops_job_health from service_role;

-- VERIFY THIS STUCK. Supabase's default-privilege automation can re-grant on
-- new tables; re-check after creating the table that service_role holds
-- INSERT, SELECT and UPDATE but NOT DELETE or TRUNCATE:
--
--   select privilege_type from information_schema.role_table_grants
--    where table_name = 'b2_ops_job_health'
--      and grantee = 'service_role';


-- =========================================================================
-- POST-DEPLOY VERIFICATION (read-only, safe to run repeatedly)
--
--   select count(*) from public.b2_ops_job_health;                      -- 0
--
-- After the first manual dispatch of each job, expect one row per job key:
--
--   select job_key, last_status, last_durable, last_logical_bucket,
--          last_attempt_at, last_success_at, last_failure_at,
--          last_records_written, last_error_class, code_version
--     from public.b2_ops_job_health
--    order by job_key;
--
-- The operational question this table exists to answer, without opening
-- Streamlit:
--
--   select job_key, last_success_at, now() - last_success_at as age
--     from public.b2_ops_job_health
--    order by last_success_at nulls first;
--
-- A row with last_durable = false is NOT clean corpus evidence, whatever its
-- last_status says. Treat it as a failure and investigate before capture is
-- allowed to continue.
-- =========================================================================
