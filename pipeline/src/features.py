"""Leakage-safe feature construction for the activation model.

The feature contract - which columns are permissible and which are forbidden -
lives in `config/activation_profile.yaml`, not in this file, so that it can be
reviewed and diffed without reading code. This module builds the permissible
set and refuses to emit anything on the forbidden list.

Two independent reasons a column can be excluded, and they are not the same
thing:

*Total leakage* - the column is only populated after the outcome. There are two:
`days_to_first_transaction` and `first_transaction_ts`, both null if and only
if `activated_30d` is false. Verified against the data: 209,976 nulls, which is
exactly 260,000 minus the 50,024 activated customers, and zero contradictions
in either direction.

*Constant in-population* - the column carries information about the funnel, but
scoring happens only among customers who completed onboarding, where it takes a
single value and therefore carries none. `completed_onboarding`, `steps_completed`,
`max_step_reached` and `abandoned_at_step` are all in this group. They are used
as the population filter and then dropped.

The scoring point is account creation (day 0), which is what makes the
onboarding-behaviour block permissible: every one of those features is already
observable by then. A funnel drop-off model could only use the signup block.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# Ordinal encoding of app versions. Ordered, not one-hot, because the
# hypothesis under test is monotone ("older is worse"), and because the
# legacy/current boundary sits between 4.1.1 and 4.2.0.
APP_VERSIONS = ["3.9.0", "4.0.2", "4.1.1", "4.2.0", "4.3.1", "4.4.0"]
APP_VERSION_ORDINAL = {v: i for i, v in enumerate(APP_VERSIONS)}
LEGACY_VERSIONS = {"3.9.0", "4.0.2", "4.1.1"}

STEP_TIME_LINEAR = [1, 2, 5, 6, 7]   # total_ms_step_n
STEP_TIME_LOG = [3, 4]               # log_ms_step_n - heavy right tail


def build_features(
    customers: pd.DataFrame,
    step_view: pd.DataFrame,
    state_view: pd.DataFrame,
    forbidden: list[str] | None = None,
) -> pd.DataFrame:
    """Build the customer-level feature table. One row per customer_id."""
    step_times = (
        step_view.pivot_table(
            index="customer_id",
            columns="step_number",
            values="total_ms_on_step",
            aggfunc="sum",
        )
        .rename(columns=lambda n: f"_ms_step_{n}")
        .reset_index()
    )

    df = (
        customers.merge(
            state_view[[
                "customer_id", "selfie_attempts", "n_retry_rows",
                "wall_clock_seconds", "completed_onboarding",
                "max_step_reached", "account_created_ts",
            ]],
            on="customer_id",
            how="left",
        )
        .merge(step_times, on="customer_id", how="left")
    )

    # ---- signup-time block -------------------------------------------------
    df["signup_hour"] = df["signup_ts"].dt.hour
    df["signup_dow"] = df["signup_ts"].dt.dayofweek
    df["app_version_ordinal"] = df["app_version"].map(APP_VERSION_ORDINAL).astype("Int64")
    df["is_legacy_version"] = df["app_version"].isin(LEGACY_VERSIONS)

    # ---- onboarding-behaviour block ---------------------------------------
    for n in STEP_TIME_LINEAR:
        col = f"_ms_step_{n}"
        df[f"total_ms_step_{n}"] = df[col].fillna(0.0) if col in df else 0.0
    for n in STEP_TIME_LOG:
        col = f"_ms_step_{n}"
        df[f"log_ms_step_{n}"] = np.log1p(df[col].fillna(0.0)) if col in df else 0.0

    ms_cols = [c for c in df.columns if c.startswith("_ms_step_")]
    df["total_ms_all_steps"] = df[ms_cols].fillna(0.0).sum(axis=1)
    df["log_total_ms"] = np.log1p(df["total_ms_all_steps"])
    df["wall_clock_seconds"] = df["wall_clock_seconds"].fillna(0.0)

    df = df.drop(columns=ms_cols + ["total_ms_all_steps"])

    forbidden = forbidden or []
    present = [c for c in forbidden if c in df.columns and c not in ("customer_id", "signup_ts")]
    # `completed_onboarding` and `max_step_reached` are carried here on purpose:
    # the population filter needs them. They are stripped by
    # `feature_matrix_columns` before anything is fitted.
    keep_for_filtering = {"completed_onboarding", "max_step_reached"}
    to_drop = [c for c in present if c not in keep_for_filtering]
    if to_drop:
        log.info("Dropped forbidden columns from feature table: %s", to_drop)
        df = df.drop(columns=to_drop)

    log.info("Feature table: %s rows, %s columns", f"{len(df):,}", df.shape[1])
    return df


NUMERIC_FEATURES = [
    "age", "signup_hour", "signup_dow", "app_version_ordinal",
    "selfie_attempts", "n_retry_rows", "wall_clock_seconds",
    "total_ms_step_1", "total_ms_step_2", "total_ms_step_5",
    "total_ms_step_6", "total_ms_step_7",
    "log_ms_step_3", "log_ms_step_4", "log_total_ms",
]
BOOLEAN_FEATURES = ["prev_bank_relationship", "referred_by_customer", "is_legacy_version"]
CATEGORICAL_FEATURES = ["acquisition_channel", "device_os", "state"]


def feature_matrix(df: pd.DataFrame, forbidden: list[str]) -> tuple[np.ndarray, list[str]]:
    """Encode the feature table into a numeric design matrix.

    Standardises numerics and one-hot encodes categoricals with the first level
    dropped. Raises if any forbidden column reaches this point - the last line
    of defence before anything is fitted.
    """
    breach = sorted(set(forbidden) & set(df.columns) - {"signup_ts", "customer_id"})
    breach = [c for c in breach if c not in ("completed_onboarding", "max_step_reached")]
    if breach:
        raise ValueError(f"Forbidden columns reached the design matrix: {breach}")

    blocks, names = [], []

    numeric = [c for c in NUMERIC_FEATURES if c in df.columns]
    if numeric:
        X = df[numeric].astype(float).to_numpy()
        mean = X.mean(axis=0)
        std = X.std(axis=0)
        std[std == 0] = 1.0
        blocks.append((X - mean) / std)
        names += numeric

    boolean = [c for c in BOOLEAN_FEATURES if c in df.columns]
    if boolean:
        blocks.append(df[boolean].astype(float).to_numpy())
        names += boolean

    for col in CATEGORICAL_FEATURES:
        if col not in df.columns:
            continue
        levels = sorted(df[col].dropna().unique().tolist())[1:]  # drop first level
        if not levels:
            continue
        blocks.append(
            np.column_stack([(df[col] == level).astype(float).to_numpy() for level in levels])
        )
        names += [f"{col}={level}" for level in levels]

    return np.column_stack(blocks), names
