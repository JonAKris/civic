"""Small pure helpers: state codes, district/party/vote normalization."""
from __future__ import annotations

STATE_FIPS = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09",
    "DE": "10", "DC": "11", "FL": "12", "GA": "13", "HI": "15", "ID": "16", "IL": "17",
    "IN": "18", "IA": "19", "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24",
    "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29", "MT": "30", "NE": "31",
    "NV": "32", "NH": "33", "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38",
    "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46",
    "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53", "WV": "54",
    "WI": "55", "WY": "56",
}
FIPS_STATE = {v: k for k, v in STATE_FIPS.items()}
ALL_STATES = sorted(s for s in STATE_FIPS if s != "DC")

# Congressional at-large codes: 0 in congress-legislators, 00/98 in TIGER (98 = DC delegate).
# Only applied to congressional districts -- PA House really has a district 98.
CD_AT_LARGE = (0, 98)


def parse_states(spec: str | None) -> list[str]:
    spec = (spec or "").strip().upper()
    if spec in ("", "ALL"):
        return list(ALL_STATES)
    out = [s.strip() for s in spec.split(",") if s.strip()]
    bad = [s for s in out if s not in STATE_FIPS]
    if bad:
        raise ValueError(f"unknown state code(s): {bad}")
    return out


def norm_district(d, at_large: tuple = ()) -> str | None:
    """'007' -> '7'; at-large codes -> 'AL'; named districts upper-cased as-is."""
    if d is None:
        return None
    s = str(d).strip()
    if not s:
        return None
    if s.isdigit():
        n = int(s)
        return "AL" if n in at_large else str(n)
    return s.upper()


def party_code(name: str | None) -> str | None:
    if not name:
        return None
    n = name.strip().lower()
    if n.startswith("democrat"):        # Democrat, Democratic, Democratic-Farmer-Labor
        return "D"
    if n.startswith("republican"):
        return "R"
    if n.startswith("independent"):
        return "I"
    return "O"


def norm_option(v: str | None) -> str:
    """Map every source's vote wording onto yes / no / present / not_voting / other."""
    s = (v or "").strip().lower()
    if s in ("yea", "aye", "yes", "y"):
        return "yes"
    if s in ("nay", "no", "n"):
        return "no"
    if s in ("present", "abstain"):
        return "present"
    if s in ("not voting", "not-voting", "absent", "excused", "nv"):
        return "not_voting"
    return "other"
