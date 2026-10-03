# civic — politician report-card data pipeline

Collects data on every member of Congress and every state legislator in the states you choose,
and stores it in PostgreSQL + PostGIS on msa1. An address lookup (`civic.officials_at(lon, lat)`)
returns the voter's US House member, both US senators, state senator and state representative,
each with report-card metrics.

## Data sources

| Data | Source | Key | Loader |
|---|---|---|---|
| Members of Congress, cross-IDs (bioguide, LIS, FEC, ICPSR) | unitedstates/congress-legislators | none | `federal-people` |
| State legislators | openstates/people (GitHub YAML) | none | `state-people` |
| District polygons (CD, state senate, state house) | Census TIGER/Line | none | `boundaries` |
| House roll calls | clerk.house.gov XML | none | `house-votes` |
| Senate roll calls | senate.gov XML | none | `senate-votes` |
| Federal bills, sponsors, cosponsors | Congress.gov API v3 | api.data.gov | `congress-bills` |
| Campaign finance totals | OpenFEC API | api.data.gov | `fec` |
| Ideology (DW-NOMINATE) | Voteview | none | `voteview` |
| State bills, sponsors, roll calls | Open States v3 API | Plural/Open States | `state-bills` |

## Setup on msa1

```bash
# PostGIS (Ubuntu/Debian; use the package matching your Postgres major version)
sudo apt install postgresql-16-postgis-3
sudo -u postgres createdb -O $USER civic
sudo -u postgres psql -d civic -c "CREATE EXTENSION postgis"   # needs superuser once

cd /home/$USER && git clone <this repo> civic && cd civic
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in keys; leave PG_PASSWORD blank to use ~/.pgpass

python -m tests.test_parsers           # offline sanity check
python -m civic.cli init-db            # tables, views, lookup functions (re-runnable)
python -m civic.cli bootstrap          # first full load
python -m civic.cli check              # data-quality report
python -m civic.cli lookup "123 Main St, Elgin, IL 60120"
```

Read-only access for a future web front end, same pattern as eodhd:

```sql
GRANT USAGE ON SCHEMA civic TO ai_agent;
GRANT SELECT ON ALL TABLES IN SCHEMA civic TO ai_agent;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA civic TO ai_agent;
ALTER DEFAULT PRIVILEGES IN SCHEMA civic GRANT SELECT ON TABLES TO ai_agent;
```

## Scheduling

Pick times clear of the 19:37 and 22:07 stock jobs:

```cron
15 2 * * *   /home/$USER/civic/bin/civic_job.bash nightly >> /home/$USER/civic/logs/civic.log 2>&1
15 3 * * 0   /home/$USER/civic/bin/civic_job.bash weekly  >> /home/$USER/civic/logs/civic.log 2>&1
```

- **nightly** runs `federal-people`, `house-votes`, `senate-votes` and `state-bills`. All four are incremental and checkpointed in `civic.etl_state`.
- **weekly** runs `state-people`, `congress-bills` (about 20 min), `voteview` and `fec` (about 40 min at the standard 1,000/hr limit).
- **boundaries** only needs re-running when maps change (see below).

Every run is logged to `civic.etl_run`. `check` shows the last run of each job.

## Rate limits and the Open States budget

The Congress.gov API allows 5,000 requests/hour (paced at 0.75 s). OpenFEC allows 1,000/hour (paced at 3.7 s). Ask the FEC for an upgraded key to speed this up.

The Open States free tier is small. `state-bills` stops after `OPENSTATES_MAX_REQUESTS` per run and resumes the next night. It walks bills oldest-update-first and checkpoints the newest `updated_at`. An Illinois session has roughly 10,000 bills at 20 per page, so the first backfill takes about a week of nightly runs. After that each run only fetches what changed. Set the budget to whatever Plural actually grants your key.

## Metrics (views)

- `v_vote_participation`: eligible floor votes, missed votes, missed %.
- `v_bill_activity`: bills sponsored, enacted, and cosponsored.
- `v_bipartisanship`: the share of a member's cosponsorships on bills whose primary sponsor is from the other major party.
- `v_report_card`: all of the above, plus the latest-cycle FEC totals and DW-NOMINATE score, one row per current official.

These are deliberately raw facts, not grades. Any letter grade should be a transparent, user-adjustable weighting on top of them.

## Known caveats

- **Redistricting.** Keep `TIGER_YEAR=2024` / `CONGRESS=119` until the 120th Congress is seated in January 2027. Several states redrew congressional maps mid-decade for the 2026 elections, so current members still represent the 2024 lines. After the switch, set `CONGRESS=120`, set `TIGER_YEAR` to a vintage that includes the 120th Congress districts, and re-run `boundaries` and `voteview`. The lookup always prefers the newest vintage it has.
- **Named or multi-member state districts** (NH, VT, MA and a few others). Open States uses district names, but TIGER uses codes. `check` lists any seats with no matching polygon. Fix these with a small mapping table if you expand beyond Illinois.
- **Senate LIS IDs** are matched only for current senators. Votes by departed senators are kept under `lis:<id>` keys.
- **State committee votes** are stored with `chamber = 'committee'` and excluded from attendance.
- **State campaign finance** isn't covered yet. Illinois data is published by the State Board of Elections and would be its own loader.
- **senate.gov** rejects obvious bot user agents. The default `USER_AGENT` uses a browser-style prefix for this reason.

## Layout

```
civic/
  config.py  db.py  net.py  util.py  lookup.py  cli.py
  loaders/ federal_people.py state_people.py boundaries.py federal_votes.py
           congress_bills.py fec.py voteview.py state_bills.py
sql/  schema.sql  views.sql
bin/  civic_job.bash
tests/ test_parsers.py
```
