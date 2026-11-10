"""Orchestrator: extract -> validate -> load -> analyze -> narrate -> deliver.

Usage:
    python -m hqb.pipeline --source cms          # live pull from CMS (scheduled job)
    python -m hqb.pipeline --source snapshot     # bundled real CMS snapshot, no network needed
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date

import pandas as pd

from . import OUTPUT_DIR, load_settings
from .analyze import build_facts
from .deliver import post_failure_alert, post_slack, render_html, send_email
from .ingest import extract
from .narrative import write_summary
from .site import build_site_data
from .transform import connect, export_for_powerbi, load
from .validate import run_checks, summarize

log = logging.getLogger("hqb")


def run(source: str, release_id: str, use_llm: bool = True, db_name: str = "warehouse.db") -> int:
    settings = load_settings()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    data = extract(settings, source)
    log.info("Extracted %s", {k: len(v) for k, v in data.items()})

    dq = summarize(run_checks(data, settings))
    (OUTPUT_DIR / "dq_results.json").write_text(json.dumps(dq, indent=2))
    log.info("Quality gate: %s", dq["counts"])

    conn = connect(OUTPUT_DIR / db_name)
    if not dq["passed"]:
        # Record the failed release for audit, but do not load facts or publish.
        conn.execute("INSERT OR REPLACE INTO dim_release VALUES (?, datetime('now'), ?, 0, ?)",
                     (release_id, source, dq["counts"]["WARN"]))
        conn.commit()
        post_failure_alert(dq)
        log.error("Quality gate FAILED; release %s not published", release_id)
        return 1

    load(conn, data, settings, release_id, source, dq)
    pbi = export_for_powerbi(conn, OUTPUT_DIR / "powerbi")
    log.info("Power BI extracts: %s", [p.name for p in pbi])

    facts = build_facts(conn, settings, dq)
    (OUTPUT_DIR / "facts.json").write_text(json.dumps(facts, indent=2))

    summary, method = write_summary(facts, use_llm=use_llm)
    (OUTPUT_DIR / "summary.txt").write_text(summary)
    log.info("Summary written by %s", method)

    rank_rows = pd.read_sql("SELECT * FROM vw_market_rank", conn).to_dict("records")
    report = OUTPUT_DIR / "benchmark_report.html"
    report.write_text(render_html(facts, summary, method, dq, rank_rows))
    log.info("Report: %s", report)

    site_data = build_site_data(conn, facts, summary, method, dq)
    (site_data.parent / "report.html").write_text(report.read_text())
    log.info("Live dashboard data: %s", site_data)

    if post_slack(summary, facts):
        log.info("Posted digest to Slack")
    if send_email(report.read_text(), facts, report):
        log.info("Emailed report")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="CMS quality & patient experience benchmark pipeline")
    p.add_argument("--source", choices=["cms", "snapshot"], default="cms")
    p.add_argument("--release", default=date.today().isoformat(), help="release id (default: today)")
    p.add_argument("--no-llm", action="store_true", help="skip the AI summary, use the template")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(run(args.source, args.release, use_llm=not args.no_llm))


if __name__ == "__main__":
    main()
