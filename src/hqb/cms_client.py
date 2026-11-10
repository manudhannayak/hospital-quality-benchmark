"""Thin client for the CMS Provider Data Catalog datastore API (data.cms.gov/provider-data).

The API is public and needs no key. Results are paged with limit/offset.
"""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)


class CMSClient:
    def __init__(self, base_url: str, page_size: int = 500, timeout: int = 60, retries: int = 3):
        self.base_url = base_url.rstrip("/")
        self.page_size = page_size
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()

    def _get(self, url: str, params: dict) -> dict:
        for attempt in range(1, self.retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                if attempt == self.retries:
                    raise
                wait = 2 ** attempt
                log.warning("CMS request failed (%s), retry %s in %ss", exc, attempt, wait)
                time.sleep(wait)
        raise RuntimeError("unreachable")

    def query(self, dataset_id: str, filters: dict[str, str | list[str]]) -> list[dict]:
        """Return every row of a dataset that matches the filters (equality, or IN for lists)."""
        url = f"{self.base_url}/{dataset_id}/0"
        params: dict[str, str | int] = {"limit": self.page_size, "count": "true", "results": "true"}
        for i, (prop, value) in enumerate(filters.items()):
            params[f"conditions[{i}][property]"] = prop
            if isinstance(value, list):
                params[f"conditions[{i}][operator]"] = "in"
                for j, v in enumerate(value):
                    params[f"conditions[{i}][value][{j}]"] = v
            else:
                params[f"conditions[{i}][value]"] = value

        rows: list[dict] = []
        offset = 0
        while True:
            params["offset"] = offset
            payload = self._get(url, params)
            batch = payload.get("results", [])
            rows.extend(batch)
            total = int(payload.get("count", len(rows)))
            log.info("%s: fetched %s/%s rows", dataset_id, len(rows), total)
            if not batch or len(rows) >= total:
                break
            offset += self.page_size
        return rows
