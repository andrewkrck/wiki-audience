# API reference (Wikimedia Pageviews + resolution helpers)

## 1. Pageviews REST API
Endpoint (one request per article + granularity + period):

```
GET https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/
    {project}/{access}/{agent}/{article}/{granularity}/{start}/{end}
```

- `{project}` — `pl.wikipedia`, `cs.wikipedia`, `uk.wikipedia`, `en.wikipedia`, …
- `{access}` — `all-access` (default), `desktop`, `mobile-web`, `mobile-app`.
- `{agent}` — `all-agents` (any traffic), `user` (**default here — human
  traffic only**), `automated` (bots/editors' tools), `spider` (crawlers).
- `{article}` — exact article title, URL-encoded (spaces → `_`). Underscores
  in titles are NOT expanded by this endpoint — use the canonical title.
- `{granularity}` — `daily` or `monthly`.
- `{start}/{end}` — inclusive, `YYYYMMDD`. Data exists from **2015-07-01**.
- No window limit: 10 years of daily data returns in a single request.
- Response: `items[]` with `timestamp` (`YYYYMMDD` or `YYYYMM`) and `views`.
  Daily responses may omit zero-view days (we zero-fill locally).
- **404** = article not found → `NotFoundError` (the resolver then tries
  canonical-title correction).
- 429/5xx: retried with exponential backoff (1s/3s/9s), honoring
  `Retry-After`. Three failures → exit 3 with a compact message.
- User-Agent header is always set (Wikimedia requires a descriptive UA).

Consistency check used by the CLI: after a daily fetch, the same period is
fetched at monthly granularity and the totals must agree within 2%
(divergence is logged to stderr as `[verify]` or surfaced as a warning).

## 2. Article resolution (cross-lingual)
1. **Wikidata** (exact mapping of a concept to pages in each language):
   - `GET https://www.wikidata.org/w/api.php?action=wbsearchentities&search=<topic>&language=en&uselang=en&limit=5`
   - `GET https://www.wikidata.org/w/api.php?action=wbgetentities&ids=<Q…>&props=sitelinks|labels|descriptions`
   - `sitelinks.{cc}wiki.title` = canonical title in that language. If a
     language has no sitelink, the topic has no page there → search fallback.
2. **MediaWiki search** fallback:
   `GET https://{project}.org/w/api.php?action=query&list=search&srsearch=<q>&srnamespace=0&srlimit=N`
   — matches page *body text*, so off-topic pages can rank high; candidates
   are then ranked by recent (3-month) mean daily views and presented with
   snippets for human/agent review.
3. **Canonical-title correction** (when a title 404s):
   `action=query&titles=<title>&redirects=1` returns the canonical title
   (fixes case/unicode drift, e.g. search-result forms vs page titles).
   If the title doesn't exist at all, the top-3 search hits are checked with
   a similarity guard (difflib ratio ≥ 0.7) before substitution, and the
   substitution is always recorded in `warnings`.
4. **Disambiguation pages** (detected via `prop=pageprops`, piggy-backed on
   the same requests above — no extra traffic):
   - Search candidates that are disambiguation (DAB) pages are dropped from
     `resolve` output — a DAB page aggregates unrelated topics, so its
     pageviews are meaningless for a single topic.
   - An explicit `--article` title that turns out to be a DAB page produces a
     loud `warnings` entry (the run still proceeds, so the agent can decide
     with the user).

## 3. Caching & etiquette
- Disk cache in `~/.cache/wiki-audience/` (override with `--cache-dir`,
  disable with `--cache-dir ""`). Key = SHA-1 of the URL. The cache lives
  outside the skill tree on purpose: it is a runtime artifact, so the skill
  directory stays portable and can be installed read-only.
- TTLs: pageviews **1 hour** (recent-day data is still changing; historical
  months are stable), search/resolution 1 day.
- 0.15 s minimum spacing between requests (Wikimedia rate-limit courtesy).
- Everything is read-only GET.

## 4. CLI exit codes
| code | meaning |
|------|---------|
| 0    | success (≥1 language analyzed / candidates found) |
| 2    | no language succeeded (details in `warnings`) |
| 3    | network/API failure after retries |
| 4    | invalid arguments (bad date, malformed `--article`, …) |

## 5. Where the code lives
- `scripts/wa/http.py` — HTTP client (retries, cache, UA).
- `scripts/wa/fetch.py` — URL builders + pageviews/search/wikidata wrappers.
- `scripts/wa/metrics.py` — all statistics (pure Python).
- `scripts/wa/charts.py` — matplotlib normalized-index chart.
- `scripts/wa/report.py` — findings generator + one-page PDF.
- `scripts/wa/cli.py` — argument parsing, orchestration, JSON output.
