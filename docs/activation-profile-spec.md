# Activation Profile Specification — MVP 2

**First-Use Activation Coach · segment definition and targeting contract**

| | |
|---|---|
| **Owner** | Engineering |
| **Consumers** | Product (policy layer), BA (measurement) |
| **Status** | Implemented in `pipeline/src/activation_profile.py`, executed — see `pipeline/evidence/` |
| **Implements** | PRD §5.2, MVP Solution B |
| **Config** | [`config/activation_profile.yaml`](../config/activation_profile.yaml) v1.0.0 |
| **Upstream model** | Model B — 30-day activation propensity |
| **Data** | 100% synthetic. No real customer data. |

---

## 1. Purpose

MVP 2 does not show the activation coach to everyone who opens an account. The PRD
requires a three-way treatment policy:

- **High propensity** → suppress or minimise contact (do-no-harm guardrail)
- **Medium propensity** → prioritise; the best efficiency opportunity
- **Low propensity** → include only where a relevant, low-cost action exists

This document defines those groups as an engineering artifact: the population they are
drawn from, the inputs used, where the boundaries sit, how the assignment is persisted,
and what the consuming policy layer may read. It is the contract between the scoring model
and the coach.

## 2. This is not a clustering discovery

The word "profile" must not be read as a request to run an unsupervised clustering
algorithm over the customer base. Two reasons, both empirical.

**The distribution has no natural clusters.** Predicted probabilities for the 115,192
scored customers form a single unimodal mass — range 0.190 to 0.732, median 0.430 for the
BA model. There is no separation to find. A k-means run would return whatever number of
groups it was asked for and present arbitrary boundaries as a discovery.

**The bands are close to an acquisition-channel sort.** Channel composition within each
band:

| Band | affiliate | organic | paid_search | paid_social | referral |
|---|---|---|---|---|---|
| `LOWER` | 13.6% | 4.2% | 19.1% | **63.1%** | 0.0% |
| `MIDDLE` | 5.5% | 54.9% | 26.9% | 5.8% | 6.9% |
| `UPPER` | 0.0% | 32.2% | 0.8% | 0.0% | **67.1%** |

Paid social runs 63.1% → 5.8% → 0.0%. Referral runs 0.0% → 6.9% → 67.1%. A channel-average
lookup on its own reaches ROC-AUC 0.622 against the full model's 0.644.

**Therefore:** the cut points here are a **documented business decision, not a statistical
finding**. What the model contributes over a five-bucket channel lookup is a continuous
per-customer score that can be ranked and cut at an arbitrary budget line. That is a real
operational gain and is the only claim made.

## 3. Population and eligibility

### 3.1 Scored population

One row per `customer_id` where `completed_onboarding = true` — **115,192** of 260,000
signups.

The 144,808 who did not complete onboarding are **excluded from scoring entirely**. Their
observed activation rate is exactly zero: with no account there is nothing to transact
with. Including them would produce a model of funnel completion wearing an activation
model's label. Funnel drop-off is MVP 1's problem.

### 3.2 Coach eligibility

```
completed_onboarding = true
AND first transaction has not occurred
AND 3 <= days_since_account_created <= 14
AND holdout_flag = false
AND no suppression rule fires (§6)
```

Eligible pool at the label horizon: **65,168** customers completed onboarding without
activating within 30 days — 56.57% of completers.

### 3.3 Scoring timestamp

Customers are scored at **day 0, account creation**. Every feature in §4 is observable
then. The score is fixed for the life of the treatment window; what changes between daily
runs is the *timing state* (§5.2), not the score.

## 4. Inputs

### 4.1 Features used (21)

**Signup-time (10)** — `age`, `signup_hour`, `signup_dow`, `acquisition_channel`,
`device_os`, `app_version`, `app_version_ordinal`, `state`, `prev_bank_relationship`,
`referred_by_customer`

**Onboarding behaviour (11)** — `selfie_attempts`, `n_retry_rows`, `wall_clock_seconds`,
`total_ms_step_1`, `total_ms_step_2`, `total_ms_step_5`, `total_ms_step_6`,
`total_ms_step_7`, `log_ms_step_3`, `log_ms_step_4`, `log_total_ms`

Both blocks are permissible because scoring happens after the account exists. A funnel
drop-off model could use only the first.

### 4.2 Columns excluded — hard constraint

| Column | Reason |
|---|---|
| `days_to_first_transaction` | Total leakage — null iff `activated_30d` is false |
| `first_transaction_ts` | Total leakage — same relationship |
| `completed_onboarding` | Population filter; constant in-population |
| `steps_completed` | Same fact as above; equals 7 for all in-population |
| `max_step_reached` | Constant 7 in-population |
| `abandoned_at_step` | Constant null in-population |
| `attempts_step_1/2/3/5/6/7` | Constant 1 — only step 4 has retries |
| `n_event_rows` | Deterministic function of `n_retry_rows` |
| `customer_id` | Identifier — key in the output, never a feature |
| `signup_ts` | Builds the temporal split — never a feature |
| `first_event_ts`, `last_event_ts` | Captured by `wall_clock_seconds` |

Leakage was verified against the files, not assumed: 209,976 nulls matching 260,000 −
50,024 activated exactly, and zero contradictions in either direction.
`features.feature_matrix()` raises if any of these reaches the design matrix, and
`checks.py` asserts none reaches the output table.

### 4.3 Known redundancy

`referred_by_customer` is true for exactly the 25,447 customers with
`acquisition_channel = referral` and false for every other customer — a perfect duplicate.
Both are retained because the models handle collinearity, but permutation importance
splits credit between them (0.0866 and 0.0329) and they must be read as **one signal, not
two**. Recommended cleanup if taken further: drop `referred_by_customer`.

### 4.4 Event aggregation

`onboarding_events` holds 1,386,264 rows for 260,000 customers because `selfie_liveness`
allows retries. The table is aggregated to `customer_id` grain **before** any join —
joining directly would duplicate exactly the customers who struggled most. Asserted: the
aggregate covers all 260,000 customers and `sum(attempts)` equals the original 1,386,264
rows, so retries are collapsed rather than dropped.

`ms_on_step` is per attempt, so per-step time is a **SUM**. MAX or AVG would understate
the strugglers.

## 5. Profile definition

The profile is **two-dimensional**: a fixed risk band crossed with a timing state. Either
axis alone is insufficient.

### 5.1 Axis 1 — risk band

Fixed probability thresholds from config: **0.369** and **0.500**.

| Band | Score range | Share | Observed activation | vs. average |
|---|---|---|---|---|
| `LOWER` | p < 0.369 | 33.4% | 29.5% | 0.69× |
| `MIDDLE` | 0.369 ≤ p < 0.500 | 38.5% | 43.1% | 1.01× |
| `UPPER` | p ≥ 0.500 | 28.1% | 58.4% | 1.36× |

Measured on the BA model's 23,039-customer temporal test set. Applied to the full 115,192
population that is roughly 38,500 / 44,300 / 32,400 customers.

Supporting attributes:

| Band | Banked before | Avg age | Selfie attempts |
|---|---|---|---|
| `LOWER` | 30.0% | 31.7 | 1.55 |
| `MIDDLE` | 42.8% | 33.7 | 1.45 |
| `UPPER` | 61.8% | 34.4 | 1.23 |

Prior banking relationship (30.0% → 61.8%) and onboarding friction (1.55 → 1.23) add real
signal on top of channel. **Device adds nothing** — Android sits at 67–70% across all
three bands. Whatever legacy app versions do to funnel completion does not carry through
to activation, which is useful: MVP 1 and MVP 2 are independent on this axis, so their
benefits cannot be double-counted.

**Why fixed thresholds and not population terciles.** Under terciles a customer's band
depends on who else was in the scoring batch, so the same customer can move bands without
changing. Fixed thresholds make assignment reproducible and auditable across runs, which
both the experiment and the cost guardrail require.

**Why 0.369 and 0.500.** Both sit at low-density dips in the predicted-probability
distribution, so a small change in a score does not flip a customer between bands. The
resulting 33.4 / 38.5 / 28.1 split is a consequence of that choice, not a target.

### 5.2 Axis 2 — timing state

Among customers who activate: median day 5; peak on days 2–3 at ~11% each against 6.6% on
day 0; 70.9% by day 7; 93.3% by day 14; 99.6% by day 28.

The **shape** of this curve is near-identical in every segment examined — week-one share
65.0% for referral against 64.8% for paid social, despite a 29-point gap in overall rates.
Only the level changes, never the pace. So the timing axis is shared across bands and
needs no per-segment tuning.

| State | Definition | Rationale |
|---|---|---|
| `PRE_WINDOW` | days 0–2, not activated | Organic activation is still peaking. Contacting here spends budget on customers about to activate anyway |
| `IN_WINDOW` | days 3–10, not activated | The intervention window. Self-serve activation has fallen off; the account is not yet dead |
| `LATE` | days 11–14, not activated | Last useful attempt |
| `DORMANT` | day 15+, not activated | Only 8.4% of activations occur after day 14. Treat as dead for MVP purposes |

The 30-day label window is not the binding constraint — 99.6% of activations land before
day 28.

### 5.3 Treatment matrix

| | `PRE_WINDOW` (0–2) | `IN_WINDOW` (3–10) | `LATE` (11–14) | `DORMANT` (15+) |
|---|---|---|---|---|
| `UPPER` | no contact | no contact | neutral coach, no incentive | no contact |
| `MIDDLE` | no contact | **coach — primary target** | coach | no contact |
| `LOWER` | no contact | coach + incentive arm | coach + incentive arm | no contact |

`UPPER` receives no paid incentive under any state. That is the PRD's do-no-harm guardrail
expressed as a rule the pipeline enforces, not a principle the consuming service is
trusted to remember.

**Sizing the primary cell.** `MIDDLE` × `IN_WINDOW` is the primary MVP 2 population. The
window is a rolling 8 days of account creations, so at steady state after the 20% holdout
that is roughly **1,435 customers on any given day at sample scale**, or **~12,000 at real
Nu Mexico inflow** (12,000 signups/day against this dataset's 1,436/day, ratio 8.35×).

A single-date backfill is not the right way to read this: on one `as_of` date most accounts
are already past day 14, so 91.8% land in `DORMANT`. That is correct behaviour for a
snapshot and misleading as a sizing figure. The pipeline reports both and labels which is
which.

### 5.4 Budget cut — the ranked list

Bands answer *what treatment*; the score decile answers *how many can we afford*. Both are
persisted so Product can cut the list at a budget line rather than at a band boundary.

| Decile | Mean predicted | Observed | Lift | Cum. share of non-activators |
|---|---|---|---|---|
| 0 (lowest) | 0.239 | 0.238 | 0.56 | 13.3% |
| 1 | 0.295 | 0.282 | 0.66 | 25.9% |
| 2 | 0.338 | 0.319 | 0.74 | **37.8%** |
| 3 | 0.374 | 0.371 | 0.87 | 48.8% |
| 4 | 0.411 | 0.408 | 0.95 | 59.2% |
| 5 | 0.449 | 0.451 | 1.05 | 68.8% |
| 6 | 0.485 | 0.484 | 1.13 | 77.8% |
| 7 | 0.528 | 0.525 | 1.22 | 86.1% |
| 8 | 0.574 | 0.555 | 1.30 | 93.9% |
| 9 (highest) | 0.645 | 0.651 | 1.52 | 100.0% |

**Targeting the lowest-scoring 30% reaches 37.8% of everyone who fails to activate.** That
is the number that converts into cost per incremental activation.

The scores are usable in arithmetic because they are calibrated — predicted and observed
track closely in all ten deciles. Calibration matters more than ranking here precisely
because these scores feed a budget calculation: systematically overconfident probabilities
would inflate any business case built on them.

## 6. Suppression rules

Evaluated after band and state assignment, in order of severity — the first rule that fires
wins, so the logged reason is the binding one rather than an arbitrary pick.

| Order | Rule | Condition | Reason code |
|---|---|---|---|
| 1 | Holdout | `holdout_flag = true` | `HOLDOUT` |
| 2 | Outside the window | `timing_state` in {`PRE_WINDOW`, `DORMANT`} | `TIMING_STATE` |
| 3 | Do-no-harm | `band = UPPER` and treatment carries an incentive | `DO_NO_HARM_NO_INCENTIVE_FOR_BAND` |
| 4 | Policy | matrix cell is `NO_CONTACT` | `NO_CONTACT_BY_POLICY` |
| — | Frequency cap | exposed within the last 72 hours | config, runtime |
| — | Lifetime cap | three exposures reached | config, runtime |
| — | Complaint signal | two dismissals, or an active support contact | config, runtime |

The last three are runtime rules for the consuming service; the thresholds live in config
so the service does not carry its own copy.

## 7. Persisted contract

### 7.1 `customer_activation_profile`

One row per `customer_id` per scoring run. 115,192 rows on the evidence run.

| Column | Type | Notes |
|---|---|---|
| `customer_id` | int64 | Primary key within a run |
| `activation_score` | double | Calibrated probability |
| `score_decile` | int | 0–9, 0 = lowest |
| `profile_band` | string | `LOWER` / `MIDDLE` / `UPPER` |
| `days_since_account_created` | int | Recomputed each run |
| `timing_state` | string | `PRE_WINDOW` / `IN_WINDOW` / `LATE` / `DORMANT` |
| `treatment_state` | string | `NO_CONTACT` / `COACH` / `COACH_INCENTIVE` / `SUPPRESSED` |
| `suppression_reason` | string | Null unless `SUPPRESSED` |
| `holdout_flag` | bool | Deterministic, see §7.3 |
| `score_source` | string | `supplied:<file>` or `pipeline_baseline_logreg` |
| `model_version` | string | Scoring model version |
| `thresholds_version` | string | Version of the config that produced the row |
| `scored_at` | timestamp | Run timestamp, UTC |

### 7.2 What the policy layer may read

`profile_band`, `timing_state`, `treatment_state`, `score_decile`, `holdout_flag`.

It must **not** read `activation_score` to invent its own thresholds, and must **not**
reach past this table into the labels tables. Every threshold lives in config, so changing
targeting is a versioned config change rather than a code change — and every row carries
the `thresholds_version` in force when it was written, so an experiment result can always
be tied to the exact policy that produced it.

### 7.3 Holdout assignment

```
holdout_flag = (sha256(customer_id) mod 100) < 20
```

Deterministic, not a runtime random draw: the same customer must land in the same arm on
every run, and the assignment must be reconstructible afterwards for analysis.

The 20% is drawn **evenly from all three bands**, not concentrated in one. This is what
makes the push effect readable *within* each band. Running an incentive arm in `LOWER`
without a push-only comparison inside `LOWER` would confound the two treatments and make
the result uninterpretable.

Observed on the evidence run: 20.12% overall, 19.89% / 20.36% / 20.08% by band.

## 8. Quality checks

`analytics/quality/checks.py` — 51 controls, non-zero exit on failure. Latest captured run:
**49 passed, 2 warnings, 0 failures**.

Structural invariants are unconditional failures; distributional expectations are
conditional. That distinction is deliberate.

| Check | Expectation | Severity |
|---|---|---|
| `customer_id` unique | one row per customer | FAIL |
| Population is completers only | 115,192 of 260,000 | FAIL |
| Score in [0, 1], no nulls | — | FAIL |
| No forbidden column in the output | excluding the key and split column | FAIL |
| `thresholds_version` stamped | matches the config in use | FAIL |
| **Band monotonicity** | activation strictly `LOWER` < `MIDDLE` < `UPPER` | FAIL |
| Band distribution | within ±2pp of 33.4 / 38.5 / 28.1 | FAIL for the BA model, **WARN** for the baseline scorer |
| Holdout share | 20% ±1pp, overall and per band | FAIL |
| Do-no-harm | no incentive reaches `UPPER` | FAIL |
| Every holdout customer suppressed | — | FAIL |
| Every suppression carries a reason | — | FAIL |

**Why the band-distribution check is conditional.** The 33.4 / 38.5 / 28.1 split is a
property of the BA model's specific score distribution. The pipeline's own baseline scorer
produces 33.13 / 35.24 / 31.63 at the same thresholds — a real difference. Enforcing one
model's expectation against a different scorer would be testing the wrong thing while
teaching the team to ignore a red check. The two warnings on the current evidence run are
this rule working as designed, and they name the reason inline.

Band monotonicity, by contrast, is never conditional: if activation does not increase
across the bands then they are not a ranking and nothing built on them means anything.
Observed: 29.51% / 43.22% / 58.23%.

## 9. Limitations

**Propensity is not uplift.** This ranks customers by *who will activate*, not by *who
will activate because they were contacted*. The dataset has no treatment variation, so
incremental effect is not estimable from it. Assigning a paid incentive to `LOWER` is a
**hypothesis to be tested**, not a finding. A true uplift model is a later evolution, only
possible once randomised treatment data exists.

**The model is weak in absolute terms.** ROC-AUC 0.644. The output is a ranking aid, not a
precise individual prediction.

**Most of the signal is one variable.** The channel lookup gets most of the way there
alone. Acquisition channel predicts *both* factors of the North Star — funnel completion
and post-account activation — which is why changing the channel mix would move both terms
at once in a way no funnel fix does. That remains a strategic follow-up, not an MVP,
because it needs CAC, LTV and channel-quality data this project does not have.

**The mechanism is unknown.** No card-delivery, account-funding, app-open, communications
or declined-transaction events exist in the data. Any statement about *why* a customer
fails to activate would be invented. What the data does support: the timing curve's shape
is identical across segments while its level differs by up to 29 points, which suggests
the difference is settled before day 1 — a property of who the customer is, not of
something that happens during the month. That is enough to justify predicting from signup
who will need help, which is the personalisation the brief asks for.

**Synthetic data.** Relationships hold within this dataset only.

## 10. Recommended next step

An A/B test designed so the two questions can be answered separately:

| Arm | Population | Measures |
|---|---|---|
| Control | ~20%, sampled evenly from all three bands | Baseline rate per band |
| Push only | Remainder of all three bands | Push effect, per band |
| Push + offer | Part of `LOWER` | Offer effect on top of push |

Spreading the control across all three bands is what makes the push effect readable within
each one. Cost per activated customer and the do-no-harm guardrail are to be evaluated on
test results, not on assumed uplift. If an incentive arm is approved, it should be
structured as **pay-on-conversion** rather than pay-on-exposure, so cost per incremental
activated customer stays close to the incentive's face value by construction instead of
being diluted across everyone exposed.
