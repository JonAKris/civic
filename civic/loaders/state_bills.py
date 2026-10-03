"""State bills, sponsorships and roll-call votes from the Open States v3 API.

The free tier is small, so this loader is budgeted (OPENSTATES_MAX_REQUESTS per run) and
resumable: it walks bills in updated-ascending order and checkpoints the newest updated_at
seen, so an interrupted backfill picks up where it stopped and later runs are incremental.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from ..config import settings
from ..db import connect, get_checkpoint, run_log, set_checkpoint, upsert
from ..net import Client
from ..util import norm_option

log = logging.getLogger(__name__)
API = "https://v3.openstates.org"


def jurisdiction_id(st: str) -> str:
    return f"ocd-jurisdiction/country:us/state:{st.lower()}/government"


def _d(s):
    return str(s)[:10] if s else None


def current_sessions(client: Client) -> dict[str, str]:
    """Latest started regular session per state, e.g. {'IL': '104th'}."""
    out, page, today = {}, 1, date.today().isoformat()
    while True:
        d = client.json(f"{API}/jurisdictions", params={
            "classification": "state", "include": "legislative_sessions", "per_page": 52, "page": page})
        for j in d.get("results") or []:
            if "state:" not in j["id"]:
                continue
            st = j["id"].split("state:")[1].split("/")[0].upper()
            sess = j.get("legislative_sessions") or []
            primary = [s for s in sess if s.get("classification", "primary") == "primary"] or sess
            started = [s for s in primary if s.get("start_date") and s["start_date"] <= today]
            pick = max(started, key=lambda s: s["start_date"]) if started else (primary[-1] if primary else None)
            if pick:
                out[st] = pick["identifier"]
        if page >= (d.get("pagination") or {}).get("max_page", 1):
            return out
        page += 1


def bill_rows(st: str, b: dict):
    """One API bill -> (bill, sponsorships, vote_events, vote_casts)."""
    now = datetime.now(timezone.utc)
    actions = b.get("actions") or []
    subjects = b.get("subject") or []
    bill = {
        "bill_id": b["id"], "level": "state", "state": st, "session": b.get("session"),
        "chamber": (b.get("from_organization") or {}).get("classification"),
        "identifier": b.get("identifier"), "title": b.get("title"),
        "introduced_date": _d(b.get("first_action_date")),
        "latest_action_date": _d(b.get("latest_action_date")),
        "latest_action": b.get("latest_action_description"),
        "became_law": any("became-law" in (a.get("classification") or []) for a in actions),
        "policy_area": subjects[0] if subjects else None,
        "url": b.get("openstates_url"), "updated_at": now,
    }
    sponsors = []
    for s in b.get("sponsorships") or []:
        pid, name = (s.get("person") or {}).get("id"), s.get("name")
        sponsors.append({"bill_id": b["id"], "sponsor_key": pid or f"name:{name}", "politician_id": pid,
                         "sponsor_name": name, "is_primary": bool(s.get("primary"))})
    events, casts = [], []
    for v in b.get("votes") or []:
        tally = {"yes": 0, "no": 0, "other": 0}
        for c in v.get("counts") or []:
            k = norm_option(c.get("option"))
            tally[k if k in ("yes", "no") else "other"] += int(c.get("value") or 0)
        events.append({
            "vote_id": v["id"], "level": "state", "state": st,
            "chamber": (v.get("organization") or {}).get("classification"),
            "session": b.get("session"), "vote_date": _d(v.get("start_date")),
            "question": v.get("motion_text"), "result": v.get("result"),
            "bill_ref": b.get("identifier"), "yes_count": tally["yes"], "no_count": tally["no"],
            "other_count": tally["other"], "source_url": b.get("openstates_url"),
        })
        for pv in v.get("votes") or []:
            pid = (pv.get("voter") or {}).get("id") or pv.get("voter_id")
            name = pv.get("voter_name")
            casts.append({"vote_id": v["id"], "voter_key": pid or f"name:{name}", "politician_id": pid,
                          "voter_name": name, "option": norm_option(pv.get("option")),
                          "raw_option": pv.get("option")})
    return bill, sponsors, events, casts


def _save_page(cur, st: str, results: list[dict]) -> None:
    bills, spons, events, casts = [], [], [], []
    for b in results:
        bl, sp, ev, vc = bill_rows(st, b)
        bills.append(bl); spons += sp; events += ev; casts += vc
    upsert(cur, "civic.bill", bills, ["bill_id"])
    upsert(cur, "civic.sponsorship", spons, ["bill_id", "sponsor_key"])
    upsert(cur, "civic.vote_event", events, ["vote_id"])
    upsert(cur, "civic.vote_cast", casts, ["vote_id", "voter_key"])


def run(states: list[str] | None = None, session: str | None = None,
        max_requests: int | None = None) -> None:
    key = settings.require("openstates_api_key", "OPENSTATES_API_KEY")
    states = states or settings.states
    budget = max_requests or settings.openstates_max_requests
    client = Client(min_interval=settings.openstates_min_interval, headers={"X-API-KEY": key})
    with run_log("state_bills") as stats:
        sessions = {s: session for s in states} if session else current_sessions(client)
        for st in states:
            sess = sessions.get(st)
            if not sess:
                log.warning("no session found for %s", st)
                continue
            ck_key = f"openstates:{st}:{sess}"
            since, page = get_checkpoint(ck_key).get("since"), 1
            while True:
                if client.requests >= budget:
                    stats["message"] = f"request budget ({budget}) reached in {st}; resumes next run"
                    log.warning(stats["message"])
                    return
                params = {"jurisdiction": jurisdiction_id(st), "session": sess,
                          "include": ["sponsorships", "votes", "actions"],
                          "sort": "updated_asc", "per_page": 20, "page": page}
                if since:
                    params["updated_since"] = since
                data = client.json(f"{API}/bills", params=params)
                results = data.get("results") or []
                if not results:
                    break
                newest = max(b.get("updated_at") or "" for b in results)
                with connect() as conn, conn.cursor() as cur:
                    _save_page(cur, st, results)
                    set_checkpoint(cur, ck_key, {"since": newest})
                stats["rows"] += len(results)
                if page >= (data.get("pagination") or {}).get("max_page", page):
                    break
                if newest == since:      # a whole page shares one timestamp: step forward
                    page += 1
                else:
                    since, page = newest, 1
            log.info("state_bills %s %s: up to date", st, sess)
