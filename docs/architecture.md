# Architecture

**Owner:** Engineering · **Status:** implemented and executed, see `pipeline/evidence/`

---

## 1. Shape of the problem

Three source tables joined on `customer_id`, one of which is at a different grain from the
other two. That single fact drives the whole design.

| Table | Rows | Grain |
|---|---|---|
| `customers` | 260,000 | one row per signup |
| `onboarding_events` | 1,386,264 | **one row per step attempt** |
| `activation_labels` | 260,000 | one row per signup |

5.33 event rows per customer. Anything that joins `onboarding_events` to `customers`
before aggregating will duplicate customers in proportion to how many attempts they made
— which means it duplicates precisely the customers who struggled, the population every
downstream question is about. The aggregation to customer grain therefore happens first,
always, and is asserted to return exactly 260,000 rows.

## 2. Stages

```
                 ┌──────────────────────────────────────────────────┐
   data/         │  io.load_tables()                                │
   ├ customers   │  three-tier source resolution:                   │
   ├ events   ───┤    parquet → csv → seeded generator              │
   └ labels      │  row-count assertions, provenance recorded       │
                 └───────────────────────┬──────────────────────────┘
                                         │
                 ┌───────────────────────▼──────────────────────────┐
                 │  state_machine.build_customer_step_view()        │
                 │  1,386,264 event rows → 1,284,599 customer-steps │
                 │  final status per step, attempts = max(attempt)  │
                 │  time on step = SUM over attempts                │
                 └───────────────────────┬──────────────────────────┘
                                         │
              ┌──────────────────────────┼──────────────────────────┐
              │                          │                          │
   ┌──────────▼─────────┐   ┌────────────▼───────────┐   ┌─────────▼──────────┐
   │ build_funnel_table │   │ build_customer_state_  │   │ features.build_    │
   │ 7 rows, one per    │   │ view() 260,000 rows,   │   │ features()         │
   │ stage, customer-   │   │ terminal state per     │   │ 21 leakage-safe    │
   │ level rates        │   │ customer               │   │ features           │
   └──────────┬─────────┘   └────────────┬───────────┘   └─────────┬──────────┘
              │                          │                          │
              │              ┌───────────▼──────────────────────────▼─────────┐
              │              │  scoring: BA model scores via --scores, or the │
              │              │  transparent logistic baseline                 │
              │              └───────────────────────┬───────────────────────┘
              │                                      │
              │              ┌───────────────────────▼───────────────────────┐
              │              │  activation_profile.build_activation_profile()│
              │              │  band × timing → treatment, per config        │
              │              └───────────────────────┬───────────────────────┘
              │                                      │
   ┌──────────▼──────────────────────────────────────▼───────────────────────┐
   │  pipeline/out/  — persisted views + run_manifest.json                   │
   │  analytics/quality/checks.py — 51 controls, non-zero exit on failure    │
   └─────────────────────────────────────────────────────────────────────────┘
```

## 3. The funnel as a state machine

Per the dataset contract, a journey ends either with `status = 'abandoned'` on some step,
or with step 7 `account_created` completed. `status = 'retry'` is an intermediate attempt
*within* a step, never a terminal state.

So the machine is a linear chain with one absorbing terminal branch per step:

```
  S1 ──► S2 ──► S3 ──► S4 ──► S5 ──► S6 ──► S7 = ACCOUNT_CREATED  (absorbing)
  │      │      │      │      │      │
  ▼      ▼      ▼      ▼      ▼      ▼
  ABANDONED_AT_STEP_n                                              (absorbing)
```

Seven terminal states observed, and the check suite asserts that no customer is left
without one.

Two invariants are enforced at build time rather than checked afterwards, because if
either fails, everything downstream is quietly wrong:

- **No customer holds two contradicting terminal statuses in the same step.** A step
  cannot be both `completed` and `abandoned`.
- **The chain is arithmetically closed.** Customers completing stage *n* must be exactly
  the set entering stage *n+1*. The builder raises if the two counts differ, which is what
  makes the 44.305% reproduction meaningful rather than coincidental.

### Aggregation decisions

| Quantity | Aggregation | Why not the alternative |
|---|---|---|
| Final status of a step | last row in `(attempt_no, event_ts)` order | Any other pick can select an intermediate `retry` |
| `attempts` | `MAX(attempt_no)` | Retained as a feature, not discarded — activation falls 45.6% → 35.4% from one attempt to three |
| Time on step | `SUM(ms_on_step)` | `ms_on_step` is per attempt. MAX or AVG understates the strugglers, who are the population of interest |
| `entering` a stage | distinct customers with any row for that step | Counting rows dilutes step 4 from 34.16% to 22.28% |

## 4. Persisted views

Written to `pipeline/out/`, gitignored because they are reproducible from the source data
plus this code.

| View | Rows | Grain | Purpose |
|---|---|---|---|
| `funnel_stage_view` | 7 | stage | Conversion table, the Product-facing artifact |
| `customer_step_view` | 1,284,599 | customer × step | Per-stage analysis without touching raw events |
| `customer_state_view` | 260,000 | customer | Terminal state, retries, wall-clock time |
| `customer_feature_view` | 260,000 | customer | Leakage-safe features for modelling |
| `customer_activation_profile` | 115,192 | customer | MVP 2 targeting — bands, timing, treatment |
| `activation_profile_band_summary` | 3 | band | Monotonicity evidence |
| `model_metrics`, `model_decile_table` | 3, 10 | — | Baselines vs. model, rank quality |
| `run_manifest.json` | — | — | Provenance for every run |

## 5. Source resolution, and why there are three tiers

`io.load_tables()` tries, in order: Parquet (the delivered format, needs `pyarrow` or
`fastparquet`), then CSV, then rebuilding in memory from `gen_d1_onboarding.py`.

The third tier exists so the pipeline is runnable and reviewable on a machine without a
Parquet engine — which is not hypothetical: the execution evidence in this repository was
produced that way. It is sound because the generator is seeded (SEED = 20260806) and the
dataset README states "same seed, same data". That claim was not taken on trust: the
regenerated tables were diffed against the delivered sample CSVs, 1,000 rows per table,
every column identical, and the regenerated data reproduces 260,000 / 1,386,264 /
44.305% / 19.240% exactly.

Whichever tier was used is written into `run_manifest.json`, so any figure quoted anywhere
in this repository can be traced back to the source that produced it.

## 6. Configuration as contract

Every threshold that decides who gets treated lives in
[`config/activation_profile.yaml`](../config/activation_profile.yaml), never in code:
band cut points, the timing window, the treatment matrix, suppression rules, the holdout
share, the feature allowlist and the forbidden list.

Two consequences worth stating plainly. Changing who the coach targets is a config change
with a version stamp, reviewable as a diff without reading Python. And every row of
`customer_activation_profile` carries the `thresholds_version` that produced it, so an
experiment result can always be tied to the exact policy in force when the row was
written.

The policy layer consuming this table may read `profile_band`, `timing_state`,
`treatment_state`, `score_decile` and `holdout_flag`. It must not read `activation_score`
to invent its own thresholds, and must not reach past this table into the labels tables.

## 7. Where this is deliberately incomplete

**No post-account-created events exist.** The event data stops at account creation. The
dataset has no card-delivery, account-funding, app-open, communications or
declined-transaction events, so the pipeline can rank who is unlikely to activate but
cannot say why. Anything asserting a mechanism would be invented. This is the single
largest gap and it is a data gap, not an implementation one.

**Scoring is a batch snapshot, not a service.** `--as-of` takes one date and scores
everyone against it. Production needs a daily run; the timing state is the only field
that changes between runs, since the score is fixed at account creation.

**The pipeline's own scorer is a baseline, not the model.** It exists so the pipeline runs
end to end and can be verified without a notebook. The BA gradient boosting model's
scores arrive through `--scores`, and the output labels which was used.

**Propensity is not uplift.** No treatment variation exists in the data, so incremental
effect is not estimable from it. The profile ranks who will activate, not who would
activate *because* they were contacted.
