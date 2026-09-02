# quality

Executable data-quality controls. 51 checks, non-zero exit on failure, so this runs in CI.

```bash
python analytics/quality/checks.py --data-dir ./data --out-dir ./pipeline/out
```

Latest captured run: **49 passed, 2 warnings, 0 failures** —
[`evidence/quality_report.txt`](evidence/quality_report.txt).

---

## Severity is a deliberate choice

A suite where everything is a failure teaches people to ignore red. So each check is
either a build failure or a warning, on purpose:

**FAIL — structural invariants.** Uniqueness, referential integrity, leakage, funnel
arithmetic, band monotonicity, holdout balance, the do-no-harm guardrail. If any of these
breaks, every number downstream is wrong.

**WARN — expectations tied to one model's score distribution.** The expected band split of
33.4 / 38.5 / 28.1 is a property of the BA model's scores. It fails the build when scores
come from that model (`score_source` starts with `supplied`), and warns when they come
from the pipeline's own baseline scorer, which produces 33.13 / 35.24 / 31.63 at the same
thresholds. Enforcing one model's expectation against a different scorer would be testing
the wrong thing.

The two warnings on the current evidence run are that rule working as intended, and they
name the reason inline.

## Groups

| Group | Checks |
|---|---|
| `integrity` | Key uniqueness in all three tables, foreign keys, 1:1 between customers and labels |
| `volume` | Row counts against the 260,000-customer configuration |
| `nulls` | Zero nulls in `customers` and `onboarding_events`; the 209,976 in `activation_labels` confined to the two leakage columns and matching `260,000 − 50,024` exactly |
| `ranges` | Signup window, age bounds, step numbers, non-negative durations, the status vocabulary |
| `target` | Completion ≈ 44.3%, activation ≈ 19.2% |
| `censoring` | Monthly activation flat — 0.64pp spread across six months |
| `leakage` | Both leak columns null iff not activated; no activation without completion; `completed_onboarding == (steps_completed == 7)` |
| `dedup` | Step view covers every customer, one row per customer-step, `sum(attempts)` equals the original event count, and the attempt-level rate differs from the customer-level rate at step 4 |
| `funnel` | Step 4 at 34.16%, largest drop is step 4, cumulative reach reproduces 44.3%, state machine agrees with the labels table |
| `profile` | Key uniqueness, population is completers only, score bounds, no forbidden column, version stamped, band monotonicity, band distribution |
| `holdout` | 20% ±1pp overall and within each band |
| `guardrail` | No incentive reaches `UPPER`, every holdout customer suppressed, every suppression carries a reason |

## Nulls are interrogated, not filled

The dataset README claims two tables carry nulls by design. Counted directly, `customers`
(9 columns) and `onboarding_events` (10 columns) have **zero**. The only nulls anywhere are
209,976 in the two leakage columns of `activation_labels`, and those are structural — the
field is empty because no transaction occurred, and `260,000 − 50,024 activated = 209,976`
exactly.

Both affected columns are dropped as leakage, so **no imputation decision arises at all.**
That is a finding, not a shortcut, and it holds because it came from counting the files
rather than reading the documentation. The check asserts the arithmetic identity, so a
future load that contradicts it fails loudly instead of being quietly imputed over.

Had there been real nulls, the treatment would not have been mean imputation. A null
appearing only among customers who abandoned is information; imputing it destroys the
signal. Full reasoning: [`docs/decision-log.md`](../../docs/decision-log.md) §C.
