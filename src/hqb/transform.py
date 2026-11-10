"""Load step: conform the extracted data into the star schema (SQLite warehouse)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import CONFIG_DIR, to_number

SQL_DIR = Path(__file__).parent / "sql"
DOMAINS = {"OVERALL_STAR": "Overall", "READM_30_HF": "Readmissions", "READM_30_PN": "Readmissions"}


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript((SQL_DIR / "schema.sql").read_text())
    return conn


NAME_FIXES = {"Honorhealth": "HonorHealth", "Honor Health": "HonorHealth", " Med Ctr": " Medical Center",
              "Va ": "VA ", "Er ": "ER ", "'S ": "'s ", "Hosp ": "Hospital ", "Hosp$": "Hospital"}


def clean_name(name: str) -> str:
    """CMS names are upper case; make them readable and consistent."""
    out = name.title()
    for bad, good in NAME_FIXES.items():
        if bad.endswith("$"):
            out = out[: -len(bad) + 1] + good if out.endswith(bad[:-1]) else out
        else:
            out = out.replace(bad, good)
    return out.replace(" - ", " ").replace("Banner University", "Banner-University")


def build_dim_hospital(hospitals: pd.DataFrame, settings: dict) -> pd.DataFrame:
    systems = pd.read_csv(CONFIG_DIR / "health_systems.csv", dtype=str)
    market = settings["market"]
    df = hospitals.merge(systems, on="facility_id", how="left")
    df["health_system"] = df["health_system"].fillna("Independent / Other")
    return pd.DataFrame({
        "facility_id": df["facility_id"],
        "facility_name": df["facility_name"].map(clean_name),
        "city": df["citytown"].str.title(),
        "county": df["countyparish"].str.title(),
        "hospital_type": df["hospital_type"],
        "ownership": df["hospital_ownership"],
        "health_system": df["health_system"],
        "is_focus_system": (df["health_system"] == settings["focus_system"]).astype(int),
        "in_market": (df["countyparish"].isin(market["counties"])
                      & df["hospital_type"].isin(market["hospital_types"])).astype(int),
    })


def build_dim_measure(settings: dict) -> pd.DataFrame:
    rows = []
    for mid, m in settings["measures"].items():
        rows.append({"measure_id": mid, "measure_label": m["label"], "unit": m["unit"],
                     "higher_is_better": int(m["higher_is_better"]),
                     "domain": DOMAINS.get(mid, "Patient Experience")})
    return pd.DataFrame(rows)


def build_fact(data: dict[str, pd.DataFrame], settings: dict, release_id: str) -> pd.DataFrame:
    v = settings["validation"]
    h = data["hospitals"]
    overall = pd.DataFrame({
        "facility_id": h["facility_id"], "measure_id": "OVERALL_STAR",
        "score": h["hospital_overall_rating"], "denominator": "", "start_date": "", "end_date": "",
    })
    long = pd.concat([overall, data["hcahps"], data["readmits"]], ignore_index=True)
    fact = pd.DataFrame({
        "release_id": release_id,
        "facility_id": long["facility_id"],
        "measure_id": long["measure_id"],
        "value": long["score"].map(to_number),
        "denominator": long["denominator"].map(to_number),
        "period_start": long["start_date"].replace("", None),
        "period_end": long["end_date"].replace("", None),
    })
    # Low-volume flag: results built on few surveys / cases are statistically shaky
    thresholds = {"H_STAR_RATING": v["min_hcahps_surveys"],
                  "READM_30_HF": v["min_readmit_denominator"], "READM_30_PN": v["min_readmit_denominator"]}
    fact["low_volume_flag"] = [
        int(m in thresholds and d is not None and d == d and d < thresholds[m])
        for m, d in zip(fact["measure_id"], fact["denominator"])
    ]
    return fact


def load(conn: sqlite3.Connection, data: dict[str, pd.DataFrame], settings: dict,
         release_id: str, source: str, dq: dict) -> None:
    dim_h = build_dim_hospital(data["hospitals"], settings)
    dim_m = build_dim_measure(settings)
    fact = build_fact(data, settings, release_id)

    with conn:
        # Dimensions are upserted (type 1 SCD): latest CMS attributes win.
        conn.executemany(
            "INSERT OR REPLACE INTO dim_hospital VALUES (?,?,?,?,?,?,?,?,?)",
            dim_h.itertuples(index=False, name=None))
        conn.executemany(
            "INSERT OR REPLACE INTO dim_measure VALUES (?,?,?,?,?)",
            dim_m.itertuples(index=False, name=None))
        conn.execute(
            "INSERT OR REPLACE INTO dim_release VALUES (?,?,?,?,?)",
            (release_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), source,
             int(dq["passed"]), dq["counts"]["WARN"]))
        # Re-running the same release is idempotent.
        conn.execute("DELETE FROM fact_measure WHERE release_id = ?", (release_id,))
        conn.executemany(
            "INSERT INTO fact_measure (release_id, facility_id, measure_id, value, denominator, "
            "period_start, period_end, low_volume_flag) VALUES (?,?,?,?,?,?,?,?)",
            fact[["release_id", "facility_id", "measure_id", "value", "denominator",
                  "period_start", "period_end", "low_volume_flag"]]
            .astype(object).where(fact.notna(), None).itertuples(index=False, name=None))
    conn.executescript((SQL_DIR / "views.sql").read_text())


def export_for_powerbi(conn: sqlite3.Connection, out_dir: Path) -> list[Path]:
    """Write the star-schema tables + views as CSV for Power BI (Get Data > Text/CSV or folder)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for table in ("dim_hospital", "dim_measure", "dim_release", "fact_measure",
                  "vw_market_rank", "vw_system_scorecard"):
        p = out_dir / f"{table}.csv"
        pd.read_sql(f"SELECT * FROM {table}", conn).to_csv(p, index=False)
        paths.append(p)
    return paths
