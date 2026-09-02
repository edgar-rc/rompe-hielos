# Backlog

**Owner:** Product — Lara Sproesser · Engineering rows updated with delivery status.

---

## P0

| Item | Owner | Deliverable | Status |
|---|---|---|---|
| Validate schemas and joins | Engineering | Reproducible ingestion | **Done** — `pipeline/src/io.py`, three-tier source resolution with row-count assertions |
| Build the seven-step state machine | Engineering | Customer-level canonical funnel | **Done** — `pipeline/src/state_machine.py`; reproduces 44.305% |
| Persist step-level and customer-level views | Engineering | `pipeline/out/` + evidence | **Done** — 7 views plus `run_manifest.json` |
| Define the leakage-safe feature set | BA + Engineering | Feature contract | **Done** — `config/activation_profile.yaml`, enforced in code and checks |
| Reproduce funnel and segment metrics | BA | EDA notebook and funnel table | Engineering side done (`docs/metrics.md`); BA notebook pending |
| Define activation eligibility and suppression | Product + BA | Targeting policy | **Done** — `docs/activation-profile-spec.md` §3, §6 |
| Implement the next-best-action baseline | BA + Engineering | Transparent policy/model | Partial — profile assignment done; action playbook pending Product |
| Implement selfie eligibility policy (Android only) | Engineering | Version/OS rules | Open — needs the ≤4.1.1 cut signed off against version-support policy |
| Prototype the update-nudge flow | Product + Engineering | Clickable demo | Open |
| Build constrained AI copy generation | Engineering + Product | Prompt/template and logs | Open |
| Define the experiment event taxonomy | BA + Engineering | Measurement plan | Open |
| Quality controls | Engineering | Executable suite | **Done** — `analytics/quality/checks.py`, 51 checks, non-zero exit |

## P1

| Item | Owner | Deliverable | Status |
|---|---|---|---|
| Add adaptive selfie guidance | Product + Engineering | Retry-aware prototype | Open |
| Add activation action ranking | BA | Model comparison | Open |
| Daily scoring cadence | Engineering | Scheduled run | Open — current runner is a single-snapshot batch |
| Add the pay-on-conversion incentive arm | Product + Finance | Economics and abuse analysis | Open — mechanic specified, face value pending Finance |
| Drop `referred_by_customer` | BA | Cleaner importances | Open — see `decision-log.md` §E4 |

## P2

| Item | Owner | Deliverable | Status |
|---|---|---|---|
| Assisted-verification fallback | Product + Operations | Service design | Future |
| Acquisition mix optimisation | Growth + Finance | Channel economics | Future — needs CAC/LTV |

## Blocked on data, not on effort

| Item | Why it matters |
|---|---|
| **Post-account-created event schema** | The event data stops at account creation. Without first-use signals — card delivery, funding, app opens, declined transactions — MVP 2 can rank who will not activate but cannot know why, and the coach has nothing to react to. This is the single largest gap in the project |
