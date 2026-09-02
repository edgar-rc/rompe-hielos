# Uso de IA

Required disclosure. Two distinct things are covered here and they should not be
confused: **AI used as a tool to build the submission**, and **AI as a component of the
product being proposed**.

---

## Part 1 — AI as a tool in building this submission

### Where it was used

| Area | Use | Human verification |
|---|---|---|
| Pipeline code | Drafting the state machine, feature builder, profile assignment and quality suite in `pipeline/` and `analytics/quality/` | Executed end to end; the funnel output was reconciled row by row against the labels table with 0 disagreements |
| Model B notebook | Designing the population definition, leakage verification, event aggregation, temporal split and calibration reporting | Notebook executed by the team; one Spark type-cast fix applied by hand; output reviewed by BA |
| Documentation | Drafting `architecture.md`, `metrics.md`, `decision-log.md`, `activation-profile-spec.md` | Every figure recomputed by the pipeline and cross-checked against the brief's stated baselines |
| PRD and analysis narrative | Structuring the problem, alternatives comparison and impact scenarios | Reviewed by Product; scenario figures explicitly labelled as scenarios, not forecasts |

### Where it was not used

**No figure in this repository originates from a language model.** Every number was
computed from the source tables by code in this repository, or by the BA notebook, and is
reproducible by re-running it. The execution evidence in `pipeline/evidence/` is captured
stdout, not transcribed output.

Three claims were checked specifically because a plausible-sounding wrong answer was easy
to produce, and all three were confirmed against the files rather than accepted:

- **Leakage.** `days_to_first_transaction` and `first_transaction_ts` are null if and only
  if `activated_30d` is false — 0 violations in both directions across 260,000 rows,
  209,976 nulls matching 260,000 − 50,024 exactly.
- **The nulls claim in the dataset README.** The README states two tables carry nulls by
  design. Counted directly, `customers` and `onboarding_events` have zero. The files win.
- **The funnel baseline.** The pipeline reproduces 44.305% completion and 19.240%
  activation, and the stage table matches customer for customer at every step.

### Verification standard applied

Anything a model asserted about *this dataset* was treated as a hypothesis until a script
confirmed it. That is the reason `analytics/quality/checks.py` exists as an executable
suite that exits non-zero rather than as a list of assertions in prose: a claim that
cannot fail a build is not a verified claim.

Two places where that standard changed a conclusion:

1. The initial selfie abandonment figure of ~22–24% was computed at the attempt level and
   is wrong for business purposes. The customer-level figure is 34.16%. See
   `decision-log.md` §A5.
2. The initial impact estimate for the selfie fix (+2.62 North Star points) assumed
   rescued customers would activate at the population average. They will not — customers
   who struggle activate at 35.4% against 45.6% for those who pass first time — so the
   honest figure is a range of +2.13 to +2.75. See `metrics.md` §5.

Both corrections went the direction that makes our own proposal look weaker, which is the
point of doing the check.

---

## Part 2 — AI as a component of the proposed product

The brief asks for an AI layer on an existing flow, so where AI does and does not make the
decision matters.

### MVP 1 — Selfie Recovery, version-aware update nudge

**AI does not make the eligibility decision.** Eligibility is a deterministic rule on
device OS and app version: Android, legacy version, before the camera opens. That is
auditable, testable and explainable in one sentence.

AI supports the surrounding work only — concise copy variants, localisation and tone,
explaining why the intervention fired, and analysing the experiment. The decision itself
stays deterministic.

### MVP 2 — First-Use Activation Coach

Five layers, and AI appears in two of them:

| Layer | Mechanism | AI? |
|---|---|---|
| Eligibility | Deterministic: completed onboarding, not yet activated, inside the day 3–10 window | No |
| Propensity | Gradient boosting model producing a calibrated score | Yes — a model, not a language model |
| Policy | Deterministic band × timing matrix from versioned config | No |
| Generation | Short guidance message from an approved content set | Yes — constrained |
| Measurement | Event tracking | No |

**Constraints on the generation layer.** The model must not invent products, benefits,
deadlines, fees or eligibility conditions. It operates inside a constrained template or
retrieval-based content set, and every treatment decision and generated message is logged.
Where signals are ambiguous, the product uses a neutral recommendation or asks the customer
to choose rather than guessing at their motivation.

**The propensity model must not be oversold.** ROC-AUC 0.644 is modest, and a channel
lookup alone reaches 0.622 — most of what the model knows is the acquisition channel. What
it adds is a continuous ranked list that can be cut at a budget line. And propensity is not
uplift: with no treatment variation in the data, incremental effect is not estimable, so
assigning an incentive to any band is a hypothesis to be tested rather than a finding.

### Data handling

100% synthetic data throughout. No real Nu customer information was involved at any point.
For a production version, no raw identity documents or biometric data would be sent to a
model; only derived features necessary for the decision.
