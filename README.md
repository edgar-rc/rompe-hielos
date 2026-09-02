# rompe-hielos

**Track C · Team 1 — From signup to first use**
Onboarding, funnel abandonment and activation. Hackathon submission, Purple Rockets.

---

## Objective

**North Star:** share of signups that make a first transaction within 30 days — `activated_30d`.

The funnel loses people at two separate moments, and they are close to the same size:

| | | |
|---|---|---|
| Signups | 260,000 | |
| Complete onboarding | 115,192 | **44.305%** |
| Activate within 30 days | 50,024 | **19.240%** |
| Activation among completers | 50,024 / 115,192 | **43.427%** |
| **Complete but never transact** | **65,168** | 56.57% of completers |

44.305% × 43.427% = 19.240%, recovered exactly from the data. Fixing only the funnel
addresses the first factor and leaves the second untouched — and the two multiply rather
than add, because of the 144,808 customers who never finish onboarding, **exactly zero**
activate. With no account there is nothing to transact with.

## The challenge

Isolate the drop-off points, predict who will abandon or go dormant, and recommend a
product intervention with an estimated cost. Three things the brief warns about, all
three verified here rather than taken on trust:

**A row is not a person.** `onboarding_events` holds 1,386,264 rows for 260,000
customers because `selfie_liveness` allows retries. Measured at the attempt level, step 4
abandonment reads **22.28%**; measured at customer level it is **34.16%**. The
customer-level figure is canonical — see [docs/metrics.md](docs/metrics.md).

**The labels table contains the answer.** `days_to_first_transaction` and
`first_transaction_ts` are null if and only if `activated_30d` is false. Both dropped,
with the verification in [docs/decision-log.md](docs/decision-log.md).

**Accuracy lies at 80.8%.** Predicting "nobody activates" is right four times in five.
Hence PR-AUC and recall at the top of the list — and note that PR-AUC's floor is the
prevalence of the population being scored, not 0.5.

## Headline findings

| Finding | Number |
|---|---|
| Largest single drop | Step 4 `selfie_liveness` — 65,129 customers, 44.98% of all abandonment |
| Android legacy vs. current at liveness | 49.75% vs. 25.97% abandonment (+23.8pp) |
| Same comparison on iOS | 28.23% vs. 25.46% (+2.8pp) — the effect is Android-specific |
| Activation by channel, among completers | referral 58.8% … paid social 29.3% (29.5pp spread) |
| Intervention window | Days 3–10. Median activation is day 5; 93.3% by day 14 |

## Repository index

| Path | Contents | Area |
|---|---|---|
| [`docs/`](docs/) | PRD, backlog, architecture, metrics, decision log, AI usage | all |
| [`docs/activation-profile-spec.md`](docs/activation-profile-spec.md) | Segment definition for MVP 2 — the coach's targeting contract | Engineering |
| [`docs/architecture.md`](docs/architecture.md) | Pipeline design, the funnel state machine, data contracts | Engineering |
| [`docs/metrics.md`](docs/metrics.md) | Metric definitions and canonical denominators | Engineering + BA |
| [`docs/decision-log.md`](docs/decision-log.md) | Every cleaning, exclusion and aggregation decision, with reasons | all |
| [`config/`](config/) | All thresholds, versioned. Targeting changes are config changes | Engineering |
| [`pipeline/`](pipeline/) | Ingestion, state machine, persisted views, execution evidence | Engineering |
| [`analytics/`](analytics/) | EDA, model, metrics, quality controls | BA |
| [`dashboard/`](dashboard/) | QuickSight link and screenshots | BA |
| [`pitch/`](pitch/) | Presentation material | Product |

## Running it

```bash
pip install -r pipeline/requirements.txt
```

### 1. Get the data

The three Parquet tables are **not committed** — 22 MB of synthetic data that anyone can
reproduce exactly. Either drop the delivered files into `./data/`, or rebuild them:

```bash
python gen_d1_onboarding.py --customers 260000 --out ./data
```

`gen_d1_onboarding.py` is the seeded generator shipped with the dataset (SEED =
20260806). Same seed, same data — verified by diffing the regenerated tables against the
delivered sample CSVs: 1,000 rows per table, every column identical.

### 2. Run the pipeline

```bash
python -m pipeline.src.run --data-dir ./data --out-dir ./pipeline/out
```

Reads the three tables, collapses retries to customer grain, builds the funnel, the
leakage-safe feature table and the MVP 2 activation profile, then writes each view plus a
run manifest recording provenance. Roughly 10 seconds on a laptop.

With the BA model's scores:

```bash
python -m pipeline.src.run --data-dir ./data --scores ./analytics/out/model_b_scores.csv
```

Without `--scores` the pipeline uses its own transparent logistic baseline, labelled
`score_source = pipeline_baseline_logreg` in the output so it is never confused with the
BA model.

### 3. Run the quality controls

```bash
python analytics/quality/checks.py --data-dir ./data --out-dir ./pipeline/out
```

51 checks across integrity, nulls, ranges, leakage, deduplication, funnel arithmetic,
band monotonicity, holdout balance and the do-no-harm guardrail. Exits non-zero on
failure, so it works in CI.

### No Parquet engine available?

`pipeline/src/io.py` resolves its source in three tiers — Parquet, then CSV, then
rebuilding in memory from the seeded generator. Pass `--generator path/to/gen_d1_onboarding.py`
to enable the third. Whichever tier was used is recorded in the run manifest, so any
published figure is traceable to its source.

## Execution evidence

| File | What it is |
|---|---|
| [`pipeline/evidence/pipeline_run.log`](pipeline/evidence/pipeline_run.log) | Captured stdout of a full run — not a transcription |
| [`pipeline/evidence/run_manifest.json`](pipeline/evidence/run_manifest.json) | Provenance: source tier, versions, row counts, headline figures |
| [`pipeline/evidence/funnel_stage_view.csv`](pipeline/evidence/funnel_stage_view.csv) | The seven-step funnel table |
| [`analytics/quality/evidence/quality_report.txt`](analytics/quality/evidence/quality_report.txt) | Captured output of the quality controls |

Generated views land in `pipeline/out/` and are gitignored — they are a function of the
source data plus this code, so committing them would only invite drift.

## A note on the data

100% synthetic. No real Nu customer information is involved anywhere in this repository.
Relationships hold within this dataset only; nothing here has been validated against real
customers.

## Team

| Area | People |
|---|---|
| Product | Lara Sproesser |
| Engineering | Edgar Román Cervantes · Angel Aviles · Eliam Castillo |
| BA | Carlos Medina · Eduardo Ortiz · David Octavio Garcia · Jose Rodolfo Lopez |
