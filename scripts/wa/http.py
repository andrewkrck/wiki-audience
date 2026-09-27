"""HTTP client with retries, rate-limit etiquette, and a local disk cache.

All network access in this skill goes through this module.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone

import requests

# Wikimedia asks every consumer to identify itself (app + contact).
DEFAULT_USER_AGENT = (
    "wiki-audience-skill/1.0 (B2C audience-research agent skill; "
    "python-requests; contact: gskill project, andrii)"
)

DEFAULT_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "wiki-audience")
PAGEVIEW_TTL = 3600  # seconds; recent-day data changes as the day completes
SEARCH_TTL = 86400  # search hits change slowly; cache a day


class ApiError(Exception):
    """Unexpected API problem (4xx other than 404, repeated 5xx, timeouts)."""


class NotFoundError(Exception):
    """HTTP 404: article has no pageview data (usually: page does not exist)."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_file(cache_dir: str, key: str) -> str:
    return os.path.join(cache_dir, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")


def cache_get(cache_dir: str, key: str, ttl: int):
    """Return cached payload if fresh enough, else None."""
    if not cache_dir:
        return None
    path = _cache_file(cache_dir, key)
    try:
        with open(path, "r", encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    fetched = rec.get("fetched_at", "")
    try:
        ts = datetime.fromisoformat(fetched).timestamp()
    except ValueError:
        return None
    if time.time() - ts > ttl:
        return None
    return rec.get("payload")


def cache_put(cache_dir: str, key: str, payload) -> None:
    if not cache_dir:
        return
    path = _cache_file(cache_dir, key)
    os.makedirs(cache_dir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"fetched_at": _now_iso(), "url_key": key, "payload": payload}, f)
    os.replace(tmp, path)


class Client:
    """Small JSON-over-HTTP client with retries and disk cache."""

    def __init__(
        self,
        cache_dir: str = DEFAULT_CACHE_DIR,
        user_agent: str = DEFAULT_USER_AGENT,
        polite_sleep: float = 0.15,
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ):
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.polite_sleep = polite_sleep
        self._last_request_ts = 0.0
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.session.headers["Accept"] = "application/json"

    def _throttle(self) -> None:
        wait = self.polite_sleep - (time.monotonic() - self._last_request_ts)
        if wait > 0:
            time.sleep(wait)

    def get_json(self, url: str, ttl: int = PAGEVIEW_TTL, cache: bool = True):
        """GET url -> JSON payload. Raises ApiError / NotFoundError."""
        if cache:
            hit = cache_get(self.cache_dir, url, ttl)
            if hit is not None:
                return hit
        short = f"request failed after 3 attempts: {url}"
        for attempt in range(3):
            self._throttle()
            try:
                resp = self.session.get(url, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as e:
                short = (f"cannot reach {url.split('/')[2]} (network/DNS problem or "
                         "Wikimedia outage): " + str(type(e).__name__))
                time.sleep(1.0 * (3 ** attempt))
                continue
            self._last_request_ts = time.monotonic()
            if resp.status_code == 404:
                raise NotFoundError(url)
            if resp.status_code == 400:
                raise ApiError(f"Bad request (400) for {url}: {resp.text[:200]}")
            if resp.status_code == 429 or resp.status_code >= 500:
                short = f"HTTP {resp.status_code} from Wikimedia API (rate-limited or server error)"
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if (retry_after or "").isdigit() else 1.0 * (3 ** attempt)
                time.sleep(delay)
                continue
            if resp.status_code >= 300:
                raise ApiError(f"HTTP {resp.status_code} from {url}: {resp.text[:200]}")
            try:
                payload = resp.json()
            except ValueError:
                raise ApiError(f"Non-JSON response from {url}: {resp.text[:200]}")
            if cache:
                cache_put(self.cache_dir, url, payload)
            return payload
        raise ApiError(short)
