"""Command line: python -m civic.cli <command>   (python -m civic.cli --help for the list)"""
from __future__ import annotations

import logging

import click

from . import lookup as lk
from .config import ROOT, settings
from .db import connect, fetch_all
from .loaders import (boundaries, congress_bills, fec, federal_people, federal_votes,
                      state_bills, state_people, voteview)
from .util import parse_states

log = logging.getLogger("civic")
CHAMBER_LABEL = {("federal", "upper"): "US Senate", ("federal", "lower"): "US House",
                 ("state", "upper"): "State Senate", ("state", "lower"): "State House"}


def _states(opt: str | None) -> list[str]:
    return parse_states(opt) if opt else settings.states


def _run_jobs(jobs) -> None:
    failed = []
    for name, fn in jobs:
        log.info("=== %s ===", name)
        try:
            fn()
        except Exception:                 # keep going; one bad source shouldn't block the rest
            log.exception("%s failed", name)
            failed.append(name)
    if failed:
        raise SystemExit(f"failed: {', '.join(failed)}")


@click.group()
@click.option("-v", "--verbose", is_flag=True)
def cli(verbose):
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@cli.command("init-db")
def init_db():
    """Create schema, tables, views and lookup functions (idempotent)."""
    with connect() as conn, conn.cursor() as cur:
        for f in ("schema.sql", "views.sql"):
            cur.execute((ROOT / "sql" / f).read_text())
            click.echo(f"applied sql/{f}")


@cli.command("federal-people")
def cmd_federal_people():
    """Current members of Congress."""
    federal_people.run()


@cli.command("state-people")
@click.option("--states", help="e.g. IL,WI or ALL (default: STATES in .env)")
def cmd_state_people(states):
    """Current state legislators."""
    state_people.run(_states(states))


@cli.command("boundaries")
@click.option("--states", help="state legislative maps to load (default: STATES)")
@click.option("--no-congressional", is_flag=True, help="skip the national congressional file")
def cmd_boundaries(states, no_congressional):
    """TIGER/Line district polygons into PostGIS."""
    boundaries.run(_states(states), congressional=not no_congressional)


@cli.command("congress-bills")
@click.option("--member", help="one bioguide id (testing)")
def cmd_congress_bills(member):
    """Sponsored / cosponsored bills for the current Congress."""
    congress_bills.run(member)


@cli.command("house-votes")
def cmd_house_votes():
    """House roll calls (incremental)."""
    federal_votes.run_house()


@cli.command("senate-votes")
def cmd_senate_votes():
    """Senate roll calls (incremental)."""
    federal_votes.run_senate()


@cli.command("voteview")
@click.option("--congress", type=int)
def cmd_voteview(congress):
    """DW-NOMINATE ideology scores."""
    voteview.run(congress)


@cli.command("fec")
@click.option("--member", help="one bioguide id (testing)")
def cmd_fec(member):
    """FEC campaign-finance totals."""
    fec.run(member)


@cli.command("state-bills")
@click.option("--states")
@click.option("--session", help="override session identifier (e.g. 104th)")
@click.option("--max-requests", type=int, help="API request budget for this run")
def cmd_state_bills(states, session, max_requests):
    """State bills, sponsors and roll calls (budgeted, resumable)."""
    state_bills.run(_states(states), session, max_requests)


@cli.command("bootstrap")
def cmd_bootstrap():
    """First full load, in dependency order."""
    _run_jobs([
        ("federal-people", federal_people.run),
        ("state-people", state_people.run),
        ("boundaries", boundaries.run),
        ("voteview", voteview.run),
        ("house-votes", federal_votes.run_house),
        ("senate-votes", federal_votes.run_senate),
        ("congress-bills", congress_bills.run),
        ("fec", fec.run),
        ("state-bills", state_bills.run),
    ])


@cli.command("nightly")
def cmd_nightly():
    """Cheap incremental refresh: rosters, new roll calls, state bills."""
    _run_jobs([
        ("federal-people", federal_people.run),
        ("house-votes", federal_votes.run_house),
        ("senate-votes", federal_votes.run_senate),
        ("state-bills", state_bills.run),
    ])


@cli.command("weekly")
def cmd_weekly():
    """Heavier refresh: state rosters, bill sponsorships, ideology, finance."""
    _run_jobs([
        ("state-people", state_people.run),
        ("congress-bills", congress_bills.run),
        ("voteview", voteview.run),
        ("fec", fec.run),
    ])


@cli.command("lookup")
@click.argument("address", required=False)
@click.option("--lat", type=float)
@click.option("--lon", type=float)
def cmd_lookup(address, lat, lon):
    """Officials and report-card metrics for an address (not logged or stored)."""
    if lat is None or lon is None:
        if not address:
            raise click.UsageError("give an ADDRESS or --lat and --lon")
        pt = lk.geocode(address)
        if not pt:
            raise SystemExit("address not found by the Census geocoder")
        lon, lat = pt
    rows = lk.officials(lon, lat)
    if not rows:
        click.echo("no officials found (are boundaries and rosters loaded for this state?)")
    for r in rows:
        seat = CHAMBER_LABEL[(r["level"], r["chamber"])]
        dist = f"-{r['district']}" if r["district"] else ""
        click.echo(f"\n{seat:<13} {r['full_name']} ({r['party_code'] or '?'}) {r['state']}{dist}")
        if r["votes_eligible"]:
            click.echo(f"   votes: {r['votes_eligible']} eligible, {r['votes_missed']} missed ({r['missed_pct']}%)")
        if r["bills_sponsored"] is not None:
            click.echo(f"   bills: {r['bills_sponsored']} sponsored, {r['bills_enacted']} enacted, "
                       f"{r['bills_cosponsored']} cosponsored")
        if r["cross_party_pct"] is not None:
            click.echo(f"   cross-party cosponsorships: {r['cross_party_pct']}% of {r['cosponsorships_scored']}")
        if r["receipts"] is not None:
            click.echo(f"   {r['finance_cycle']} cycle: ${r['receipts']:,.0f} raised, "
                       f"${(r['pac_contributions'] or 0):,.0f} from PACs")
        if r["nominate_dim1"] is not None:
            click.echo(f"   DW-NOMINATE: {r['nominate_dim1']:+.3f}  (-1 liberal .. +1 conservative)")


@cli.command("check")
def cmd_check():
    """Data-quality report: seats with no matching boundary, last run of each job."""
    missing = fetch_all(
        """SELECT p.level, p.chamber, p.state, p.district, p.full_name
           FROM civic.politician p
           WHERE p.is_current AND p.district IS NOT NULL
             AND (p.level = 'federal' OR p.state = ANY(%s))
             AND NOT EXISTS (SELECT 1 FROM civic.district_boundary b
                             WHERE b.level = p.level AND b.chamber = p.chamber
                               AND b.state = p.state AND b.district = p.district)
           ORDER BY 1, 2, 3, 4""", (settings.states,))
    click.echo(f"seats with no matching boundary: {len(missing)}")
    for m in missing[:40]:
        click.echo(f"   {m['level']:<8}{m['chamber']:<6}{m['state']} {m['district']:<14} {m['full_name']}")
    click.echo("\nlast run per job:")
    for r in fetch_all("""SELECT DISTINCT ON (job) job, started_at, status, rows, message
                          FROM civic.etl_run ORDER BY job, started_at DESC"""):
        click.echo(f"   {r['job']:<16}{r['started_at']:%Y-%m-%d %H:%M}  {r['status']:<8}"
                   f"{r['rows'] or 0:>8}  {r['message'] or ''}")


if __name__ == "__main__":
    cli()
