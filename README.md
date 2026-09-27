# wiki-audience — an Agent Skill for Wikipedia pageview research

A standalone [Agent Skill](https://agentskills.io/specification) that a fast,
low-cost AI agent (e.g. Claude Haiku 4.5-class) uses to help B2C app founders
decide **which topics to develop next and which languages to launch in**, by:

1. resolving a topic to the correct Wikipedia article in each language
   (Wikidata sitelinks, then MediaWiki search with a visibility trail,
   disambiguation pages excluded),
2. fetching real pageview data from the
   [Wikimedia Pageviews API](https://wikimedia.org/api/rest_v1/metrics/pageviews),
3. computing growth, trend-significance, **reliability** metrics and
   **spike / event-driven traffic detection** (separating news-viral
   traffic from durable trends),
4. drawing a normalized comparison chart (300 DPI) and a **one-page
   shareable PDF report** with the numbers and caveats.

The agent orchestrates the bundled Python CLI (`scripts/wiki_audience.py`)
and interprets its JSON — it does not write analysis code at runtime.

## Layout

```
wiki-audience/
├── SKILL.md                  # agent instructions (Agent Skills format)
├── LICENSE                   # MIT
├── requirements.txt          # requests + matplotlib (exact pins)
├── .gitignore
├── scripts/
│   ├── wiki_audience.py      # CLI entry (PEP 723 inline deps for `uv run`)
│   └── wa/
│       ├── cli.py            # argparse subcommands: resolve | analyze | series
│       ├── http.py           # HTTP client: retries, backoff, disk cache, UA
│       ├── fetch.py          # pageviews / search / Wikidata wrappers
│       ├── metrics.py        # all statistics (pure Python, no numpy)
│       ├── charts.py         # normalized-index chart (matplotlib)
│       └── report.py         # findings + one-page PDF renderer
├── references/
│   ├── metrics.md            # exact formulas (for "how was X computed?")
│   └── api.md                # endpoints, parameters, caching, exit codes
└── tests/
    └── test_metrics.py       # stdlib-only unit tests on synthetic series
```

## Quickstart

```bash
# one command does it all (uv creates the venv automatically)
uv run scripts/wiki_audience.py analyze --topic "intermittent fasting" \
  --languages pl,cs --last 730 --out ./wa-out
# -> prints JSON; writes wa-out/chart.png, wa-out/report.pdf, wa-out/analysis.json
```

## How the output was verified

This project was developed with AI assistance (code generation + live
iteration). Every non-trivial claim below was verified by re-running the
commands; nothing is taken on faith.

**1. Unit tests on synthetic data (`tests/test_metrics.py`, stdlib only).**
OLS slope/intercept on exact lines (slope=2 → measured 2.0, r²=1), CAGR
doubled-over-a-year = +100%, 12-vs-12 YoY on constructed 24-month series,
partial-month handling, reliability ordering (smooth growth > noisy growth,
flat noise capped at non-high), spike detection (planted spikes caught;
no false positives on flat, steady-growth, or all-zero series; event-driven
threshold scales with window length), weekend/weekday ratio, trend
re-fit with spikes zeroed, normalized-index base-month=100, seasonality
flagging of a planted +60% month. `python3 tests/test_metrics.py` →
ALL TESTS PASSED.

**2. API ground-truth cross-checks (live, September 2026).**
- Daily and monthly granularities must be internally consistent: for every
  language in every demo run the CLI re-fetches the same window at monthly
  granularity and compares totals — all runs reported 0.00% divergence.
- Endpoint semantics were probed before coding: `access` values
  (`all-access|desktop|mobile-web|mobile-app`), `agent` values
  (`all-agents|user|automated|spider`), inclusive `YYYYMMDD` windows, no
  window cap (a 10-year daily request returns fine), 404 for missing
  articles, data availability from 2015-07-01.
- A live bug was caught this way: MediaWiki's `pageids` batch parameter
  requires `|` separators (comma batch errors with `badinteger`);
  search-result titles also can drift from canonical titles (unicode
  normalization) — both handled and tested.

**3. End-to-end scenario runs (the three example queries).**
- `intermittent fasting` pl vs cs, 2 years: correct articles resolved
  (cs via Wikidata Q1666254 → *Přerušovaný půst*; pl has no sitelink —
  the closest page *Głodówka lecznicza* was picked explicitly); findings:
  PL ≈5.8K views/2y declining (−39% YoY, significant), CS ≈6.7K views/2y
  declining (−59% CAGR, significant) → both small and shrinking, reported
  with reliability levels.
- `astronomy` uk, 3 years: Wikidata resolved *Астрономія*; −34%/yr CAGR,
  t=−28 (significant), reliability 60.6/100 — with the expected caveat that
  the −55% 12m-YoY vs +33% 3m-momentum split says the decline may be
  reversing short-term.
- `English language` across pl/cs/uk/de/sv, 2 years: one run, 5 languages,
  ~25 s wall clock; all five pages resolved via Wikidata Q1860 sitelinks;
  high reliability (88–95) in all, all trending down 6–34%/yr (Wikipedia
  language articles are stable reference pages — the report says exactly
  that via the metrics).
- Spike / event detection against real data (the spec's Tier-3 cases):
  `KPop Demon Hunters` en, 2 years — the viral 2025 release produced 125
  spike days (max 23× baseline); the run marks it **event-driven**, caps
  reliability at 60, and reports the trend re-fit with spikes zeroed. The
  `astronomy` uk page is likewise event-driven (a genuine 10× traffic step
  in Oct 2023, then decay — verified in the raw daily series). The control
  (`Głodówka lecznicza` pl, steadily declining) is correctly **not**
  flagged. A page that is merely declining is not a false positive because
  spike days must exceed 3× the median baseline, not just sit above an
  early-window mean.
- Disambiguation handling: passing `en:Apple (disambiguation)` explicitly
  yields a loud `warnings` entry (the run still proceeds); search-based
  `resolve` candidates silently exclude DAB pages (detected via
  `pageprops`, piggy-backed on the existing title-batch request — no extra
  HTTP traffic).
- PDF: single A4 page, text-extracted with pypdf to confirm table,
  findings and assumptions all render — re-verified for the 5-language
  case (longest header, wrapped onto two lines); chart inspected visually
  and exported at 300 DPI.

> Note: the reliability numbers recorded above used the first
> implementation of the score (5 components, incl. a completeness term).
> The completeness term was later removed — the API omits zero-view days,
> so "days present in the response" was a measurement artifact, not a data
> property (see `references/metrics.md`). Re-running the same scenarios
> with the final score gives slightly lower reliability values (e.g. uk
> astronomy 53.4 instead of 60.6); the levels and conclusions are unchanged.

**4. Cheap-model full-scenario test (Qwen3-27B quantized, local OpenAI-compatible
server, driven non-interactively by the `pi` harness with `--skill`).**
Run 1 — "Compare demand for intermittent fasting in Polish and Czech…":
the model loaded the skill, ran `resolve` first, correctly rejected the
off-topic top search candidates on pl, discovered that pl.wikipedia has **no
dedicated intermittent-fasting page** (verified the red link "Dieta
przerywana" does not exist), chose the closest real page *Głodówka lecznicza*
with that reasoning on the record, and ran `analyze` with `--article`
(it self-corrected one transposed-title typo using the script's warning).
Final answer: a metric table for both languages (all values cross-checked
against its own `analysis.json` — exact match, zero invented numbers), the
reliability levels, the page-coverage asymmetry, a decision recommendation
framed as the founder's call, and the pageviews ≠ purchase-intent caveat.
Transcript archived in `tests/agent-transcripts/`. Wall clock ≈ 34 min on a
local 27B (human-interactive equivalent: one `resolve` + one `analyze`
≈ 30 s + reading the JSON). Run 2 — "Has astronomy interest in Ukraine
grown over the last 3 years? How reliable?": a single `analyze --last 1095`
run (auto-resolution via Wikidata, no manual article picking needed), ~7 min
wall clock, and the answer separated **direction** (declining —
statistically significant, t = −28.4) from **magnitude** (−34%/yr CAGR is
noisy at this volume) — exactly the reliability-signal separation the
metric was designed to express; all figures matched the JSON it produced.
The test validated the design goal: a small
model only needs to call one or two commands, read JSON, and summarize.

**5. What the AI did and did not do.**
AI (assistant in this development session) wrote the code, designed the
metrics, and drafted the docs. A human-directed loop drove live API
probing and validation: each API assumption was checked with a real
request before being baked into code; each unit-test expectation was
derived from hand-computed values, not from the code's own output. Any
future number quoted to a user should be traceable to a reproducible
command (all three demo commands are in §3 above).

## Iterative development — from basic queries to deep research

The skill is staged so complexity can be added without rewrites.

**Stage 1 (implemented): single article per language, 1–3 years, daily
granularity.** One pageviews request per language per run, plus the
monthly-granularity cross-check, the Wikidata resolution (2 requests per
topic) and, when no Wikidata sitelink exists, a search per language. A
5-language run takes ~25 s wall clock. Good for the founder's quick
prioritization questions.

**Stage 2: topic aggregation & better resolution.**
- Sum several related articles (e.g. *16:8 diet* + *Intermittent
  fasting* + *Głodówka lecznicza*) into a topic series: `analyze` accepts
  `--articles "pl:Title1+Title2;cs:Title3"`; metrics then apply to the
  summed series (reliability uses the summed volume, which is usually
  much higher).
- Reverse resolution: given a *local-language* article, find its Wikidata
  item (DBpedia/Wikidata URL in the page, or `wbsearchentities` on the
  title) and expand to other languages automatically.
- Add `--granularity monthly` as the fetch default for periods > 3 years
  (smaller payloads, same metrics).

**Stage 3: larger volumes & faster runs.**
- Parallelize fetches with a 4-worker thread pool (the client's 150 ms
  throttle and Wikimedia's 429 handling already protect against
  rate limits); 20-language runs drop from ~100 s to ~15 s.
- Long-term history: 10-year daily is fine in one request (verified), but
  store raw responses in the cache as CSV/parquet and build a small
  read-only data warehouse for repeated analyses; `series` already
  exports per-language CSVs.
- Seasonal decomposition (additive STL-style: 36-weekly or 12-monthly
  moving average) to separate seasonality from trend — the current
  `seasonality` month-flags, `weekend_weekday_ratio`, and robust
  spike detection are a deliberately simple first step.

**Stage 4: decision-grade research.**
- Combine with app-store keyword volumes / Google Trends for the same
  markets (corroborate the pageview signal; pageviews = attention proxy
  only).
- Hypothesis testing over multiple candidates at once: a `screen`
  subcommand that ranks N topics × M languages by (size, growth,
  reliability) into a single table — the obvious next CLI surface.
- Statistical honesty upgrades: bootstrap CIs on YoY/CAGR (currently
  point estimates + significance of the linear fit), and per-month
  confidence ribbons on the chart.

**Engineering notes that will matter at scale:** keep the disk cache
(pageview responses carry a 1-hour TTL - recent-day data is still changing;
the cache lives in `~/.cache/wiki-audience`, outside the skill tree, so the
skill stays portable and can be installed read-only), keep metrics in pure
Python until profiling says otherwise (a 2-year daily series is ~730 points;
numpy buys little), keep JSON-on-stdout so agents never parse HTML, and
keep exit codes stable (0/2/3/4 are part of the agent contract).
