"""Wikimedia pageviews fetching and article-title resolution.

Endpoints (Wikimedia Pageviews REST API):
  GET https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/
      {project}/{access}/{agent}/{article}/{granularity}/{start}/{end}
    access  in: all-access, desktop, mobile-web, mobile-app
    agent   in: all-agents, user, automated, spider
    granularity in: daily, monthly   (start/end are YYYYMMDD, inclusive)
    Data available from 2015-07-01.

  MediaWiki search / title resolution on each language edition:
    https://{project}/w/api.php?action=query&list=search&...
    https://{project}/w/api.php?action=query&pageids=...
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from datetime import date

from . import metrics as M
from .http import ApiError, Client, NotFoundError

ACCESS_VALUES = ("all-access", "desktop", "mobile-web", "mobile-app")
AGENT_VALUES = ("all-agents", "user", "automated", "spider")
DATA_START = date(2015, 7, 1)  # earliest available pageview data


@dataclass(frozen=True)
class PageviewRequest:
    """Bundled request context: where to fetch from and which traffic filter
    to apply. Travels together through every fetch function."""

    client: Client
    project: str
    access: str = "all-access"
    agent: str = "user"


def canonical_title(client: Client, project: str, title: str) -> dict | None:
    """Canonical page title for `title` on `project`, or None if missing.

    Returns {"title": str, "disambiguation": bool}; the flag is True when the
    page is a disambiguation page (aggregates unrelated topics - not a usable
    target for market research).
    """
    url = (
        f"https://{project}.org/w/api.php?action=query&titles={urllib.parse.quote(title)}"
        "&redirects=1&prop=pageprops&format=json&formatversion=2"
    )
    try:
        payload = client.get_json(url, ttl=86400)
    except (ApiError, NotFoundError):
        return None
    pages = (payload.get("query") or {}).get("pages") or []
    if pages and "missing" not in pages[0]:
        p = pages[0]
        return {
            "title": p["title"],
            "disambiguation": "disambiguation" in (p.get("pageprops") or {}),
        }
    return None


def check_disambiguation(client: Client, project: str, titles: list[str]) -> set[str]:
    """Return the subset of `titles` that are disambiguation pages
    (one batched MediaWiki request; titles compared after redirect)."""
    url = (
        "https://"
        f"{project}.org/w/api.php?action=query&titles={urllib.parse.quote(chr(10).join(titles))}"
        "&redirects=1&prop=pageprops&format=json&formatversion=2"
    )
    payload = client.get_json(url, ttl=86400)
    dabs = set()
    for p in (payload.get("query") or {}).get("pages") or []:
        if "missing" in p:
            continue
        if "disambiguation" in (p.get("pageprops") or {}):
            dabs.add(p["title"])
    return dabs

_TAGS = re.compile(r"<[^>]+>")


def project_for_language(code: str) -> str:
    """'pl' -> 'pl.wikipedia'; accepts 'pl.wikipedia' too. Raises ValueError."""
    code = code.strip().lower()
    if "." in code:
        dom = code
    else:
        if not re.fullmatch(r"[a-z]{2,9}", code):
            raise ValueError(
                f"invalid language code '{code}' (expected e.g. pl, cs, uk, or pl.wikipedia)"
            )
        dom = f"{code}.wikipedia"
    return dom


def _search_url(project: str, query: str, limit: int) -> str:
    q = urllib.parse.quote(query)
    return (
        f"https://{project}.org/w/api.php?action=query&list=search"
        f"&srsearch={q}&srnamespace=0&srlimit={limit}"
        f"&format=json&formatversion=2"
    )


def search_candidates(
    client: Client,
    project: str,
    query: str,
    limit: int = 6,
    intitle: bool = False,
) -> list[dict]:
    """Search a language edition; resolve canonical titles via pageids.

    intitle=True restricts matching to page titles (precise, for suggesting
    near-identical pages). Returns [{pageid, title (canonical), snippet}]
    in search-relevance order. Red (nonexistent) suggestions are dropped.
    """
    sr = f'intitle:"{query}"' if intitle else query
    payload = client.get_json(_search_url(project, sr, limit), ttl=86400)
    hits = (payload.get("query") or {}).get("search") or []
    if not hits:
        return []
    pageids = "|".join(str(h["pageid"]) for h in hits if h.get("pageid"))
    by_pageid = {}
    if pageids:
        res = client.get_json(
            f"https://{project}.org/w/api.php?action=query&pageids="
            f"{urllib.parse.quote(pageids)}&prop=pageprops&format=json&formatversion=2",
            ttl=86400,
        )
        for p in (res.get("query") or {}).get("pages") or []:
            # skip missing AND disambiguation pages: a DAB page aggregates
            # unrelated topics, so it is never a valid candidate
            if "missing" not in p and "disambiguation" not in (p.get("pageprops") or {}):
                by_pageid[p["pageid"]] = p["title"]
    out = []
    for h in hits:
        canonical = by_pageid.get(h.get("pageid"))
        if canonical is None:
            continue  # deleted in between; search title unreliable anyway
        snippet = _TAGS.sub("", h.get("snippet") or "")
        out.append(
            {
                "pageid": h.get("pageid"),
                "title": canonical,
                "snippet": " ".join(snippet.split())[:200],
            }
        )
    return out


def wikidata_titles(client: Client, topic: str, codes: list[str]) -> dict | None:
    """Resolve a topic to a Wikidata item and return per-language canonical
    page titles via sitelinks.

    Returns {"qid", "label", "description", "titles": {code: title}} or None.
    """
    url = (
        "https://www.wikidata.org/w/api.php?action=wbsearchentities"
        f"&search={urllib.parse.quote(topic)}&language=en&uselang=en"
        "&limit=5&format=json&formatversion=2"
    )
    payload = client.get_json(url, ttl=86400)
    results = payload.get("search") or []
    if not results:
        return None
    # prefer an exact label match, else take the top hit
    item = next((r for r in results if (r.get("label") or "").lower() == topic.lower()),
                results[0])
    qid = item["id"]
    langs = "|".join(sorted(set(codes + ["en"])))
    det = client.get_json(
        "https://www.wikidata.org/w/api.php?action=wbgetentities"
        f"&ids={qid}&props=sitelinks|labels|descriptions&languages={langs}"
        "&format=json&formatversion=2",
        ttl=86400,
    )
    entity = ((det.get("entities") or {}).get(qid)) or {}
    sitelinks = entity.get("sitelinks") or {}
    titles = {}
    for code in codes:
        link = sitelinks.get(f"{code}wiki")
        if link and link.get("title"):
            titles[code] = link["title"]
    labels = entity.get("labels") or {}
    desc = entity.get("descriptions") or {}
    return {
        "qid": qid,
        "label": (labels.get("en") or next(iter(labels.values()), {})).get("value"),
        "description": (desc.get("en") or next(iter(desc.values()), {})).get("value"),
        "titles": titles,
    }


def fetch_pageviews(
    req: PageviewRequest,
    title: str,
    start: date,
    end: date,
    granularity: str = "daily",
) -> dict:
    """Fetch views for one article; returns {period_start: views}.

    daily  -> keys are date objects, zero-filled for days with no traffic.
    monthly -> keys are (year, month) tuples, one entry per month (partial
               months included; use per-day averages for fair comparison).
    Raises NotFoundError if the article has no data (page missing/renamed).
    """
    if req.access not in ACCESS_VALUES:
        raise ValueError(f"access must be one of {ACCESS_VALUES}")
    if req.agent not in AGENT_VALUES:
        raise ValueError(f"agent must be one of {AGENT_VALUES}")
    if start > end:
        raise ValueError("start is after end")
    article = urllib.parse.quote(title)
    url = (
        "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
        f"{req.project}/{req.access}/{req.agent}/{article}/{granularity}/{start:%Y%m%d}/{end:%Y%m%d}"
    )
    payload = req.client.get_json(url, ttl=3600)
    out: dict = {}
    if granularity == "daily":
        out = {d: 0 for d in M.daterange(start, end)}
        for item in payload.get("items") or []:
            ts = item["timestamp"]  # YYYYMMDDHH
            key = date(int(ts[0:4]), int(ts[4:6]), int(ts[6:8]))
            if key in out:
                out[key] = int(item["views"])
        return out
    if granularity == "monthly":
        for item in payload.get("items") or []:
            ts = item["timestamp"]
            out[(int(ts[0:4]), int(ts[4:6]))] = int(item["views"])
        return out
    raise ValueError(f"granularity must be daily or monthly, got {granularity}")
