# dashboard

**Owner:** BA — David Garcia Medina

QuickSight link:

https://us-east-1.quicksight.aws.amazon.com/sn/account/nu-qs-prod/accounts/182399701629/dashboards/d4dbf6d0-cd72-43ce-a6f4-853ced9d7b51
---

## To do

- [ ] Build the three views below
- [ ] Add the QuickSight link here, or drop screenshots into this folder and reference them
- [ ] If QuickSight stalls, screenshots count — the brief says so explicitly

## The three views the brief asks for

**1 · Seven-step funnel.** Customer-level abandonment per stage. Source:
`pipeline/out/funnel_stage_view`, which the pipeline writes on every run and which
reproduces the 44.305% baseline. Numbers in
[`docs/metrics.md`](../docs/metrics.md) §3.

Label the denominator on the chart. Step 4 is **34.16%** at customer level and 22.28% at
attempt level, and the attempt-level figure has already caused confusion once in this
project — see [`docs/decision-log.md`](../docs/decision-log.md) §A5.

**2 · Conversion by channel and device.** Source: `pipeline/out/customer_state_view` joined
to `customers`. Show segment size next to every rate — a 90% conversion on 40 people is
noise, and the jury will ask.

The finding to make visible: acquisition channel moves *both* factors of the North Star.
Referral 80 of 100 pass the selfie and 58.8% of those activate, so 47 end up using the
account; paid social 53 pass and 29.3% activate, so 15 do. Same product investment, three
times the customers.

**3 · Risk distribution and the top-10% list.** Source:
`pipeline/out/customer_activation_profile` — it carries `activation_score`,
`score_decile`, `profile_band`, `timing_state` and `treatment_state`, so this view needs
no extra computation.

Worth showing on this view: the distribution is a single unimodal mass with no natural
clusters, which is exactly why the band boundaries are a documented decision rather than a
discovery. And the cumulative-capture column — targeting the lowest-scoring 30% reaches
37.8% of everyone who fails to activate — is the number that converts into cost per
incremental activation.

## Handoff to Product

The brief asks for a one-page handoff so Product can write the recommendation without
coming back with questions. Four things it needs:

| | |
|---|---|
| Critical stage | Step 4 `selfie_liveness` — 65,129 customers, 44.98% of all abandonment |
| Priority segments | Android + legacy version (+23.8pp abandonment, Android-only effect); paid social (29.3% activation vs. referral's 58.8%) |
| Size of the targetable list | `MIDDLE` × `IN_WINDOW` ≈ 1,435/day at sample scale, ~12,000/day at real inflow |
| Expected extra activations | State as a range, not a point. The selfie fix is +2.13 to +2.75 North Star points — see [`docs/metrics.md`](../docs/metrics.md) §5 for why the range cannot be narrowed |
