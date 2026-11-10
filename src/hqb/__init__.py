"""HonorHealth Quality & Patient Experience Benchmark - automated CMS BI pipeline."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
NA_VALUES = {"", "Not Available", "Not Applicable", "N/A", None}


def load_settings() -> dict:
    with open(CONFIG_DIR / "settings.yaml") as fh:
        return yaml.safe_load(fh)


def to_number(value):
    """CMS publishes suppressed values as text like 'Not Available'. Convert to float or None."""
    if value in NA_VALUES:
        return None
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except ValueError:
        return None
