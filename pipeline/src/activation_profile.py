"""Activation profile assignment for MVP 2 - the First-Use Activation Coach.

Implements `docs/activation-profile-spec.md`. Every threshold is read from
`config/activation_profile.yaml`; none is hard-coded here, so changing who gets
targeted is a config change with a version stamp rather than a code change.

The profile is two-dimensional by design:

    risk band  x  timing state  ->  treatment

The risk band comes from fixed probability thresholds, deliberately not from
population terciles: under terciles a customer's band depends on who else was
in the scoring batch, so the same customer can move bands without changing.
Fixed thresholds keep the assignment reproducible across runs, which both the
experiment and the cost guardrail require.

The timing state exists because a risk band alone does not determine what to
do. Among customers who activate, the median is day 5, the peak is days 2-3,
70.9% have activated by day 7 and 93.3% by day 14. Before day 3 the coach would
spend budget on customers who were about to activate unaided; after day 14 only
8.4% of activations remain. The window is days 3-10, and it is the same window
for every band because the shape of the timing curve is segment-invariant -
week-one share is 65.0% for referral against 64.8% for paid social, despite a
29-point gap in their overall rates. Only the level differs, not the pace.

The band boundaries are a documented decision, not a discovery. Predicted
probabilities form a single unimodal mass; there are no natural clusters to
find. Unsupervised clustering was rejected on that basis and the cut points
were placed at low-density dips instead, so that a small change in a score does
not flip a customer between bands.
"""

from __future__ import annotations

import hashlib
import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

BANDS = ("LOWER", "MIDDLE", "UPPER")
TIMING_STATES = ("PRE_WINDOW", "IN_WINDOW", "LATE", "DORMANT")


def assign_band(scores: pd.Series, cfg: dict) -> pd.Series:
    """Map calibrated probabilities to LOWER / MIDDLE / UPPER."""
    lower = float(cfg["bands"]["lower_upper_bound"])
    middle = float(cfg["bands"]["middle_upper_bound"])
    if not 0 < lower < middle < 1:
        raise ValueError(f"Band thresholds must satisfy 0 < {lower} < {middle} < 1")
    return pd.Series(
        np.where(scores < lower, "LOWER", np.where(scores < middle, "MIDDLE", "UPPER")),
        index=scores.index,
        dtype="object",
    )


def assign_timing_state(days_since_account: pd.Series, cfg: dict) -> pd.Series:
    """Map days since account creation to a timing state."""
    t = cfg["timing"]
    pre_end = int(t["pre_window_end_day"])
    win_start, win_end = int(t["window_start_day"]), int(t["window_end_day"])
    late_end = int(t["late_end_day"])
    if not pre_end < win_start <= win_end < late_end:
        raise ValueError("Timing boundaries in config are not ordered")

    d = days_since_account
    return pd.Series(
        np.select(
            [d <= pre_end, (d >= win_start) & (d <= win_end), d <= late_end],
            ["PRE_WINDOW", "IN_WINDOW", "LATE"],
            default="DORMANT",
        ),
        index=d.index,
        dtype="object",
    )


def stable_holdout(customer_ids: pd.Series, cfg: dict) -> pd.Series:
    """Deterministic holdout from a stable hash of customer_id.

    Not a runtime random draw. The same customer must land in the same arm on
    every scoring run and the assignment must be reconstructible afterwards for
    analysis - `random()` satisfies neither.
    """
    share = float(cfg["holdout"]["share"])
    cutoff = int(round(share * 100))

    def bucket(cid) -> int:
        digest = hashlib.sha256(str(cid).encode()).hexdigest()
        return int(digest[:8], 16) % 100

    return customer_ids.map(bucket) < cutoff


def apply_treatment_matrix(
    band: pd.Series, timing_state: pd.Series, cfg: dict
) -> pd.Series:
    matrix = cfg["treatment_matrix"]
    missing = [
        (b, s) for b in BANDS for s in TIMING_STATES
        if s not in matrix.get(b, {})
    ]
    if missing:
        raise ValueError(f"treatment_matrix is missing cells: {missing}")
    pairs = pd.DataFrame({"band": band, "state": timing_state})
    return pairs.apply(lambda r: matrix[r["band"]][r["state"]], axis=1)


def apply_suppression(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Resolve `treatment_state` and `suppression_reason`.

    Rules are evaluated in order of severity; the first that fires wins, so the
    logged reason is the binding one rather than an arbitrary pick.
    """
    s = cfg["suppression"]
    treatment = df["proposed_treatment"].copy()
    reason = pd.Series(pd.NA, index=df.index, dtype="object")

    def fire(mask: pd.Series, label: str) -> None:
        nonlocal treatment, reason
        hit = mask & reason.isna()
        treatment = treatment.mask(hit, "SUPPRESSED")
        reason = reason.mask(hit, label)

    fire(df["holdout_flag"], "HOLDOUT")
    fire(df["timing_state"].isin(s.get("suppress_timing_states", [])), "TIMING_STATE")
    fire(
        df["profile_band"].isin(s.get("no_incentive_for_bands", []))
        & (df["proposed_treatment"] == "COACH_INCENTIVE"),
        "DO_NO_HARM_NO_INCENTIVE_FOR_BAND",
    )
    fire(df["proposed_treatment"] == "NO_CONTACT", "NO_CONTACT_BY_POLICY")

    df = df.copy()
    df["treatment_state"] = treatment
    df["suppression_reason"] = reason
    return df


def build_activation_profile(
    scores: pd.DataFrame,
    cfg: dict,
    as_of: pd.Timestamp,
    score_source: str,
    model_version: str | None = None,
) -> pd.DataFrame:
    """Build the `customer_activation_profile` table.

    `scores` must carry: customer_id, activation_score, account_created_ts,
    and `activated_before_as_of` (whether the customer has already transacted).
    """
    required = {"customer_id", "activation_score", "account_created_ts"}
    missing = required - set(scores.columns)
    if missing:
        raise ValueError(f"scores is missing columns: {sorted(missing)}")

    df = scores.copy()
    if df["activation_score"].isna().any():
        raise ValueError("activation_score contains nulls")
    if not df["activation_score"].between(0, 1).all():
        raise ValueError("activation_score outside [0, 1]")
    if df["customer_id"].duplicated().any():
        raise ValueError("customer_id is not unique in scores")

    df["days_since_account_created"] = (
        (as_of - df["account_created_ts"]).dt.total_seconds() // 86400
    ).astype("Int64")

    df["profile_band"] = assign_band(df["activation_score"], cfg)
    df["score_decile"] = pd.qcut(
        df["activation_score"].rank(method="first"), 10, labels=False
    ).astype(int)
    df["timing_state"] = assign_timing_state(
        df["days_since_account_created"].astype("float"), cfg
    )
    df["holdout_flag"] = stable_holdout(df["customer_id"], cfg)
    df["proposed_treatment"] = apply_treatment_matrix(
        df["profile_band"], df["timing_state"], cfg
    )

    if "activated_before_as_of" in df.columns:
        already = df["activated_before_as_of"].fillna(False).astype(bool)
        df.loc[already, "proposed_treatment"] = "NO_CONTACT"

    df = apply_suppression(df, cfg)

    df["score_source"] = score_source
    df["model_version"] = model_version or cfg.get("model_version", "unspecified")
    df["thresholds_version"] = cfg["version"]
    df["scored_at"] = as_of

    columns = [
        "customer_id", "activation_score", "score_decile", "profile_band",
        "days_since_account_created", "timing_state", "treatment_state",
        "suppression_reason", "holdout_flag", "score_source",
        "model_version", "thresholds_version", "scored_at",
    ]
    out = df[columns]

    log.info("Activation profile: %s customers", f"{len(out):,}")
    for band in BANDS:
        share = (out["profile_band"] == band).mean()
        log.info("  %-7s %6.2f%%  (%s customers)", band, 100 * share,
                 f"{int((out['profile_band'] == band).sum()):,}")
    return out


def band_summary(profile: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    """Observed activation rate per band - the monotonicity evidence."""
    merged = profile.merge(
        labels[["customer_id", "activated_30d"]], on="customer_id", how="left"
    )
    base = merged["activated_30d"].mean()
    out = (
        merged.groupby("profile_band")
        .agg(
            customers=("customer_id", "size"),
            mean_score=("activation_score", "mean"),
            observed_activation=("activated_30d", "mean"),
        )
        .reindex(list(BANDS))
        .reset_index()
    )
    out["share"] = out["customers"] / out["customers"].sum()
    out["vs_average"] = out["observed_activation"] / base if base else np.nan
    return out
