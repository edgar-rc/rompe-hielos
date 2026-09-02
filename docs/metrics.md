# Metrics

**Owner:** Engineering + BA · Every figure here is recomputed by `pipeline/src/run.py` on
each execution; the captured output is in `pipeline/evidence/pipeline_run.log`.

---

## 1. North Star

```
activated_30d = customers with a first transaction within 30 days / total signups
              = 50,024 / 260,000
              = 19.240%
```

It decomposes multiplicatively, and the decomposition is the reason this project has two
MVPs rather than one:

| Factor | Value | Addressed by |
|---|---|---|
| Signups that complete onboarding | 44.305% | MVP 1 — Selfie Recovery |
| Of those, activating within 30 days | 43.427% | MVP 2 — First-Use Activation Coach |
| **Signups activating within 30 days** | **19.240%** | both, multiplicatively |

44.305% × 43.427% = 19.240%, recovered exactly. The factors multiply rather than add:
of the 144,808 customers who never complete onboarding, exactly **zero** activate, and
the pipeline asserts this on every run. Improving one factor scales whatever the other
already delivers.

## 2. The canonical denominator

**This is the single most important definition in the repository.** It has been got wrong
once already in this project's own working figures, and the correction is documented
rather than quietly applied.

`selfie_liveness` is the only step with retries, so it is the only step where the choice
of denominator changes the answer:

| Measurement | Definition | Step 4 result |
|---|---|---|
| Attempt level | `abandoned` rows ÷ all attempt rows for the step | **22.28%** |
| **Customer level (canonical)** | distinct customers who never moved past the step ÷ distinct customers who reached it | **34.16%** |

292,307 attempt rows map to only 190,642 distinct customers. A customer who succeeds on
their third try still contributes two non-abandoned rows, so the attempt-level rate
dilutes exactly the step where customers struggle most — it is wrong in a direction that
makes the problem look smaller.

**Every business decision, the funnel table, the North Star narrative and the pitch use
the customer-level rate.** The attempt-level rate is retained only as an operational
metric for retry friction and camera SDK performance, and must be labelled as such
wherever it appears. The quality suite asserts that the two rates differ by more than
5pp, so this distinction cannot silently collapse.

## 3. Funnel, stage by stage

Customer level, reproduced by the pipeline on every run.

| Step | Stage | Entering | Completed | Abandoned | Stage rate | Cum. reach | Share of all abandonment |
|---|---|---|---|---|---|---|---|
| 1 | `phone_verification` | 260,000 | 247,309 | 12,691 | 4.88% | 95.12% | 8.76% |
| 2 | `personal_data` | 247,309 | 227,271 | 20,038 | 8.10% | 87.41% | 13.84% |
| 3 | `id_document_upload` | 227,271 | 190,642 | 36,629 | 16.12% | 73.32% | 25.29% |
| 4 | **`selfie_liveness`** | 190,642 | 125,513 | **65,129** | **34.16%** | 48.27% | **44.98%** |
| 5 | `address` | 125,513 | 118,672 | 6,841 | 5.45% | 45.64% | 4.72% |
| 6 | `terms_acceptance` | 118,672 | 115,192 | 3,480 | 2.93% | 44.30% | 2.40% |
| 7 | `account_created` | 115,192 | 115,192 | 0 | 0.00% | 44.30% | 0.00% |

**The sentence for the pitch:** step 4 `selfie_liveness` concentrates 65,129 lost
customers, 44.98% of all funnel abandonment. It is both the highest stage rate and the
largest absolute loss, which is unusual and makes prioritisation easy.

## 4. Activation

| Metric | Value |
|---|---|
| Completed onboarding | 115,192 (44.305%) |
| Activated within 30 days | 50,024 (19.240%) |
| Activation among completers | 43.427% |
| **Completed but never transacted** | **65,168** (56.57% of completers) |
| Activated without completing | 0 — asserted every run |

### Timing

Among customers who do activate: median day 5.12. The peak is not day 0 (6.6%) but days 1
to 3, at 10.7% / 11.2% / 10.9% — nobody transacts instantly, then the curve collapses.

**State the convention, because it changes the number.** "By day 7" is ambiguous between
*days 0–6* and *days 0–7 inclusive*, and the two differ by more than six points. Both are
given here; the inclusive column is the one quoted elsewhere.

| | Days 0 to N−1 (`< N`) | Days 0 to N inclusive (`≤ N`) |
|---|---|---|
| Day 7 | 64.5% | **70.9%** |
| Day 14 | 91.6% | **93.3%** |
| Day 28 | 99.6% | 99.7% |

By calendar week, unambiguously: week 1 (days 0–6) 64.5%, week 2 27.1%, week 3 6.5%,
week 4 and later 1.9%. Activations landing on day 14 or later: 8.4%.

Three consequences, all of which shape the MVP 2 policy:

- **Intervention window: days 3–10.** Earlier and customers activate unaided; later and
  almost nobody does.
- **Day 14 is a reasonable threshold for declaring the account dead** — only 8.4% of
  activations happen after it.
- **The 30-day label window is not binding.** 99.6% activate before day 28, so the label
  is not truncating anything, and no cohort needed excluding for censoring. Monthly
  activation is flat across the window (19.37% first month, 18.96% last).

## 5. Segment cuts

The three cuts the brief names, plus age. Always reported with segment size, because a
90% rate on 40 people is noise.

### Acquisition channel — the dominant axis

Activation within 30 days, among completers:

| Channel | Activation | | Liveness abandonment |
|---|---|---|---|
| referral | 58.8% | | 19.62% |
| organic | 47.3% | | — |
| paid_search | 39.0% | | — |
| affiliate | 33.9% | | — |
| paid_social | 29.3% | | 47.37% |

29.5 points between best and worst — wider than the 28-point spread the same channels
show at the selfie step.

**The two effects stack, and this is the number for the pitch.** Of every 100 customers
reaching the selfie screen:

| | Pass the selfie | Then activate at | End up using the account |
|---|---|---|---|
| From referral | 80 | 58.8% | **47** |
| From paid social | 53 | 29.3% | **15** |

Three times the activated customers for the same product investment. Which is why
channels must be measured by **cost per activated customer**, not cost per signup: with
both effects stacked, paid social's real cost is far higher than the acquisition report
shows.

### Device and app version

| Comparison | Liveness abandonment | Read |
|---|---|---|
| Android legacy (≤4.1.1) vs. current (≥4.2.0) | 49.75% vs. 25.97% (**+23.8pp**) | Large, and Android-wide rather than a paid-social artifact |
| iOS legacy vs. current | 28.23% vs. 25.46% (+2.8pp) | Within noise — **the effect is not present on iOS** |
| Android legacy + paid social at liveness | 19,596 entrants, 68.47% abandonment | Best initial segment for the technical intervention |

The version effect being Android-only is what scopes MVP 1 to Android and keeps an iOS
variant out of it. The plausible mechanism is camera/OS-level liveness performance rather
than app UX alone, but that is a hypothesis, not a finding.

**Device does not carry through to activation.** Android sits at 67–70% in all three risk
bands. Whatever legacy versions do to funnel completion, they do not affect whether the
account gets used — which is convenient, because it means MVP 1 and MVP 2 are independent
on this axis and their benefits cannot be double-counted.

### Age — the axis that inverts

| Age | Activation among completers |
|---|---|
| 18–24 | 40.3% |
| 25–34 | 42.5% |
| 35–44 | 44.9% |
| 45–54 | 47.1% |
| 55+ | 49.3% |

Monotone, 9 points, older customers activate more. The useful part is the contrast: at the
selfie step age is completely flat (33.9%–34.4%). **Age does not predict whether you
finish onboarding, but it does predict whether you use the account.** The two leaks have
different segmentation axes, so the same targeting cannot serve both.

Why, these data cannot say. Spending capacity and intent at signup are both consistent
with it and not separable here.

### Onboarding friction predicts a dead account

| Selfie attempts | Activation |
|---|---|
| Passed first time | 45.6% |
| Needed 2 | 40.2% |
| Needed 3 or more | 35.4% |

10 points. This forces a **downward correction to our own impact estimate**: the customers
rescued by fixing the selfie step are precisely the ones least likely to use the account,
so projecting their activation at the 43.4% population average overstates the gain.

The arithmetic, all of it reproducible from the data. Closing the Android legacy-vs-current
gap at the selfie step — 49.75% vs. 25.97% abandonment, 23.78pp across 65,880 Android
legacy customers who reach the step — recovers **15,666 customers** to onboarding
completion. Applying an activation rate to them:

| Activation rate applied | Extra activations | North Star points |
|---|---|---|
| 43.43% — population average | 6,803 | **+2.62** ← the original estimate |
| 35.4% — needed 3+ selfie attempts | 5,546 | **+2.13** |
| 45.6% — passed first time | 7,144 | **+2.75** |

So the honest figure is a range of **+2.13 to +2.75 points**, not +2.62. The range cannot
be narrowed with these data: if struggling *causes* abandonment, removing the bug moves
those customers to the 45.6% rate; if struggling is merely a *symptom* of low intent, they
stay near 35.4%.

Bringing the bounded range and the reason it cannot be closed is worth more than a single
tidy number, and it is the first question anyone competent will ask.

## 6. Model metrics

### Why PR-AUC, and against which floor

With 19.2% positives overall, accuracy is worthless: "nobody activates" scores 80.8%.
Hence PR-AUC, reported first.

But **the floor is the prevalence of the population being scored, not 0.5.** MVP 2 scores
only onboarding completers, where activation runs at 43.4%, so classes there are close to
balanced and severe imbalance is not the binding problem. On the test split the PR-AUC
floor is 0.4284. Quoting 0.56 against a floor of 0.5 would overstate the model; quoting it
against 0.4284 is the honest comparison. A funnel-abandonment model would face the real
imbalance.

### Baselines, established before the model

| Model | ROC-AUC | PR-AUC | Brier |
|---|---|---|---|
| Constant (prevalence) | 0.5000 | 0.4284 | 0.2449 |
| Channel lookup | 0.6221 | 0.5162 | 0.2335 |
| Logistic regression (pipeline baseline) | 0.6455 | 0.5636 | 0.2295 |
| Gradient boosting (BA model B, scoring model) | 0.6440 | 0.5627 | 0.2299 |

Temporal split on `signup_ts` at 2026-06-25 — not random, because in production the model
scores signups that have not happened yet, and a random split would let it see the period
it is evaluated on. Train 91,818 at 43.575% activation, test 23,374 at 42.842%: close
enough to confirm nothing drifts materially over the window.

**The number to be careful with.** A channel-average lookup alone reaches 0.6221. The full
model reaches 0.6455. **Most of what the model knows is the acquisition channel.** What it
adds is a continuous per-customer score instead of five buckets, so a ranked list can be
cut wherever the budget runs out. That is a real operational gain and should be claimed as
exactly that — not as the discovery of a hidden segment.

The two model families finish within 0.002 PR-AUC of each other, which is a tie.

### Rank quality — the table that justifies a cut point

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

**Targeting the lowest-scoring 30% reaches 37.8% of everyone who fails to activate.**
That is the figure that converts into a cost-per-incremental-activation estimate.

Predicted and observed track each other closely in every decile, which is what makes
these scores safe to do arithmetic with. Calibration matters more than ranking here
precisely because the scores feed a budget calculation: systematically overconfident
probabilities would inflate any business case built on them.

### Guardrail metrics

- Cost per incremental activated customer — the binding threshold, Product/Finance owns it
- Activation among high-propensity customers vs. their holdout — the do-no-harm check
- Cannibalisation: customers who would have activated untreated
- Coach exposure, dismissal and complaint signals; support contacts

## 7. Scale

| | |
|---|---|
| Signup window | 2026-02-01 to 2026-07-31 (181 days) |
| Sample inflow | 1,436 signups/day |
| Real Nu Mexico inflow | 12,000 signups/day |
| **Ratio** | **8.35×** |

Any per-day figure computed on this sample must be multiplied by 8.35 to reach real
scale. Impact figures quoted for the pitch use real inflow; figures quoted as pipeline
output use sample scale. Which is which is stated at every point of use.

## 8. What these metrics cannot answer

Deliberately part of the pitch, not an appendix to it. We know **who** does not activate,
with reasonable precision. **Why**, we do not.

| Missing data | Question it would answer |
|---|---|
| Physical card delivery | Not transacting because the card never arrived? |
| Account funding events | Not transacting because there is no balance? |
| App opens | Forgot, or opens the app and finds nothing to do? |
| Communications sent and opened | Did we tell them? Did it work? |
| Declined transactions | Tried and could not? |

None of it is in the dataset. Any statement about mechanism — "the card doesn't arrive",
"they have nothing to fund with" — would be invented.

There is one structural clue. The shape of the timing curve is near-identical across every
segment (week-one share 65.0% for referral against 64.8% for paid social) even though
their overall rates differ by 29 points. **Only the level changes, never the pace.** That
suggests what separates an activator from a non-activator is settled before day 1 — a
property of who the customer is, not of something that happens during the month. It does
not explain the mechanism, but it does justify predicting from signup who will need help,
which is exactly the personalisation the brief asks for.
