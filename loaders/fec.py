"""Campaign finance totals per cycle from the OpenFEC API (federal members only).

A standard api.data.gov key allows 1,000 FEC requests/hour, so this is paced at ~3.7 s/request
(~40 min for all of Congress). Ask the FEC for an upgraded key if you want it faster.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..config import settings
from ..db import connect, fetch_all, run_log, upsert
from ..net import Client

log = logging.getLogger(__name__)
API = "https://api.open.fec.gov/v1"


def _ids_for(row: dict) -> list[str]:
    """Prefer FEC ids for the chamber they now sit in (H... / S...)."""
    ids = [i for i in (row["fec_ids"] or []) if i]
    prefix = "S" if row["chamber"] == "upper" else "H"
    return [i for i in ids if i.startswith(prefix)] or ids


def run(member: str | None = None, min_interval: float = 3.7) -> None:
    key = settings.require("fec_api_key", "FEC_API_KEY")
    client = Client(min_interval=min_interval)
    sql = "SELECT politician_id, chamber, fec_ids FROM civic.politician WHERE level='federal' AND is_current"
    members = fetch_all(sql + (" AND politician_id = %s" if member else ""), (member,) if member else None)
    with run_log("fec") as stats:
        for i, m in enumerate(members, 1):
            rows = []
            for cid in _ids_for(m):
                data = client.json(f"{API}/candidate/{cid}/totals/",
                                   params={"api_key": key, "per_page": 10, "sort": "-cycle"},
                                   ok404=True) or {}
                for r in data.get("results") or []:
                    rows.append({
                        "politician_id": m["politician_id"],
                        "fec_candidate_id": cid,
                        "cycle": int(r["cycle"]),
                        "receipts": r.get("receipts"),
                        "disbursements": r.get("disbursements"),
                        "individual_contributions": r.get("individual_contributions"),
                        "pac_contributions": r.get("other_political_committee_contributions"),
                        "cash_on_hand": r.get("last_cash_on_hand_end_period",
                                              r.get("cash_on_hand_end_period")),
                        "coverage_end_date": (r.get("coverage_end_date") or "")[:10] or None,
                        "updated_at": datetime.now(timezone.utc),
                    })
            with connect() as conn, conn.cursor() as cur:
                stats["rows"] += upsert(cur, "civic.finance_summary", rows,
                                        ["politician_id", "fec_candidate_id", "cycle"])
            if i % 50 == 0:
                log.info("fec: %d/%d members", i, len(members))
