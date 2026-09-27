"""Command-line interface for the wiki-audience skill.

Subcommands:
  resolve  - find and rank candidate articles for a topic in each language
  analyze  - fetch pageviews, compute trend/reliability metrics, chart, PDF
  series   - dump raw daily series to CSV (for deeper inspection)

Output contract: structured JSON on stdout; progress/diagnostics on stderr.
Exit codes: 0 ok (>=1 language), 2 no language succeeded, 3 API/network
failure, 4 invalid arguments.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from . import fetch as F
from . import metrics as M
from .charts import render_chart
from .http import ApiError, Client, DEFAULT_CACHE_DIR, NotFoundError
from .report import default_assumptions, generate_findings, render_pdf


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def parse_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise ValueError(f"invalid date '{s}' (expected YYYY-MM-DD)")


def parse_langs(s: str) -> list[tuple[str, str]]:
    """'pl,cs' or 'pl.wikipedia,cs' -> [(code, project), ...] in order."""
    out = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        code = part if "." not in part else part.split(".")[0]
        out.append((code, F.project_for_language(part)))
    if not out:
        raise ValueError("--languages must list at least one language code (e.g. pl,cs)")
    return out


def parse_kv_map(s: str | None, flag: str) -> dict:
    """'pl:Title A;cs:Title B' -> {pl: 'Title A', ...} (semicolon-separated
    so titles may contain commas)."""
    out = {}
    if not s:
        return out
    for part in s.split(";"):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"{flag} entries must be 'lang:Title' (got '{part}')")
        code, _, title = part.partition(":")
        code, title = code.strip().lower(), title.strip()
        if not re.fullmatch(r"[a-z]{2,9}", code):
            raise ValueError(f"{flag}: invalid language code '{code}'")
        if not title:
            raise ValueError(f"{flag}: empty title for '{code}'")
        out[code] = title
    return out


def parse_period(args, today: date):
    """Resolve --start/--end/--last into (start, end, warnings)."""
    if args.end:
        end = parse_date(args.end)
    elif args.start:
        end = today
    else:
        end = today
    if args.last:
        if args.start:
            raise ValueError("use either --last or --start, not both")
        start = end - timedelta(days=args.last - 1)
    elif args.start:
        start = parse_date(args.start)
    else:
        start = end - timedelta(days=729)  # default: last 730 days
    warns = []
    if start < F.DATA_START:
        warns.append(
            f"start date clamped from {start} to {F.DATA_START} (Wikipedia pageview "
            "data starts 2015-07-01)"
        )
        start = F.DATA_START
    if (end - start).days < 13:
        raise ValueError("period must cover at least 14 days")
    if end > today:
        end = today
        warns.append(f"end date in the future; set to today ({today})")
    return start, end, warns


def fetch_article(
    req: F.PageviewRequest,
    title: str,
    start: date,
    end: date,
    notes: list,
) -> tuple[str, dict]:
    """Fetch the daily series for `title`; tries canonical-title and
    search-based correction. Returns (canonical_article, series).
    Raises NotFoundError if the article has no data."""
    try:
        return title, F.fetch_pageviews(req, title, start, end)
    except NotFoundError:
        pass
    canon = F.canonical_title(req.client, req.project, title)
    retry = canon["title"] if canon else None
    if canon and canon["disambiguation"]:
        notes.append(
            f"{req.project}: '{retry}' is a disambiguation page (it aggregates unrelated "
            "topics) - rerun with a specific page instead"
        )
    if retry and retry != title:
        notes.append(f"{req.project}: '{title}' had no data; canonical title is '{retry}' - retrying")
    if not retry:
        # title not on this wiki at all: try the closest search hit (typo tolerance),
        # but only accept it if it clearly resembles the requested title
        hits = []
        try:
            hits = F.search_candidates(req.client, req.project, title, limit=3)
        except (ApiError, NotFoundError):
            pass
        for h in hits:
            ratio = difflib.SequenceMatcher(None, title.casefold(),
                                            h["title"].casefold()).ratio()
            if ratio >= 0.7:
                retry = h["title"]
                notes.append(
                    f"{req.project}: '{title}' not found; matched similar page "
                    f"'{retry}' (similarity {ratio:.0%}) - verify relevance"
                )
                break
    if retry:
        if retry != title:
            try:
                return retry, F.fetch_pageviews(req, retry, start, end)
            except NotFoundError:
                pass
        # retry == title: the page exists under this exact title but has no
        # data in the window - refetching the same URL cannot help
    # final: the article has no data; suggest the nearest existing pages
    # (title search first - much more precise than full-text token matching)
    try:
        hits = F.search_candidates(req.client, req.project, title, limit=3, intitle=True)
        if not hits:
            hits = F.search_candidates(req.client, req.project, title, limit=3)
        sugg = [h["title"] for h in hits if h["title"].casefold() != title.casefold()][:3]
        if sugg:
            notes.append(
                f"{req.project}: '{title}' has no pageview data (page may not exist); "
                f"nearest existing pages: {', '.join(sugg)} - rerun with --article using one of them"
            )
        else:
            notes.append(
                f"{req.project}: '{title}' has no pageview data (page may not exist or has no traffic)"
            )
    except (ApiError, NotFoundError):
        notes.append(f"{req.project}: '{title}' has no pageview data")
    raise NotFoundError(f"'{title}' has no pageview data")


def rank_candidates(
    req: F.PageviewRequest,
    hits: list[dict],
    today: date,
    top: int,
) -> list[dict]:
    """Rank search hits by recent (3-month) mean daily views."""
    end = today
    ym = (end.month - 3) % 12 + 1
    yr = end.year - (1 if ym > end.month else 0)
    start = date(yr, ym, 1)
    days = (end - start).days + 1
    out = []
    for h in hits:
        try:
            series = F.fetch_pageviews(
                req, h["title"], start, end, granularity="monthly"
            )
            total = sum(series.values())
        except (ApiError, NotFoundError):
            total = 0
        out.append(
            {
                "title": h["title"],
                "snippet": h["snippet"],
                "source": "search",
                "total_3m": total,
                "mean_daily_3m": round(total / days, 1) if days else 0.0,
            }
        )
    out.sort(key=lambda c: c["mean_daily_3m"], reverse=True)
    return out[:top]


def resolve_article(
    req: F.PageviewRequest,
    code: str,
    topic: str,
    explicit: dict,
    search_overrides: dict,
    wd: dict | None,
    today: date,
    notes: list,
    top_candidates: int = 6,
):
    """Pick the article for one language.

    Priority: explicit --article > Wikidata sitelink (exact cross-lingual page)
    > most-viewed search candidate. Returns (article, resolution, ranked) or
    None if nothing usable was found.
    """
    if code in explicit:
        return explicit[code], "explicit", []
    query = search_overrides.get(code, topic)
    candidates = []
    if wd and code in wd["titles"]:
        t = wd["titles"][code]
        candidates.append({
            "title": t,
            "snippet": f"Wikidata sitelink for {wd['qid']} ('{wd['label']}')",
            "source": "wikidata",
            "total_3m": None,
            "mean_daily_3m": None,
        })
    try:
        hits = F.search_candidates(req.client, req.project, query, limit=top_candidates)
        search_ranked = rank_candidates(req, hits, today, top_candidates)
    except (ApiError, NotFoundError) as e:
        notes.append(f"{req.project}: search failed ({e})")
        search_ranked = []
    # wikidata candidates first; then search candidates (dedupe by title)
    seen = {c["title"] for c in candidates}
    for c in search_ranked:
        if c["title"] not in seen:
            candidates.append(c)
            seen.add(c["title"])
    if not candidates:
        notes.append(f"{req.project}: no candidates found for '{query}'")
        return None
    chosen = candidates[0]
    if chosen["source"] == "wikidata":
        resolution = "wikidata"
        notes.append(f"{code}: resolved via Wikidata {wd['qid']} -> '{chosen['title']}'")
    else:
        resolution = "auto"
        notes.append(
            f"{code}: auto-resolved to '{chosen['title']}' (highest recent views among "
            f"search candidates for '{query}')"
        )
    return chosen["title"], resolution, candidates


@dataclass
class RunCtx:
    """Shared setup, parsed once per subcommand run."""

    client: Client
    today: date
    langs: list[tuple[str, str]]
    warns: list
    outdir: str | None = None
    explicit: dict = field(default_factory=dict)
    search_overrides: dict = field(default_factory=dict)
    wd: dict | None = None


def _get_wd(client: Client, topic: str, codes: list[str], warns: list) -> dict | None:
    try:
        wd = F.wikidata_titles(client, topic, codes)
    except (ApiError, NotFoundError) as e:
        warns.append(f"Wikidata resolution failed ({e}); using search only")
        return None
    return wd


def prepare(args, topic: str) -> RunCtx:
    """Everything the subcommands share: client, langs, overrides, Wikidata."""
    client = Client(cache_dir=args.cache_dir)
    ctx = RunCtx(
        client=client,
        today=today_utc(),
        langs=parse_langs(args.languages),
        warns=[],
        outdir=getattr(args, "out", None),
        explicit=parse_kv_map(getattr(args, "article", None), "--article"),
        search_overrides=parse_kv_map(getattr(args, "search", None), "--search"),
    )
    if topic:
        ctx.wd = _get_wd(client, topic, [c for c, _ in ctx.langs], ctx.warns)
    # explicit titles that are disambiguation pages aggregate unrelated topics:
    # warn loudly instead of silently analyzing a DAB page
    if ctx.explicit:
        by_project: dict[str, list[str]] = {}
        for code, title in ctx.explicit.items():
            by_project.setdefault(F.project_for_language(code), []).append(title)
        for proj, titles in by_project.items():
            try:
                dabs = F.check_disambiguation(client, proj, titles)
            except (ApiError, NotFoundError):
                continue
            for t in sorted(dabs):
                ctx.warns.append(
                    f"{proj}: explicit article '{t}' is a DISAMBIGUATION page - it "
                    "aggregates unrelated topics; rerun with a specific page "
                    "(see `resolve` candidates)"
                )
    return ctx


def cmd_resolve(args) -> int:
    ctx = prepare(args, args.topic)
    result = {"topic": args.topic, "languages": {}}
    if ctx.wd:
        result["wikidata"] = {"qid": ctx.wd["qid"], "label": ctx.wd["label"],
                              "description": ctx.wd["description"]}
    for code, project in ctx.langs:
        query = ctx.search_overrides.get(code, args.topic)
        log(f"[resolve] {code}: resolving on {project}")
        req = F.PageviewRequest(ctx.client, project, args.access, args.agent)
        try:
            res = resolve_article(req, code, args.topic, {}, ctx.search_overrides,
                                  ctx.wd, ctx.today, ctx.warns,
                                  top_candidates=args.candidates)
            result["languages"][code] = {"candidates": res[2] if res else []}
        except ApiError as e:
            log(f"[resolve] {code}: API error: {e}")
            result["languages"][code] = {"candidates": [], "error": str(e)}
            continue
    result["note"] = (
        "candidates are ranked: Wikidata sitelinks first (exact cross-lingual "
        "page), then search hits by recent views. Pick the right titles, then run "
        "analyze with --article \"lang:Title;lang:Title\"."
    )
    if ctx.warns:
        result["warnings"] = ctx.warns
    print(json.dumps(result, ensure_ascii=False, indent=2))
    ok = any(v.get("candidates") for v in result["languages"].values())
    return 0 if ok else 2


def cmd_series(args) -> int:
    ctx = prepare(args, args.topic)
    start, end, period_warns = parse_period(args, ctx.today)
    ctx.warns.extend(period_warns)
    os.makedirs(ctx.outdir, exist_ok=True)
    result = {"period": {"start": str(start), "end": str(end)}, "languages": {},
              "warnings": ctx.warns}
    n_ok = 0
    for code, project in ctx.langs:
        notes = []
        req = F.PageviewRequest(ctx.client, project, args.access, args.agent)
        art = resolve_article(req, code, args.topic, ctx.explicit,
                              ctx.search_overrides, ctx.wd, ctx.today, notes)
        if art is None:
            result["languages"][code] = {"error": "; ".join(notes) or "no article found"}
            continue
        article, resolution, _ = art
        try:
            got, series = fetch_article(req, article, start, end, notes)
        except (ApiError, NotFoundError) as e:
            result["languages"][code] = {"error": str(e) or f"article '{article}' has no data"}
            continue
        csv_path = os.path.join(ctx.outdir, f"series_{code}.csv")
        vals = [int(v) for v in M.series_values(series, start, end)]
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("date,views\n")
            for d, v in zip(M.daterange(start, end), vals):
                f.write(f"{d},{v}\n")
        total = sum(vals)
        n = len(vals)
        peak_i = max(range(n), key=lambda i: vals[i])
        result["languages"][code] = {
            "article": got,
            "resolution": resolution,
            "file": os.path.abspath(csv_path),
            "rows": n,
            "total": total,
            "mean_daily": round(total / n, 1),
            "peak_day": {"date": str(start + timedelta(days=peak_i)),
                         "views": vals[peak_i]},
            "head": [{"date": str(start + timedelta(days=i)), "views": vals[i]}
                     for i in range(min(3, n))],
            "tail": [{"date": str(start + timedelta(days=i)), "views": vals[i]}
                     for i in range(max(0, n - args.tail), n)],
        }
        if notes:
            ctx.warns.extend(notes)
        n_ok += 1
        log(f"[series] {code}: {n} rows -> {csv_path}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if n_ok else 2


def verify_against_monthly(
    client: Client,
    block: dict,
    start: date,
    end: date,
    warns: list,
) -> None:
    """Cross-check: daily total must equal monthly total (self-verification)."""
    if block["points"]["months"] < 12:
        return
    req = F.PageviewRequest(client, block["project"], block["access"], block["agent"])
    try:
        monthly = F.fetch_pageviews(
            req, block["article"], start, end, granularity="monthly",
        )
    except (ApiError, NotFoundError):
        return
    m_total = sum(monthly.values())
    d_total = block["volume"]["total"]
    if m_total > 0:
        div = abs(m_total - d_total) / m_total
        if div > 0.02:
            warns.append(
                f'{block["language"]}: data check failed - daily total {d_total} vs monthly '
                f'total {m_total} (divergence {div:.1%}); treat numbers with caution'
            )
        else:
            log(f'[verify] {block["language"]}: daily vs monthly totals agree '
                f'({div:.2%} divergence)')


def cmd_analyze(args) -> int:
    ctx = prepare(args, args.topic)
    start, end, period_warns = parse_period(args, ctx.today)
    ctx.warns.extend(period_warns)
    os.makedirs(ctx.outdir, exist_ok=True)

    blocks: dict[str, dict] = {}
    months_by_lang: dict[str, list[dict]] = {}
    for code, project in ctx.langs:
        log(f"[analyze] {code}: resolving article on {project}")
        req = F.PageviewRequest(ctx.client, project, args.access, args.agent)
        res = resolve_article(
            req, code, args.topic, ctx.explicit, ctx.search_overrides,
            ctx.wd, ctx.today, ctx.warns,
        )
        if res is None:
            log(f"[analyze] {code}: skipped (see warnings)")
            continue
        article, resolution, _ranked = res
        log(f"[analyze] {code}: fetching daily series for '{article}'")
        try:
            got, series = fetch_article(req, article, start, end, ctx.warns)
        except (ApiError, NotFoundError) as e:
            ctx.warns.append(f"{code}: article '{article}' could not be fetched ({e}); skipped")
            log(f"[analyze] {code}: skipped ({e})")
            continue
        blocks[code], months_by_lang[code] = M.analyze_series(
            series, start, end, got, project, code, resolution,
            args.access, args.agent,
        )

    if not blocks:
        result = {"topic": args.topic, "period": {"start": str(start), "end": str(end)},
                  "languages": {}, "warnings": ctx.warns}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        log("ERROR: no language could be analyzed (see warnings). "
            "Try `resolve` to see candidate articles, or pass --article explicitly.")
        return 2

    for code, block in blocks.items():
        verify_against_monthly(ctx.client, block, start, end, ctx.warns)

    analysis = {
        "topic": args.topic,
        "period": {"start": str(start), "end": str(end),
                   "days": (end - start).days + 1},
        "access": args.access,
        "agent": args.agent,
        "languages": blocks,
        "comparison": M.compare_languages(months_by_lang),
        "assumptions": [],
        "warnings": ctx.warns,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "https://wikimedia.org/api/rest_v1/metrics/pageviews",
        "files": {},
    }

    # dynamic assumptions
    auto = [c for c in blocks if blocks[c]["resolution"] != "explicit"]
    if auto:
        desc = ", ".join(
            f"{c} -> \"{blocks[c]['article']}\" ({blocks[c]['resolution']})"
            for c in auto
        )
        analysis["assumptions"].append(
            f"Articles resolved automatically: {desc}. Wikidata = exact cross-lingual "
            "page for the topic; auto = highest recent views among search candidates. "
            "Verify relevance before investment decisions; re-run with --article to override."
        )
    if (end - start).days < 730:
        analysis["assumptions"].append(
            "Period is shorter than 2 years: some metrics (yoy_12m, seasonality) "
            "require 24 months of data and may be null."
        )
    analysis["assumptions"].extend(default_assumptions(analysis))
    analysis["findings"] = generate_findings(analysis)

    # chart + pdf
    first_lang = next(iter(blocks))
    yms = [m["ym"] for m in months_by_lang[first_lang]]
    series_for_chart = {c.upper(): M.normalized_index(months_by_lang[c]) for c in blocks}
    if not args.no_charts and yms:
        png = os.path.join(ctx.outdir, "chart.png")
        render_chart(
            png, yms, series_for_chart,
            title=f'{args.topic} - normalized monthly views',
            subtitle=f'{analysis["period"]["start"]} to {analysis["period"]["end"]}, '
                     f'{args.access}/{args.agent}',
        )
        analysis["files"]["chart"] = os.path.abspath(png)
    if not args.no_pdf and yms:
        pdf = os.path.join(ctx.outdir, "report.pdf")
        render_pdf(pdf, analysis, analysis["findings"], yms, series_for_chart)
        analysis["files"]["pdf"] = os.path.abspath(pdf)

    json_path = os.path.join(ctx.outdir, "analysis.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)
    analysis["files"]["json"] = os.path.abspath(json_path)

    print(json.dumps(analysis, ensure_ascii=False, indent=2))
    return 0


class _Parser(argparse.ArgumentParser):
    """argparse normally exits 2 on usage errors; we reserve 2 for
    'no language succeeded' and use 4 for all invalid arguments."""

    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(4)


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(
        prog="wiki_audience.py",
        description="Analyze Wikipedia pageviews: trends, reliability, charts, reports.",
    )
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=_Parser)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--out", default="./wa-out", help="output directory (default ./wa-out)")
    common.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR,
                        help="disk cache dir; empty string disables cache")
    common.add_argument("--access", choices=F.ACCESS_VALUES, default="all-access")
    common.add_argument("--agent", choices=F.AGENT_VALUES, default="user",
                        help="user = human traffic only (default)")

    pr = sub.add_parser("resolve", parents=[common],
                        help="find and rank candidate articles per language")
    pr.add_argument("topic")
    pr.add_argument("--languages", required=True, help="comma list, e.g. pl,cs,uk")
    pr.add_argument("--candidates", type=int, default=6, help="candidates per language (default 6)")
    pr.add_argument("--search", default=None,
                    help="per-language search override: 'pl:custom query;cs:other'")
    pr.set_defaults(fn=cmd_resolve)

    pa = sub.add_parser("analyze", parents=[common],
                        help="full analysis: metrics, chart, PDF")
    pa.add_argument("--topic", required=True, help="topic as phrased by the user")
    pa.add_argument("--languages", required=True, help="comma list, e.g. pl,cs")
    pa.add_argument("--article", default=None,
                    help="explicit articles: 'pl:Title A;cs:Title B' (semicolon-separated)")
    pa.add_argument("--search", default=None,
                    help="per-language search override for auto-resolution: 'pl:query;cs:query'")
    pa.add_argument("--start", default=None, help="YYYY-MM-DD (default: 730 days before end)")
    pa.add_argument("--end", default=None, help="YYYY-MM-DD (default: today UTC)")
    pa.add_argument("--last", type=int, default=None,
                    help="period length in days ending today (overrides --start)")
    pa.add_argument("--no-charts", action="store_true")
    pa.add_argument("--no-pdf", action="store_true")
    pa.set_defaults(fn=cmd_analyze)

    ps = sub.add_parser("series", parents=[common],
                        help="dump raw daily series to CSV")
    ps.add_argument("--topic", required=True)
    ps.add_argument("--languages", required=True)
    ps.add_argument("--article", default=None)
    ps.add_argument("--search", default=None)
    ps.add_argument("--start", default=None)
    ps.add_argument("--end", default=None)
    ps.add_argument("--last", type=int, default=None)
    ps.add_argument("--tail", type=int, default=14, help="days to preview in JSON (default 14)")
    ps.set_defaults(fn=cmd_series)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 4
    except ApiError as e:
        print(f"ERROR: network/API failure: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
