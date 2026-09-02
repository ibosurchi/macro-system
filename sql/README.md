# Operator-run SQL

Every file here is executed **by hand**, by an operator, in the Supabase SQL
editor. The application never executes DDL. That is not a convention: the
architectural guard in
`tests/test_b2_stage_d_storage.py::test_no_ddl_verb_appears_anywhere` fails if a
schema verb ever appears in `apex/b2_validation_bridge.py` or in any pure
validation module.

Run files in numeric order. Each is idempotent (`create table if not exists`,
`create index if not exists`) and safe to re-run.

| File | Table | Status |
|---|---|---|
| `001_b2_market_observation_revisions.sql` | `b2_market_observation_revisions` | Stage D-4 |

## `b2_market_observations` is not in this directory

That table predates in-repo SQL and was created out of band, before this
directory existed. Its DDL is therefore **not** recorded here, and nothing in
this directory creates, alters or drops it. `001` references it with a foreign
key and assumes it already exists.

This is a known gap rather than a design choice. If its definition is ever
recovered from the Supabase dashboard it should be recorded here as `000_`,
purely as documentation — never as something to run against a live table that
already holds captured history.
