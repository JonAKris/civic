"""Sponsored + cosponsored bills for each current member (Congress.gov API v3).

Limit is 5,000 requests/hour per key; ~540 members x 2 lists x 1-2 pages takes ~20 min.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..config import settings
from ..db import connect, fetch_all, run_log, upsert
from ..net import Client

log = logging.getLogger(__name__)
API = "https://api.congress.gov/v3"
TYPE_SLUG = {
    "hr": "house-bill", "s": "senate-bill",
    "hres": "house-resolution", "sres": "senate-resolution",
    "hjres": "house-joint-resolution", "sjres": "senate-joint-resolution",
    "hconres": "house-concurrent-resolution", "sconres": "senate-concurrent-resolution",
}


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def bill_row(item: dict) -> dict | None:
    t, num, c = item.get("type"), item.get("number"), item.get("congress")
    if not (t and num and c):          # amendments come through these lists too; skip them
        return None
    t = t.lower()
    la = item.get("latestAction") or {}
    text = la.get("text") or ""
    slug = TYPE_SLUG.get(t)
    return {
        "bill_id": f"us-{c}-{t}-{num}",
        "level": "federal",
        "state": None,
        "session": str(c),
        "chamber": "lower" if t.startswith("h") else "upper",
        "identifier": f"{t.upper()} {num}",
        "title": item.get("title"),
        "introduced_date": item.get("introducedDate"),
        "latest_action_date": la.get("actionDate"),
        "latest_action": text or None,
        "became_law": "Became Public Law" in text or "Became Private Law" in text,
        "policy_area": (item.get("policyArea") or {}).get("name"),
        "url": f"https://www.congress.gov/bill/{ordinal(int(c))}-congress/{slug}/{num}" if slug else item.get("url"),
        "updated_at": datetime.now(timezone.utc),
    }


def member_items(client: Client, key: str, bioguide: str, kind: str):
    """Yield items from /member/{id}/{sponsored|cosponsored}-legislation (newest first)."""
    field = "sponsoredLegislation" if kind == "sponsored" else "cosponsoredLegislation"
    offset = 0
    while True:
        data = client.json(f"{API}/member/{bioguide}/{kind}-legislation",
                           params={"api_key": key, "format": "json", "limit": 250, "offset": offset},
                           ok404=True) or {}
        items = data.get(field) or []
        yield from items
        if len(items) < 250 or not (data.get("pagination") or {}).get("next"):
            return
        if all((it.get("congress") or 0) < settings.congress for it in items[-10:]):
            return                       # paged past the congress we care about
        offset += 250


def run(member: str | None = None) -> None:
    key = settings.require("congress_api_key", "CONGRESS_API_KEY")
    client = Client(min_interval=0.75)
    if member:
        members = [member]
    else:
        members = [r["politician_id"] for r in fetch_all(
            "SELECT politician_id FROM civic.politician WHERE level='federal' AND is_current ORDER BY 1")]
    with run_log("congress_bills") as stats:
        for i, bio in enumerate(members, 1):
            bills, spons = [], []
            for kind in ("sponsored", "cosponsored"):
                for item in member_items(client, key, bio, kind):
                    if item.get("congress") != settings.congress:
                        continue
                    row = bill_row(item)
                    if not row:
                        continue
                    bills.append(row)
                    spons.append({"bill_id": row["bill_id"], "sponsor_key": bio, "politician_id": bio,
                                  "sponsor_name": None, "is_primary": kind == "sponsored"})
            with connect() as conn, conn.cursor() as cur:
                upsert(cur, "civic.bill", bills, ["bill_id"])
                stats["rows"] += upsert(cur, "civic.sponsorship", spons, ["bill_id", "sponsor_key"])
            if i % 50 == 0:
                log.info("congress_bills: %d/%d members", i, len(members))
        log.info("congress_bills: %d sponsorship rows", stats["rows"])
