"""Textual TUI dashboard — full-page scroll, everything in one view."""

from __future__ import annotations

import math
from datetime import datetime

import pyfiglet
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Static, DataTable
from textual.binding import Binding

from rich.text import Text
from rich.table import Table as RichTable

from . import db as db_module


# ── Palette ───────────────────────────────────────────────────────────

PROVIDER = {
    "deepseek": {"label": "DeepSeek", "main": "#5faf5f", "shades": ["#87d787", "#a0e0a0", "#baeaba", "#d4f4d4"]},
    "claude":   {"label": "Claude",   "main": "#af5faf", "shades": ["#d787d7", "#e0a0e0", "#eabaea", "#f4d4f4"]},
    "qwen":     {"label": "Qwen",     "main": "#5f87af", "shades": ["#87afd7", "#a0c0e0", "#bad1ea", "#d4e2f4"]},
    "gpt":      {"label": "OpenAI",   "main": "#af875f", "shades": ["#d7af87", "#e0c0a0", "#ead1ba", "#f4e2d4"]},
    "openai":   {"label": "OpenAI",   "main": "#af875f", "shades": ["#d7af87", "#e0c0a0", "#ead1ba", "#f4e2d4"]},
    "gemini":   {"label": "Gemini",   "main": "#5fafaf", "shades": ["#87d7d7", "#a0e0e0", "#baeaea", "#d4f4f4"]},
    "glm":      {"label": "GLM",      "main": "#afaf5f", "shades": ["#d7d787", "#e0e0a0", "#eaeaba", "#f4f4d4"]},
    "doubao":   {"label": "Doubao",   "main": "#af5f87", "shades": ["#d787af", "#e0a0c0", "#eabad1", "#f4d4e2"]},
    "kimi":     {"label": "Kimi",     "main": "#875faf", "shades": ["#af87d7", "#c0a0e0", "#d1baea", "#e2d4f4"]},
    "ernie":    {"label": "ERNIE",    "main": "#5f8787", "shades": ["#87afaf", "#a0c0c0", "#bad1d1", "#d4e2e2"]},
    "other":    {"label": "Other",    "main": "#878787", "shades": ["#afafaf", "#c0c0c0", "#d1d1d1", "#e2e2e2"]},
}

POLICY_PALETTE = ["#5faf5f", "#5f87af", "#afaf5f", "#af5faf",
                  "#5fafaf", "#af875f", "#87af5f", "#af5f87"]


# ── Helpers ───────────────────────────────────────────────────────────

def _si(val) -> int:
    try: return int(val)
    except (TypeError, ValueError): return 0

def _sf(val) -> float:
    try: return float(val)
    except (TypeError, ValueError): return 0.0

def _shorten(model: str, n: int) -> str:
    if len(model) <= n: return model
    for pfx in ["deepseek-v4-","deepseek-","claude-","gpt-",
                "gemini-","qwen3.","qwen","glm-","kimi-","doubao-","ernie-"]:
        if model.startswith(pfx):
            s = model[len(pfx):]; return s if len(s) <= n else s[:n]
    return model[:n]


# ── Content builders ──────────────────────────────────────────────────

def _stats_table(summary: dict, cascade: dict) -> RichTable:
    saved = summary["saved_amount"]
    sign, clr = ("+", "green") if saved >= 0 else ("", "red")
    t = RichTable.grid(padding=(0, 1))
    t.add_column(style="dim", max_width=9)
    t.add_column()
    t.add_row("Requests",  f"{summary['total_requests']:,}")
    t.add_row("Cost",      f"¥{summary['total_cost']:,.2f}")
    t.add_row("Saved",     f"[{clr}]{sign}¥{abs(saved):,.2f}[/]")
    t.add_row("",          f"[{clr}]({summary['saved_pct']}%)[/]")
    t.add_row("Cascade",   f"{cascade['cascade_pct']}%")
    t.add_row("Direct",    f"{cascade['direct_pct']}%")
    t.add_row("Failed",    f"{cascade['failed']}")
    t.add_row("",          "")
    t.add_row("[dim]Updated[/]", f"[dim]{datetime.now():%m-%d %H:%M}[/]")
    return t


def _policy_table(policies: list[dict], bar_w: int) -> RichTable:
    t = RichTable.grid(padding=(0, 1))
    t.add_column(max_width=16, overflow="ellipsis")
    t.add_column(justify="right", width=4)
    t.add_column()
    if not policies: return t
    mx = max(p["cost"] for p in policies)
    for i, p in enumerate(policies[:10]):
        style = POLICY_PALETTE[i % len(POLICY_PALETTE)]
        n = math.ceil(p["cost"] / mx * bar_w) if mx else 0
        bar = Text("█" * n + "░" * (bar_w - n)); bar.stylize(style)
        t.add_row(
            p["policy"][:16],
            str(p["requests"]),
            Text.assemble(bar, f"  {p['pct']:.0f}%  ¥{p['cost']:.2f}"),
        )
    return t


def _model_table(model_rows: list, bar_w: int, label_w: int) -> RichTable:
    """Cost-based bars: provider % of global, model % of provider."""
    t = RichTable.grid(padding=(0, 1))
    t.add_column(max_width=label_w + 4, overflow="ellipsis")
    t.add_column(justify="right", width=6)
    t.add_column()

    groups: dict[str, list[dict]] = {}
    all_models: list[dict] = []
    for r in model_rows:
        model = r["routed_model"]; p = "other"
        for key in PROVIDER:
            if key != "other" and model.lower().startswith(key): p = key; break
        m = {"model": model, "cnt": r["cnt"], "cost": r["cost"]}
        groups.setdefault(p, []).append(m)
        all_models.append(m)

    total_cost_all = sum(m["cost"] for m in all_models) or 1
    total_req_all = sum(m["cnt"] for m in all_models)

    sorted_p = sorted(groups.items(), key=lambda kv: sum(m["cost"] for m in kv[1]), reverse=True)

    for p_name, models in sorted_p:
        info = PROVIDER.get(p_name, PROVIDER["other"])
        p_cost = sum(m["cost"] for m in models)
        p_req  = sum(m["cnt"] for m in models)
        p_pct  = p_cost / total_cost_all * 100  # % of global

        n = math.ceil(p_cost / total_cost_all * bar_w)
        bar = Text("█" * n + "░" * (bar_w - n)); bar.stylize(info["main"])
        t.add_row(
            f"[{info['main']}]{info['label']}[/]",
            str(p_req),
            Text.assemble(bar, f"  {p_pct:.0f}%  ¥{p_cost:.2f}"),
        )
        for j, m in enumerate(sorted(models, key=lambda x: x["cost"], reverse=True)):
            si = min(j, len(info["shades"]) - 1)
            m_pct_of_provider = m["cost"] / max(p_cost, 0.001) * 100
            n2 = math.ceil(m["cost"] / total_cost_all * bar_w)
            bar2 = Text("█" * n2 + "░" * (bar_w - n2)); bar2.stylize(info["shades"][si])
            t.add_row(
                f"  {_shorten(m['model'], label_w)}",
                str(m["cnt"]),
                Text.assemble(bar2, f"  {m_pct_of_provider:.0f}%  ¥{m['cost']:.2f}"),
            )
    return t


def _daily_table(daily: list[dict], bar_w: int) -> tuple[Text, RichTable | None]:
    """Returns (legend_header, body_table). Legend stays fixed, body scrolls."""
    if not daily or len(daily) < 2:
        return Text("[dim](need 2+ days of data)[/]"), None

    days = [d["day"][5:] for d in daily]
    actuals = [d["actual_cost"] for d in daily]
    baselines = [d["compared_cost"] for d in daily]
    max_all = max(max(actuals), max(baselines)) or 1

    # ── Fixed legend header ───────────────────────────
    total_ac = sum(actuals)
    total_cc = sum(baselines)
    saved = total_cc - total_ac
    hdr = RichTable.grid(padding=(0, 1))
    hdr.add_column(width=6, style="dim")
    hdr.add_column(width=bar_w)
    hdr.add_column()
    hdr.add_column()

    legend = Text()
    legend.append("█", style="#7fc77f"); legend.append(" Actual  ", style="dim")
    legend.append("█", style="#aaaaaa"); legend.append(" Baseline  ", style="dim")
    legend.append(f"Total ¥{total_ac:.2f} vs ¥{total_cc:.2f}  ", style="dim")
    legend.append(f"Saved ¥{saved:+.2f}", style="green" if saved > 0 else "red")
    hdr.add_row("", legend, Text(""), Text(""))

    col_hdrs = Text("Date", style="dim")
    hdr.add_row(col_hdrs, Text(""), Text("Saved", style="dim"), Text("Reqs", style="dim"))

    # ── Scrollable body ───────────────────────────────
    t = RichTable.grid(padding=(0, 1))
    t.add_column(width=6, style="dim")
    t.add_column(width=bar_w)
    t.add_column()
    t.add_column()

    for i in range(len(daily) - 1, -1, -1):  # newest first
        if i < len(daily) - 1:
            t.add_row("", Text(""), Text(""), Text(""))
        ac, cc = actuals[i], baselines[i]
        diff = cc - ac
        na = math.ceil(ac / max_all * bar_w)
        nc = math.ceil(cc / max_all * bar_w)
        shared = min(na, nc)

        if na >= nc:
            bar = Text()
            bar.append("█" * shared, style="#aaaaaa")
            bar.append("█" * (na - shared), style="#7fc77f")
            bar.append("░" * (bar_w - na))
        else:
            bar = Text()
            bar.append("█" * shared, style="#7fc77f")
            bar.append("█" * (nc - shared), style="#aaaaaa")
            bar.append("░" * (bar_w - nc))

        if diff > 0:
            label = Text(f"+¥{diff:.2f}", style="green")
        elif diff < 0:
            label = Text(f"-¥{abs(diff):.2f}", style="red")
        else:
            label = Text("¥0.00", style="dim")

        t.add_row(days[i], bar, label, str(daily[i]["requests"]))

    return hdr, t


def _format_optimizer_result(result) -> str:
    """Format optimization result into display text."""
    if not result or not result.suggestions:
        return "  (no suggestions — try with more data)"
    lines = []
    for s in result.suggestions:
        saving = f"+¥{s.estimated_savings_monthly:.2f}/mo" if s.estimated_savings_monthly > 0 else ""
        lines.append(f"  [{s.risk}] {s.title}  {saving}")
        lines.append(f"       {s.description[:120]}")
        if s.yaml_snippet:
            for yl in s.yaml_snippet.strip().split("\n")[:4]:
                lines.append(f"       [dim]{yl}[/]")
        lines.append("")
    if result.total_estimated_savings > 0:
        lines.append(f"  [green]Total: +¥{result.total_estimated_savings:.2f}/mo[/]")
    return "\n".join(lines)


# ── Textual App ───────────────────────────────────────────────────────

class PolicyFlowDashboard(App):
    """Full-page scroll dashboard — all content in one VerticalScroll."""

    CSS = """
    Screen { background: #000000; }

    #header {
        dock: top;
        height: auto;
        padding: 1 2;
        border: solid #3a4a5a;
        background: #0a0a14;
    }
    #header Static {
        color: #aaccee;
    }

    #page {
        height: 1fr;
    }

    /* ── Top row: Stats | Policy ──────────────────────── */
    #top-row {
        height: auto;
    }
    #stats-card {
        width: 1fr; height: auto;
        border: solid #3a4a3a;
    }
    #policy-card {
        width: 3fr; height: auto; margin-left: 1;
        border: solid #3a3a5a;
    }

    /* ── Full-width sections ─────────────────────────── */
    .section {
        height: auto; margin-top: 1;
    }
    #model-card    { border: solid #4a3a4a; }
    #daily-card    { border: solid #4a4a3a; }
    #optimize-card { border: solid #3a4a4a; }
    #recent-card   { border: solid #3a3a3a; }

    /* ── Daily card: fixed header, scrollable body ─ */
    #daily-card {
        height: auto;
    }
    #daily-scroll {
        height: 15;
    }
    #daily-header {
        height: auto;
    }

    /* ── Card internals ─────────────────────────────── */
    .card-title {
        height: 1;
        padding: 0 2;
        color: #888888;
        text-style: bold;
    }
    .card-body {
        height: auto;
        padding: 0 1;
    }

    /* ── DataTable ──────────────────────────────────── */
    #recent-table { height: 12; }
    DataTable > .datatable--header {
        background: #1a1a1a;
        color: #666666;
        text-style: bold;
    }
    DataTable > .datatable--cursor { background: #2a2a44; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, days: int = 30, by_day: bool = False, config_path: str = "policyflow.yaml"):
        super().__init__()
        self.days = days
        self.by_day = by_day
        self.config_path = config_path

    def compose(self) -> ComposeResult:
        from rich.text import Text as Rt
        banner = Rt(pyfiglet.figlet_format("PolicyFlow", font="standard"), style="#aaccee")
        yield Static(banner, id="header")

        with VerticalScroll(id="page"):
            # ── Row 1: Stats + Policy ────────────────────
            with Horizontal(id="top-row"):
                with VerticalScroll(id="stats-card"):
                    yield Static("STATS", classes="card-title")
                    yield Static("", id="stats-body", classes="card-body")

                with VerticalScroll(id="policy-card"):
                    yield Static("POLICY DISTRIBUTION", classes="card-title")
                    yield Static("", id="policy-body", classes="card-body")

            # ── Model Usage ──────────────────────────────
            with VerticalScroll(id="model-card", classes="section"):
                yield Static("MODEL USAGE BY PROVIDER", classes="card-title")
                yield Static("", id="model-body", classes="card-body")

            # ── Daily Cost Comparison ────────────────────
            with Vertical(id="daily-card", classes="section"):
                yield Static("DAILY COST COMPARISON", classes="card-title")
                yield Static("", id="daily-header", classes="card-body")  # legend fixed
                with VerticalScroll(id="daily-scroll"):
                    yield Static("", id="daily-body", classes="card-body")  # rows scroll

            # ── AI Optimization Suggestions ──────────────
            with Vertical(id="optimize-card", classes="section"):
                yield Static("AI OPTIMIZATION SUGGESTIONS", classes="card-title")
                yield Static("(loading...)", id="optimize-body", classes="card-body")

            # ── Recent Requests ──────────────────────────
            with Vertical(id="recent-card", classes="section"):
                yield Static("RECENT REQUESTS", classes="card-title")
                yield DataTable(id="recent-table")

            # spacer
            yield Static("")

    def on_mount(self) -> None:
        db_module.init_db()
        self._refresh()

    def action_refresh(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        days = self.days
        summary  = db_module.query_summary(days)
        policies = db_module.query_policy_breakdown(days)
        cascade  = db_module.query_cascade_stats(days)
        daily    = db_module.query_daily_costs(days)
        recent   = db_module.query_recent_requests(50)

        conn = db_module.get_db()
        model_rows = conn.execute(
            """SELECT routed_model, COUNT(*) cnt,
                      COALESCE(SUM(estimated_cost), 0) cost,
                      COALESCE(SUM(prompt_tokens), 0) prompt_tok,
                      COALESCE(SUM(completion_tokens), 0) comp_tok
               FROM requests WHERE timestamp >= date('now', ? || ' days')
               GROUP BY routed_model ORDER BY cost DESC""",
            (f"-{days}",),
        ).fetchall()
        conn.close()

        w = self.size.width
        right_w = max(40, w * 3 // 4)
        bar_w   = max(6, right_w - 24)
        label_w = max(8, min(18, w // 6))

        self.query_one("#stats-body",  Static).update(_stats_table(summary, cascade))
        self.query_one("#policy-body", Static).update(_policy_table(policies, bar_w))
        self.query_one("#model-body",  Static).update(_model_table(model_rows, bar_w, label_w))

        # Daily chart — legend fixed + rows scrollable
        daily_hdr, daily_body = _daily_table(daily, bar_w + 20)
        if daily_body:
            self.query_one("#daily-header", Static).update(daily_hdr)
            self.query_one("#daily-body",   Static).update(daily_body)
        else:
            self.query_one("#daily-header", Static).update("[dim](need 2+ days of data)[/]")
            self.query_one("#daily-body",   Static).update("")

        # Recent
        dt: DataTable = self.query_one("#recent-table", DataTable)
        dt.clear(columns=True)
        dt.add_columns("Time", "Policy", "Model", "Tok", "Cost")
        for r in recent[:50]:
            tok = _si(r.get("prompt_tokens", 0)) + _si(r.get("completion_tokens", 0))
            ts = r.get("timestamp", "")
            dt.add_row(
                ts[5:16] if ts else "",  # MM-DD HH:MM
                (r.get("policy_name") or "-")[:16],
                r.get("routed_model", "?")[:18],
                f"{tok // 1000}k" if tok else "-",
                f"¥{_sf(r.get('estimated_cost', 0)):.3f}",
            )

        # Optimization (async, deferred)
        self._load_optimizer()

    def _load_optimizer(self) -> None:
        """Run AI optimizer as async task, update widget when done."""
        import asyncio
        from .config import Config
        from .proxy import UpstreamProxy

        async def _run():
            try:
                from .optimizer import generate_optimizations
                config = Config(self.config_path)
                proxy = UpstreamProxy(config)
                try:
                    result = await generate_optimizations(config, proxy, days=self.days, max_suggestions=4)
                    text = _format_optimizer_result(result)
                    self.query_one("#optimize-body", Static).update(text)
                finally:
                    await proxy.close()
            except Exception as e:
                self.query_one("#optimize-body", Static).update(f"[dim](optimizer error: {e})[/]")

        asyncio.get_event_loop().create_task(_run())


def run_dashboard(db_mod, days: int = 30) -> None:
    PolicyFlowDashboard(days=days).run()
