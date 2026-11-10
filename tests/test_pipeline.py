"""Tests for the quality gate, transforms, alerting and the AI-summary guardrail."""
import sqlite3

import pandas as pd
import pytest

from hqb import load_settings, to_number
from hqb.analyze import build_facts
from hqb.ingest import load_snapshot
from hqb.narrative import template_summary, ungrounded_numbers
from hqb.transform import build_fact, clean_name, connect, load
from hqb.validate import run_checks, summarize


@pytest.fixture
def settings():
    return load_settings()


@pytest.fixture
def data():
    return load_snapshot()


def test_to_number_handles_cms_suppression():
    assert to_number("Not Available") is None
    assert to_number("19.4") == 19.4
    assert to_number("1,209") == 1209


def test_clean_name():
    assert clean_name("HONORHEALTH SCOTTSDALE THOMPSON PEAK MED CTR") == \
        "HonorHealth Scottsdale Thompson Peak Medical Center"


def test_snapshot_passes_quality_gate(data, settings):
    dq = summarize(run_checks(data, settings))
    assert dq["passed"], [r for r in dq["results"] if r["status"] == "FAIL"]


def test_gate_fails_on_partial_pull(data, settings):
    data["hcahps"] = data["hcahps"].head(30)
    dq = summarize(run_checks(data, settings))
    assert not dq["passed"]


def test_gate_fails_on_out_of_range_value(data, settings):
    data["readmits"].loc[0, "score"] = "140"
    dq = summarize(run_checks(data, settings))
    failed = {(r["check"], r["dataset"]) for r in dq["results"] if r["status"] == "FAIL"}
    assert ("value_range", data["readmits"].loc[0, "measure_id"]) in failed


def test_gate_fails_on_orphan_facility(data, settings):
    extra = data["hcahps"].iloc[[0]].assign(facility_id="999999")
    data["hcahps"] = pd.concat([data["hcahps"], extra])
    dq = summarize(run_checks(data, settings))
    assert not dq["passed"]


def test_low_volume_flag(data, settings):
    fact = build_fact(data, settings, "r1")
    tempe = fact[(fact.facility_id == "030037") & (fact.measure_id == "READM_30_PN")].iloc[0]
    assert tempe.denominator == 57 and tempe.low_volume_flag == 0
    small = fact[(fact.measure_id == "H_STAR_RATING") & (fact.denominator < 100)]
    assert small.low_volume_flag.eq(1).all()


def _warehouse(tmp_path, data, settings, release="2026-11-01"):
    conn = connect(tmp_path / "w.db")
    dq = summarize(run_checks(data, settings))
    load(conn, data, settings, release, "snapshot", dq)
    return conn, dq


def test_focus_system_average_matches_source(tmp_path, data, settings):
    conn, dq = _warehouse(tmp_path, data, settings)
    facts = build_facts(conn, settings, dq)
    rec = next(m for m in facts["measures"] if m["measure_id"] == "H_RECMND_DY")
    # 8 HonorHealth campuses: 72, 53, 77, 72, 72, 60, 84, 84
    assert rec["focus_avg"] == pytest.approx(71.75)
    assert facts["focus_hospitals"] == 8


def test_readmission_rank_respects_direction(tmp_path, data, settings):
    conn, _ = _warehouse(tmp_path, data, settings)
    best = conn.execute("SELECT facility_id, value FROM vw_market_rank "
                        "WHERE measure_id='READM_30_HF' AND market_rank=1").fetchone()
    lowest = conn.execute("SELECT MIN(value) FROM vw_market_rank WHERE measure_id='READM_30_HF'").fetchone()
    assert best[1] == lowest[0]  # lowest readmission rate ranks first


def test_rerun_is_idempotent(tmp_path, data, settings):
    conn, _ = _warehouse(tmp_path, data, settings)
    n1 = conn.execute("SELECT COUNT(*) FROM fact_measure").fetchone()[0]
    _warehouse(tmp_path, data, settings)
    n2 = sqlite3.connect(tmp_path / "w.db").execute("SELECT COUNT(*) FROM fact_measure").fetchone()[0]
    assert n1 == n2


def test_release_change_alert(tmp_path, data, settings):
    _warehouse(tmp_path, data, settings, "2026-08-01")
    changed = {k: v.copy() for k, v in data.items()}
    mask = (changed["hcahps"].facility_id == "030087") & (changed["hcahps"].measure_id == "H_RECMND_DY")
    changed["hcahps"].loc[mask, "score"] = "65"  # Shea drops from 72 to 65
    conn, dq = _warehouse(tmp_path, changed, settings, "2026-11-01")
    facts = build_facts(conn, settings, dq)
    assert any(a["change"] == -7 and "Shea" in a["hospital"] for a in facts["alerts"])


def test_guardrail_rejects_invented_numbers(tmp_path, data, settings):
    conn, dq = _warehouse(tmp_path, data, settings)
    facts = build_facts(conn, settings, dq)
    assert not ungrounded_numbers(template_summary(facts), facts)
    assert "47.3" in ungrounded_numbers("Shea improved to 47.3% recommend.", facts)
