"""Address -> lon/lat -> districts -> officials + report-card metrics.

Privacy: the address is sent only to the Census geocoder (a free federal service) and is
never logged or stored. For zero third-party exposure, pass --lat/--lon from a local geocoder.
"""
from __future__ import annotations

from .db import fetch_all
from .net import Client

GEOCODER = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"


def geocode_match(address: str) -> tuple[float, float, str] | None:
    """(lon, lat, matched address as the Census geocoder normalised it) or None."""
    d = Client().json(GEOCODER, params={"address": address, "benchmark": "Public_AR_Current",
                                         "format": "json"})
    matches = ((d or {}).get("result") or {}).get("addressMatches") or []
    if not matches:
        return None
    c = matches[0]["coordinates"]
    return float(c["x"]), float(c["y"]), matches[0].get("matchedAddress") or address


def geocode(address: str) -> tuple[float, float] | None:
    m = geocode_match(address)
    return (m[0], m[1]) if m else None


def officials(lon: float, lat: float) -> list[dict]:
    return fetch_all(
        """SELECT rc.*
           FROM civic.officials_at(%s, %s) o
           JOIN civic.v_report_card rc USING (politician_id)
           ORDER BY rc.level, rc.chamber DESC, rc.full_name""",
        (lon, lat),
    )


def districts(lon: float, lat: float) -> list[dict]:
    return fetch_all("SELECT * FROM civic.districts_at(%s, %s)", (lon, lat))
