"""Deterministic findings + one-page PDF report.

Everything here is template-driven from the analysis dict, so the same data
always yields the same report. The agent's job is to *present* these results
(and add its own framing in chat), not to re-derive numbers.
"""
from __future__ import annotations

import textwrap

A4_W, A4_H = 8.27, 11.69  # inches
MARGIN = 0.55
USABLE_W = A4_W - 2 * MARGIN


def fmt_int(n: float) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 10_000:
        return f"{n / 1_000:.1f}K"
    if n >= 1_000:
        return f"{n / 1_000:.2f}K"
    return f"{int(round(n))}"


def fmt_pct(x: float | None, digits: int = 0) -> str:
    if x is None:
        return "n/a"
    return f"{x * 100:+.{digits}f}%"


def generate_findings(analysis: dict) -> list[str]:
    """Data-backed statements, deterministic order and wording."""
    out: list[str] = []
    langs = analysis["languages"]
    period = f'{analysis["period"]["start"]} to {analysis["period"]["end"]}'

    for lang, b in langs.items():
        g = b["growth"]
        if g["cagr"] is not None:
            growth = f"grew at {fmt_pct(g['cagr'])} per year (CAGR {g['cagr'] * 100:+.0f}%/yr)"
        elif g["yoy_12m"] is not None:
            growth = f"changed {fmt_pct(g['yoy_12m'])} in the last 12 months vs the previous 12"
        elif g["momentum_3m"] is not None:
            growth = f"changed {fmt_pct(g['momentum_3m'])} in the last 3 months vs the previous 3"
        else:
            reason = g.get("cagr_null_reason") or g.get("yoy_null_reason") or "insufficient history"
            growth = f"no reliable growth rate ({reason})"
        tt = b["trend_test"]
        sig = "statistically significant" if tt["significant"] else "not statistically significant"
        out.append(
            f'{lang.upper()}: "{b["article"]}" — {fmt_int(b["volume"]["total"])} views in {period} '
            f'(avg {fmt_int(b["volume"]["mean_daily"])}/day); trend {sig} (t={tt["t"]}).'
        )
        out.append(
            f'{lang.upper()}: {growth}; '
            f'reliability {b["reliability"]["level"].upper()} ({b["reliability"]["score"]}/100).'
        )
        an = b.get("anomalies") or {}
        if an.get("event_driven"):
            out.append(
                f'{lang.upper()}: EVENT-DRIVEN TRAFFIC - {an["spike_count"]} spike day(s), '
                f'up to {an["max_ratio"]}x baseline; growth may be news/viral hype rather '
                'than durable demand.'
            )
        wr = b.get("weekend_weekday_ratio")
        if wr is not None and (wr >= 1.5 or wr <= 0.67):
            if wr >= 1.5:
                out.append(f'{lang.upper()}: weekend traffic is {wr:.1f}x weekday - part of the '
                           "pattern is weekly seasonality, not growth.")
            else:
                out.append(f'{lang.upper()}: weekday traffic is {1 / wr:.1f}x weekend - part of '
                           "the pattern is weekly seasonality, not growth.")

    comp = analysis.get("comparison") or {}
    if comp.get("latest_index") and len(comp["latest_index"]) >= 2:
        out.append(comp["note"] or "")
    # seasonality concentration (only when 24+ months of data exist)
    for lang, b in langs.items():
        s = b.get("seasonality")
        if s and s["flagged_months"]:
            m = ", ".join(s["flagged_months"][:4])
            out.append(
                f'{lang.upper()}: strong YoY jumps (+30% or more) only in {m} — '
                "part of the growth may be seasonal rather than a durable trend."
            )
    out = [s for s in out if s]
    return out[:8]


def _wrap_lines(text: str, width: int) -> list[str]:
    return textwrap.wrap(text, width=width) or [""]


def render_pdf(
    path: str,
    analysis: dict,
    findings: list[str],
    chart_months: list[str],
    chart_series: dict,
) -> str:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from .charts import plot_index

    fig = plt.figure(figsize=(A4_W, A4_H), dpi=150)
    fig.patch.set_facecolor("white")

    def text(x, y, s, size=8.5, weight="normal", color="black", va="top"):
        fig.text(x, y, s, fontsize=size, fontweight=weight, color=color, va=va)

    # ---- header
    text(MARGIN / A4_W, (A4_H - 0.35) / A4_H,
         f"Wikipedia audience report: {analysis['topic']}", size=14, weight="bold")
    period = analysis["period"]
    text(MARGIN / A4_W, (A4_H - 0.62) / A4_H,
         f'Period {period["start"]} to {period["end"]}  ·  access: {analysis["access"]}  ·  '
         f'agents: {analysis["agent"]}  ·  generated {analysis["generated_at"][:10]}',
         size=8, color="dimgray")
    arts = "   \u00b7   ".join(
        f'{l.upper()}: "{b["article"]}" ({b["resolution"]})' for l, b in analysis["languages"].items()
    )
    for i, line in enumerate(_wrap_lines(arts, 112)[:2]):
        text(MARGIN / A4_W, (A4_H - 0.88 - 0.17 * i) / A4_H, line, size=7.5, color="dimgray")

    # ---- table
    col_x = [0.55, 1.18, 3.55, 4.42, 5.17, 5.92, 6.67]
    headers = ["Language", "Article", "Total views", "Avg/day", "CAGR/yr", "YoY 12m", "Reliability"]
    ty = A4_H - 1.30
    for h, x in zip(headers, col_x):
        text(x, ty, h, size=7.5, weight="bold")
    fig.add_artist(plt.Line2D([MARGIN / A4_W, 1 - MARGIN / A4_W], [ty / A4_H - 0.008] * 2,
                              transform=fig.transFigure, color="black", linewidth=0.8))
    y = ty - 0.26
    for lang, b in analysis["languages"].items():
        g = b["growth"]
        vals = [
            lang.upper(),
            (b["article"][:30] + "…") if len(b["article"]) > 31 else b["article"],
            fmt_int(b["volume"]["total"]),
            fmt_int(b["volume"]["mean_daily"]),
            fmt_pct(g["cagr"]),
            fmt_pct(g["yoy_12m"]),
            f'{b["reliability"]["level"]} ({b["reliability"]["score"]:.0f})',
        ]
        for v, x in zip(vals, col_x):
            text(x, y, v, size=7.5)
        y -= 0.24
    y -= 0.12

    # ---- chart
    chart_top = y - 0.12
    chart_h = 3.1
    ax = fig.add_axes([(MARGIN - 0.1) / A4_W, (chart_top - chart_h) / A4_H,
                       (USABLE_W + 0.2) / A4_W, chart_h / A4_H])
    plot_index(ax, chart_months, chart_series,
               title="Normalized monthly views (first month = 100)")
    y = chart_top - chart_h - 0.38

    # ---- findings
    text(MARGIN, y, "Key findings", size=9.5, weight="bold")
    y -= 0.24
    for f in findings[:6]:
        for line in _wrap_lines("• " + f, 108):
            if y < 1.9:
                break
            text(MARGIN, y, line, size=8)
            y -= 0.185
        y -= 0.05
        if y < 1.9:
            break
    y -= 0.12

    # ---- assumptions
    text(MARGIN, y, "Assumptions & limitations", size=9.5, weight="bold")
    y -= 0.24
    for a in analysis["assumptions"]:
        for line in _wrap_lines("– " + a, 112):
            if y < 0.62:
                text(MARGIN, y, "– … (see JSON output for the full list)", size=7)
                y -= 0.15
                break
            text(MARGIN, y, line, size=7)
            y -= 0.155
        y -= 0.04
        if y < 0.62:
            break

    text(MARGIN / A4_W, 0.18 / A4_H,
         "Data: Wikimedia Pageviews API (wikimedia.org/api/rest_v1/metrics/pageviews), CC0 1.0. "
         "Pageviews measure reader attention only — not purchase intent.",
         size=6.5, color="dimgray")

    fig.savefig(path, facecolor="white")
    plt.close(fig)
    return path


def default_assumptions(analysis: dict) -> list[str]:
    """Fixed, always-true limitations for this data source."""
    a = analysis
    return [
        "Pageviews measure reader attention only; they are not purchase intent or willingness to pay.",
        f"Traffic filter: {a['agent']} agents, {a['access']} access (all devices unless overridden). "
        "Bot traffic is excluded by default.",
        "The topic is approximated by the single article resolved per language; related pages and "
        "subtopics are NOT aggregated.",
        "Wikipedia pageview data is available from 2015-07-01; earlier periods are unavailable.",
        "Small language editions have sparse traffic; rates computed from few views are noisy "
        "(see reliability scores).",
    ]
