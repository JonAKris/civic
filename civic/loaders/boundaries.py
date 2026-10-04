"""Census TIGER/Line district polygons -> civic.district_boundary (PostGIS).

Congressional (CD), state senate (SLDU) and house (SLDL) are each one file per state.
119th-Congress CDs are published in TIGER2025 (CD_TIGER_YEAR); legislative maps use
TIGER_YEAR. Zips are cached under DATA_DIR/tiger/<year>/.
"""
from __future__ import annotations

import logging

import psycopg2
import shapefile                      # pyshp (reads .zip directly)
from shapely.geometry import shape

from ..config import settings
from ..db import connect, run_log, upsert
from ..net import Client
from ..util import CD_AT_LARGE, FIPS_STATE, STATE_FIPS, norm_district

log = logging.getLogger(__name__)
BASE = "https://www2.census.gov/geo/tiger/TIGER{year}"
GEOM_SQL = "ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_GeomFromWKB(%s, 4269)), 3))"
# column order must match the dict built in _rows()
TEMPLATE = "(%s, %s, %s, %s, %s, %s, %s, %s, " + GEOM_SQL + ")"


def _rows(zip_path, level: str, chamber: str, code_field: str, at_large=(),
          vintage: int | None = None) -> list[dict]:
    rows = []
    with shapefile.Reader(str(zip_path)) as sf:
        for sr in sf.iterShapeRecords():
            rec = sr.record.as_dict()
            st = FIPS_STATE.get(rec["STATEFP"])
            code = str(rec.get(code_field) or "").strip()
            # skip territories, blanks, and 'ZZZ' (water / undefined areas)
            if not st or not code or code.upper().startswith("ZZ") or sr.shape.shapeType == 0:
                continue
            geom = shape(sr.shape.__geo_interface__)
            rows.append({
                "level": level,
                "chamber": chamber,
                "state": st,
                "state_fips": rec["STATEFP"],
                "district": norm_district(code, at_large),
                "geoid": rec["GEOID"],
                "name": rec.get("NAMELSAD"),
                "vintage": vintage or settings.tiger_year,
                "geom": psycopg2.Binary(geom.wkb),
            })
    return rows


def jobs(states: list[str], congressional: bool = True) -> list[tuple]:
    """(url, level, chamber, code_field, at_large, vintage) for each file to load."""
    y, base = settings.tiger_year, BASE.format(year=settings.tiger_year)
    cy, cbase, c = settings.cd_tiger_year, BASE.format(year=settings.cd_tiger_year), settings.congress
    out = []
    for st in states:
        f = STATE_FIPS[st]
        if congressional:
            out.append((f"{cbase}/CD/tl_{cy}_{f}_cd{c}.zip", "federal", "lower",
                        f"CD{c}FP", CD_AT_LARGE, cy))
        for kind, chamber in (("sldu", "upper"), ("sldl", "lower")):
            out.append((f"{base}/{kind.upper()}/tl_{y}_{f}_{kind}.zip", "state", chamber,
                        f"{kind.upper()}ST", (), y))
    return out


def run(states: list[str] | None = None, congressional: bool = True) -> None:
    states = states or settings.states
    client = Client(min_interval=0.5)
    with run_log("boundaries") as stats:
        for url, level, chamber, field, at_large, vintage in jobs(states, congressional):
            ddir = settings.data_dir / "tiger" / str(vintage)
            path = client.download(url, ddir / url.rsplit("/", 1)[1], ok404=True)
            if path is None:
                if level == "federal":   # every state has one -- missing means a bad URL/year
                    raise RuntimeError(f"congressional district file not found: {url} "
                                       "(check CD_TIGER_YEAR / CONGRESS in .env)")
                log.info("not published, skipping: %s", url)   # e.g. Nebraska has no SLDL
                continue
            rows = _rows(path, level, chamber, field, at_large, vintage)
            with connect() as conn, conn.cursor() as cur:
                n = upsert(cur, "civic.district_boundary", rows,
                           ["level", "chamber", "geoid", "vintage"],
                           template=TEMPLATE, page_size=50)
            stats["rows"] += n
            log.info("%s: %d districts", path.name, n)
