# pipeline

**Owner:** Engineering — Edgar Román Cervantes · Angel Aviles · Eliam Castillo

Ingestion, the funnel state machine, persisted views and execution evidence.
Design rationale: [`docs/architecture.md`](../docs/architecture.md).

---

## Run it

```bash
pip install -r requirements.txt
python -m pipeline.src.run --data-dir ./data --out-dir ./pipeline/out
```

| Flag | Default | Purpose |
|---|---|---|
| `--data-dir` | `./data` | Where the three source tables live |
| `--out-dir` | `./pipeline/out` | Where persisted views are written |
| `--config` | `config/activation_profile.yaml` | Thresholds and the feature contract |
| `--generator` | — | Path to `gen_d1_onboarding.py`, enabling the generated source tier |
| `--scores` | — | CSV/Parquet with `customer_id` + `activation_score` from the BA model |
| `--as-of` | last `account_created_ts` | Scoring date |
| `--verbose` | off | Debug logging, including per-epoch training loss |

Roughly 10 seconds end to end on the 260,000-customer dataset.

## Modules

| File | Responsibility |
|---|---|
| `src/io.py` | Three-tier source resolution (Parquet → CSV → seeded generator), row-count assertions, view persistence |
| `src/state_machine.py` | The seven-step funnel. Collapses 1,386,264 event rows to 1,284,599 customer-steps, builds the customer state view and the funnel table |
| `src/features.py` | Leakage-safe feature construction and the design-matrix encoder, which raises if a forbidden column reaches it |
| `src/scoring.py` | Channel-lookup baseline, NumPy logistic regression, and ROC-AUC / PR-AUC / Brier / decile table implemented directly |
| `src/activation_profile.py` | MVP 2 band × timing → treatment assignment, deterministic holdout, suppression |
| `src/run.py` | Entrypoint. Wires the stages, prints the reconciliation, writes the manifest |

## Outputs

Written to `--out-dir`, which is **gitignored** — the views are a function of the source
data plus this code, so committing them would only invite drift.

| View | Rows | Grain |
|---|---|---|
| `funnel_stage_view` | 7 | stage |
| `customer_step_view` | 1,284,599 | customer × step |
| `customer_state_view` | 260,000 | customer |
| `customer_feature_view` | 260,000 | customer |
| `customer_activation_profile` | 115,192 | customer (completers only) |
| `activation_profile_band_summary` | 3 | band |
| `model_metrics`, `model_decile_table` | 3, 10 | — |
| `run_manifest.json` | — | provenance for the run |

## Evidence

`evidence/` is committed, and explicitly un-ignored against the repository's blanket
`*.log` rule. Everything in it is captured output, not transcription.

| File | What it is |
|---|---|
| `pipeline_run.log` | Full stdout of a complete run |
| `run_manifest.json` | Source tier, library versions, row counts, headline figures, band distribution |
| `funnel_stage_view.csv` | The seven-step funnel table |
| `model_metrics.csv` | Baselines vs. model |
| `model_decile_table.csv` | Rank quality |
| `activation_profile_band_summary.csv` | Band sizes and observed activation |
| `customer_state_view_sample.csv` | First 100 rows of the customer state view |

No per-customer sample of `customer_activation_profile` is committed. Rows of that table
are one record per customer, and even on synthetic data they are the wrong thing to keep
in a repository — the band sizes, treatment split and observed activation rates in
`activation_profile_band_summary.csv` and `pipeline_run.log` are the evidence that
matters. Regenerate the full table locally by running the pipeline.

All persisted floats are rounded to six decimals and timestamps truncated to whole
seconds (`io.WRITE_PRECISION`). Full float64 repr writes a rate as seventeen significant
decimals, which reads as a long numeric run to a secret scanner and blocks the commit.
Six decimals is more precision than any figure here is quoted to.

### What the last run showed

```
Event rows per customer: 5.33 (a row is not a person)
Step 4 selfie_liveness: 292,307 attempt rows -> 190,642 distinct customers

step 4  selfie_liveness  entering 190,642  abandoned 65,129  stage rate 34.16%
Largest drop: step 4 selfie_liveness - 65,129 customers lost, 44.98% of all abandonment
Onboarding completion: 44.305% (baseline 44.3%)

completed_onboarding disagreements: 0
max_step_reached vs steps_completed disagreements: 0
activated_30d overall: 19.240% (baseline 19.2%)
activated without completing: 0 (must be 0)
```

## A note on the third source tier

The pipeline can rebuild the tables in memory from `gen_d1_onboarding.py` when no Parquet
engine is installed. This is sound, not a shortcut: the generator is seeded (SEED =
20260806), the dataset README states "same seed, same data", and that claim was verified
by diffing the regenerated tables against the delivered sample CSVs — 1,000 rows per
table, every column identical. The regenerated data reproduces 260,000 / 1,386,264 /
44.305% / 19.240% exactly.

Which tier a run used is recorded in `run_manifest.json`, so any figure quoted anywhere in
this repository is traceable to the source that produced it.
