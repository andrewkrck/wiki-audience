"""Trend, growth, and reliability metrics for Wikipedia pageview series.

Pure Python (no third-party imports) so it can be unit-tested anywhere.

Formulas are documented in references/metrics.md; keep the two in sync.
"""
from __future__ import annotations

import math
import urllib.parse
from datetime import date, timedelta

CAGR_MIN_BASE = 10.0  # views/day in the base month; below this, rates are meaningless
CAGR_MIN_MONTHS = 13  # need at least a full year of monthly data to annualize
AVG_DAYS_PER_MONTH = 30.4375  # 365.25 / 12


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def ols(y: list[float]) -> dict:
    """Ordinary least squares of y on day index x = 0..n-1.

    Returns slope, intercept, r2, t-statistic for slope, two-sided p-value
    (normal approximation; accurate enough for the 14+ day windows this
    skill accepts), residuals, and the mean of y.
    """
    n = len(y)
    if n < 3:
        return {"slope": 0.0, "intercept": 0.0, "r2": 0.0, "t": 0.0, "p": 1.0,
                "residuals": [float(v) for v in y], "mean": sum(y) / n if n else 0.0}
    xm = (n - 1) / 2.0
    ym = sum(y) / n
    sxx = sum((i - xm) ** 2 for i in range(n))
    sxy = sum((i - xm) * (v - ym) for i, v in enumerate(y))
    slope = sxy / sxx
    intercept = ym - slope * xm
    residuals = [v - (intercept + slope * i) for i, v in enumerate(y)]
    ss_res = sum(r * r for r in residuals)
    ss_tot = sum((v - ym) ** 2 for v in y)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    if n > 2 and ss_res > 0:
        s2 = ss_res / (n - 2)
        se = math.sqrt(s2 / sxx)
        t = slope / se if se > 0 else 0.0
    else:
        t = math.copysign(float("inf"), slope) if slope != 0 else 0.0
    p = 0.0 if math.isinf(t) else 2.0 * (1.0 - norm_cdf(abs(t)))
    return {"slope": slope, "intercept": intercept, "r2": max(0.0, min(1.0, r2)),
            "t": t, "p": p, "residuals": residuals, "mean": ym}


def daterange(start: date, end: date):
    """Every date from start to end (inclusive)."""
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


SPIKE_SIGMA = 3.0      # flag a day more than 3 robust sigmas above the baseline
SPIKE_RATIO = 3.0      # and (when there is a baseline) at least 3x the median
MIN_SPIKE_VIEWS = 10   # absolute floor: below this, noise dominates


def detect_spikes(vals: list[float]) -> dict:
    """Robust single-day spike detection (baseline = median; sigma = 1.4826*MAD,
    floored by the Poisson expectation sqrt(median+1) so low-volume pages are
    not flagged for normal count noise).

    A day is a spike when it exceeds median + SPIKE_SIGMA*sigma AND (if a
    baseline exists) is at least SPIKE_RATIO times the median - the ratio
    guard keeps steady-growing pages from flagging their own high end.
    Returns {spike_indices, max_ratio, event_driven}; event_driven = spikes on
    2%+ of the days (min 3), or one day >= 10x the baseline (news event /
    viral hit / bot burst). Windows under 14 days cannot be assessed.
    """
    n = len(vals)
    if n < 14:
        return {"spike_indices": [], "max_ratio": None, "event_driven": False}
    med = sorted(vals)[n // 2]
    mad = sorted(abs(v - med) for v in vals)[n // 2]
    sigma = max(1.4826 * mad, math.sqrt(med + 1.0))
    idx: list[int] = []
    max_ratio = None
    for i, v in enumerate(vals):
        if v < MIN_SPIKE_VIEWS:
            continue
        if med > 0 and v < SPIKE_RATIO * med:
            continue
        if v <= med + SPIKE_SIGMA * sigma:
            continue
        idx.append(i)
        r = v / max(med, 1.0)
        max_ratio = r if max_ratio is None else max(max_ratio, r)
    event = len(idx) >= max(3, n // 50) or (max_ratio is not None and max_ratio >= 10)
    return {
        "spike_indices": idx,
        "max_ratio": round(max_ratio, 1) if max_ratio is not None else None,
        "event_driven": event,
    }


def weekend_weekday_ratio(vals: list[float], start: date) -> float | None:
    """Weekend per-day mean divided by weekday per-day mean.

    Day-of-week seasonality: >=1.5 or <=0.67 means a strong weekly pattern
    (relevant when comparing raw totals across partial windows). None for
    windows under 14 days.
    """
    n = len(vals)
    if n < 14:
        return None
    we = wd = 0
    n_we = n_wd = 0
    for i, v in enumerate(vals):
        if (start + timedelta(days=i)).weekday() >= 5:
            we += v
            n_we += 1
        else:
            wd += v
            n_wd += 1
    if n_we == 0 or n_wd == 0:
        return None
    return round((we / n_we) / max(wd / n_wd, 1e-9), 2)


def series_values(daily: dict, start: date, end: date) -> list[float]:
    """Daily series as a flat list from start to end (inclusive),
    zero-filled for days absent from `daily`."""
    return [float(daily.get(d, 0)) for d in daterange(start, end)]


def monthly_means(daily: dict, start: date, end: date) -> list[dict]:
    """Per calendar month: per-day average views.

    Partial months (the current one) use only elapsed days, so months are
    comparable. Returns [{ym: 'YYYY-MM', days, mean_daily}] in order.
    """
    out = []
    cur = date(start.year, start.month, 1)
    while cur <= end:
        ym = (cur.year, cur.month)
        # days of this month inside [start, end]
        m_start = max(start, cur)
        nxt = date(cur.year, cur.month, 1)
        if cur.month == 12:
            m_end_full = date(cur.year, 12, 31)
        else:
            m_end_full = date(cur.year, cur.month + 1, 1) - timedelta(days=1)
        m_end = min(end, m_end_full)
        if m_start <= m_end:
            vals = []
            d = m_start
            while d <= m_end:
                vals.append(float(daily.get(d, 0)))
                d += timedelta(days=1)
            out.append({
                "ym": f"{cur.year:04d}-{cur.month:02d}",
                "days": len(vals),
                "mean_daily": sum(vals) / len(vals) if vals else 0.0,
            })
        cur = date(cur.year, cur.month + 1, 1) if cur.month < 12 else date(cur.year + 1, 1, 1)
    return out


def _month_index(ym: str) -> int:
    y, m = int(ym[:4]), int(ym[5:7])
    return y * 12 + m


def cagr(months: list[dict]) -> tuple[float | None, str | None]:
    """Compound annual growth rate between first and last month per-day means.

    Requires CAGR_MIN_MONTHS months: annualizing a short span magnifies noise
    into a meaningless rate. Returns (cagr, reason_if_null).
    """
    if len(months) < CAGR_MIN_MONTHS:
        return None, (f"need at least {CAGR_MIN_MONTHS} months "
                      "for an annualized growth rate")
    first, last = months[0], months[-1]
    if first["mean_daily"] < CAGR_MIN_BASE:
        return None, (
            f"base month {first['ym']} averages {first['mean_daily']:.1f} views/day "
            f"(< {CAGR_MIN_BASE:.0f}); growth rate not meaningful at this volume"
        )
    years = (_month_index(last["ym"]) - _month_index(first["ym"])) / 12.0
    if years <= 0 or last["mean_daily"] <= 0:
        return None, "insufficient interval between months"
    rate = (last["mean_daily"] / first["mean_daily"]) ** (1.0 / years) - 1.0
    return rate, None


def window_ratio(months: list[dict], n: int) -> tuple[float | None, str | None]:
    """(mean of last n months per-day) / (mean of previous n months) - 1.

    Shared shape for yoy_12m (n=12) and momentum_3m (n=3).
    """
    if len(months) < 2 * n:
        return None, f"need {2 * n} months for a {n}-vs-{n} month comparison"
    last = sum(m["mean_daily"] for m in months[-n:]) / n
    prev = sum(m["mean_daily"] for m in months[-2 * n:-n]) / n
    if prev <= 0:
        return None, f"previous {n} months have no views"
    return last / prev - 1.0, None


def yoy_12m(months: list[dict]) -> tuple[float | None, str | None]:
    """(last 12 months per-day mean) / (previous 12 months per-day mean) - 1."""
    return window_ratio(months, 12)


def momentum_3m(months: list[dict]) -> tuple[float | None, str | None]:
    """(last 3 months per-day mean) / (previous 3 months per-day mean) - 1."""
    return window_ratio(months, 3)


def seasonality_flags(months: list[dict]) -> dict | None:
    """Month-level YoY change; flags months with unusual jumps.

    Requires 24+ months. Returns None otherwise, else
    {yoy_by_month: {ym: yoy}, flagged: [ym, ...]} where flagged = yoy >= +30%.
    """
    if len(months) < 24:
        return None
    by_ym = {m["ym"]: m["mean_daily"] for m in months}
    yoy = {}
    for m in months:
        y, mo = int(m["ym"][:4]), int(m["ym"][5:7])
        prev_ym = f"{y - 1:04d}-{mo:02d}"
        if prev_ym in by_ym and by_ym[prev_ym] > 0:
            yoy[m["ym"]] = m["mean_daily"] / by_ym[prev_ym] - 1.0
    flagged = sorted(ym for ym, v in yoy.items() if v >= 0.30)
    return {"yoy_by_month": yoy, "flagged": flagged}


def reliability(
    daily: dict, start: date, end: date, fit: dict, total: int, anomalies: dict
) -> dict:
    """Score 0-100 + human-readable reasons. See references/metrics.md.

    Components (weights):
      significance  30%  min(1, |t|/3) - is the slope != 0?
      stability     25%  split-half: do both halves trend the same way?
      noise         25%  residual std / median level (daily wobble vs level)
      volume        20%  min(1, mean_daily/100) - low counts are noisy
    Hard gates: total < 1000 views caps at 25; trend not significant caps at
    50; event-driven traffic (spike detection) caps at 60.

    (There is deliberately no 'completeness' component: the API omits
    zero-view days, so 'days present in the response' cannot distinguish
    missing data from zero traffic.)
    """
    n_days = (end - start).days + 1
    vals = series_values(daily, start, end)
    mean_daily = sum(vals) / n_days
    median_daily = sorted(vals)[len(vals) // 2]

    sig = min(1.0, abs(fit["t"]) / 3.0) if math.isfinite(fit["t"]) else 1.0
    significant = abs(fit["t"]) >= 1.96
    reasons: list[str] = []

    # split-half directional stability
    half = n_days // 2
    if half >= 14:
        fit1 = ols(vals[:half])
        fit2 = ols(vals[half:])
        s1, s2 = fit1["slope"], fit2["slope"]
        if s1 * s2 >= 0:
            mag = max(abs(s1), abs(s2))
            stab = 1.0 - 0.5 * min(1.0, abs(s1 - s2) / mag) if mag > 0 else 0.5
        else:
            stab = 0.0
        if stab >= 0.5:
            reasons.append("growth direction is stable across the first and second halves")
        else:
            reasons.append("growth direction flipped between the two halves of the period")
    else:
        stab = 0.5  # short window: neutral
        reasons.append("period too short to assess split-half stability")
    # volume
    vol = min(1.0, mean_daily / 100.0)
    # noise: residual wobble relative to the median daily level
    resid = fit["residuals"]
    rmean = sum(resid) / len(resid)
    rvar = sum((r - rmean) ** 2 for r in resid) / len(resid)
    cv_level = math.sqrt(rvar) / max(median_daily, 1.0)
    noise = 1.0 - min(1.0, cv_level / 0.8)

    score = 100.0 * (0.30 * sig + 0.25 * stab + 0.25 * noise + 0.20 * vol)

    if math.isfinite(fit["t"]):
        if significant:
            reasons.append(f"trend is statistically significant (t={fit['t']:.1f}, p={fit['p']:.3g})")
        else:
            reasons.append(f"trend is NOT statistically significant (t={fit['t']:.1f}, p={fit['p']:.3g})")
    else:
        reasons.append("trend fits a straight line exactly (t is infinite)")
    if cv_level >= 0.5:
        reasons.append(f"high day-to-day wobble (residual std = {cv_level:.0%} of median level)")
    elif cv_level <= 0.25:
        reasons.append(f"low day-to-day wobble (residual std = {cv_level:.0%} of median level)")
    else:
        reasons.append(f"moderate day-to-day wobble (residual std = {cv_level:.0%} of median level)")
    if mean_daily >= 100:
        reasons.append(f"high volume ({mean_daily:.0f} views/day)")
    elif mean_daily >= 10:
        reasons.append(f"moderate volume ({mean_daily:.0f} views/day)")
    else:
        reasons.append(f"very low volume ({mean_daily:.1f} views/day) - rates are unstable")
    if total < 1000:
        score = min(score, 25.0)
        reasons.insert(0, f"insufficient total volume ({total} views over the whole period)")
    if not significant:
        score = min(score, 50.0)
    if anomalies.get("event_driven"):
        score = min(score, 60.0)
        reasons.insert(
            0,
            f"event-driven traffic: {anomalies['spike_count']} spike day(s), up to "
            f"{anomalies['max_ratio']}x baseline - treat growth as possibly hype, not durable demand",
        )
    level = "high" if score >= 70 else "medium" if score >= 40 else "low"
    return {"score": round(score, 1), "level": level, "reasons": reasons}


def analyze_series(
    daily: dict,
    start: date,
    end: date,
    article: str,
    project: str,
    language: str,
    resolution: str,
    access: str,
    agent: str,
) -> tuple[dict, list[dict]]:
    """Full per-language metric block + monthly series (for chart/comparison).
    The block is public JSON; the monthly series is internal data."""
    n_days = (end - start).days + 1
    vals = series_values(daily, start, end)
    total = int(sum(vals))
    mean_daily = total / n_days
    sorted_vals = sorted(vals)
    median_daily = sorted_vals[len(sorted_vals) // 2]
    fit = ols(vals)
    months = monthly_means(daily, start, end)
    g, g_reason = cagr(months)
    y, y_reason = yoy_12m(months)
    m3, m3_reason = momentum_3m(months)
    trend_per_month_pct = (fit["slope"] * AVG_DAYS_PER_MONTH / mean_daily * 100.0
                           if mean_daily > 0 else None)
    spikes = detect_spikes(vals)
    trend_test = {
        "r2": round(fit["r2"], 3),
        "t": round(fit["t"], 2) if math.isfinite(fit["t"]) else None,
        "p": fit["p"],
        "significant": abs(fit["t"]) >= 1.96,
    }
    if spikes["spike_indices"]:
        # trend re-fit with spike days zeroed: how much of the slope is hype?
        spike_set = set(spikes["spike_indices"])
        fit_excl = ols([0.0 if i in spike_set else v for i, v in enumerate(vals)])
        trend_test["excluding_spikes"] = {
            "slope": round(fit_excl["slope"], 3),
            "t": round(fit_excl["t"], 2) if math.isfinite(fit_excl["t"]) else None,
            "p": fit_excl["p"],
            "significant": abs(fit_excl["t"]) >= 1.96,
        }
    spike_days = [(start + timedelta(days=i)).isoformat() for i in spikes["spike_indices"]]
    anomalies = {
        "spike_days": spike_days[:10],
        "spike_count": len(spike_days),
        "max_ratio": spikes["max_ratio"],
        "event_driven": spikes["event_driven"],
    }
    rel = reliability(daily, start, end, fit, total, anomalies)
    season = seasonality_flags(months)
    block = {
        "language": language,
        "project": project,
        "article": article,
        "url": f"https://{project}.org/wiki/{urllib.parse.quote(article.replace(' ', '_'))}",
        "resolution": resolution,
        "volume": {
            "total": total,
            "mean_daily": round(mean_daily, 1),
            "median_daily": round(median_daily, 1),
        },
        "growth": {
            "cagr": round(g, 3) if g is not None else None,
            "cagr_null_reason": g_reason,
            "yoy_12m": round(y, 3) if y is not None else None,
            "yoy_null_reason": y_reason,
            "momentum_3m": round(m3, 3) if m3 is not None else None,
            "momentum_null_reason": m3_reason,
            "linear_trend_per_month_pct": (round(trend_per_month_pct, 2)
                                           if trend_per_month_pct is not None else None),
            "slope_views_per_day": round(fit["slope"], 3),
        },
        "trend_test": trend_test,
        "anomalies": anomalies,
        "weekend_weekday_ratio": weekend_weekday_ratio(vals, start),
        "reliability": rel,
        "seasonality": {
            "flagged_months": season["flagged"],
            "yoy_by_month": {k: round(v, 3) for k, v in season["yoy_by_month"].items()},
        } if season else None,
        "points": {
            "days": n_days,
            "months": len(months),
        },
        "access": access,
        "agent": agent,
    }
    return block, months


def normalized_index(months: list[dict]) -> list[float | None]:
    """Monthly per-day means scaled to first month = 100."""
    base = None
    for m in months:
        if m["mean_daily"] > 0:
            base = m["mean_daily"]
            break
    if base is None:
        return [None] * len(months)
    return [round(100.0 * m["mean_daily"] / base, 1) for m in months]


def compare_languages(months_by_lang: dict[str, list[dict]]) -> dict:
    """Cross-language comparison from per-language monthly series."""
    if len(months_by_lang) < 2:
        return {"index": None, "note": "single language - no cross-language comparison"}
    index: dict[str, dict[str, float]] = {}
    labels: dict[str, list[str]] = {}
    for lang, months in months_by_lang.items():
        labels[lang] = [m["ym"] for m in months]
        idx = normalized_index(months)
        index[lang] = dict(zip(labels[lang], idx))
    # latest common month index values
    latest: dict[str, float] = {}
    for lang, series in index.items():
        for ym in reversed(labels[lang]):
            if series.get(ym) is not None:
                latest[lang] = series[ym]
                break
    note = None
    if len(latest) >= 2:
        items = sorted(latest.items(), key=lambda kv: kv[1], reverse=True)
        top, bottom = items[0], items[-1]
        if top[1] > 0:
            ratio = top[1] / bottom[1] if bottom[1] > 0 else None
            if ratio is not None:
                note = (f"{top[0]} ends at index {top[1]:.0f} vs {bottom[0]} at "
                        f"{bottom[1]:.0f} ({ratio:.1f}x) in the latest month")
    return {"index": index, "latest_index": latest, "note": note}
