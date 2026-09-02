# analytics

**Owner:** BA — Eduardo Ortiz (lead) · Carlos Medina (segments) · David Garcia (model) ·
Jose Lopez (quality)

EDA, model, metrics and quality controls.

---

## What is already here

| Path | Contents | Owner | Status |
|---|---|---|---|
| [`quality/`](quality/) | Executable quality suite — 51 checks, non-zero exit on failure | Engineering + Jose | **Done and executed** |
| [`quality/evidence/quality_report.txt`](quality/evidence/quality_report.txt) | Captured output: 49 passed, 2 warnings, 0 failures | — | **Done** |

## What still needs committing

Notebooks live outside git today. The brief asks for them here, and it asks for commits
from all four BAs.

| Notebook | Owner | Purpose |
|---|---|---|
| `00_carga.ipynb` | all | Load the three tables, produce the data dictionary — name, type, % nulls, cardinality per column |
| `01_embudo.ipynb` | Eduardo | Retry resolution and the stage-by-stage conversion table |
| `02_segmentos.ipynb` | Carlos | Cuts by device, app version, acquisition channel, age, state, prior banking |
| `03_baseline.ipynb` | David | Baselines before the model, with the split and seed recorded |
| `04_modelo.ipynb` | David | `activated_30d` model, imbalance-aware metrics, feature importance |
| `model_b_activation_propensity.ipynb` | David / Rodolfo | The scoring model behind MVP 2 |

### Two things worth reusing rather than redoing

**The funnel is already reproduced.** `pipeline/src/state_machine.py` builds the
customer-level funnel table and it matches the 44.305% baseline, with the stage table in
[`docs/metrics.md`](../docs/metrics.md) §3. Rather than rebuilding the deduplication in a
notebook, read `pipeline/out/customer_step_view` and `customer_state_view` — that is what
they exist for, and it removes the risk of two different funnels circulating.

**The leakage audit is already done and documented.**
[`docs/decision-log.md`](../docs/decision-log.md) §B has the verification with counts. Two
total-leak columns, four constant-in-population columns, and six constant attempt
counters. Do not re-derive it; extend it if something new appears.

## Handing scores to the pipeline

The MVP 2 profile consumes model scores through a one-column contract:

```
customer_id, activation_score
```

Write that file, then:

```bash
python -m pipeline.src.run --data-dir ./data --scores ./analytics/out/model_b_scores.csv
```

Without `--scores` the pipeline falls back to its own transparent logistic baseline and
labels the output `score_source = pipeline_baseline_logreg`, so the two can never be
confused. Supplying real scores also flips the band-distribution check from WARN to FAIL
— see [`docs/decision-log.md`](../docs/decision-log.md) §F7 for why that is deliberate.

## Running the quality suite

```bash
python analytics/quality/checks.py --data-dir ./data --out-dir ./pipeline/out
```

Groups: integrity, volume, nulls, ranges, target prevalence, censoring, leakage,
deduplication, funnel arithmetic, profile structure, holdout balance, guardrails.

Structural invariants fail the build. Distributional expectations that are specific to one
model's score distribution warn instead — the reasoning is in the module docstring, and it
matters because a suite that cries wolf is a suite people learn to ignore.
