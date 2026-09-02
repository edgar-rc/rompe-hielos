"""Pipeline entrypoint.

    python -m pipeline.src.run --data-dir ./data --out-dir ./pipeline/out

Stages, in order:

    1. ingest        three source tables, with row-count assertions
    2. state machine seven-step funnel, retries collapsed to customer grain
    3. views         step-level and customer-level, persisted
    4. funnel        stage-by-stage conversion at customer level
    5. features      leakage-safe feature table
    6. score         BA model scores if supplied, else the transparent baseline
    7. profile       activation profile for MVP 2, per config thresholds
    8. manifest      run provenance written next to the outputs

Every figure printed here is recomputed from the source tables on each run. The
run log in `pipeline/evidence/` is captured output, not a transcription.
"""

from __future__ import annotations

import argparse
import json
import logging
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from pipeline.src import activation_profile as ap  # noqa: E402
from pipeline.src import features as ft  # noqa: E402
from pipeline.src import io as tio  # noqa: E402
from pipeline.src import scoring as sc  # noqa: E402
from pipeline.src import state_machine as sm  # noqa: E402

log = logging.getLogger("pipeline")

DEFAULT_CONFIG = REPO_ROOT / "config" / "activation_profile.yaml"


def load_config(path: Path) -> dict:
    import yaml

    with open(path) as fh:
        return yaml.safe_load(fh)


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def section(title: str) -> None:
    log.info("")
    log.info("=" * 78)
    log.info("  %s", title)
    log.info("=" * 78)


def main() -> int:
    parser = argparse.ArgumentParser(description="Onboarding funnel and activation pipeline")
    parser.add_argument("--data-dir", default=str(REPO_ROOT / "data"),
                        help="directory holding the three source tables")
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "pipeline" / "out"),
                        help="where persisted views are written")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--generator", default=None,
                        help="path to gen_d1_onboarding.py, for the generated source tier")
    parser.add_argument("--scores", default=None,
                        help="CSV/Parquet with customer_id and activation_score "
                             "from the BA activation model")
    parser.add_argument("--as-of", default=None,
                        help="scoring date, ISO format. Defaults to the last "
                             "account_created_ts in the data.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    started = time.time()
    out_dir = Path(args.out_dir)
    cfg = load_config(Path(args.config))

    log.info("Onboarding funnel and activation pipeline")
    log.info("Python %s on %s", platform.python_version(), platform.platform())
    log.info("pandas %s / numpy %s", pd.__version__, np.__version__)
    log.info("Config: %s (thresholds version %s)", args.config, cfg["version"])

    # ---- 1. ingest ---------------------------------------------------------
    section("1. Ingest")
    loaded = tio.load_tables(
        args.data_dir,
        generator_path=args.generator,
        n_customers=int(cfg["population"]["total_signups"]),
    )
    customers = loaded["customers"]
    events = loaded["onboarding_events"]
    labels = loaded["activation_labels"]

    # ---- 2. state machine --------------------------------------------------
    section("2. State machine - collapse retries to customer grain")
    log.info(
        "Event rows per customer: %.2f (a row is not a person)",
        len(events) / customers["customer_id"].nunique(),
    )
    selfie_rows = int((events["step_number"] == 4).sum())
    selfie_customers = int(events.loc[events["step_number"] == 4, "customer_id"].nunique())
    log.info(
        "Step 4 selfie_liveness: %s attempt rows -> %s distinct customers",
        f"{selfie_rows:,}", f"{selfie_customers:,}",
    )
    step_view = sm.build_customer_step_view(events)
    state_view = sm.build_customer_state_view(step_view, customers)

    n_signups = len(customers)
    assert state_view["customer_id"].nunique() == n_signups, "state view lost customers"

    # ---- 3. funnel ---------------------------------------------------------
    section("3. Funnel - stage by stage, customer level")
    funnel = sm.build_funnel_table(step_view, n_signups)
    log.info("")
    for _, r in funnel.iterrows():
        log.info(
            "  step %d  %-20s entering %7s  abandoned %6s  stage rate %6.2f%%  "
            "cum reach %6.2f%%",
            r["step_number"], r["step_name"],
            f"{r['customers_entering']:,}", f"{r['customers_abandoned']:,}",
            100 * r["stage_abandonment_rate"], 100 * r["cumulative_reach_after_stage"],
        )
    worst = funnel.loc[funnel["customers_abandoned"].idxmax()]
    log.info("")
    log.info(
        "  Largest drop: step %d %s - %s customers lost, %.2f%% of all abandonment",
        worst["step_number"], worst["step_name"],
        f"{worst['customers_abandoned']:,}", 100 * worst["share_of_all_abandonment"],
    )

    completion = state_view["completed_onboarding"].mean()
    log.info("  Onboarding completion: %.3f%% (baseline 44.3%%)", 100 * completion)

    # ---- 4. reconcile against labels --------------------------------------
    section("4. Reconcile the state machine against activation_labels")
    check = state_view.merge(
        labels, on="customer_id", how="inner", suffixes=("_sm", "_lbl")
    )
    mismatch = int((check["completed_onboarding_sm"] != check["completed_onboarding_lbl"]).sum())
    log.info("  completed_onboarding disagreements: %s", f"{mismatch:,}")
    steps_mismatch = int((check["max_step_reached"] != check["steps_completed"]).sum())
    log.info("  max_step_reached vs steps_completed disagreements: %s", f"{steps_mismatch:,}")
    if mismatch or steps_mismatch:
        raise SystemExit("State machine does not reconcile with the labels table.")

    activation = labels["activated_30d"].mean()
    completers = labels[labels["completed_onboarding"]]
    log.info("  activated_30d overall:            %.3f%% (baseline 19.2%%)", 100 * activation)
    log.info("  activation among completers:      %.3f%%", 100 * completers["activated_30d"].mean())
    log.info("  completed but never activated:    %s customers",
             f"{int((labels['completed_onboarding'] & ~labels['activated_30d']).sum()):,}")
    non_completer_activations = int(
        (~labels["completed_onboarding"] & labels["activated_30d"]).sum()
    )
    log.info("  activated without completing:      %s (must be 0)", non_completer_activations)
    if non_completer_activations:
        raise SystemExit("Activation observed without onboarding completion.")

    # ---- 5. features -------------------------------------------------------
    section("5. Features - leakage-safe set")
    forbidden = list(cfg["forbidden_features"])
    feature_table = ft.build_features(customers, step_view, state_view, forbidden=forbidden)
    log.info("  Forbidden list has %d entries; none reaches the design matrix.", len(forbidden))

    # ---- 6. score ----------------------------------------------------------
    section("6. Score the scored population")
    pop = feature_table[feature_table["completed_onboarding"]].copy()
    pop = pop.merge(labels[["customer_id", "activated_30d"]], on="customer_id", how="left")
    log.info("  Scored population: %s of %s signups (%.3f%%)",
             f"{len(pop):,}", f"{n_signups:,}", 100 * len(pop) / n_signups)
    log.info("  Non-completers excluded: %s - they activate at 0%% by construction",
             f"{n_signups - len(pop):,}")

    if args.scores:
        score_path = Path(args.scores)
        supplied = (
            pd.read_parquet(score_path) if score_path.suffix == ".parquet"
            else pd.read_csv(score_path)
        )
        pop = pop.merge(supplied[["customer_id", "activation_score"]], on="customer_id", how="left")
        if pop["activation_score"].isna().any():
            raise SystemExit(f"{int(pop['activation_score'].isna().sum()):,} customers "
                             "have no supplied score.")
        score_source = f"supplied:{score_path.name}"
        metrics = pd.DataFrame([sc.evaluate("supplied scores", pop["activated_30d"].to_numpy(),
                                            pop["activation_score"].to_numpy())])
    else:
        log.info("  No --scores supplied; using the transparent pipeline baseline.")
        cutoff = pd.Timestamp(cfg["validation"]["cutoff"])
        train_mask = pop["signup_ts"] <= cutoff
        train, test = pop[train_mask], pop[~train_mask]
        log.info("  Temporal split at %s: train %s (%.3f%% act) / test %s (%.3f%% act)",
                 cfg["validation"]["cutoff"], f"{len(train):,}",
                 100 * train["activated_30d"].mean(), f"{len(test):,}",
                 100 * test["activated_30d"].mean())

        X_all, names = ft.feature_matrix(pop, forbidden)
        y_all = pop["activated_30d"].astype(float).to_numpy()
        X_train, y_train = X_all[train_mask.to_numpy()], y_all[train_mask.to_numpy()]
        X_test, y_test = X_all[~train_mask.to_numpy()], y_all[~train_mask.to_numpy()]

        log.info("  Design matrix: %d features", len(names))
        log.info("")
        log.info("  Baselines and model, evaluated on the temporal test set:")
        prevalence = np.full(len(y_test), y_train.mean())
        rows = [
            sc.evaluate("constant (prevalence)", y_test, prevalence),
            sc.evaluate("channel lookup", y_test, sc.channel_lookup_baseline(train, test)),
        ]
        w = sc.fit_logistic(X_train, y_train, l2=float(cfg["validation"].get("l2", 1.0)))
        p_test = sc.predict_logistic(w, X_test)
        rows.append(sc.evaluate("logistic regression (pipeline)", y_test, p_test))
        metrics = pd.DataFrame(rows)
        log.info("")
        log.info("  PR-AUC floor is test-set prevalence, %.4f - not 0.5.", y_test.mean())

        pop["activation_score"] = sc.predict_logistic(w, X_all)
        score_source = "pipeline_baseline_logreg"

        deciles = sc.decile_table(y_test, p_test)
        log.info("")
        log.info("  Rank quality on the test set:")
        for _, r in deciles.iterrows():
            log.info("    decile %d  predicted %.3f  observed %.3f  lift %.2f  "
                     "cum. non-activators %5.1f%%",
                     int(r["decile"]), r["mean_predicted"], r["observed"], r["lift"],
                     100 * r["cum_share_of_non_activators"])
        tio.write_table(deciles, out_dir, "model_decile_table")

    # ---- 7. activation profile --------------------------------------------
    section("7. Activation profile for MVP 2")
    # account_created_ts already arrives on the feature table via the state view.
    # Floored to the second: the nanosecond fraction is meaningless here and its
    # 9-digit tail is the kind of long numeric run that DLP scanners flag.
    as_of = (
        pd.Timestamp(args.as_of) if args.as_of else pop["account_created_ts"].max()
    ).floor("s")
    log.info("  as_of = %s", as_of)

    scores_in = pop[["customer_id", "activation_score", "account_created_ts"]].copy()
    scores_in["activated_before_as_of"] = False
    profile = ap.build_activation_profile(
        scores_in, cfg, as_of=as_of, score_source=score_source
    )

    summary = ap.band_summary(profile, labels)
    log.info("")
    log.info("  Band                customers    share   mean score   observed act.   vs avg")
    for _, r in summary.iterrows():
        log.info("  %-8s %18s  %6.2f%%      %.4f         %6.2f%%    %.2fx",
                 r["profile_band"], f"{int(r['customers']):,}", 100 * r["share"],
                 r["mean_score"], 100 * r["observed_activation"], r["vs_average"])

    log.info("")
    log.info("  Timing states at this as_of date:")
    for state in ap.TIMING_STATES:
        n = int((profile["timing_state"] == state).sum())
        log.info("    %-12s %9s  (%5.2f%%)", state, f"{n:,}", 100 * n / len(profile))
    log.info("")
    log.info("  This is a single-snapshot backfill, so most accounts are far past")
    log.info("  day 14 and land in DORMANT. In production the pipeline runs daily")
    log.info("  and IN_WINDOW is a rolling 8 days of account creations; the")
    log.info("  steady-state figure below is the one that sizes the MVP.")

    treat = profile["treatment_state"].value_counts()
    log.info("")
    log.info("  Treatment states:")
    for state, n in treat.items():
        log.info("    %-18s %9s  (%5.2f%%)", state, f"{n:,}", 100 * n / len(profile))

    primary = profile[
        (profile["profile_band"] == cfg["primary_target"]["band"])
        & (profile["timing_state"] == cfg["primary_target"]["timing_state"])
        & (profile["treatment_state"] != "SUPPRESSED")
    ]
    window_days = (
        int(cfg["timing"]["window_end_day"]) - int(cfg["timing"]["window_start_day"]) + 1
    )
    band_share = float((profile["profile_band"] == cfg["primary_target"]["band"]).mean())
    accounts_per_day = len(profile) / 181  # 181-day signup window
    steady_state = (
        accounts_per_day * window_days * band_share * (1 - float(cfg["holdout"]["share"]))
    )

    log.info("")
    log.info("  Primary MVP 2 target: %s x %s",
             cfg["primary_target"]["band"], cfg["primary_target"]["timing_state"])
    log.info("    at this as_of snapshot:            %s customers", f"{len(primary):,}")
    log.info("    steady state, sample scale:        %s in the window on any given day",
             f"{int(steady_state):,}")
    log.info("    steady state, real inflow (x%.2f):  %s customers",
             float(cfg["scale"]["ratio"]),
             f"{int(steady_state * float(cfg['scale']['ratio'])):,}")

    # ---- 8. persist and manifest ------------------------------------------
    section("8. Persist views")
    written = [
        tio.write_table(funnel, out_dir, "funnel_stage_view"),
        tio.write_table(step_view, out_dir, "customer_step_view"),
        tio.write_table(state_view, out_dir, "customer_state_view"),
        tio.write_table(feature_table, out_dir, "customer_feature_view"),
        tio.write_table(profile, out_dir, "customer_activation_profile"),
        tio.write_table(summary, out_dir, "activation_profile_band_summary"),
        tio.write_table(metrics, out_dir, "model_metrics"),
    ]

    manifest = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "elapsed_seconds": round(time.time() - started, 1),
        "python": platform.python_version(),
        "pandas": pd.__version__,
        "numpy": np.__version__,
        "source_tier": loaded.tier,
        "source_location": loaded.location,
        "thresholds_version": cfg["version"],
        "score_source": score_source,
        "as_of": str(as_of),
        "row_counts": {
            "customers": len(customers),
            "onboarding_events": len(events),
            "activation_labels": len(labels),
            "customer_step_view": len(step_view),
            "customer_state_view": len(state_view),
            "customer_activation_profile": len(profile),
        },
        "headline": {
            "onboarding_completion": round(float(completion), 5),
            "activated_30d": round(float(activation), 5),
            "activation_among_completers": round(float(completers["activated_30d"].mean()), 5),
            "largest_drop_step": int(worst["step_number"]),
            "largest_drop_stage_rate": round(float(worst["stage_abandonment_rate"]), 5),
            "largest_drop_customers": int(worst["customers_abandoned"]),
        },
        "band_distribution": {
            b: round(float((profile["profile_band"] == b).mean()), 5) for b in ap.BANDS
        },
        "outputs": [p.name for p in written],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "run_manifest.json", "w") as fh:
        json.dump(manifest, fh, indent=2)
    log.info("  wrote run_manifest.json")

    log.info("")
    log.info("Done in %.1fs", time.time() - started)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
