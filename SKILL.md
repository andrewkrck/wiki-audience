---
name: wiki-audience
description: >
  Analyze Wikipedia pageviews to help B2C app founders decide which content
  topics to develop and which languages to launch in. Resolves a topic to the
  right Wikipedia article per language (Wikidata cross-lingual mapping,
  disambiguation-aware), computes growth/trend/reliability metrics over a
  chosen period, detects spike/event-driven traffic, draws a comparison
  chart, and renders a one-page shareable PDF report with the numbers and
  caveats. Use when the
  user asks which topics have growing or audience interest in specific
  countries/languages (e.g. "does Polish interest in intermittent fasting
  beat Czech?", "is astronomy interest in Ukraine growing?", "which language
  editions show most demand for English-learning content?"), or asks to
  compare Wikipedia audience demand across languages, produce a topic-demand
  report, or get a chart of Wikipedia search/attention trends.
license: MIT
metadata: {"version": "1.0.0", "homepage": "https://wikimedia.org/api/rest_v1/metrics/pageviews"}
---

# Wiki Audience — Wikipedia pageview research for topic/language decisions

You help a B2C app founder decide **which topics to develop and which
languages to launch in**, using real Wikipedia pageview data. A script does
all data work; **you orchestrate it and interpret its JSON output**. Never
invent or estimate numbers — every figure you cite must come from the
script's output.

## Runner (how to run the script)

Preferred — `uv` (auto-creates a venv with the pinned deps, zero install):

```bash
uv run scripts/wiki_audience.py <command> ...
```

If `uv` is unavailable: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`,
then use `.venv/bin/python scripts/wiki_audience.py ...`.

All commands write outputs (JSON, `chart.png`, `report.pdf`) into `./wa-out/`
unless `--out DIR` says otherwise. JSON goes to stdout; diagnostics to stderr.
Exit codes: 0 ok · 2 no language could be analyzed · 3 network/API failure ·
4 bad arguments. On failure, show the user the stderr message verbatim and
suggest the next step (e.g. re-run — transient errors retry automatically).

## Workflow

### Step 1 — Resolve articles (do this first, unless the user names exact articles)

```bash
uv run scripts/wiki_audience.py resolve "<topic>" --languages pl,cs
```

Output: per language, ranked candidates. **Wikidata sitelinks** (exact
cross-lingual page for the topic) come first, then search hits ranked by
recent views. Read the titles and snippets:

- Candidate `source: "wikidata"` → exact page for the topic — use it.
- `source: "search"` → may be off-topic (the search matches page *body
  text*). Only use it if the title/snippet clearly matches the topic.
- If nothing matches (e.g. the topic has no page in that language), tell the
  user and suggest: a different language query via `--search "pl:native phrase"`,
  or a broader topic.

### Step 2 — Analyze

```bash
uv run scripts/wiki_audience.py analyze --topic "<topic>" --languages pl,cs \
  --article "pl:Głodówka lecznicza;cs:Přerušovaný půst" --last 730
```

- `--article "lang:Title;lang:Title"` — pick the titles from Step 1
  (semicolon-separated; titles may contain commas). Omit to let the script
  auto-resolve (Wikidata first, then best search hit) — fine for quick runs,
  but **verify the resolved titles** in the output and say which pages were
  used.
- `--last N` — period in days ending today (default 730 = 2 years). Use
  `--start YYYY-MM-DD --end YYYY-MM-DD` for exact windows. Data exists only
  from **2015-07-01**.
- Optional: `--access mobile-app|desktop|mobile-web|all-access`
  (default `all-access`), `--agent user|automated|spider|all-agents`
  (default `user` = human traffic only).
- `--no-charts --no-pdf` to skip file output.

One run per question is usually enough. For "which language is X strongest
in", one `analyze` over all candidate languages beats N single-language runs.

### Step 3 — Answer the founder

Summarize in plain language, grounded ONLY in the JSON fields below, and
point to the deliverables (`wa-out/report.pdf` one-page shareable report,
`wa-out/chart.png`, `wa-out/analysis.json` full data):

- **Volume**: `languages.<code>.volume` (total, mean/median per day).
- **Growth**: `growth.cagr` (yearly, needs 13+ months and ≥10 views/day base),
  `growth.yoy_12m` (last 12 vs previous 12 months), `growth.momentum_3m`.
  A field may be `null` with a `*_null_reason` — report it as
  "not computable at this volume/history", not as zero.
- **Trend**: `trend_test` (r², t, p, `significant`; `excluding_spikes`
  re-fits the trend with spike days zeroed — if its slope shrinks a lot, the
  "growth" is a few anomalous days, not a trend).
- **Anomalies**: `anomalies.event_driven` (news/viral spikes — 3+ spike days
  or one ≥10× baseline) and `weekend_weekday_ratio` (weekly seasonality).
  Never present event-driven or seasonal traffic as durable demand.
- **Reliability**: `reliability.score` (0–100), `level` (high/medium/low) and
  `reasons` — always mention the level when making a recommendation.
- **Cross-language**: `comparison.latest_index` (normalized, first month =
  100) and `comparison.note` — use for "which language has the bigger
  audience".
- `findings` (pre-written, data-backed sentences) and `assumptions`
  (methodology caveats).

Decision framing to give the founder (their call, not yours): combine
absolute size vs growth vs reliability. E.g. "PL has 3× the volume of CS but
is declining; CS is smaller yet growing — if the goal is growth, CS is the
safer bet; if it's immediate audience size, PL." End with the standing
caveat: **Wikipedia pageviews measure reader attention, not purchase
intent** — they are a cheap proxy, to be confirmed with app-store or search
data before investing.

## Output contract (what the script guarantees)

- `analyze` prints one JSON object: `topic`, `period`, `languages` (per
  language: article, url, volume, growth, trend_test, anomalies,
  weekend_weekday_ratio, reliability, seasonality, access/agent),
  `comparison`, `findings`, `assumptions`, `warnings`, `files`.
  `resolve` prints ranked candidates (disambiguation pages are never
  candidates). `series` writes `series_<lang>.csv` (daily raw data) and
  previews it.
- If a language fails (missing article, API error) it is dropped into
  `warnings` and the others still succeed; if all fail, exit 2.
- `assumptions` lists everything that could distort the numbers (filters
  used, auto-resolution, short periods, sparse small editions).

## Iterating (follow-up questions)

The founder will refine. Handle common follow-ups without redoing everything:

- Different window: re-run `analyze` with `--last`/`--start/--end`.
- Exact pages: re-run with `--article`.
- Deeper look at one language: `series --topic ... --languages pl --last 365`
  then read/plot the CSV yourself if needed.
- Device split: `--access mobile-app` vs `desktop` (app demand signal).
- Seasonality check: 3-year window (`--last 1095`) — `seasonality` flags
  months with +30% YoY jumps.

## Methodology

Formulas (OLS slope, CAGR, 12-vs-12 YoY, reliability score composition,
normalized index) and API details (endpoints, access/agent values, caching,
limits) live in `references/metrics.md` and `references/api.md`. Read them
only if the user asks how a number was computed — do not quote formulas
into answers unless asked.

## Hard rules

1. Never invent, round, or interpolate numbers that are not in the script
   output (including chart values). If a metric is `null`, say so.
2. Always state which Wikipedia pages were used (article names are in the
   output) — the user must be able to verify them.
3. Always surface reliability level with any growth claim.
4. Keep the "pageviews ≠ purchase intent" caveat in every final answer.
5. If the script fails, show stderr verbatim; do not pretend it succeeded.
6. Diagnose with the skill's own subcommands (`resolve`, `series`); do not
   hand-roll Wikimedia API URLs — when a page has no data the warnings
   already suggest the nearest existing pages.
7. Verify a page actually exists before relying on it: a title that exists
   as a *suggestion* in search is not a page. `resolve`/`analyze` only ever
   return articles that have real pageview data.
8. Read `warnings` before answering: they flag disambiguation pages,
   data-check failures, and corrections — surface any that affect the answer.
