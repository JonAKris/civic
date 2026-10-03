"""Current members of Congress from the unitedstates/congress-legislators dataset (no key)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from psycopg2.extras import Json

from ..db import connect, run_log, upsert
from ..net import Client
from ..util import CD_AT_LARGE, norm_district, party_code

log = logging.getLogger(__name__)
URL = "https://unitedstates.github.io/congress-legislators/legislators-current.json"


def to_row(m: dict) -> dict:
    ids, name, term = m["id"], m["name"], m["terms"][-1]
    chamber = "upper" if term["type"] == "sen" else "lower"
    bio = ids["bioguide"]
    full = name.get("official_full") or " ".join(x for x in (name.get("first"), name.get("last")) if x)
    return {
        "politician_id": bio,
        "level": "federal",
        "chamber": chamber,
        "state": term["state"],
        "district": None if chamber == "upper" else norm_district(term.get("district"), CD_AT_LARGE),
        "full_name": full,
        "first_name": name.get("first"),
        "last_name": name.get("last"),
        "party": term.get("party"),
        "party_code": party_code(term.get("party")),
        "is_current": True,
        "term_start": term.get("start"),
        "term_end": term.get("end"),
        "bioguide_id": bio,
        "lis_id": ids.get("lis"),
        "icpsr_id": str(ids["icpsr"]) if ids.get("icpsr") else None,
        "govtrack_id": str(ids["govtrack"]) if ids.get("govtrack") else None,
        "fec_ids": ids.get("fec") or [],
        "openstates_id": None,
        "url": term.get("url"),
        "photo_url": f"https://theunitedstates.io/images/congress/225x275/{bio}.jpg",
        "email": None,
        "extra": Json({"senate_class": term.get("class"), "state_rank": term.get("state_rank"),
                       "phone": term.get("phone"), "office": term.get("address")}),
        "updated_at": datetime.now(timezone.utc),
    }


def run() -> None:
    with run_log("federal_people") as stats:
        members = Client().json(URL)
        rows = [to_row(m) for m in members]
        with connect() as conn, conn.cursor() as cur:
            # anyone not in today's file (resigned, died) drops to is_current = false
            cur.execute("UPDATE civic.politician SET is_current = false WHERE level = 'federal'")
            stats["rows"] = upsert(cur, "civic.politician", rows, ["politician_id"])
        log.info("federal_people: %d current members", stats["rows"])
