"""Publish step: write the live dashboard's data file (docs/data.json) for GitHub Pages.

The dashboard (docs/index.html) is static HTML/JS that reads this file, so every scheduled
pipeline run refreshes the public site with no server.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import ROOT

SITE_DIR = ROOT / "docs"


def build_site_data(conn: sqlite3.Connection, facts: dict, summary: str, method: str, dq: dict) -> Path:
    hospitals = pd.read_sql("SELECT * FROM dim_hospital WHERE in_market = 1", conn)
    measures = pd.read_sql("SELECT * FROM dim_measure", conn)
    rank = pd.read_sql("SELECT facility_id, measure_id, value, denominator, low_volume_flag, "
                       "market_rank, market_n, market_avg FROM vw_market_rank", conn)
    releases = pd.read_sql("SELECT release_id, source, dq_pass FROM dim_release ORDER BY release_id", conn)
    history = pd.read_sql(
        "SELECT f.release_id, h.health_system, f.measure_id, ROUND(AVG(f.value), 2) AS avg "
        "FROM fact_measure f JOIN dim_hospital h USING (facility_id) "
        "JOIN dim_release r ON r.release_id = f.release_id AND r.dq_pass = 1 "
        "WHERE h.in_market = 1 AND f.value IS NOT NULL "
        "GROUP BY f.release_id, h.health_system, f.measure_id", conn)

    values: dict[str, dict] = {}
    for r in rank.itertuples():
        values.setdefault(r.facility_id, {})[r.measure_id] = {
            "v": r.value, "n": None if pd.isna(r.denominator) else int(r.denominator),
            "lv": bool(r.low_volume_flag), "rank": int(r.market_rank), "of": int(r.market_n)}

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "focus_system": facts["focus_system"],
        "release_id": facts["release_id"],
        "source": facts["source"],
        "market": facts["market"],
        "reporting_periods": facts["reporting_periods"],
        "summary": summary,
        "summary_method": method,
        "measures": measures.to_dict("records"),
        "system_scorecard": facts["measures"],
        "hospitals": [
            {"id": h.facility_id, "name": h.facility_name, "city": h.city, "system": h.health_system,
             "focus": bool(h.is_focus_system), "values": values.get(h.facility_id, {})}
            for h in hospitals.itertuples() if h.facility_id in values],
        "alerts": facts["alerts"],
        "data_quality": dq,
        "releases": releases.to_dict("records"),
        "history": history.to_dict("records"),
    }
    SITE_DIR.mkdir(exist_ok=True)
    path = SITE_DIR / "data.json"
    path.write_text(json.dumps(payload, indent=1, default=float))
    return path
