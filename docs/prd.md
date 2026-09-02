# PRD — Onboarding Recovery & First-Use Activation

**Owner:** Product — Lara Sproesser
**Status:** placeholder in this repository. The authoritative draft lives outside git and
needs to be committed here before submission.

---

## To do

- [ ] Paste the current PRD draft into this file, or replace it with the exported version
- [ ] Reconcile any figure that disagrees with [`metrics.md`](metrics.md) — that file is
      reproduced from the data on every pipeline run, so it is the reference
- [ ] Close the pending items in the PRD's own §9 table that are now resolved:
      the canonical selfie denominator (34.16%, see `decision-log.md` §A5), the leakage
      verification (§B1), the nulls finding (§C1), and the state-machine definition
      (`architecture.md` §3)

## Summary of what is already decided

Recorded here so the rest of the repository is readable without the full PRD.

### The problem, in two parts

The funnel loses people twice, and the two leaks are close to the same size:

| | |
|---|---|
| Signups | 260,000 |
| Complete onboarding | 115,192 (44.305%) |
| Activate within 30 days | 50,024 (19.240%) |
| **Complete but never transact** | **65,168** — 56.57% of completers |

Of the 144,808 who do not complete onboarding, exactly zero activate. The two factors
multiply rather than add.

### Two MVPs, selected from five alternatives

**MVP 1 — Selfie Recovery: version-aware update nudge.** Android customers on legacy app
versions get a lightweight, dismissible update prompt before the selfie camera opens.
Targets the largest single drop (step 4, 65,129 customers, 44.98% of all abandonment).
Scoped to Android only: the legacy-vs-current gap is +23.8pp on Android and +2.8pp on iOS,
so an iOS variant would add engineering cost without a validated benefit. Eligibility is
deterministic on device and version; AI supports copy, not the decision.

**MVP 2 — First-Use Activation Coach.** A post-account-created in-app coach recommending
one relevant first action, targeted by propensity band and timing. Addresses the 65,168
customers who open an account and never use it. Targeting contract:
[`activation-profile-spec.md`](activation-profile-spec.md).

### Alternatives considered and not selected

| Alternative | Decision |
|---|---|
| Adaptive selfie capture coach | P1 / extension — needs camera signals and approved guidance |
| Assisted-verification fallback | Future / pilot — needs operations capacity and compliance |
| Acquisition-channel mix shift toward referral | Strategic follow-up — needs CAC, LTV and channel-quality data this project does not have |

The key call is fixing the funnel and the activation gap over reallocating acquisition
budget. The selfie fix benefits every channel at once, including paid social — the worst
activator at 29.3% and the largest volume at 34% of signups — without waiting on a
cross-team budget decision.

### Guardrails

- Cost per incremental activated customer must stay within the Product/Finance threshold
- Customers with high natural propensity must not be unnecessarily interrupted or
  incentivised — enforced in the pipeline, not left to the consuming service
  (`decision-log.md` §F6)

### Impact, stated honestly

All impact figures are **scenario estimates, not causal forecasts**, and must be
recalibrated after experiment results.

The selfie fix was initially estimated at +2.62 North Star points. The corrected range is
**+2.13 to +2.75**, because customers rescued by fixing the step activate at 35.4%–45.6%
rather than the 43.4% population average — struggling in onboarding predicts a dead
account. The range cannot be narrowed with these data, and the reason it cannot is worth
more in the pitch than a single tidy number. See [`metrics.md`](metrics.md) §5.
