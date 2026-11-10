"""Data quality gate. Every CMS refresh must pass these checks before it reaches the dashboard.

Each check returns PASS, WARN (load continues, flagged in the report) or FAIL (pipeline stops,
the dashboard keeps the last good release, and an alert goes out).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

import pandas as pd

from . import to_number


@dataclass
class CheckResult:
    check: str
    dataset: str
    status: str  # PASS / WARN / FAIL
    detail: str


def _parse_date(s: str):
    try:
        return datetime.strptime(s, "%m/%d/%Y")
    except (TypeError, ValueError):
        return None


def run_checks(data: dict[str, pd.DataFrame], settings: dict) -> list[CheckResult]:
    cfg = settings["validation"]
    measures_cfg = settings["measures"]
    results: list[CheckResult] = []
    add = lambda *a: results.append(CheckResult(*a))  # noqa: E731

    hospitals = data["hospitals"]
    known_ids = set(hospitals["facility_id"])

    # 1. Volume: a partial pull must never overwrite a full one
    for name, df in data.items():
        n = df["facility_id"].nunique()
        ok = n >= cfg["min_hospitals_per_dataset"]
        add("row_volume", name, "PASS" if ok else "FAIL",
            f"{n} hospitals (minimum {cfg['min_hospitals_per_dataset']})")

    # 2. Primary key uniqueness
    dup = hospitals["facility_id"].duplicated().sum()
    add("unique_key", "hospitals", "PASS" if dup == 0 else "FAIL", f"{dup} duplicate facility_id")
    for name in ("hcahps", "readmits"):
        dup = data[name].duplicated(subset=["facility_id", "measure_id"]).sum()
        add("unique_key", name, "PASS" if dup == 0 else "FAIL", f"{dup} duplicate facility+measure rows")

    # 3. Referential integrity: every measure row must belong to a known hospital
    for name in ("hcahps", "readmits"):
        orphans = set(data[name]["facility_id"]) - known_ids
        add("referential_integrity", name, "PASS" if not orphans else "FAIL",
            f"{len(orphans)} facility_ids not in hospital dimension")

    # 4. Expected measures present
    expected = {"hcahps": settings["cms"]["datasets"]["hcahps"]["measures"],
                "readmits": settings["cms"]["datasets"]["readmissions"]["measures"]}
    for name, ms in expected.items():
        missing = set(ms) - set(data[name]["measure_id"])
        add("measures_present", name, "PASS" if not missing else "FAIL",
            "all expected measures present" if not missing else f"missing {sorted(missing)}")

    # 5. Suppression / null rate per measure (CMS suppresses small hospitals; spikes mean trouble)
    long = pd.concat([data["hcahps"], data["readmits"]])
    for measure_id, grp in long.groupby("measure_id"):
        rate = grp["score"].map(to_number).isna().mean()
        status = "PASS" if rate <= cfg["max_null_rate"] else "WARN"
        add("null_rate", measure_id, status, f"{rate:.0%} not reported (limit {cfg['max_null_rate']:.0%})")
    # CMS only star-rates acute care hospitals; psychiatric, rehab and critical access sites are N/A by design.
    acute = hospitals[hospitals["hospital_type"] == "Acute Care Hospitals"]
    rating_null = acute["hospital_overall_rating"].map(to_number).isna().mean()
    add("null_rate", "OVERALL_STAR", "PASS" if rating_null <= cfg["max_null_rate"] else "WARN",
        f"{rating_null:.0%} of {len(acute)} acute care hospitals not rated")

    # 6. Value ranges
    for measure_id, grp in long.groupby("measure_id"):
        unit = measures_cfg.get(measure_id, {}).get("unit", "pct")
        lo, hi = cfg["value_ranges"][unit]
        vals = grp["score"].map(to_number).dropna()
        bad = int(((vals < lo) | (vals > hi)).sum())
        add("value_range", measure_id, "PASS" if bad == 0 else "FAIL", f"{bad} values outside [{lo}, {hi}]")

    # 7. Reporting period: one consistent period per measure, and parseable dates
    for measure_id, grp in long.groupby("measure_id"):
        periods = grp[["start_date", "end_date"]].drop_duplicates()
        parsed = all(_parse_date(d) for d in periods["end_date"])
        status = "PASS" if len(periods) == 1 and parsed else "WARN"
        add("reporting_period", measure_id, status,
            f"{len(periods)} distinct period(s), ending {', '.join(periods['end_date'])}")

    return results


def summarize(results: list[CheckResult]) -> dict:
    counts = {s: sum(r.status == s for r in results) for s in ("PASS", "WARN", "FAIL")}
    return {"counts": counts, "passed": counts["FAIL"] == 0, "results": [asdict(r) for r in results]}
