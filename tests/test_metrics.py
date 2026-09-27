"""Self-contained tests for wa.metrics (no third-party imports required).

Run:  python3 tests/test_metrics.py
"""
import os
import random
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from wa import metrics as M  # noqa: E402

START = date(2024, 1, 1)
FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def daily_from_values(vals):
    d = {START + timedelta(days=i): v for i, v in enumerate(vals)}
    return (START, START + timedelta(days=len(vals) - 1), d)


def make_blocks():
    """Two languages, 13 months of monthly means for compare_languages."""
    def months_series(base, growth):
        out = []
        y, m = 2024, 1
        for i in range(13):
            out.append({"ym": f"{y:04d}-{m:02d}", "days": 30,
                        "mean_daily": base * (growth ** i)})
            m += 1
            if m == 13:
                y, m = 2025, 1
        return out

    return {"pl": months_series(100, 1.0), "cs": months_series(50, 1.06)}


def test_ols_exact():
    print("ols: exact linear fit")
    y = [5.0 + 2.0 * i for i in range(100)]
    fit = M.ols(y)
    check("slope", abs(fit["slope"] - 2.0) < 1e-9, f"slope={fit['slope']}")
    check("intercept", abs(fit["intercept"] - 5.0) < 1e-9)
    check("r2 == 1", fit["r2"] > 0.9999, f"r2={fit['r2']}")
    check("p == 0", fit["p"] == 0.0)


def test_ols_flat():
    print("ols: flat series is not significant")
    rng = random.Random(42)
    y = [100.0 + rng.uniform(-2, 2) for _ in range(365)]
    fit = M.ols(y)
    check("t small", abs(fit["t"]) < 1.96, f"t={fit['t']}")
    check("p > 0.05", fit["p"] > 0.05, f"p={fit['p']}")


def test_cagr():
    print("cagr: doubling over one year")
    months = []
    for i in range(13):  # 2024-01 .. 2025-01
        y, m = (2024, 1 + i) if i < 12 else (2025, 1)
        months.append({"ym": f"{y:04d}-{m:02d}", "days": 30,
                       "mean_daily": 100.0 if i == 0 else (200.0 if i == 12 else 100.0)})
    g, reason = M.cagr(months)
    check("cagr == 1.0", g is not None and abs(g - 1.0) < 1e-9, f"g={g} r={reason}")

    g, reason = M.cagr([{"ym": f"2024-{(i % 12) + 1:02d}", "days": 30,
                         "mean_daily": 5.0}
                        for i in range(12)]
                       + [{"ym": "2025-01", "days": 31, "mean_daily": 10.0}])
    check("low base -> None", g is None and "base month" in reason, f"r={reason}")

    short, reason = M.cagr([{"ym": "2024-01", "days": 31, "mean_daily": 100.0},
                            {"ym": "2024-02", "days": 28, "mean_daily": 200.0}])
    check("<13 months -> None", short is None and "13 months" in reason, f"r={reason}")


def test_yoy_and_momentum():
    print("yoy_12m and momentum_3m")
    months = ([{"ym": f"2023-{m:02d}", "days": 30, "mean_daily": 100.0} for m in range(1, 13)]
              + [{"ym": f"2024-{m:02d}", "days": 30, "mean_daily": 120.0} for m in range(1, 13)])
    y, reason = M.yoy_12m(months)
    check("yoy == 0.2", y is not None and abs(y - 0.2) < 1e-9, f"y={y}")

    six = ([{"ym": f"2024-{m:02d}", "days": 30, "mean_daily": 100.0} for m in range(1, 4)]
           + [{"ym": f"2024-{m:02d}", "days": 30, "mean_daily": 150.0} for m in range(4, 7)])
    m3, reason = M.momentum_3m(six)
    check("momentum == 0.5", m3 is not None and abs(m3 - 0.5) < 1e-9, f"m={m3}")

    y, reason = M.yoy_12m(six)
    check("yoy needs 24 months", y is None and "24 months" in reason)

    # zero-view previous window must be rejected, not divided by zero
    m3, reason = M.momentum_3m(
        [{"ym": f"2024-{m:02d}", "days": 30, "mean_daily": 0.0} for m in range(1, 4)]
        + [{"ym": f"2024-{m:02d}", "days": 30, "mean_daily": 50.0} for m in range(4, 7)])
    check("zero previous window -> None", m3 is None and "no views" in reason, f"r={reason}")


def test_monthly_means_partial():
    print("monthly_means: constant series -> constant per-day means (incl. partial month)")
    end = date(2024, 3, 20)
    daily = {}
    d = START
    while d <= end:
        daily[d] = 10
        d += timedelta(days=1)
    months = M.monthly_means(daily, START, end)
    check("3 months", len(months) == 3, f"n={len(months)}")
    check("all means 10", all(abs(m["mean_daily"] - 10.0) < 1e-9 for m in months),
          f"{[m['mean_daily'] for m in months]}")
    check("last month partial days", months[-1]["days"] == 20, f"days={months[-1]['days']}")


def test_reliability_levels():
    print("reliability: smooth growth vs flat noise vs noisy growth")
    n = 365
    smooth = [100.0 + 2.0 * i for i in range(n)]
    rng = random.Random(7)
    flat = [100.0 + rng.uniform(-8, 8) for _ in range(n)]
    # low base + heavy wobble: the noise should visibly lower the score
    noisy = [20.0 + 0.2 * i + rng.uniform(-30, 30) for i in range(n)]

    res = {}
    for name, vals in [("smooth", smooth), ("flat", flat), ("noisy", noisy)]:
        start, end, daily = daily_from_values(vals)
        fit = M.ols(list(vals))
        res[name] = M.reliability(daily, start, end, fit, int(sum(vals)),
                                  M.detect_spikes(list(vals)))

    check("smooth high", res["smooth"]["level"] == "high",
          f"{res['smooth']['level']} {res['smooth']['score']}")
    check("flat low", res["flat"]["level"] != "high",
          f"{res['flat']['level']} {res['flat']['score']}")
    check("noisy < smooth", res["noisy"]["score"] < res["smooth"]["score"],
          f"noisy={res['noisy']['score']} smooth={res['smooth']['score']}")
    check("flat not significant reason",
          any("NOT statistically significant" in r for r in res["flat"]["reasons"]))
    check("flat capped at 50", res["flat"]["score"] <= 50.0, f"{res['flat']['score']}")


def test_analyze_series_shape():
    print("analyze_series: shape and sanity of the full metric block")
    n = 400
    vals = [100.0 + 1.5 * i for i in range(n)]
    start, end, daily = daily_from_values(vals)
    block, months = M.analyze_series(daily, start, end, "Test article",
                                     "en.wikipedia", "en", "explicit", "all-access", "user")
    g = block["growth"]
    check("no internal keys", "_months" not in block)
    check("returns monthly series", isinstance(months, list) and len(months) == 14)
    check("total", block["volume"]["total"] == int(sum(vals)),
          f"total={block['volume']['total']}")
    check("cagr present", g["cagr"] is not None and g["cagr"] > 0.5, f"cagr={g['cagr']}")
    check("cagr sane (<1000%/yr)", g["cagr"] < 10, f"cagr={g['cagr']}")
    check("14 months (2024-01..2025-02)", block["points"]["months"] == 14, f"{block['points']}")
    check("significant", block["trend_test"]["significant"] is True)
    check("url encoded", block["url"] == "https://en.wikipedia.org/wiki/Test_article",
          f"{block['url']}")
    check("reliability present", 0 <= block["reliability"]["score"] <= 100)


def test_normalized_index():
    print("normalized_index: first month = 100; zero base handled")
    months = [{"ym": "2024-01", "days": 31, "mean_daily": 20.0},
              {"ym": "2024-02", "days": 28, "mean_daily": 40.0},
              {"ym": "2024-03", "days": 31, "mean_daily": 0.0}]
    idx = M.normalized_index(months)
    check("index", idx == [100.0, 200.0, 0.0], f"{idx}")
    idx0 = M.normalized_index([{"ym": "2024-01", "days": 31, "mean_daily": 0.0},
                                {"ym": "2024-02", "days": 28, "mean_daily": 10.0}])
    check("zero base rebase", idx0 == [0.0, 100.0], f"{idx0}")


def test_seasonality():
    print("seasonality_flags: spike month flagged")
    months = []
    y, m = 2024, 1
    for i in range(24):
        val = 100.0
        if (y, m) == (2025, 7):
            val = 160.0  # +60% vs 2024-07
        months.append({"ym": f"{y:04d}-{m:02d}", "days": 30, "mean_daily": val})
        m += 1
        if m == 13:
            y, m = 2025, 1
    s = M.seasonality_flags(months)
    check("flagged 2025-07", s and "2025-07" in s["flagged"], f"{s and s['flagged']}")
    check("yoy 2025-07 = 0.6", s and abs(s["yoy_by_month"]["2025-07"] - 0.6) < 1e-9)
    check("needs 24 months", M.seasonality_flags(months[:-1]) is None)


def test_compare_languages():
    print("compare_languages: index + note")
    comp = M.compare_languages(make_blocks())
    check("has index for both", set(comp["index"]) == {"pl", "cs"})
    check("pl first month 100", comp["index"]["pl"]["2024-01"] == 100.0)
    check("cs grew", comp["index"]["cs"]["2025-01"] > 100.0, f"{comp['index']['cs']['2025-01']}")
    check("note present", comp["note"] and "x" in comp["note"], f"{comp['note']}")
    single = M.compare_languages({"pl": make_blocks()["pl"]})
    check("single lang note", single["note"].startswith("single language"))


def test_spikes():
    print("detect_spikes: planted spikes, no false positives")
    rng = random.Random(1)
    flat = [100.0 + rng.uniform(-2, 2) for _ in range(120)]
    r = M.detect_spikes(flat)
    check("flat: no spikes", r["spike_indices"] == [] and not r["event_driven"])

    blip = list(flat)
    blip[60] = 500.0  # single 5x blip: a spike, but not event-driven
    r = M.detect_spikes(blip)
    check("single blip: one spike", r["spike_indices"] == [60], f"{r['spike_indices']}")
    check("single blip: not event-driven", not r["event_driven"])
    check("single blip: ratio ~5", r["max_ratio"] is not None and 4 < r["max_ratio"] < 6,
          f"{r['max_ratio']}")

    events = list(flat)
    for i in (30, 80, 100):
        events[i] = 400.0  # three 4x spikes -> event-driven
    r = M.detect_spikes(events)
    check("three spikes: event-driven", r["event_driven"] and len(r["spike_indices"]) >= 3,
          f"{r['spike_indices']}")

    r = M.detect_spikes([0.0] * 120)
    check("all zeros: no spikes", r["spike_indices"] == [])

    rising = [10.0 + 1.0 * i for i in range(365)]  # steady growth: no false positives
    r = M.detect_spikes(rising)
    check("steady growth: no false spikes", r["spike_indices"] == [], f"{r['spike_indices']}")

    r = M.detect_spikes([10.0] * 10)
    check("<14 days: not assessed", r["spike_indices"] == [] and r["max_ratio"] is None)


def test_weekend_ratio():
    print("weekend_weekday_ratio: weekend-heavy series ~2.0")
    vals = []
    for i in range(42):
        d = START + timedelta(days=i)
        vals.append(20.0 if d.weekday() >= 5 else 10.0)
    ratio = M.weekend_weekday_ratio(vals, START)
    check("ratio == 2.0", ratio == 2.0, f"{ratio}")
    check("short window -> None", M.weekend_weekday_ratio(vals[:10], START) is None)


def test_trend_excluding_spikes():
    print("analyze_series: spikes zeroed lower the trend t-statistic")
    rng = random.Random(3)
    vals = [100.0 + 0.8 * i + rng.uniform(-3, 3) for i in range(120)]
    for i in (60, 80, 100):
        vals[i] = 600.0  # upward spikes inflate the slope
    start, end, daily = daily_from_values(vals)
    block, _ = M.analyze_series(daily, start, end, "Spike test", "en.wikipedia", "en",
                                "explicit", "all-access", "user")
    tt = block["trend_test"]
    ex = tt["excluding_spikes"]
    check("excluding_spikes present", ex is not None)
    full_slope = block["growth"]["slope_views_per_day"]
    check("slope drops when spikes zeroed", ex["slope"] < full_slope,
          f"full={full_slope} excl={ex['slope']}")
    check("anomalies block", block["anomalies"]["spike_count"] >= 3
          and block["anomalies"]["event_driven"])
    check("event-driven caps reliability", block["reliability"]["score"] <= 60.0,
          f"{block['reliability']['score']}")
    check("no internal keys in block", "_months" not in block)


if __name__ == "__main__":
    for fn in [
        test_ols_exact,
        test_ols_flat,
        test_cagr,
        test_yoy_and_momentum,
        test_monthly_means_partial,
        test_reliability_levels,
        test_analyze_series_shape,
        test_spikes,
        test_weekend_ratio,
        test_trend_excluding_spikes,
        test_normalized_index,
        test_seasonality,
        test_compare_languages,
    ]:
        fn()
        print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURES: {FAILURES}")
        sys.exit(1)
    print("ALL TESTS PASSED")
