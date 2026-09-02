-- =========================================================================
-- B2 Stage D-5: b2_validation_outcomes
--
-- OPERATOR-RUN. Paste into the Supabase SQL editor and execute by hand.
-- The application NEVER executes DDL: tests/test_b2_stage_d_storage.py
-- ::test_no_ddl_verb_appears_anywhere asserts that no schema verb appears in
-- apex/b2_validation_bridge.py or in any pure validation module.
--
-- WHY THIS TABLE EXISTS
-- A prediction fact, a market fact and an evaluation fact are three different
-- things. b2_shadow_records holds what B2 claimed. b2_market_observations
-- holds what the market printed. What an evaluation CONCLUDED from both is a
-- third kind of fact, and writing it into either of the first two would
-- destroy the separation the whole architecture rests on. Nothing in this file
-- creates, alters, drops or writes to either of those tables, or to
-- b2_market_observation_revisions.
--
-- TACTICAL ONLY. The horizon CHECK below is deliberately narrow. The audit
-- measured that a three-day Execution window yields at most two daily bars,
-- that 45% of observations get fewer than two, and that the 16% getting none
-- are weekend-correlated -- a systematic sampling bias, not random loss.
-- Persisting an Execution verdict from that would be false precision, so the
-- database refuses it rather than trusting the application to. Widening this
-- CHECK is a separate, separately-approved stage.
--
-- RUN ORDER: execute this file BEFORE deploying code that writes to it. The
-- validation path fails open if the table is absent -- the evaluation and the
-- cohort are returned intact and the failure is reported separately -- but
-- there is no reason to rely on that.
-- =========================================================================

create table if not exists public.b2_validation_outcomes (
    -- PHYSICAL identity: sha256("val"|validation_id|input_hash) truncated to
    -- 32 hex, the same construction and width as observation_id, content_hash
    -- and revision_id.
    --
    -- The identity is the JOB and the EVIDENCE. It deliberately excludes
    -- outcome_hash, and that exclusion is load-bearing: if the verdict were
    -- part of the identity, a run that reached a DIFFERENT conclusion from
    -- IDENTICAL evidence would quietly append a second row and look like
    -- ordinary supersession. Keeping it out makes that collide here instead,
    -- where the application catches it and reports a determinism defect.
    outcome_row_id            text primary key,

    -- Stable across an immature evaluation and its eventual matured rerun of
    -- the SAME job. Not unique on its own: one job legitimately produces one
    -- row per distinct evidence state.
    validation_id             text not null,
    -- Changes exactly when the evidence D-2C2 actually used changes.
    input_hash                text not null,
    -- Changes exactly when the resolved verdict changes. A FIELD, never part
    -- of the identity. See above.
    outcome_hash              text not null,

    outcome_schema_version    text not null,
    validation_schema_version text not null,
    validation_config_version text not null,
    validation_config_hash    text not null,

    -- The prediction fact this is ABOUT. Identity and integrity only -- the
    -- payload is NOT copied here. b2_shadow_records is append-only, so the
    -- join can never go stale and a copy could only ever disagree.
    shadow_storage_id         text not null,
    shadow_record_id          text not null,
    shadow_content_hash       text not null,
    shadow_schema_version     integer,

    instrument                text not null,
    horizon                   text not null,
    asset_class               text,
    -- EVALUATION TIME: when B2 made the claim.
    evaluated_at              timestamptz not null,
    -- RUN TIME: the instant this run claimed to speak for. Also the capture
    -- bound (R4) that admitted the bar evidence below.
    as_of                     timestamptz not null,
    claim_direction           text not null,

    -- The six orthogonal axes, verbatim from the envelope. No axis is a score
    -- and no axis collapses into another.
    data_resolution           text not null,
    direction_outcome         text not null,
    setup_invalidation        text not null,
    thesis_invalidation       text not null,
    execution_outcome         text not null,
    eligibility_pool          text not null,
    exclusion_reason          text,
    calibration_eligible      boolean not null default false,

    -- Evidence quality. Orthogonal to whether the claim was right.
    readiness_tier            text not null,
    provenance_grade          text,
    maturity_state            text not null,
    coverage_status           text,
    finalization_status       text,
    path_complete             boolean not null default false,
    -- FINAL only when no later bar could change the answer:
    -- maturity MATURED and data_resolution RESOLVED and path_complete.
    finality                  text not null,

    -- MEASUREMENTS, deliberately not scores. Under a PROVISIONAL row the
    -- excursions are LOWER BOUNDS.
    terminal_return           double precision,
    mfe                       double precision,
    mae                       double precision,
    mfe_atr                   double precision,
    mae_atr                   double precision,
    bars_to_mfe               integer,
    bars_to_mae               integer,
    path_bars                 integer,

    -- Which market series actually answered.
    anchor_status             text,
    market_symbol             text,
    bound_symbol              text,
    series_binding_quality    text,
    terminal_observation_id   text,
    terminal_bar_time         timestamptz,
    used_bar_count            integer,
    duplicates_collapsed      integer,
    malformed_row_count       integer,

    -- The input_hash PREIMAGE: an ordered array of
    -- {observation_id, content_hash}, in the order canonicalize_bars produced
    -- and never re-sorted. A sorted bag of content hashes would be blind to
    -- which bar holds which content, and two paths that swap them would look
    -- identical while resolving to different returns. This is what makes a
    -- persisted result reproducible from the row.
    bar_evidence              jsonb not null default '[]'::jsonb,
    conflict_ids              text[] not null default '{}'::text[],

    -- first_seen_at is the DATABASE's own record of when this conclusion was
    -- first recorded. The client never sends it, so it cannot be backdated,
    -- and ON CONFLICT DO NOTHING means a re-run never moves it.
    first_seen_at             timestamptz not null default now(),
    meta                      jsonb not null default '{}'::jsonb,

    -- TACTICAL ONLY. See the header.
    constraint b2_vo_horizon_ck
        check (horizon = 'tactical'),

    constraint b2_vo_id_shape_ck
        check (outcome_row_id ~ '^[0-9a-f]{32}$'),
    constraint b2_vo_hash_shape_ck
        check (validation_id ~ '^[0-9a-f]{32}$'
               and input_hash ~ '^[0-9a-f]{32}$'
               and outcome_hash ~ '^[0-9a-f]{32}$'),

    constraint b2_vo_finality_ck
        check (finality in ('final', 'provisional')),

    -- FINAL is not a label a client may assert freely: it must be earned by
    -- all three conditions at once.
    constraint b2_vo_final_earned_ck
        check (finality <> 'final'
               or (maturity_state = 'matured'
                   and data_resolution = 'resolved'
                   and path_complete)),

    -- The OutcomeAxes invariants, mirrored from the application so they hold
    -- even if a future code path forgets them.
    --
    -- INVARIANT 1 -- an immature observation has no verdict of any kind.
    -- D-5's gate never persists NOT_MATURED at all; this is the second lock.
    constraint b2_vo_not_matured_ck
        check (data_resolution <> 'not_matured'
               or direction_outcome = 'unresolved'),
    -- INVARIANT 2 -- absent market data says nothing about the claim.
    constraint b2_vo_absent_data_ck
        check (data_resolution not in ('insufficient_data', 'unavailable')
               or direction_outcome not in ('confirmed', 'failed')),
    -- INVARIANT 3 -- silent exclusion is how a denominator shrinks unnoticed.
    constraint b2_vo_excluded_reason_ck
        check (eligibility_pool <> 'excluded' or exclusion_reason is not null),
    -- INVARIANT 4 -- the captured pool admits nothing it did not observe.
    constraint b2_vo_captured_pool_ck
        check (eligibility_pool <> 'captured' or data_resolution <> 'unavailable'),
    -- Calibration eligibility requires BOTH a captured pool and a real
    -- verdict. Recorded here as a fact; NOTHING in this stage reads it.
    constraint b2_vo_calibration_ck
        check (not calibration_eligible
               or (eligibility_pool = 'captured'
                   and direction_outcome in ('confirmed', 'failed'))),

    -- The natural key, asserted for the same reason the other two tables
    -- assert theirs: if outcome_row_id derivation is ever broken by a bug,
    -- inserts fail loudly instead of silently storing one conclusion twice
    -- under two different ids.
    constraint b2_vo_natural_key_uq
        unique (validation_id, input_hash)
);

-- "every evidence state of this validation job, oldest first" -- how a
-- provisional row and the row that superseded it are read together.
create index if not exists b2_vo_job_idx
    on public.b2_validation_outcomes (validation_id, first_seen_at);

-- "what do we know about this shadow observation" -- the join back to the
-- prediction fact.
create index if not exists b2_vo_shadow_idx
    on public.b2_validation_outcomes (shadow_storage_id, first_seen_at);

-- "which results are settled, per instrument" -- the read a researcher does.
create index if not exists b2_vo_instrument_idx
    on public.b2_validation_outcomes (instrument, horizon, finality, evaluated_at);


-- =========================================================================
-- RLS / SERVICE-ROLE POSTURE
--
-- Identical to b2_market_observations and b2_market_observation_revisions:
-- RLS enabled, no policies. RLS with zero policies denies anon and
-- authenticated outright. service_role has BYPASSRLS and is the only
-- credential this path uses, server-side only, never copied, logged or
-- widened.
-- =========================================================================

alter table public.b2_validation_outcomes enable row level security;

revoke all on public.b2_validation_outcomes from anon;
revoke all on public.b2_validation_outcomes from authenticated;

-- Append-only enforced by the DATABASE rather than by convention. The
-- validation path only ever INSERTs and SELECTs, so this costs the
-- application nothing and makes silently rewriting a recorded conclusion
-- impossible even from a future code bug.
--
-- The friction is intentional: correcting a row later requires re-granting as
-- the postgres role, which is a deliberate, visible act.
--
-- VERIFY THIS STUCK. Supabase's default-privilege automation can re-grant on
-- new tables; re-check after creating the table that service_role holds
-- INSERT and SELECT but not UPDATE or DELETE:
--
--   select privilege_type from information_schema.role_table_grants
--    where table_name = 'b2_validation_outcomes'
--      and grantee = 'service_role';
revoke update, delete, truncate on public.b2_validation_outcomes from service_role;


-- =========================================================================
-- POST-DEPLOY VERIFICATION (read-only, safe to run repeatedly)
--
--   select count(*) from public.b2_validation_outcomes;               -- 0
--
-- The first tactical maturity is 2026-09-13. Until then, a dry run over the
-- whole population must report every observation as withheld_not_matured and
-- write nothing. After the first persisted run:
--
--   select instrument, finality, maturity_state, direction_outcome,
--          used_bar_count, first_seen_at
--     from public.b2_validation_outcomes
--    order by first_seen_at;
--
-- Re-running the same capture immediately must add NO row: the conclusion is
-- already known, and its first_seen_at must be unchanged.
--
-- NOTE ON window_end: deliberately NOT a column. It is derivable from
-- evaluated_at, horizon and validation_config_hash -- all of which are stored
-- -- and a stored derivation is a second definition that can drift from the
-- one the evaluation actually used.
-- =========================================================================
