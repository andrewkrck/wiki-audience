# Metrics reference

All metrics are computed in `scripts/wa/metrics.py` (pure Python, no numpy).
Inputs: a daily series of pageviews for one article (one language),
zero-filled for missing days, over the requested period.

## Per-language metrics (`languages.<code>` in analyze output)

Besides the metric sections below, each language block carries:
`article`, `url`, `resolution` (`explicit` | `wikidata` | `auto`),
`points` (number of days / months in the window), `access`, `agent`,
`weekend_weekday_ratio` (weekend per-day mean ÷ weekday per-day mean; `null`
for windows <14 days — ≥1.5 or ≤0.67 means a strong weekly seasonality,
which is not growth).

### volume
- `total` — sum of daily views in the period.
- `mean_daily` / `median_daily` — total ÷ days / median of daily values.

### trend_test — OLS linear regression of views/day vs day index
- `r2` — coefficient of determination.
- `t` — t-statistic of the slope (df = n−2), `p` — two-sided p-value
  approximated with the normal distribution (df ≥ 100 here, so the
  approximation is accurate).
- `significant` — |t| ≥ 1.96.
- `excluding_spikes` — the same test re-fit with spike days zeroed
  (present only when spikes were detected): `slope`, `t`, `p`, `significant`.
  If the slope shrinks a lot, the trend is driven by a few anomalous days
  rather than sustained change.

(The slope itself lives in `growth.slope_views_per_day` / `growth.linear_trend_per_month_pct`; there is no separate direction field — read the sign of the slope.)

### anomalies — spike / event detection
- `spike_days` — dates (max 10 listed) more than **3 robust sigmas** above
  the median baseline (sigma = 1.4826 × MAD, floored by the Poisson
  expectation √(median+1); a day must also be ≥3× the median when a
  baseline exists, and ≥10 views absolute — this keeps steady-growing and
  low-volume pages from flagging themselves).
- `spike_count` — total number of spike days.
- `max_ratio` — largest spike ÷ median baseline.
- `event_driven` — true when spikes cover 2%+ of the days (min 3), or one
  day is ≥10× the baseline (news coverage, viral hit, or bot burst). Such
  traffic is **not** durable demand; the reliability score is capped at 60
  in that case.

### growth
- `cagr` — compound annual growth rate:
  `(mean_last_month ÷ mean_first_month)^(1/yrs) − 1`, where the means are
  per-day averages of the first and last calendar month in the period and
  `yrs` is the span between them in years. Requires ≥13 months of data and a
  base month with ≥10 views/day (below that the rate is numerically
  unstable); otherwise `null` with `cagr_null_reason`.
- `yoy_12m` — (per-day mean of last 12 months ÷ per-day mean of previous 12
  months) − 1. Requires ≥24 months; `null` + `yoy_null_reason` otherwise.
  A robust comparison that ignores intra-year seasonality.
- `momentum_3m` — (per-day mean of last 3 months ÷ per-day mean of previous 3)
  − 1. Requires ≥6 months; `null` + `momentum_null_reason` otherwise.
- `linear_trend_per_month_pct` — OLS slope × 30.4375 ÷ mean_daily × 100
  (expected views next month, relative to the period mean).
- `slope_views_per_day` — the OLS slope, raw.

All growth rates use **per-day** monthly means, so partial months (e.g. the
current month) compare fairly.

### reliability — 0..100 score with human-readable `reasons`
Weighted blend of four components (weights in parentheses):
1. **Significance** (30%): `min(1, |t| / 3)` — is the slope reliably ≠ 0?
2. **Stability** (25%): split the period in halves; 1.0 if both halves trend
   the same direction and with similar magnitude, 0.0 if directions flip.
   Short periods (<28 days) get a neutral 0.5.
3. **Noise** (25%): `1 − min(1, cv / 0.8)` where `cv` = std of OLS
   residuals ÷ median daily views (day-to-day wobble relative to level).
4. **Volume** (20%): `min(1, mean_daily / 100)` — Poisson noise dominates at
   low counts.

There is deliberately **no completeness component**: the API omits
zero-view days, so "fraction of days present in the response" cannot
distinguish missing data from zero traffic — it would have been a
measurement artifact, not a property of the data.

Hard gates applied afterwards:
- `total < 1000` views over the whole period → score capped at **25**.
- Trend not statistically significant (|t| < 1.96) → score capped at **50**.
- Event-driven traffic (`anomalies.event_driven`) → score capped at **60**.

Levels: **high ≥ 70**, **medium ≥ 40**, **low < 40**.

### seasonality (requires ≥24 months)
- `yoy_by_month` — per calendar month, last year's value vs the year before.
- `flagged_months` — months with YoY ≥ +30%: isolated jumps may be
  seasonal (news event, sports season) rather than durable growth.
`null` when <24 months of data.

### comparison (`comparison` in analyze output)
- `index` — per language, monthly means normalized to first month = 100
  (per-day means, so unequal month lengths don't distort).
- `latest_index` — each language's index value in the latest common month.
- `note` — one-line relative statement (e.g. "cs is 2.0× pl in the latest
  month"). For >30:1 ranges, use the chart instead of raw ratios.

## Known limitations (also in report assumptions)
- Pageviews ≈ reader attention, not purchase intent.
- One article per language approximates the topic; related pages are not
  aggregated.
- Bot/spider traffic excluded by default (`--agent user`); "user" still
  includes logged-in editors browsing in bulk.
- Data starts 2015-07-01.
- Small editions: few views/day → wide confidence intervals; trust the
  reliability score, not the precise percentage.
