"""Current state legislators from the openstates/people GitHub repo (YAML, no key needed)."""
from __future__ import annotations

import logging
import tarfile
from datetime import date, datetime, timezone

import yaml
from psycopg2.extras import Json

from ..config import settings
from ..db import connect, run_log, upsert
from ..net import Client
from ..util import norm_district, party_code

log = logging.getLogger(__name__)
TARBALL = "https://codeload.github.com/openstates/people/tar.gz/refs/heads/main"


def person_row(doc: dict, state: str, today: str | None = None) -> dict | None:
    today = today or date.today().isoformat()
    role = None
    for r in doc.get("roles") or []:
        if r.get("type") not in ("upper", "lower", "legislature"):
            continue
        if r.get("end_date") and str(r["end_date"]) < today:
            continue
        if r.get("start_date") and str(r["start_date"]) > today:
            continue                      # elected but not yet seated
        role = r
    if not role:
        return None
    party = next((p.get("name") for p in doc.get("party") or []
                  if not p.get("end_date") or str(p["end_date"]) >= today), None)
    links = doc.get("links") or []
    pid = doc["id"]
    return {
        "politician_id": pid,
        "level": "state",
        # Nebraska's unicameral 'legislature' maps to TIGER's upper-chamber (SLDU) file
        "chamber": "lower" if role["type"] == "lower" else "upper",
        "state": state,
        "district": norm_district(role.get("district")),
        "full_name": doc.get("name"),
        "first_name": doc.get("given_name"),
        "last_name": doc.get("family_name"),
        "party": party,
        "party_code": party_code(party),
        "is_current": True,
        "term_start": str(role["start_date"]) if role.get("start_date") else None,
        "term_end": str(role["end_date"]) if role.get("end_date") else None,
        "bioguide_id": None, "lis_id": None, "icpsr_id": None, "govtrack_id": None,
        "fec_ids": [],
        "openstates_id": pid,
        "url": links[0].get("url") if links else None,
        "photo_url": doc.get("image"),
        "email": doc.get("email"),
        "extra": Json({"offices": doc.get("offices") or []}),
        "updated_at": datetime.now(timezone.utc),
    }


def run(states: list[str] | None = None) -> None:
    states = states or settings.states
    wanted = {s.lower() for s in states}
    with run_log("state_people") as stats:
        path = Client().download(TARBALL, settings.data_dir / "openstates-people.tar.gz", cache=False)
        rows = []
        with tarfile.open(path, "r:gz") as tf:
            for m in tf:
                parts = m.name.split("/")   # people-main/data/il/legislature/Name-uuid.yml
                if (not m.isfile() or len(parts) != 5 or parts[1] != "data"
                        or parts[2] not in wanted or parts[3] != "legislature"):
                    continue
                doc = yaml.safe_load(tf.extractfile(m))
                row = person_row(doc, parts[2].upper())
                if row:
                    rows.append(row)
        with connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE civic.politician SET is_current = false WHERE level = 'state' AND state = ANY(%s)",
                        ([s.upper() for s in wanted],))
            stats["rows"] = upsert(cur, "civic.politician", rows, ["politician_id"])
        log.info("state_people: %d legislators in %d state(s)", stats["rows"], len(wanted))
