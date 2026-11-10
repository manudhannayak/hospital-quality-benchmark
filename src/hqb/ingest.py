"""Extract step: pull the latest CMS release (or load the bundled snapshot) into a common shape.

Output is three pandas DataFrames:
  hospitals : one row per hospital (facility_id, name, location, type, ownership, overall rating)
  hcahps    : long format  facility_id, measure_id, score, denominator, start_date, end_date
  readmits  : same long format
"""
from __future__ import annotations

import logging

import pandas as pd

from . import DATA_DIR
from .cms_client import CMSClient

log = logging.getLogger(__name__)

HOSPITAL_COLS = ["facility_id", "facility_name", "citytown", "countyparish", "state",
                 "hospital_type", "hospital_ownership", "hospital_overall_rating"]
LONG_COLS = ["facility_id", "measure_id", "score", "denominator", "start_date", "end_date"]


def load_snapshot() -> dict[str, pd.DataFrame]:
    snap = DATA_DIR / "snapshot"
    read = lambda name: pd.read_csv(snap / name, dtype=str, keep_default_na=False)  # noqa: E731
    return {
        "hospitals": read("hospital_general_info.csv"),
        "hcahps": read("hcahps.csv"),
        "readmits": read("readmissions.csv"),
    }


def pull_from_cms(settings: dict) -> dict[str, pd.DataFrame]:
    cms = settings["cms"]
    state = settings["market"]["state"]
    client = CMSClient(cms["base_url"], cms["page_size"], cms["timeout_seconds"])
    ds = cms["datasets"]

    hosp_raw = client.query(ds["hospital_general_info"]["id"], {"state": state})
    hospitals = pd.DataFrame(hosp_raw)[HOSPITAL_COLS]

    hc_raw = pd.DataFrame(client.query(ds["hcahps"]["id"],
                                       {"state": state, "hcahps_measure_id": ds["hcahps"]["measures"]}))
    # Star-rating measures keep their value in a different column than percentage measures.
    is_star = hc_raw["hcahps_measure_id"].str.endswith("STAR_RATING")
    hcahps = pd.DataFrame({
        "facility_id": hc_raw["facility_id"],
        "measure_id": hc_raw["hcahps_measure_id"],
        "score": hc_raw["patient_survey_star_rating"].where(is_star, hc_raw["hcahps_answer_percent"]),
        "denominator": hc_raw["number_of_completed_surveys"].where(is_star, ""),
        "start_date": hc_raw["start_date"],
        "end_date": hc_raw["end_date"],
    })

    rd_raw = pd.DataFrame(client.query(ds["readmissions"]["id"],
                                       {"state": state, "measure_id": ds["readmissions"]["measures"]}))
    readmits = rd_raw[LONG_COLS]

    return {"hospitals": hospitals.astype(str), "hcahps": hcahps.astype(str), "readmits": readmits.astype(str)}


def extract(settings: dict, source: str) -> dict[str, pd.DataFrame]:
    if source == "snapshot":
        log.info("Loading bundled CMS snapshot")
        return load_snapshot()
    log.info("Pulling live data from CMS Provider Data Catalog")
    return pull_from_cms(settings)
