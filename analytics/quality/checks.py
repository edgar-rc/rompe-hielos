"""Executable quality controls. Exits non-zero on any failure.

    python analytics/quality/checks.py --data-dir ./data --out-dir ./pipeline/out

Design principles, because a check suite that nobody trusts is worse than none:

**A check either fails the build or it is a warning, and which one is a
deliberate choice.** Structural invariants (uniqueness, referential integrity,
leakage, funnel arithmetic) are FAIL: if they break, every number downstream is
wrong. Distributional expectations (band shares) are FAIL only when the scores
come from the signed-off BA model, and WARN when they come from the pipeline's
own baseline scorer - the expected 33.4 / 38.5 / 28.1 split is a property of
that specific model's score distribution, so enforcing it against a different
scorer would be testing the wrong thing.

**Nulls are interrogated, not filled.** The dataset README claims two tables
carry nulls by design. The files disagree: `customers` and `onboarding_events`
have none, and the only nulls anywhere are 209,976 in the two leakage columns
of `activation_labels` - exactly 260,000 minus the 50,024 activated customers.
Those are structural: the field is empty because there was no transaction to
date. Both columns are dropped, so no imputation decision arises. The check
below asserts that finding rather than the README, and will fail loudly if a
future load contradicts it.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from pipeline.src import io as tio  # noqa: E402
from pipeline.src import state_machine as sm  # noqa: E402

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


@dataclass
class Report:
    rows: list[tuple[str, str, str, str]] = field(default_factory=list)

    def add(self, group: str, name: str, status: str, detail: str = "") -> None:
        self.rows.append((group, name, status, detail))
        icon = {PASS: "  ok  ", WARN: " warn ", FAIL: " FAIL "}[status]
        print(f"[{icon}] {group:<14} {name:<44} {detail}")

    def check(self, group: str, name: str, ok: bool, detail: str = "",
              on_fail: str = FAIL) -> bool:
        self.add(group, name, PASS if ok else on_fail, detail)
        return ok

    @property
    def failures(self) -> list[tuple[str, str, str, str]]:
        return [r for r in self.rows if r[2] == FAIL]

    @property
    def warnings(self) -> list[tuple[str, str, str, str]]:
        return [r for r in self.rows if r[2] == WARN]


def load_config(path: Path) -> dict:
    import yaml

    with open(path) as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# Source-table checks
# ---------------------------------------------------------------------------

def check_sources(r: Report, tables: dict, cfg: dict) -> None:
    customers = tables["customers"]
    events = tables["onboarding_events"]
    labels = tables["activation_labels"]
    n = int(cfg["population"]["total_signups"])

    r.check("integrity", "customers.customer_id unique",
            not customers["customer_id"].duplicated().any(),
            f"{customers['customer_id'].nunique():,} distinct")
    r.check("integrity", "activation_labels.customer_id unique",
            not labels["customer_id"].duplicated().any(),
            f"{labels['customer_id'].nunique():,} distinct")
    r.check("integrity", "events.event_id unique",
            not events["event_id"].duplicated().any() if "event_id" in events else True,
            f"{len(events):,} rows")

    cust_ids = set(customers["customer_id"])
    r.check("integrity", "events FK -> customers",
            set(events["customer_id"]).issubset(cust_ids),
            f"{events['customer_id'].nunique():,} referenced")
    r.check("integrity", "labels FK -> customers",
            set(labels["customer_id"]) == cust_ids,
            "1:1 with customers")

    r.check("volume", "customers row count", len(customers) == n, f"{len(customers):,}")
    r.check("volume", "activation_labels row count", len(labels) == n, f"{len(labels):,}")

    # ---- nulls: interrogate, do not fill ----------------------------------
    for name, df in (("customers", customers), ("onboarding_events", events)):
        nulls = int(df.isna().sum().sum())
        r.check("nulls", f"{name} has zero nulls", nulls == 0,
                f"{nulls:,} nulls across {df.shape[1]} columns")

    leak_cols = [c for c in ("days_to_first_transaction", "first_transaction_ts")
                 if c in labels.columns]
    other = [c for c in labels.columns if c not in leak_cols]
    r.check("nulls", "labels nulls confined to leakage columns",
            int(labels[other].isna().sum().sum()) == 0,
            f"non-leak columns: {int(labels[other].isna().sum().sum()):,} nulls")

    activated = int(labels["activated_30d"].sum())
    expected_nulls = len(labels) - activated
    for col in leak_cols:
        actual = int(labels[col].isna().sum())
        r.check("nulls", f"{col} nulls are structural", actual == expected_nulls,
                f"{actual:,} nulls = {len(labels):,} - {activated:,} activated")

    # ---- date ranges ------------------------------------------------------
    start = pd.Timestamp(cfg["scale"]["signup_window_start"])
    end = pd.Timestamp(cfg["scale"]["signup_window_end"]) + pd.Timedelta(days=1)
    in_range = customers["signup_ts"].between(start, end).all()
    r.check("ranges", "signup_ts inside the stated window", bool(in_range),
            f"{customers['signup_ts'].min()} .. {customers['signup_ts'].max()}")

    r.check("ranges", "age within [18, 78]",
            bool(customers["age"].between(18, 78).all()),
            f"{customers['age'].min()} .. {customers['age'].max()}")

    r.check("ranges", "step_number within [1, 7]",
            bool(events["step_number"].between(1, 7).all()), "")
    r.check("ranges", "ms_on_step non-negative",
            bool((events["ms_on_step"] >= 0).all()), "")
    r.check("ranges", "status in {completed, abandoned, retry}",
            set(events["status"].unique()) <= {"completed", "abandoned", "retry"},
            str(sorted(events["status"].unique())))

    # ---- target prevalence ------------------------------------------------
    completion = labels["completed_onboarding"].mean()
    activation = labels["activated_30d"].mean()
    r.check("target", "onboarding completion ~ 44.3%",
            abs(completion - 0.443) < 0.005, f"{100 * completion:.3f}%")
    r.check("target", "activated_30d ~ 19.2%",
            abs(activation - 0.192) < 0.005, f"{100 * activation:.3f}%")

    # ---- censoring --------------------------------------------------------
    monthly = (
        labels.merge(customers[["customer_id", "signup_ts"]], on="customer_id")
        .assign(month=lambda d: d["signup_ts"].dt.to_period("M"))
        .groupby("month")["activated_30d"].mean()
    )
    spread = float(monthly.max() - monthly.min())
    r.check("censoring", "monthly activation flat (no right-censoring)", spread < 0.02,
            f"spread {100 * spread:.2f}pp across {len(monthly)} months")


# ---------------------------------------------------------------------------
# Leakage checks
# ---------------------------------------------------------------------------

def check_leakage(r: Report, tables: dict, cfg: dict) -> None:
    labels = tables["activation_labels"]

    for col in ("days_to_first_transaction", "first_transaction_ts"):
        if col not in labels.columns:
            continue
        activated_null = int((labels["activated_30d"] & labels[col].isna()).sum())
        notact_filled = int((~labels["activated_30d"] & labels[col].notna()).sum())
        r.check("leakage", f"{col} null iff not activated",
                activated_null == 0 and notact_filled == 0,
                f"activated-but-null {activated_null}, not-activated-but-filled {notact_filled}")

    completed = labels["completed_onboarding"]
    r.check("leakage", "no activation without completing onboarding",
            int((~completed & labels["activated_30d"]).sum()) == 0,
            f"{int((~completed & labels['activated_30d']).sum())} violations")
    r.check("leakage", "completed_onboarding == (steps_completed == 7)",
            int((completed != (labels["steps_completed"] == 7)).sum()) == 0,
            "the two columns are the same fact")

    forbidden = set(cfg["forbidden_features"])
    r.check("leakage", "forbidden list covers both total-leak columns",
            {"days_to_first_transaction", "first_transaction_ts"} <= forbidden,
            f"{len(forbidden)} entries on the list")


# ---------------------------------------------------------------------------
# Funnel checks
# ---------------------------------------------------------------------------

def check_funnel(r: Report, tables: dict, cfg: dict) -> None:
    events = tables["onboarding_events"]
    customers = tables["customers"]
    labels = tables["activation_labels"]

    step_view = sm.build_customer_step_view(events)
    state_view = sm.build_customer_state_view(step_view, customers)
    funnel = sm.build_funnel_table(step_view, len(customers))

    r.check("dedup", "step view covers every customer",
            step_view["customer_id"].nunique() == len(customers),
            f"{step_view['customer_id'].nunique():,} of {len(customers):,}")
    r.check("dedup", "one row per customer-step",
            not step_view.duplicated(["customer_id", "step_number"]).any(), "")
    r.check("dedup", "retries collapsed, not dropped",
            int(step_view["attempts"].sum()) == len(events),
            f"sum(attempts) {int(step_view['attempts'].sum()):,} == "
            f"{len(events):,} event rows")

    # A row is not a person: the attempt-level rate must differ from the
    # customer-level rate at step 4, and the customer-level one is canonical.
    s4 = events[events["step_number"] == 4]
    attempt_rate = (s4["status"] == "abandoned").sum() / len(s4)
    customer_rate = float(
        funnel.loc[funnel["step_number"] == 4, "stage_abandonment_rate"].iloc[0]
    )
    r.check("dedup", "attempt-level != customer-level at step 4",
            abs(attempt_rate - customer_rate) > 0.05,
            f"attempt {100 * attempt_rate:.2f}% vs customer {100 * customer_rate:.2f}%")
    r.check("funnel", "step 4 stage abandonment ~ 34.16%",
            abs(customer_rate - 0.3416) < 0.002, f"{100 * customer_rate:.2f}%")

    r.check("funnel", "largest drop is step 4",
            int(funnel.loc[funnel["customers_abandoned"].idxmax(), "step_number"]) == 4,
            f"{int(funnel['customers_abandoned'].max()):,} customers lost")

    final = float(funnel["cumulative_reach_after_stage"].iloc[-1])
    r.check("funnel", "cumulative reach reproduces the 44.3% baseline",
            abs(final - 0.443) < 0.005, f"{100 * final:.3f}%")

    r.check("funnel", "state machine agrees with labels on completion",
            int((state_view.set_index("customer_id")["completed_onboarding"]
                 != labels.set_index("customer_id")["completed_onboarding"]).sum()) == 0,
            "0 disagreements")
    r.check("funnel", "max_step_reached agrees with steps_completed",
            int((state_view.set_index("customer_id")["max_step_reached"]
                 != labels.set_index("customer_id")["steps_completed"]).sum()) == 0,
            "0 disagreements")

    terminal = state_view["terminal_state"].value_counts()
    r.check("funnel", "no customer left without a terminal state",
            int(terminal.get("NO_EVENTS", 0)) == 0,
            f"{len(terminal)} distinct terminal states")


# ---------------------------------------------------------------------------
# Activation-profile checks
# ---------------------------------------------------------------------------

def check_profile(r: Report, out_dir: Path, cfg: dict, tables: dict) -> None:
    path = next(
        (out_dir / f"customer_activation_profile.{s}" for s in ("parquet", "csv")
         if (out_dir / f"customer_activation_profile.{s}").exists()),
        None,
    )
    if path is None:
        r.add("profile", "customer_activation_profile present", WARN,
              f"not found in {out_dir} - run the pipeline first")
        return

    profile = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    labels = tables["activation_labels"]

    r.check("profile", "customer_id unique",
            not profile["customer_id"].duplicated().any(), f"{len(profile):,} rows")
    r.check("profile", "population is the completers only",
            len(profile) == int(labels["completed_onboarding"].sum()),
            f"{len(profile):,} of {len(labels):,} signups")
    r.check("profile", "score within [0, 1], no nulls",
            bool(profile["activation_score"].between(0, 1).all())
            and not profile["activation_score"].isna().any(),
            f"{profile['activation_score'].min():.3f} .. "
            f"{profile['activation_score'].max():.3f}")

    # `customer_id` and `signup_ts` are on the forbidden list as *features* -
    # an identifier and the split column carry no signal and must never be
    # fitted on. They are still legitimate as the output table's key and
    # provenance, so they are exempted here rather than removed from the list.
    NOT_FEATURES_BUT_ALLOWED = {"customer_id", "signup_ts"}
    leak = sorted(
        (set(cfg["forbidden_features"]) - NOT_FEATURES_BUT_ALLOWED) & set(profile.columns)
    )
    r.check("profile", "no forbidden column in the output table", not leak,
            f"{leak}" if leak else "clean")

    r.check("profile", "thresholds_version stamped",
            profile["thresholds_version"].nunique() == 1
            and profile["thresholds_version"].iloc[0] == cfg["version"],
            str(profile["thresholds_version"].iloc[0]))

    # ---- band monotonicity: structural, always FAIL -----------------------
    merged = profile.merge(labels[["customer_id", "activated_30d"]], on="customer_id")
    rates = merged.groupby("profile_band")["activated_30d"].mean()
    ordered = [rates.get(b, float("nan")) for b in ("LOWER", "MIDDLE", "UPPER")]
    r.check("profile", "activation strictly increasing across bands",
            ordered[0] < ordered[1] < ordered[2],
            "  ".join(f"{b} {100 * v:.2f}%" for b, v in zip(("LOWER", "MIDDLE", "UPPER"), ordered)))

    # ---- band distribution: FAIL only for the signed-off model ------------
    supplied = str(profile["score_source"].iloc[0]).startswith("supplied")
    tolerance = float(cfg["bands"]["distribution_tolerance_pp"]) / 100
    for band, expected in cfg["bands"]["expected_distribution"].items():
        actual = float((profile["profile_band"] == band).mean())
        deviation = abs(actual - float(expected))
        r.check(
            "profile", f"{band} share within +/-{100 * tolerance:.0f}pp",
            deviation <= tolerance,
            f"{100 * actual:.2f}% vs expected {100 * float(expected):.1f}% "
            f"(dev {100 * deviation:.2f}pp)"
            + ("" if supplied else "; baseline scorer, expectation is model-specific"),
            on_fail=FAIL if supplied else WARN,
        )

    # ---- holdout ----------------------------------------------------------
    share = float(profile["holdout_flag"].mean())
    tol = float(cfg["holdout"]["share_tolerance"])
    target = float(cfg["holdout"]["share"])
    r.check("holdout", "overall share on target",
            abs(share - target) <= tol, f"{100 * share:.2f}% vs {100 * target:.0f}%")
    for band in ("LOWER", "MIDDLE", "UPPER"):
        sub = profile[profile["profile_band"] == band]
        if not len(sub):
            continue
        s = float(sub["holdout_flag"].mean())
        r.check("holdout", f"{band} share on target",
                abs(s - target) <= tol, f"{100 * s:.2f}%")

    # ---- do-no-harm guardrail --------------------------------------------
    banned = cfg["suppression"].get("no_incentive_for_bands", [])
    breach = int(
        profile["profile_band"].isin(banned).mul(
            profile["treatment_state"] == "COACH_INCENTIVE"
        ).sum()
    )
    r.check("guardrail", f"no incentive reaches {banned}", breach == 0,
            f"{breach} breaches")

    r.check("guardrail", "every holdout customer is suppressed",
            int((profile["holdout_flag"]
                 & (profile["treatment_state"] != "SUPPRESSED")).sum()) == 0, "")
    r.check("guardrail", "every suppression carries a reason",
            int(((profile["treatment_state"] == "SUPPRESSED")
                 & profile["suppression_reason"].isna()).sum()) == 0, "")


# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Quality controls")
    parser.add_argument("--data-dir", default=str(REPO_ROOT / "data"))
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "pipeline" / "out"))
    parser.add_argument("--config", default=str(REPO_ROOT / "config" / "activation_profile.yaml"))
    parser.add_argument("--generator", default=None)
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    loaded = tio.load_tables(
        args.data_dir,
        generator_path=args.generator,
        n_customers=int(cfg["population"]["total_signups"]),
    )
    tables = loaded.tables

    print()
    print("=" * 100)
    print(f"  Quality controls - source tier {loaded.tier} ({loaded.location})")
    print(f"  Config {Path(args.config).name}, thresholds version {cfg['version']}")
    print("=" * 100)
    print()

    r = Report()
    check_sources(r, tables, cfg)
    print()
    check_leakage(r, tables, cfg)
    print()
    check_funnel(r, tables, cfg)
    print()
    check_profile(r, Path(args.out_dir), cfg, tables)

    print()
    print("=" * 100)
    total = len(r.rows)
    print(f"  {total - len(r.failures) - len(r.warnings)} passed, "
          f"{len(r.warnings)} warning(s), {len(r.failures)} failure(s)")
    for _, name, _, detail in r.warnings:
        print(f"    warn: {name} - {detail}")
    for _, name, _, detail in r.failures:
        print(f"    FAIL: {name} - {detail}")
    print("=" * 100)

    return 1 if r.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
