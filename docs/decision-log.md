# Decision log

Every cleaning, exclusion and aggregation decision, with the reason and where it is
enforced. The brief requires this documented; it is also the file to read first when a
number here disagrees with a number somewhere else.

**Convention:** each entry states the decision, the reason, and the check that keeps it
true. A decision with no enforcing check is marked as such.

---

## A. Grain and deduplication

### A1 · A row is not a person

**Decision.** `onboarding_events` is collapsed to one row per `(customer_id,
step_number)` before anything counts anything. The aggregate must return 260,000 distinct
customers.

**Reason.** 1,386,264 event rows for 260,000 customers, 5.33 per customer, because
`selfie_liveness` allows retries. Joining events to customers before aggregating
duplicates customers in proportion to their attempt count — i.e. it duplicates exactly
the customers who struggled, who are the population every downstream question is about.

**Enforced by.** `state_machine.build_customer_step_view()` raises if any customer-step
lacks a terminal status or holds contradicting ones. `checks.py`: *step view covers every
customer*, *one row per customer-step*, *retries collapsed not dropped*
(`sum(attempts) == 1,386,264`).

### A2 · Final status per step is the last row in attempt order

**Decision.** Sort by `(customer_id, step_number, attempt_no, event_ts)` and take the last
status. `retry` can never be a step's final state.

**Reason.** `retry` is intermediate by definition. Any other selection rule can pick an
intermediate row and misclassify the step's outcome.

**Enforced by.** `build_customer_step_view()` raises on a non-terminal final status, and
separately raises if any customer-step carries both `completed` and `abandoned`. Zero
violations observed.

### A3 · Attempt counts are kept, not discarded

**Decision.** `attempts = MAX(attempt_no)` per customer-step is retained as a feature;
`selfie_attempts` and `n_retry_rows` reach the model.

**Reason.** Deduplication is about not miscounting customers, not about throwing away the
retry signal. It is one of the stronger activation features (permutation importance
0.0174): activation falls from 45.6% for first-attempt customers to 35.4% for those
needing three or more.

### A4 · Time on a step is a SUM across attempts

**Decision.** `total_ms_on_step = SUM(ms_on_step)`. Never MAX, never AVG.

**Reason.** `ms_on_step` is recorded per attempt, so the sum is the only aggregation that
answers "how long did this customer spend here". MAX or AVG would understate the
strugglers, who are the population of interest.

### A5 · Customer-level rates are canonical; attempt-level rates are operational

**Decision.** Every funnel figure, the North Star narrative and the pitch use the
customer-level stage abandonment rate. Step 4 is **34.16%**, not 22.28%.

**Reason.** 292,307 step-4 attempt rows map to 190,642 distinct customers. A customer who
succeeds on the third try contributes two non-abandoned rows, so the attempt-level rate
dilutes the very step where customers struggle most. It is wrong in the direction that
makes the problem look smaller. An earlier working figure of ~22–24% in this project came
from that denominator and is superseded.

**Enforced by.** `checks.py`: *attempt-level != customer-level at step 4* asserts the two
differ by more than 5pp, so the distinction cannot silently collapse.

### A6 · The funnel chain must be arithmetically closed

**Decision.** Customers completing stage *n* must be exactly the set entering stage *n+1*.
The builder raises otherwise.

**Reason.** Without this, reproducing the 44.3% baseline is a coincidence rather than
evidence that the deduplication is right. With it, the reproduction is a real check.

**Enforced by.** `build_funnel_table()`; `checks.py`: *cumulative reach reproduces the
44.3% baseline* → 44.305%.

---

## B. Target leakage

### B1 · Two columns are total leakage — verified, not assumed

**Decision.** `days_to_first_transaction` and `first_transaction_ts` are dropped before
any modelling.

**Reason.** Both are null **if and only if** `activated_30d` is false. They are only
populated after the outcome being predicted, so at the moment a prediction is actually
needed they are empty. Leaving either in produces a near-perfect offline score and a
worthless model.

**Verification, run against the files:**

| Check | Result |
|---|---|
| Activated but null timestamp | 0 |
| Not activated but has timestamp | 0 |
| Nulls in `days_to_first_transaction` | 209,976 |
| 260,000 − 50,024 activated | 209,976 ✓ |

**Enforced by.** `checks.py`: *`{col}` null iff not activated* for both columns, plus
*forbidden list covers both total-leak columns*. `features.feature_matrix()` raises if
either reaches the design matrix.

### B2 · Four more columns are constant in-population

**Decision.** `completed_onboarding`, `steps_completed`, `max_step_reached` and
`abandoned_at_step` are used as the population filter and then excluded as features.

**Reason.** A different exclusion reason from B1, and worth distinguishing. These carry
real information about the funnel, but MVP 2 scores only customers who completed
onboarding, and within that population each takes a single value: `completed_onboarding`
is always true, `steps_completed` and `max_step_reached` are always 7,
`abandoned_at_step` is always null. A constant carries no signal.

`completed_onboarding` and `steps_completed = 7` were confirmed to be the same fact — zero
discrepancies in either direction across 260,000 rows.

**Enforced by.** `checks.py`: *`completed_onboarding == (steps_completed == 7)`* and
*no activation without completing onboarding*.

### B3 · Six per-step attempt counters excluded as constants

**Decision.** `attempts_step_1/2/3/5/6/7` excluded.

**Reason.** All identically 1 — step 4 is the only step with retries. `n_event_rows` is
also excluded as a deterministic function of `n_retry_rows` in this data.

### B4 · Identifier and split column excluded as features, retained as metadata

**Decision.** `customer_id` and `signup_ts` are on the forbidden-features list, but remain
in the output table as key and provenance.

**Reason.** Neither carries signal and neither may be fitted on; both are needed to
identify a row and to reconstruct the temporal split. The quality check exempts exactly
these two by name rather than dropping them from the forbidden list, so the exemption is
visible instead of implicit.

### B5 · Per-stage leakage does not apply to MVP 2, but constrains any funnel model

**Decision.** Noted, not implemented here.

**Reason.** MVP 2 scores customers after the account exists, so all onboarding-behaviour
features are legitimately available. A funnel drop-off model is a different matter: it
must not use features derived from steps after the one being predicted, and would be
restricted to the signup-time block.

---

## C. Nulls

### C1 · The dataset README is wrong about nulls, and the files win

**Decision.** No imputation anywhere in the pipeline.

**Reason.** The README states two tables carry nulls by design. Counted directly:

| Table | Columns | Nulls |
|---|---|---|
| `customers` | 9 | **0** |
| `onboarding_events` | 10 | **0** |
| `activation_labels` | 6 | 209,976, in 2 columns only |

The 209,976 are **structural, not missing**: 260,000 − 50,024 activated = 209,976 exactly.
The field is empty because there was no transaction to put a date on. Both affected
columns are the B1 leakage columns and are dropped, so no imputation decision arises at
all.

This is a finding rather than a shortcut, and it holds because it came from counting the
files rather than reading the documentation.

**Enforced by.** `checks.py`: *`{table}` has zero nulls*, *labels nulls confined to
leakage columns*, *`{col}` nulls are structural* — the last asserts the arithmetic
identity, so a future load that contradicts it fails loudly.

### C2 · Imputers retained as a safeguard, inert here

**Decision.** Median and mode imputers stay in the preprocessing path.

**Reason.** They protect against future data that does carry nulls. On this dataset they
have nothing to act on. Stated explicitly so nobody reads their presence as evidence that
imputation happened.

### C3 · Had there been nulls, they would have been treated as signal

**Decision.** Recorded for completeness. A null appearing only among customers who
abandoned is information, not a hole; mean-imputing it destroys the signal. The decision
per column would have been indicator / own category / imputation / discard, with a stated
reason. Not applicable here — see C1.

---

## D. Population and validation

### D1 · MVP 2 scores only onboarding completers

**Decision.** 115,192 of 260,000 signups.

**Reason.** Activation is only evaluated among completers; the 144,808 who dropped out
never had the opportunity to transact and activate at exactly 0%. Training on all 260,000
would produce a model of funnel completion wearing an activation model's label.

Consequence for metrics: the positive class is 43.4% of this population, not 19.2%.

**Enforced by.** `checks.py`: *population is the completers only*, *no activation without
completing onboarding* (0 violations).

### D2 · The validation split is temporal, not random

**Decision.** Split on `signup_ts` at 2026-06-25; last ~20% held out. Train 91,818 at
43.575% activation, test 23,374 at 42.842%.

**Reason.** In production the model scores signups that have not happened yet. A random
split lets the model see the period it is evaluated on, which inflates the score without
improving the model.

The near-identical rates across the two periods confirm nothing drifts materially over the
window.

**Note on a discrepancy.** The BA model B notebook reports 92,153 / 23,039 at the same
cutoff date. The small difference comes from how the split boundary is applied, not from
different data. Both are reported where they were produced rather than silently
reconciled.

### D3 · No cohorts excluded for censoring

**Decision.** All six monthly cohorts retained.

**Reason.** A 30-day label cannot be complete for signups near the end of the observation
window, so this was checked rather than assumed. Monthly activation is flat: 19.37% in the
first month, 18.96% in the last, a 0.64pp spread. And 99.6% of activations occur before
day 28, so the 30-day window is not truncating anything.

**Enforced by.** `checks.py`: *monthly activation flat (no right-censoring)*.

### D4 · A baseline was established before the model

**Decision.** Two references, both computed before any model was fitted: the prevalence
constant (PR-AUC 0.4284) and a channel-average lookup (ROC-AUC 0.6221, PR-AUC 0.5162).

**Reason.** A model that cannot clearly beat a five-row lookup table does not justify its
complexity, and saying so out loud is worth more than hiding it. The full model reaches
0.6455 — better, but the channel lookup gets most of the way there alone.

---

## E. Features

### E1 · Scoring happens at account creation, which is what makes the behaviour block legal

**Decision.** Both feature blocks are permitted: signup-time (10 features) and
onboarding-behaviour (11 features).

**Reason.** MVP 2 scores customers at day 0, after the account exists, so selfie attempts
and per-step timings are already observable. A funnel drop-off model could use only the
signup block.

### E2 · App version is encoded ordinally, not one-hot

**Decision.** `app_version_ordinal`, plus an `is_legacy_version` flag at the ≤4.1.1 / ≥4.2.0
boundary.

**Reason.** The hypothesis under test is monotone — older is worse — and the legacy/current
boundary is where the observed effect sits (+23.8pp on Android). One-hot would discard the
ordering.

**Open.** The ≤4.1.1 cut is a working definition and still needs Engineering sign-off
against the real version-support policy.

### E3 · Steps 3 and 4 use log time; the rest use linear

**Decision.** `log_ms_step_3`, `log_ms_step_4`, `log_total_ms`; linear elsewhere.

**Reason.** Those two steps have heavy right tails — document upload and selfie both admit
long stalls — and the generator's own drop-off logic keys off a slow-document threshold.

### E4 · A perfect duplicate was kept, and must be read as one signal

**Decision.** `referred_by_customer` and `acquisition_channel = referral` both retained.

**Reason.** `referred_by_customer` is true for exactly the 25,447 customers whose channel
is `referral` and false for every other customer — a perfect duplicate. The models handle
collinearity, but permutation importance splits credit between them (0.0866 and 0.0329),
so the two must be read as **one signal, not two findings**. Reporting them as the top two
features separately would double-count.

**Recommended cleanup if this is taken further:** drop `referred_by_customer`. Not done
here, to keep the reported importances comparable to the BA notebook's.

---

## F. Activation profile for MVP 2

Full specification in [`activation-profile-spec.md`](activation-profile-spec.md).

### F1 · The profiles are a decision, not a discovery

**Decision.** Unsupervised clustering was rejected. Band boundaries are fixed at 0.369 and
0.500.

**Reason.** Predicted probabilities run from 0.190 to 0.732 with a median of 0.430, in a
single unimodal mass. There are no natural clusters to find. A k-means run would return
whatever number of groups it was asked for and present arbitrary boundaries as a discovery.
The cut points were placed at low-density dips instead, so that a small change in a score
does not flip a customer between bands.

### F2 · Fixed thresholds, not population terciles

**Decision.** Absolute probability thresholds from config.

**Reason.** Under terciles a customer's band depends on the composition of the scoring
batch, so the same customer can change bands without changing. Fixed thresholds keep
assignment reproducible and auditable across runs, which both the experiment and the cost
guardrail require.

### F3 · The profile is two-dimensional: band × timing state

**Decision.** Risk band crossed with `PRE_WINDOW` / `IN_WINDOW` / `LATE` / `DORMANT`.

**Reason.** A band alone does not determine what to do. The intervention window is days
3–10: before day 3 organic activation is still peaking (the mode is days 2–3), and after
day 14 only 8.4% of activations remain. The timing axis is shared across all bands because
the shape of the timing curve is segment-invariant — week-one share is 65.0% for referral
against 64.8% for paid social, despite a 29-point gap in overall rates. Only the level
differs, never the pace.

### F4 · Channel dominance is disclosed, not hidden

**Decision.** Stated in the spec, the metrics doc and the pitch.

**Reason.** The bands are close to an acquisition-channel sort: paid social runs 63.1% →
5.8% → 0.0% across them, referral 0.0% → 6.9% → 67.1%. The claimed gain over a channel
lookup is a continuous, budget-cuttable ranking — not the discovery of a hidden segment.
Overclaiming here is the easiest way to lose credibility with anyone who checks.

### F5 · The holdout is deterministic and stratified

**Decision.** 20%, from `sha256(customer_id) mod 100 < 20`, spread evenly across all three
bands.

**Reason.** A runtime random draw would put the same customer in different arms on
different runs and could not be reconstructed for analysis. Spreading the control across
all three bands is what makes the push effect readable *within* each band; running an
incentive arm in `LOWER` without a push-only comparison inside `LOWER` would confound the
two treatments.

**Enforced by.** `checks.py`: *overall share on target* (20.12%), plus per-band shares
(19.89% / 20.36% / 20.08%), and *every holdout customer is suppressed*.

### F6 · The do-no-harm guardrail is enforced by the pipeline, not by the policy layer

**Decision.** `UPPER` receives no paid incentive under any timing state; the rule lives in
config and is asserted in the check suite.

**Reason.** The PRD requires that customers with high natural propensity are not
unnecessarily interrupted or incentivised. Leaving that to the consuming service to
remember is how guardrails quietly stop holding.

**Enforced by.** `checks.py`: *no incentive reaches `['UPPER']`* → 0 breaches; *every
suppression carries a reason*.

### F7 · Band-distribution checks fail only for the signed-off model

**Decision.** The expected 33.4 / 38.5 / 28.1 split is a FAIL when scores come from the BA
model and a WARN when they come from the pipeline's own baseline scorer.

**Reason.** That distribution is a property of one specific model's score distribution.
The pipeline baseline produces 33.13 / 35.24 / 31.63 at the same thresholds — a real
difference, and enforcing the model's expectation against a different scorer would be
testing the wrong thing while teaching the team to ignore a red check. Structural
invariants stay unconditional FAILs.

The current evidence run therefore shows 49 passed, 2 warnings, 0 failures, and the two
warnings are this rule working as intended.

### F8 · Band monotonicity is an unconditional failure

**Decision.** Observed activation must increase strictly `LOWER` < `MIDDLE` < `UPPER`.

**Reason.** If it does not, the bands are not a ranking and nothing built on them means
anything. Observed on the evidence run: 29.51% / 43.22% / 58.23%.

---

## G. Data handling and reproducibility

### G1 · Source data is not committed

**Decision.** `/data/` is gitignored.

**Reason.** 22 MB of synthetic Parquet that anyone can reproduce exactly from the seeded
generator. Committing it adds weight and invites a stale copy diverging from the
generator.

### G2 · The generator is a legitimate third source tier

**Decision.** `io.load_tables()` resolves Parquet, then CSV, then rebuilding in memory
from `gen_d1_onboarding.py`.

**Reason.** The pipeline must be runnable on a machine without a Parquet engine — not
hypothetical, since the execution evidence in this repository was produced that way. Sound
because the generator is seeded (SEED = 20260806) and the regenerated tables were diffed
against the delivered sample CSVs: 1,000 rows per table, every column identical, and the
regenerated data reproduces 260,000 / 1,386,264 / 44.305% / 19.240% exactly.

The tier actually used is written into `run_manifest.json`, so any published figure is
traceable to its source.

### G3 · Generated views are not committed; execution evidence is

**Decision.** `/pipeline/out/` gitignored. `pipeline/evidence/` and
`analytics/quality/evidence/` explicitly un-ignored, overriding the repository's blanket
`*.log` rule.

**Reason.** Views are a function of source plus code and would only drift. The run logs
are the deliverable the brief asks for, and they are captured stdout, not transcriptions.

### G4 · Persisted values are rounded, and no per-customer rows are committed

**Decision.** Floats are rounded to six decimals and timestamps truncated to whole seconds
before any view is written (`io.WRITE_PRECISION`). No per-customer sample of
`customer_activation_profile` is committed.

**Reason.** Two reasons, one practical and one about what belongs in a repository.

Full float64 repr writes a rate as seventeen significant decimals. Strip the decimal point
and that is a seventeen-digit numeric run, which the commit-time DLP hook reads as a
possible card or phone number and blocks. The flags are false positives on synthetic data,
but the fix is to stop emitting the pattern rather than to register an exemption — six
decimals is already more precision than any figure in this project is quoted to, and
nothing downstream reads more. Nanosecond timestamp fractions were removed for the same
reason and carry no information here.

Separately, rows of `customer_activation_profile` are one record per customer. Even
synthetic, a file of per-customer records with a risk score attached is the wrong artifact
to keep in git. The band sizes, treatment split and observed activation rates in
`activation_profile_band_summary.csv` and the run log are the evidence that matters, and
the full table regenerates in about ten seconds.

### G5 · Thresholds are versioned into every output row

**Decision.** Every row of `customer_activation_profile` carries `thresholds_version`,
`model_version`, `score_source` and `scored_at`.

**Reason.** An experiment result must be attributable to the exact policy in force when
the row was written. Without the stamp, a mid-experiment config change is undetectable
after the fact.

---

## H. Open items owned by Engineering

| Item | Why it matters | Status |
|---|---|---|
| Post-account-created event schema | The event data stops at account creation, so MVP 2 has no first-use signals to react to. Largest gap in the whole project | **Open** — data gap, not implementation |
| Legacy-version threshold ≤4.1.1 | Currently a working definition | Partially resolved — effect confirmed Android-specific; the cut needs sign-off against real version-support policy |
| Daily scoring cadence | Current runner is a single-snapshot batch | Open — timing state is the only field that changes between runs |
| Scores contract with BA | `--scores` expects `customer_id` + `activation_score` | Agreed, pending the notebook writing that file |

---

## I. Superseded

| Figure | Superseded by | Note |
|---|---|---|
| Selfie abandonment ~22–24% | **34.16%** (A5) | Attempt-level denominator; retained only as an operational retry metric |
| Selfie fix worth +2.62 North Star points | **+2.13 to +2.75** | Corrected downward: rescued customers activate at 35.4%–45.6%, not the 43.4% average. See `metrics.md` §5 |
