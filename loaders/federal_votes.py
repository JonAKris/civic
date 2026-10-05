"""House (clerk.house.gov) and Senate (senate.gov) roll-call XML -> vote_event / vote_cast.

Incremental: the last loaded roll number per year/session is checkpointed in civic.etl_state.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime

from ..config import settings
from ..db import connect, fetch_all, get_checkpoint, run_log, set_checkpoint, upsert
from ..net import Client
from ..util import norm_option

log = logging.getLogger(__name__)
HOUSE_URL = "https://clerk.house.gov/evs/{year}/roll{roll:03d}.xml"
SENATE_MENU = "https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_{c}_{s}.xml"
SENATE_VOTE = ("https://www.senate.gov/legislative/LIS/roll_call_votes/"
               "vote{c}{s}/vote_{c}_{s}_{n:05d}.xml")


def congress_years(congress: int) -> list[tuple[int, int]]:
    first = 1787 + 2 * congress            # 119th -> 2025
    return [(1, first), (2, first + 1)]


def _tally(casts: list[dict]) -> dict:
    t = {"yes_count": 0, "no_count": 0, "other_count": 0}
    for c in casts:
        k = {"yes": "yes_count", "no": "no_count"}.get(c["option"], "other_count")
        t[k] += 1
    return t


def _house_date(s: str | None):
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return datetime.strptime((s or "").strip(), fmt).date()
        except ValueError:
            pass
    return None


def parse_house(xml: bytes, url: str = "") -> tuple[dict, list[dict]]:
    root = ET.fromstring(xml)
    md = root.find("vote-metadata")
    congress = int(md.findtext("congress"))
    session = (md.findtext("session") or "1").strip()[0]      # '1st' -> '1'
    roll = int(md.findtext("rollcall-num"))
    vote_id = f"us-house-{congress}-{session}-{roll}"
    casts = []
    for rv in root.iter("recorded-vote"):
        leg = rv.find("legislator")
        bid = leg.get("name-id") if leg is not None else None
        name = (leg.text or "").strip() if leg is not None else None
        raw = rv.findtext("vote")
        casts.append({"vote_id": vote_id, "voter_key": bid or f"name:{name}", "politician_id": bid,
                      "voter_name": name, "option": norm_option(raw), "raw_option": raw})
    ev = {"vote_id": vote_id, "level": "federal", "state": None, "chamber": "lower",
          "session": session, "vote_date": _house_date(md.findtext("action-date")),
          "question": md.findtext("vote-question"), "result": md.findtext("vote-result"),
          "bill_ref": md.findtext("legis-num"), **_tally(casts), "source_url": url}
    return ev, casts


def parse_senate(xml: bytes, lis_map: dict, url: str = "") -> tuple[dict, list[dict]]:
    root = ET.fromstring(xml)
    congress = int(root.findtext("congress"))
    session = root.findtext("session").strip()
    num = int(root.findtext("vote_number"))
    vote_id = f"us-senate-{congress}-{session}-{num}"
    m = re.match(r"\s*([A-Za-z]+ \d{1,2}, \d{4})", root.findtext("vote_date") or "")
    vdate = datetime.strptime(m.group(1), "%B %d, %Y").date() if m else None
    casts = []
    for mem in root.iter("member"):
        lis = mem.findtext("lis_member_id")
        pid = lis_map.get(lis)
        raw = mem.findtext("vote_cast")
        casts.append({"vote_id": vote_id, "voter_key": pid or f"lis:{lis}", "politician_id": pid,
                      "voter_name": mem.findtext("member_full"), "option": norm_option(raw),
                      "raw_option": raw})
    ev = {"vote_id": vote_id, "level": "federal", "state": None, "chamber": "upper",
          "session": session, "vote_date": vdate,
          "question": root.findtext("vote_question_text") or root.findtext("question"),
          "result": root.findtext("vote_result_text") or root.findtext("vote_result"),
          "bill_ref": root.findtext("document/document_name"), **_tally(casts), "source_url": url}
    return ev, casts


def _save(cur, ev: dict, casts: list[dict]) -> int:
    upsert(cur, "civic.vote_event", [ev], ["vote_id"])
    return upsert(cur, "civic.vote_cast", casts, ["vote_id", "voter_key"])


def run_house(max_misses: int = 3) -> None:
    client = Client(min_interval=0.5)
    with run_log("house_votes") as stats, connect() as conn:
        for session, year in congress_years(settings.congress):
            if year > date.today().year:
                continue
            key = f"house:{settings.congress}:{session}"
            roll = get_checkpoint(key).get("last_roll", 0) + 1
            misses = 0
            while misses < max_misses:
                url = HOUSE_URL.format(year=year, roll=roll)
                r = client.get(url, ok404=True)
                # the Clerk answers a not-yet-held roll call with a 200 HTML page, not a 404,
                # so treat "no XML" exactly like a 404: count a miss and move to the next roll
                if r is None or b"<vote-metadata>" not in r.content[:2000]:
                    log.debug("house: no roll-call XML at %s", url)
                    misses += 1
                    roll += 1
                    continue
                misses = 0
                ev, casts = parse_house(r.content, url)
                with conn.cursor() as cur:
                    stats["rows"] += _save(cur, ev, casts)
                    set_checkpoint(cur, key, {"last_roll": roll})
                conn.commit()
                roll += 1
            log.info("house %s: through roll %d", year, get_checkpoint(key).get("last_roll", 0))


def run_senate() -> None:
    client = Client(min_interval=0.5)
    lis_map = {r["lis_id"]: r["politician_id"] for r in fetch_all(
        "SELECT lis_id, politician_id FROM civic.politician WHERE lis_id IS NOT NULL")}
    c = settings.congress
    with run_log("senate_votes") as stats, connect() as conn:
        for session, year in congress_years(c):
            if year > date.today().year:
                continue
            menu = client.get(SENATE_MENU.format(c=c, s=session), ok404=True)
            if menu is None:
                continue
            numbers = sorted(int(v.findtext("vote_number"))
                             for v in ET.fromstring(menu.content).iter("vote"))
            key = f"senate:{c}:{session}"
            last = get_checkpoint(key).get("last_vote", 0)
            for n in (n for n in numbers if n > last):
                url = SENATE_VOTE.format(c=c, s=session, n=n)
                r = client.get(url, ok404=True)
                if r is None:
                    log.warning("senate vote listed but missing: %s", url)
                    continue
                ev, casts = parse_senate(r.content, lis_map, url)
                with conn.cursor() as cur:
                    stats["rows"] += _save(cur, ev, casts)
                    set_checkpoint(cur, key, {"last_vote": n})
                conn.commit()
            log.info("senate %s-%s: %d votes listed", c, session, len(numbers))
