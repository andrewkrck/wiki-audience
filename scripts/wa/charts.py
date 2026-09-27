"""Chart rendering (matplotlib). A single canonical chart type:
the normalized monthly index (first month = 100), one line per language.
Used both for the PNG export and for the chart embedded in the PDF.
"""
from __future__ import annotations

from datetime import date

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402


def ym_to_date(ym: str) -> date:
    return date(int(ym[:4]), int(ym[5:7]), 1)


def _maybe_log(ax: "matplotlib.axes.Axes", values: list) -> None:
    """Log scale only when one language's index is >25x another's minimum
    (and no zeros, which break log axes)."""
    nums = [v for v in values if v is not None]
    if len(nums) < 2 or min(nums) <= 0:
        return
    if max(nums) / min(nums) > 25:
        ax.set_yscale("log")


def plot_index(
    ax,
    yms: list[str],
    series: dict,
    title: str = "",
    subtitle: str | None = None,
) -> None:
    """Draw normalized-index lines on an existing axes.

    series: {label: [value or None per ym]}
    """
    x = [ym_to_date(y) for y in yms]
    all_vals = [v for vals in series.values() for v in vals if v is not None]
    for label, vals in series.items():
        ax.plot(x, vals, label=label, linewidth=1.5, marker="o", markersize=2.2)
    _maybe_log(ax, all_vals)
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonthday=1, interval=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
    ax.grid(alpha=0.3, linewidth=0.5)
    ax.tick_params(labelsize=7.5)
    ax.set_ylabel("index (first month = 100)", fontsize=8)
    if title:
        ax.set_title(title, fontsize=9.5, fontweight="bold", loc="left")
    if subtitle:
        ax.text(
            0.0, 1.02, subtitle, transform=ax.transAxes, fontsize=7.5, color="dimgray"
        )
    if len(series) > 1:
        ax.legend(fontsize=7.5, loc="upper left", framealpha=0.9)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig = ax.get_figure()
    fig.autofmt_xdate(rotation=0)


def render_chart(
    path: str,
    yms: list[str],
    series: dict,
    title: str,
    subtitle: str | None = None,
) -> str:
    fig, ax = plt.subplots(figsize=(7.6, 3.9), dpi=300)
    plot_index(ax, yms, series, title=title, subtitle=subtitle)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path
