"""Settings from .env / environment (same PG_* convention as the eodhd repo)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from .util import parse_states

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


class Settings:
    def __init__(self) -> None:
        self.pg_host = os.getenv("PG_HOST", "127.0.0.1")
        self.pg_port = int(os.getenv("PG_PORT", "5432"))
        self.pg_db = os.getenv("PG_DB", "civic")
        self.pg_user = os.getenv("PG_USER", "jon")
        self.pg_password = os.getenv("PG_PASSWORD", "")

        self.congress_api_key = os.getenv("CONGRESS_API_KEY", "")
        self.fec_api_key = os.getenv("FEC_API_KEY", "")
        self.openstates_api_key = os.getenv("OPENSTATES_API_KEY", "")
        self.openstates_max_requests = int(os.getenv("OPENSTATES_MAX_REQUESTS", "450"))
        self.openstates_min_interval = float(os.getenv("OPENSTATES_MIN_INTERVAL", "6.5"))

        self.congress = int(os.getenv("CONGRESS", "119"))
        self.tiger_year = int(os.getenv("TIGER_YEAR", "2024"))
        self.states = parse_states(os.getenv("STATES", "IL"))
        data_dir = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
        self.data_dir = data_dir if data_dir.is_absolute() else ROOT / data_dir
        # senate.gov rejects obvious bot user agents, so keep a browser-style prefix
        self.user_agent = os.getenv(
            "USER_AGENT",
            "Mozilla/5.0 (X11; Linux x86_64) civic-report-card/0.1 (personal research)",
        )

    @property
    def dsn(self) -> str:
        parts = [f"host={self.pg_host}", f"port={self.pg_port}",
                 f"dbname={self.pg_db}", f"user={self.pg_user}"]
        if self.pg_password:            # blank -> libpq falls back to ~/.pgpass
            parts.append(f"password={self.pg_password}")
        return " ".join(parts)

    def require(self, attr: str, env: str) -> str:
        val = getattr(self, attr)
        if not val:
            raise SystemExit(f"{env} is not set (see .env.example)")
        return val


settings = Settings()
