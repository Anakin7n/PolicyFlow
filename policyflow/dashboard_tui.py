"""Full-screen CLI dashboard — inspired by NadirClaw's layout design.

Uses rich.layout.Layout + rich.live.Live for a clean split-panel dashboard.
"""

from __future__ import annotations

import sys
from datetime import datetime

import pyfiglet
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

_HEADER = pyfiglet.figlet_format("PolicyFlow", font="big")


def _safe_int(val) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def _build_bar(value: float, max_value: float, width: int = 24) -> str:
    """Build a horizontal bar using unicode block chars."""
    if max_value <= 0:
        return ""
    filled = int(value / max_value * width)
    return "#" * filled + "-" * (width - filled)


def _build_multi_bar(
    segments: list[tuple[str, float, str]],  # (label, value, color)
    max_value: float,
    width: int = 24,
) -> Text:
    """Build a single bar with colored segments for model tiers within a provider."""
    if max_value <= 0:
        return Text("-" * width)
    text = Text()
    for label, val, color in segments:
        w = max(1, int(val / max_value * width))
        text.append("#" * w, style=color)
    remaining = width - sum(max(1, int(v / max_value * width)) for _, v, _ in segments)
    if remaining > 0:
        text.append("-" * remaining, style="dim")
    return text


def run_dashboard(db_module, days: int = 30) -> None:
    """Launch the full-screen dashboard. Press Ctrl+C to exit."""
    console = Console()

    def make_layout() -> Layout:
        # ── Load data ─────────────────────────────────────────
        summary = db_module.query_summary(days)
        policies = db_module.query_policy_breakdown(days)
        cascade = db_module.query_cascade_stats(days)
        daily = db_module.query_daily_costs(days)
        recent = db_module.query_recent_requests(20)

        # ── Collect model stats with tier breakdown ───────────
        conn = db_module.get_db()
        model_rows = conn.execute(
            """SELECT routed_model, COUNT(*) as cnt,
                      COALESCE(SUM(estimated_cost), 0) as cost,
                      COALESCE(SUM(prompt_tokens), 0) as prompt_tok,
                      COALESCE(SUM(completion_tokens), 0) as comp_tok
               FROM requests WHERE timestamp >= date('now', ? || ' days')
               GROUP BY routed_model ORDER BY cost DESC""",
            (f"-{days}",),
        ).fetchall()
        conn.close()

        # Group models by provider (prefix-based)
        providers: dict[str, list[dict]] = {}
        provider_colors = {
            "deepseek": ["green", "bright_green"],
            "claude": ["magenta", "bright_magenta"],
            "qwen": ["cyan", "bright_cyan"],
            "gpt": ["yellow", "bright_yellow"],
            "gemini": ["blue", "bright_blue"],
        }

        for r in model_rows:
            model = r["routed_model"]
            # Determine provider from model name prefix
            p = "other"
            for prefix in ["deepseek", "claude", "qwen", "gpt", "gemini", "glm", "doubao", "kimi", "ernie"]:
                if model.startswith(prefix):
                    p = prefix
                    break
            if p not in providers:
                providers[p] = []
            providers[p].append({"model": model, "cnt": r["cnt"], "cost": r["cost"],
                                 "p_tok": r["prompt_tok"], "c_tok": r["comp_tok"]})

        provider_names = {"deepseek": "DeepSeek", "claude": "Claude", "qwen": "Qwen",
                          "gpt": "GPT/OpenAI", "gemini": "Gemini", "glm": "GLM",
                          "doubao": "Doubao", "kimi": "Kimi", "ernie": "ERNIE", "other": "Other"}

        # ── Layout ────────────────────────────────────────────
        root = Layout()
        root.split_column(
            Layout(Panel(Text(_HEADER, style="bold cyan"), border_style="cyan"), size=9),
            Layout(name="body"),
            Layout(name="footer", size=3),
        )

        # ── Body: stats (left 1/3) + charts (right 2/3) ───────
        body = root["body"]
        body.split_row(
            Layout(name="left", ratio=1),
            Layout(name="right", ratio=2),
        )

        # ── Left: Stats panel ─────────────────────────────────
        stats = Table.grid(padding=(0, 2))
        stats.add_row(Text("Total Requests", style="bold dim"),
                      Text(f"{summary['total_requests']:,}", style="bold white"))
        stats.add_row(Text("Actual Cost", style="bold dim"),
                      Text(f"${summary['total_cost']:,.2f}", style="bold yellow"))
        stats.add_row(Text("Saved", style="bold dim"),
                      Text(f"${summary['saved_amount']:,.2f} ({summary['saved_pct']}%)", style="bold green"))
        stats.add_row(Text("Cascade Rate", style="bold dim"),
                      Text(f"{cascade['cascade_pct']}%", style="bold magenta"))
        stats.add_row(Text("Direct OK", style="bold dim"),
                      Text(f"{cascade['direct_pct']}%", style="dim"))
        stats.add_row("", Text(""))
        stats.add_row(Text("Period", style="bold dim"),
                      Text(f"{days} days", style="dim"))
        stats.add_row(Text("Time", style="bold dim"),
                      Text(datetime.now().strftime("%Y-%m-%d %H:%M"), style="dim"))
        left_col = Layout(Panel(stats, title="Stats", border_style="green"), name="stats")

        # ── Right top: Policy distribution bar chart ──────────
        policy_table = Table(title="Policy Distribution", show_header=True,
                             header_style="bold", box=box.SIMPLE)
        policy_table.add_column("Policy", style="bold", max_width=16)
        policy_table.add_column("Requests", justify="right")
        policy_table.add_column("Bar", min_width=24)
        policy_table.add_column("Cost", justify="right")
        policy_table.add_column("%", justify="right")

        max_pol = max((p["requests"] for p in policies), default=1)
        colors = ["green", "cyan", "yellow", "magenta", "blue", "red"]
        for i, p in enumerate(policies[:8]):
            color = colors[i % len(colors)]
            bar = _build_bar(p["requests"], max_pol)
            policy_table.add_row(
                p["policy"][:16],
                str(p["requests"]),
                Text(bar, style=color),
                f"${p['cost']:.2f}",
                f"{p['pct']:.1f}%",
            )

        # ── Right middle: Model breakdown with tier bars ──────
        model_table = Table(title="Model Usage by Provider", show_header=True,
                            header_style="bold", box=box.SIMPLE)
        model_table.add_column("Provider", style="bold", max_width=10)
        model_table.add_column("Models (colored by tier)", min_width=30)
        model_table.add_column("Requests", justify="right")
        model_table.add_column("Cost", justify="right")
        model_table.add_column("Tokens", justify="right")

        total_requests = max((sum(m["cnt"] for m in models) for models in providers.values()), default=1)
        for p_name in sorted(providers.keys()):
            models = providers[p_name]
            p_colors = provider_colors.get(p_name, ["white", "bright_white"])
            total_p = sum(m["cnt"] for m in models)
            total_p_cost = sum(m["cost"] for m in models)
            total_p_tok = sum(m["p_tok"] + m["c_tok"] for m in models)

            # Build multi-segment bar: each model is a segment
            bar = Text()
            for j, m in enumerate(models):
                c = p_colors[j % len(p_colors)]
                w = max(1, int(m["cnt"] / total_requests * 30))
                bar.append("#" * w, style=c)
            # Label the segments
            label = Text()
            for j, m in enumerate(models):
                c = p_colors[j % len(p_colors)]
                short = m["model"].replace(p_name + "-", "").replace(p_name, "")[:12]
                label.append(f" {short} ", style=c)
            model_table.add_row(
                provider_names.get(p_name, p_name),
                label,
                str(total_p),
                f"${total_p_cost:.2f}",
                f"{total_p_tok:,}",
            )

        right_col = Layout(name="charts")
        right_col.split_column(
            Layout(Panel(policy_table, border_style="blue"), ratio=2),
            Layout(Panel(model_table, border_style="yellow"), ratio=2),
        )

        body["left"].update(left_col)
        body["right"].update(right_col)

        # ── Footer: Recent requests ───────────────────────────
        recent_table = Table(title="Recent Requests", show_header=True,
                             header_style="bold", box=box.SIMPLE)
        recent_table.add_column("Time", style="dim", max_width=10)
        recent_table.add_column("Type", max_width=14)
        recent_table.add_column("Model", max_width=24)
        recent_table.add_column("Tokens", justify="right")
        recent_table.add_column("Cost", justify="right")

        for r in recent[:20]:
            tokens = _safe_int(r.get("prompt_tokens", 0)) + _safe_int(r.get("completion_tokens", 0))
            status_mark = " " if r.get("success", True) else "x"
            recent_table.add_row(
                r.get("timestamp", "")[5:16] if r.get("timestamp") else "?",
                r.get("policy_name") or "-",
                f"{status_mark} {r.get('routed_model', '?')[:22]}",
                f"{tokens:,}" if tokens else "?",
                f"${_safe_int(r.get('estimated_cost', 0)):.4f}" if r.get("estimated_cost") else "?",
            )

        root["footer"].update(
            Panel(recent_table, border_style="dim",
                  subtitle=f"[dim]Q Quit | R Refresh | {len(recent)} shown[/dim]"))

        return root

    # Show full-screen dashboard, wait for Q or Ctrl+C
    layout = make_layout()
    console.clear()
    try:
        with Live(layout, console=console, screen=True, auto_refresh=False):
            try:
                import msvcrt  # Windows
                console.print("\n")
                while True:
                    if msvcrt.kbhit():
                        key = msvcrt.getch().decode("utf-8", errors="ignore").lower()
                        if key in ("q", "\x1b"):
                            break
            except ImportError:
                # Unix: wait for Ctrl+C
                import signal
                signal.pause()
    except (KeyboardInterrupt, Exception):
        pass
    finally:
        console.clear()
        console.print("[dim]Dashboard closed.[/dim]")
