"""DW-NOMINATE ideology scores from Voteview (UCLA). No key needed."""
from __future__ import annotations

import csv
import io
import logging

from ..config import settings
from ..db import connect, run_log, upsert
from ..net import Client

log = logging.getLogger(__name__)
URL = "https://voteview.com/static/data/out/members/{ch}{c}_members.csv"


def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def run(congress: int | None = None) -> None:
    c = congress or settings.congress
    client = Client(min_interval=1.0)
    with run_log("voteview") as stats:
        rows = []
        for ch, chamber in (("H", "lower"), ("S", "upper")):
            text = client.get(URL.format(ch=ch, c=c)).text
            for r in csv.DictReader(io.StringIO(text)):
                bid = (r.get("bioguide_id") or "").strip()
                if not bid or r.get("chamber") == "President":
                    continue
                rows.append({"politician_id": bid, "congress": int(r["congress"]), "chamber": chamber,
                             "icpsr_id": r.get("icpsr"), "nominate_dim1": _f(r.get("nominate_dim1")),
                             "nominate_dim2": _f(r.get("nominate_dim2"))})
        with connect() as conn, conn.cursor() as cur:
            stats["rows"] = upsert(cur, "civic.ideology", rows, ["politician_id", "congress", "chamber"])
        log.info("voteview: %d scores for the %dth Congress", stats["rows"], c)
