# pitch

**Owner:** Product — Lara Sproesser

Presentation material. Placeholder — needs the deck committed before submission.

---

## To do

- [ ] Commit the deck (PDF or exported slides) into this folder
- [ ] Check every figure against [`docs/metrics.md`](../docs/metrics.md), which is
      reproduced from the data on every pipeline run

## Demo narrative, as agreed

1. The seven-step funnel — step 4 `selfie_liveness` is the largest drop at 34.16% stage
   abandonment, 65,129 customers, 44.98% of all abandonment
2. Filter to paid social + Android + legacy versions — 68.47% vs. 36.16%
3. Show that the same version gap does **not** exist on iOS (28.23% vs. 25.46%), which is
   what justifies scoping MVP 1 to Android
4. The version-aware update nudge, before the camera opens
5. The event log: exposure, update click, return, selfie completion
6. Switch to a customer who completed onboarding but has not transacted
7. The propensity band, the suppression decision, the selected next-best action
8. The constrained AI message, generated from an approved playbook
9. Control vs. treatment metrics and the cost-per-incremental-activation guardrail,
   including the pay-on-conversion mechanic
10. State plainly what is measured, what is a scenario, and what still depends on
    validation

## Three things that will win more credit than an extra chart

**The second leak is the same size as the first.** 115,192 customers open an account and
65,168 of them never transact — 56.6%. And because exactly zero of the 144,808
non-completers activate, the two leaks multiply rather than add. Most teams will pitch the
funnel fix alone.

**Correct your own number out loud.** The selfie fix was estimated at +2.62 North Star
points. The honest figure is **+2.13 to +2.75**, because customers who struggle in
onboarding activate at 35.4% against 45.6% for those who pass first time — so the people
we would rescue are precisely the ones least likely to use the account. The range cannot
be closed with these data: if struggling *causes* abandonment they move to 45.6%; if it is
a *symptom* of low intent they stay at 35.4%.

This is the first question anyone competent will ask. Arriving with the bounded range and
the reason it cannot be narrowed is worth more than a single tidy number.

**Say what the model does not know.** ROC-AUC 0.644, and a plain channel-average lookup
reaches 0.622 — most of what the model knows is the acquisition channel. What it adds is a
continuous ranked list that can be cut at a budget line. And propensity is not uplift:
with no treatment variation in the data, "give the low band a discount" is a hypothesis,
not a finding.

The dataset also has no card-delivery, funding, app-open or declined-transaction events,
so **why** customers do not activate is unanswerable here. Listing what is missing is
stronger than inventing a mechanism, and there is one real clue to offer: the timing curve
has the same shape in every segment while its level varies by 29 points, which suggests
the outcome is settled before day 1.

## Numbers most likely to be challenged

| Claim | Where it is defended |
|---|---|
| Selfie abandonment is 34.16%, not 22% | [`docs/metrics.md`](../docs/metrics.md) §2 — denominator, customer level vs. attempt level |
| No target leakage in the model | [`docs/decision-log.md`](../docs/decision-log.md) §B — verified with counts, 0 violations |
| Three propensity bands | [`docs/activation-profile-spec.md`](../docs/activation-profile-spec.md) §2 — the cut points are a decision, not a clustering discovery |
| Nulls were handled | [`docs/decision-log.md`](../docs/decision-log.md) §C — the README's nulls claim is wrong; the only nulls are structural and in dropped columns |
| The funnel reproduces the baseline | [`pipeline/evidence/pipeline_run.log`](../pipeline/evidence/pipeline_run.log) — captured output, 44.305% |
