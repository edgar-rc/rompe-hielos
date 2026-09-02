"""The onboarding funnel as a seven-step state machine.

The single most consequential modelling decision in this pipeline. Everything
downstream - the funnel table, the segment cuts, the model features - inherits
whatever is decided here, so the reasoning is written out rather than implied.

The problem
-----------
`onboarding_events` holds 1,386,264 rows for 260,000 customers, roughly 5.3
rows per customer. That is not because customers pass through five steps; it is
because `selfie_liveness` (step 4) allows retries and each attempt writes its
own row. **A row is not a person.** Counting rows produces a funnel that is
wrong in a specific and misleading direction: it dilutes the abandonment rate
of exactly the step where customers struggle most, because a customer who
eventually succeeds on their third attempt contributes two extra non-abandoned
rows.

Concretely: 292,307 attempt rows for step 4 map to 190,642 distinct customers.
Measured at the attempt level, step 4 abandonment reads ~22-24%. Measured at
the customer level it is 34.16%. The customer-level figure is the canonical one
for every business decision; see docs/metrics.md.

The state machine
-----------------
Per the dataset README, a customer's journey ends either with `status =
'abandoned'` on some step, or with `step_name = 'account_created'` completed.
`status = 'retry'` marks an intermediate attempt within a step.

So the machine is a strict linear chain with one terminal branch per step:

    S1 -> S2 -> S3 -> S4 -> S5 -> S6 -> S7 (ACCOUNT_CREATED, absorbing)
     |     |     |     |     |     |
     v     v     v     v     v     v
    ABANDONED_AT_STEP_n (absorbing)

Collapsing to one row per (customer_id, step_number) means picking the *final*
status of the step and the *maximum* attempt number. The retry count is not
discarded - it is aggregated into `attempts`, which turns out to be one of the
stronger activation features (permutation importance 0.0174, and activation
falls from 45.6% for first-attempt customers to 35.4% for those needing three
or more).

Time on a step is a **SUM** over attempts, never a MAX or a MEAN. `ms_on_step`
is per attempt, so summing is the only aggregation that answers "how long did
this customer spend here", and it is the only one that does not understate the
strugglers who are the population of interest.
"""

from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)

STEPS = [
    (1, "phone_verification"),
    (2, "personal_data"),
    (3, "id_document_upload"),
    (4, "selfie_liveness"),
    (5, "address"),
    (6, "terms_acceptance"),
    (7, "account_created"),
]
STEP_NAMES = {n: name for n, name in STEPS}
N_STEPS = len(STEPS)

# Status precedence when collapsing attempts to one row per customer-step.
# 'retry' is intermediate by definition and can never be a step's final state,
# so a terminal status always wins over it.
TERMINAL_STATUSES = ("completed", "abandoned")


def build_customer_step_view(events: pd.DataFrame) -> pd.DataFrame:
    """Collapse the event log to one row per (customer_id, step_number).

    Returns columns:
        customer_id, step_number, step_name, final_status, attempts,
        total_ms_on_step, first_attempt_ts, last_attempt_ts
    """
    required = {
        "customer_id", "step_number", "step_name",
        "attempt_no", "status", "ms_on_step", "event_ts",
    }
    missing = required - set(events.columns)
    if missing:
        raise ValueError(f"onboarding_events is missing columns: {sorted(missing)}")

    df = events.sort_values(["customer_id", "step_number", "attempt_no", "event_ts"])

    grouped = df.groupby(["customer_id", "step_number"], sort=False)
    view = grouped.agg(
        attempts=("attempt_no", "max"),
        n_rows=("attempt_no", "size"),
        total_ms_on_step=("ms_on_step", "sum"),
        first_attempt_ts=("event_ts", "min"),
        last_attempt_ts=("event_ts", "max"),
        final_status=("status", "last"),
    ).reset_index()

    view["step_name"] = view["step_number"].map(STEP_NAMES)

    # A step whose last row is 'retry' would mean a journey that stops
    # mid-step with no terminal status. The generator does not produce this,
    # but the contract must be enforced rather than assumed.
    dangling = view.loc[~view["final_status"].isin(TERMINAL_STATUSES)]
    if len(dangling):
        raise ValueError(
            f"{len(dangling):,} customer-steps end on a non-terminal status. "
            "Attempt ordering or the status contract is not what is assumed. "
            f"Sample: {dangling.head(3).to_dict('records')}"
        )

    # No customer may hold two contradicting terminal states in the same step.
    contradictions = (
        df[df["status"].isin(TERMINAL_STATUSES)]
        .groupby(["customer_id", "step_number"])["status"]
        .nunique()
    )
    n_bad = int((contradictions > 1).sum())
    if n_bad:
        raise ValueError(
            f"{n_bad:,} customer-steps carry both 'completed' and 'abandoned'. "
            "Deduplication cannot proceed."
        )

    log.info(
        "Customer-step view: %s rows from %s event rows (%s customers)",
        f"{len(view):,}", f"{len(events):,}", f"{view['customer_id'].nunique():,}",
    )
    return view[[
        "customer_id", "step_number", "step_name", "final_status",
        "attempts", "n_rows", "total_ms_on_step",
        "first_attempt_ts", "last_attempt_ts",
    ]]


def build_customer_state_view(
    step_view: pd.DataFrame, customers: pd.DataFrame
) -> pd.DataFrame:
    """One row per customer: terminal state of the whole journey.

    Returns columns:
        customer_id, max_step_reached, terminal_state, abandoned_at_step,
        abandoned_at_step_name, completed_onboarding, selfie_attempts,
        n_retry_rows, wall_clock_seconds, account_created_ts
    """
    reached = step_view.groupby("customer_id")["step_number"].max().rename("max_step_reached")

    abandoned = (
        step_view.loc[step_view["final_status"] == "abandoned", ["customer_id", "step_number"]]
        .groupby("customer_id")["step_number"].min()
        .rename("abandoned_at_step")
    )

    completed_step7 = set(
        step_view.loc[
            (step_view["step_number"] == N_STEPS)
            & (step_view["final_status"] == "completed"),
            "customer_id",
        ]
    )

    selfie = (
        step_view.loc[step_view["step_number"] == 4, ["customer_id", "attempts"]]
        .set_index("customer_id")["attempts"].rename("selfie_attempts")
    )

    retries = (
        (step_view["n_rows"] - 1).groupby(step_view["customer_id"]).sum().rename("n_retry_rows")
    )

    span = step_view.groupby("customer_id").agg(
        journey_start_ts=("first_attempt_ts", "min"),
        journey_end_ts=("last_attempt_ts", "max"),
    )

    state = (
        customers[["customer_id"]]
        .merge(reached, on="customer_id", how="left")
        .merge(abandoned, on="customer_id", how="left")
        .merge(selfie, on="customer_id", how="left")
        .merge(retries, on="customer_id", how="left")
        .merge(span, on="customer_id", how="left")
    )

    state["max_step_reached"] = state["max_step_reached"].fillna(0).astype(int)
    state["n_retry_rows"] = state["n_retry_rows"].fillna(0).astype(int)
    state["selfie_attempts"] = state["selfie_attempts"].fillna(0).astype(int)
    state["completed_onboarding"] = state["customer_id"].isin(completed_step7)
    state["abandoned_at_step_name"] = state["abandoned_at_step"].map(STEP_NAMES)
    state["terminal_state"] = state.apply(_terminal_state, axis=1)
    state["wall_clock_seconds"] = (
        state["journey_end_ts"] - state["journey_start_ts"]
    ).dt.total_seconds()
    state["account_created_ts"] = state["journey_end_ts"].where(state["completed_onboarding"])

    # A completed journey cannot also be an abandoned one.
    both = state["completed_onboarding"] & state["abandoned_at_step"].notna()
    if both.any():
        raise ValueError(f"{int(both.sum()):,} customers are both completed and abandoned.")

    log.info(
        "Customer state view: %s customers, %s completed (%.3f%%)",
        f"{len(state):,}",
        f"{int(state['completed_onboarding'].sum()):,}",
        100 * state["completed_onboarding"].mean(),
    )
    return state[[
        "customer_id", "max_step_reached", "terminal_state",
        "abandoned_at_step", "abandoned_at_step_name", "completed_onboarding",
        "selfie_attempts", "n_retry_rows", "wall_clock_seconds",
        "journey_start_ts", "journey_end_ts", "account_created_ts",
    ]]


def _terminal_state(row: pd.Series) -> str:
    if row["completed_onboarding"]:
        return "ACCOUNT_CREATED"
    step = row["abandoned_at_step"]
    if pd.notna(step):
        return f"ABANDONED_AT_STEP_{int(step)}"
    return "NO_EVENTS"


def build_funnel_table(step_view: pd.DataFrame, n_signups: int) -> pd.DataFrame:
    """Stage-by-stage conversion, measured at customer level.

    `entering` for step n is the number of distinct customers with any row for
    that step - i.e. customers who reached the stage, not attempt rows.
    """
    rows = []
    cumulative_entering = n_signups
    for step_no, step_name in STEPS:
        stage = step_view[step_view["step_number"] == step_no]
        entering = stage["customer_id"].nunique()
        abandoned = int((stage["final_status"] == "abandoned").sum())
        completed = entering - abandoned
        rows.append({
            "step_number": step_no,
            "step_name": step_name,
            "customers_entering": entering,
            "customers_completed": completed,
            "customers_abandoned": abandoned,
            "stage_abandonment_rate": abandoned / entering if entering else 0.0,
            "cumulative_reach": entering / n_signups,
            "attempt_rows": int(stage["n_rows"].sum()),
        })
        cumulative_entering = completed

    funnel = pd.DataFrame(rows)
    total_abandoned = funnel["customers_abandoned"].sum()
    funnel["share_of_all_abandonment"] = (
        funnel["customers_abandoned"] / total_abandoned if total_abandoned else 0.0
    )
    funnel["cumulative_reach_after_stage"] = funnel["customers_completed"] / n_signups

    # The chain must be internally consistent: everyone who completes stage n
    # is exactly the set that enters stage n+1.
    for i in range(len(funnel) - 1):
        completed = funnel.loc[i, "customers_completed"]
        entering_next = funnel.loc[i + 1, "customers_entering"]
        if completed != entering_next:
            raise ValueError(
                f"Funnel chain broken between steps {i + 1} and {i + 2}: "
                f"{completed:,} completed but {entering_next:,} entered."
            )

    del cumulative_entering
    return funnel
