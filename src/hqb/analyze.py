"""Turn the warehouse views into a compact 'facts' payload.

Every number in the digest (including the AI-written summary) comes from this payload,
so the narrative can be checked against it.
"""
from __future__ import annotations

import sqlite3

import pandas as pd


def _better_gap(value: float, ref: float, higher_is_better: int) -> float:
    """Positive = better than reference, regardless of measure direction."""
    return round((value - ref) if higher_is_better else (ref - value), 2)


def build_facts(conn: sqlite3.Connection, settings: dict, dq: dict) -> dict:
    focus = settings["focus_system"]
    rank = pd.read_sql("SELECT * FROM vw_market_rank", conn)
    score = pd.read_sql("SELECT * FROM vw_system_scorecard", conn)
    release = pd.read_sql("SELECT * FROM dim_release ORDER BY release_id DESC LIMIT 1", conn).iloc[0]
    periods = pd.read_sql(
        "SELECT measure_id, MIN(period_start) ps, MAX(period_end) pe FROM fact_measure "
        "WHERE release_id = ? AND period_end IS NOT NULL GROUP BY measure_id", conn,
        params=(release["release_id"],))

    measures = []
    for mid, grp in score.groupby("measure_id", sort=False):
        mine = grp[grp["health_system"] == focus]
        if mine.empty:
            continue
        hib = int(mine["higher_is_better"].iloc[0])
        named = grp[grp["health_system"] != "Independent / Other"].copy()
        named["sort"] = named["system_avg"] * (1 if hib else -1)
        ordered = named.sort_values("sort", ascending=False)
        measures.append({
            "measure_id": mid,
            "label": mine["measure_label"].iloc[0],
            "unit": mine["unit"].iloc[0],
            "higher_is_better": bool(hib),
            "focus_avg": float(mine["system_avg"].iloc[0]),
            "market_avg": float(mine["market_avg"].iloc[0]),
            "gap_vs_market": _better_gap(mine["system_avg"].iloc[0], mine["market_avg"].iloc[0], hib),
            "focus_rank_among_systems": int(list(ordered["health_system"]).index(focus) + 1),
            "systems_compared": int(len(ordered)),
            "leader_system": ordered["health_system"].iloc[0],
            "leader_avg": float(ordered["system_avg"].iloc[0]),
            "system_avgs": {r.health_system: float(r.system_avg) for r in ordered.itertuples()},
        })

    hospitals = []
    focus_rows = rank[rank["health_system"] == focus]
    for name, grp in focus_rows.groupby("facility_name"):
        hospitals.append({
            "hospital": name,
            "measures": {
                r.measure_id: {"value": float(r.value), "rank": int(r.market_rank), "of": int(r.market_n),
                               "gap_vs_market": _better_gap(r.value, r.market_avg, r.higher_is_better),
                               "low_volume": bool(r.low_volume_flag)}
                for r in grp.itertuples()
            },
        })

    # Strengths / concerns: focus hospitals in the top or bottom quartile of the market
    rank["pctile"] = (rank["market_rank"] - 1) / (rank["market_n"] - 1).clip(lower=1)
    fr = rank[(rank["health_system"] == focus) & (rank["low_volume_flag"] == 0)]
    to_list = lambda df: [  # noqa: E731
        {"hospital": r.facility_name, "measure": r.measure_label, "value": float(r.value),
         "rank": int(r.market_rank), "of": int(r.market_n), "market_avg": float(r.market_avg)}
        for r in df.itertuples()]
    strengths = to_list(fr[fr["pctile"] <= 0.25].sort_values("pctile"))
    concerns = to_list(fr[fr["pctile"] >= 0.75].sort_values("pctile", ascending=False))

    # Alerts: release-over-release moves beyond configured thresholds
    alerts_cfg = settings["alerts"]
    chg = pd.read_sql("SELECT c.*, m.unit, m.measure_label FROM vw_release_change c "
                      "JOIN dim_measure m USING (measure_id)", conn)
    if not chg.empty:
        limit = chg["unit"].map({"stars": alerts_cfg["star_change"], "pct": alerts_cfg["pct_change"]})
        chg = chg[chg["change"].abs() >= limit]
    alerts = [{"hospital": r.facility_name, "system": r.health_system, "measure": r.measure_label,
               "prior": float(r.prior_value), "current": float(r.value), "change": float(r.change)}
              for r in chg.itertuples()]

    return {
        "focus_system": focus,
        "release_id": release["release_id"],
        "source": release["source"],
        "market": "Phoenix metro acute-care hospitals (Maricopa + Pinal counties)",
        "market_hospitals": int(rank["facility_id"].nunique()),
        "focus_hospitals": int(focus_rows["facility_id"].nunique()),
        "reporting_periods": {r.measure_id: f"{r.ps} to {r.pe}" for r in periods.itertuples()},
        "measures": measures,
        "hospitals": hospitals,
        "strengths": strengths,
        "concerns": concerns,
        "alerts": alerts,
        "data_quality": dq["counts"],
    }
