"""HTTP client with retries, polite rate limiting, and a request counter (for API budgets)."""
from __future__ import annotations

import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import settings


class Client:
    def __init__(self, min_interval: float = 0.0, headers: dict | None = None) -> None:
        s = requests.Session()
        retry = Retry(total=5, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=("GET",), respect_retry_after_header=True)
        s.mount("https://", HTTPAdapter(max_retries=retry))
        s.headers["User-Agent"] = settings.user_agent
        if headers:
            s.headers.update(headers)
        self.session = s
        self.min_interval = min_interval
        self.requests = 0
        self._last = 0.0

    def get(self, url, params=None, ok404=False, timeout=120, stream=False):
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        self.requests += 1
        r = self.session.get(url, params=params, timeout=timeout, stream=stream)
        if ok404 and r.status_code == 404:
            return None
        r.raise_for_status()
        return r

    def json(self, url, params=None, ok404=False):
        r = self.get(url, params=params, ok404=ok404)
        return None if r is None else r.json()

    def download(self, url, dest, ok404=False, cache=True) -> Path | None:
        """Stream a file to disk (atomic rename). With cache=True an existing file is reused."""
        dest = Path(dest)
        if cache and dest.exists() and dest.stat().st_size > 0:
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = self.get(url, ok404=ok404, stream=True)
        if r is None:
            return None
        tmp = dest.with_name(dest.name + ".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        tmp.replace(dest)
        return dest
