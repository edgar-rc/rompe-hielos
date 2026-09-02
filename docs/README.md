# docs

| File | Contents | Owner | Status |
|---|---|---|---|
| [`prd.md`](prd.md) | Product requirements — problem, alternatives, the two MVPs, scope, experiment design | Product | see file |
| [`backlog.md`](backlog.md) | Prioritised backlog with owners and dependencies | Product | see file |
| [`architecture.md`](architecture.md) | Pipeline design, the funnel state machine, data contracts, source resolution | Engineering | Implemented and executed |
| [`activation-profile-spec.md`](activation-profile-spec.md) | Segment definition for MVP 2 — the coach's targeting contract | Engineering | Implemented and executed |
| [`metrics.md`](metrics.md) | Metric definitions, canonical denominators, segment cuts, model metrics | Engineering + BA | Reproduced from the data |
| [`decision-log.md`](decision-log.md) | Every cleaning, exclusion and aggregation decision, with reasons and enforcing checks | all | Living |
| [`uso-de-ia.md`](uso-de-ia.md) | Required disclosure — AI as a build tool, and AI as a product component | all | Living |

## Reading order

**If you have five minutes** — the root [`README.md`](../README.md), then §3 of
[`metrics.md`](metrics.md) for the funnel table.

**If you are judging this** — [`decision-log.md`](decision-log.md) is where the reasoning
lives. Sections A5 (the canonical denominator), B1 (leakage), C1 (the README's nulls claim
is wrong) and F1 (the profiles are a decision, not a discovery) are the ones that carry
the argument.

**If a number here disagrees with a number somewhere else** —
[`decision-log.md`](decision-log.md) §I lists what has been superseded and why. Two
figures were corrected during the project, both in the direction that makes our own
proposal look weaker.
