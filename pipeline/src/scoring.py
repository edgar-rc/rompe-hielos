"""Transparent fallback scorer and imbalance-aware metrics.

Scores for the activation profile come from the BA activation model when its
output is available (`--scores`). This module supplies what the pipeline needs
to run and be verified without it:

* a **channel lookup baseline** - historical activation rate per acquisition
  channel, the business rule any model has to beat before its complexity is
  justified;
* a **penalised logistic regression** on the leakage-safe feature set, fitted
  with plain gradient descent in NumPy so that the pipeline has no heavyweight
  dependency and every step is inspectable;
* ROC-AUC, PR-AUC (average precision) and the Brier score, implemented
  directly.

Scores produced here are labelled `score_source = "pipeline_baseline_logreg"`
in the output so they are never mistaken for the BA model's. Whichever source
is used is recorded in the run manifest.

On the metrics: PR-AUC is reported first because the brief requires it, but the
floor is the *prevalence of the scored population*, not 0.5. Among onboarding
completers activation runs at 43.4%, so classes here are close to balanced and
severe imbalance is not the binding problem it is for the funnel-abandonment
target. Saying so is more useful than quoting a number against the wrong floor.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def roc_auc(y: np.ndarray, p: np.ndarray) -> float:
    """Mann-Whitney U formulation, with ties given average rank."""
    y = np.asarray(y).astype(bool)
    n_pos, n_neg = int(y.sum()), int((~y).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p), dtype=float)
    ranks[order] = np.arange(1, len(p) + 1, dtype=float)
    # average ranks within tied groups
    sorted_p = p[order]
    i = 0
    while i < len(sorted_p):
        j = i
        while j + 1 < len(sorted_p) and sorted_p[j + 1] == sorted_p[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j + 2) / 2.0
        i = j + 1
    return (ranks[y].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def pr_auc(y: np.ndarray, p: np.ndarray) -> float:
    """Average precision, summed over *distinct* score thresholds.

    Ties are collapsed rather than broken arbitrarily. This matters: a constant
    predictor must score exactly the prevalence, and it only does so if every
    equal score sits at the same threshold. Breaking ties by row order would
    make a useless model look slightly better or worse than its floor purely as
    an artifact of sort order.
    """
    y = np.asarray(y).astype(bool)
    if y.sum() == 0:
        return float("nan")
    order = np.argsort(-p, kind="mergesort")
    y_sorted, p_sorted = y[order], p[order]

    # last index of each distinct-score group
    boundaries = np.flatnonzero(np.diff(p_sorted) != 0)
    boundaries = np.append(boundaries, len(p_sorted) - 1)

    tp = np.cumsum(y_sorted)[boundaries]
    n_pred = boundaries + 1
    precision = tp / n_pred
    recall = tp / y.sum()
    recall_prev = np.concatenate([[0.0], recall[:-1]])
    return float(np.sum(precision * (recall - recall_prev)))


def brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((np.asarray(y).astype(float) - p) ** 2))


def decile_table(y: np.ndarray, p: np.ndarray) -> pd.DataFrame:
    """Rank-quality table: mean predicted, observed, lift, cumulative capture."""
    df = pd.DataFrame({"y": np.asarray(y).astype(int), "p": p})
    df["decile"] = pd.qcut(df["p"].rank(method="first"), 10, labels=False)
    base = df["y"].mean()
    total_neg = int((df["y"] == 0).sum())
    out = (
        df.groupby("decile")
        .agg(customers=("y", "size"), mean_predicted=("p", "mean"), observed=("y", "mean"))
        .reset_index()
    )
    out["lift"] = out["observed"] / base if base else np.nan
    non_act = df.assign(neg=1 - df["y"]).groupby("decile")["neg"].sum()
    out["cum_share_of_non_activators"] = non_act.cumsum().values / total_neg
    return out


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------

def channel_lookup_baseline(
    train: pd.DataFrame, test: pd.DataFrame, target: str = "activated_30d"
) -> np.ndarray:
    """Predict each customer's channel's historical activation rate."""
    rates = train.groupby("acquisition_channel")[target].mean()
    return test["acquisition_channel"].map(rates).fillna(train[target].mean()).to_numpy()


def fit_logistic(
    X: np.ndarray,
    y: np.ndarray,
    l2: float = 1.0,
    lr: float = 0.5,
    epochs: int = 600,
    seed: int = 42,
) -> np.ndarray:
    """Full-batch gradient descent on the L2-penalised log-loss.

    Returns the weight vector with the intercept in position 0. Deterministic
    given the seed; the seed only sets the (zero) initialisation, kept explicit
    so the run is reproducible by inspection.
    """
    rng = np.random.default_rng(seed)
    Xb = np.column_stack([np.ones(len(X)), X])
    w = np.zeros(Xb.shape[1])
    del rng
    n = len(Xb)
    for epoch in range(epochs):
        z = Xb @ w
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))
        grad = Xb.T @ (p - y) / n
        grad[1:] += (l2 / n) * w[1:]
        w -= lr * grad
        if epoch % 200 == 0:
            loss = -np.mean(
                y * np.log(np.clip(p, 1e-12, 1)) + (1 - y) * np.log(np.clip(1 - p, 1e-12, 1))
            )
            log.debug("  epoch %4d  log-loss %.5f", epoch, loss)
    return w


def predict_logistic(w: np.ndarray, X: np.ndarray) -> np.ndarray:
    z = np.column_stack([np.ones(len(X)), X]) @ w
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


def evaluate(name: str, y: np.ndarray, p: np.ndarray) -> dict:
    row = {
        "model": name,
        "roc_auc": roc_auc(y, p),
        "pr_auc": pr_auc(y, p),
        "brier": brier(y, p),
    }
    log.info(
        "  %-34s ROC-AUC %.4f  PR-AUC %.4f  Brier %.4f",
        name, row["roc_auc"], row["pr_auc"], row["brier"],
    )
    return row
