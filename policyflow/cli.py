"""PolicyFlow CLI — command-line tool for serve, report, classify, export, optimize.

Usage:
    policyflow serve [--host HOST] [--port PORT] [--reload]
    policyflow report [--since 7d] [--by-model] [--by-day]
    policyflow classify "<prompt>"
    policyflow export [--format csv|json] [--since 7d] [--output FILE]
    policyflow optimize [--since 30d] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from datetime import datetime

import pyfiglet
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

console = Console(highlight=False)


def _logo() -> str:
    """Big ASCII logo via pyfiglet."""
    return pyfiglet.figlet_format("PolicyFlow", font="big")


# ══════════════════════════════════════════════════════════════════════
# Chart helpers
# ══════════════════════════════════════════════════════════════════════

def _bar(items: list[tuple[str, float]], max_w: int = 44) -> Text:
    """Colored horizontal bar chart."""
    if not items:
        return Text("(no data)\n", style="dim")
    max_v = max(v for _, v in items) or 1
    out = Text()
    colors = ["green", "cyan", "yellow", "magenta", "blue", "red"]
    for i, (label, val) in enumerate(items):
        w = max(1, int(val / max_v * max_w))
        color = colors[i % len(colors)]
        out.append(f"  {label:<22} ", style="dim")
        out.append("#" * w, style=color)
        out.append(f" {val:,.2f}\n")
    return out


def _spark(values: list[float], width: int = 40) -> str:
    """Sparkline using ASCII-safe characters."""
    if not values:
        return "(no data)"
    lo, hi = min(values), max(values)
    span = hi - lo or 1
    chars = ".,-:=+*#@"
    return "".join(chars[min(len(chars)-1, int((v-lo)/span*(len(chars)-1)))] for v in values[-width:])


# ══════════════════════════════════════════════════════════════════════
# Parse helpers
# ══════════════════════════════════════════════════════════════════════

def parse_duration(s: str) -> int:
    s = s.strip().lower()
    if s.endswith("d"):
        return max(1, int(s[:-1]))
    if s.endswith("m"):
        return max(1, int(s[:-1]) * 30)
    return 30


# ══════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(prog="policyflow", description="PolicyFlow CLI")
    subparsers = parser.add_subparsers(dest="command")

    p = subparsers.add_parser("serve", help="启动 PolicyFlow 服务")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true", default=False)

    p = subparsers.add_parser("report", help="查看成本分析报告")
    p.add_argument("--since", default="30d", help="统计周期 (7d/30d/1m)")
    p.add_argument("--by-model", action="store_true", help="按模型拆分")
    p.add_argument("--by-day", action="store_true", help="按日期拆分")

    p = subparsers.add_parser("classify", help="测试策略路由")
    p.add_argument("prompt", help="要测试的 prompt 文本")

    p = subparsers.add_parser("export", help="导出日志数据")
    p.add_argument("--format", choices=["csv", "json"], default="csv")
    p.add_argument("--since", default="30d")
    p.add_argument("--output", default="-", help="输出文件 (默认 stdout)")

    p = subparsers.add_parser("optimize", help="AI 优化建议")
    p.add_argument("--since", default="30d")
    p.add_argument("--dry-run", action="store_true", default=True,
                   help="仅打印建议，不修改文件 (默认)")

    args = parser.parse_args()
    console.clear()
    if args.command == "serve":
        cmd_serve(args)
    elif args.command == "report":
        cmd_report(args)
    elif args.command == "classify":
        cmd_classify(args)
    elif args.command == "export":
        cmd_export(args)
    elif args.command == "optimize":
        cmd_optimize(args)
    else:
        parser.print_help()
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════
# serve
# ══════════════════════════════════════════════════════════════════════

def cmd_serve(args) -> None:
    import uvicorn
    console.print(_logo(), style="bold cyan")
    console.print("  Poli the Route-Owl   |   v0.5.0   |   策略路由中间件")
    console.print(f"  [cyan]http://{args.host}:{args.port}[/cyan]")
    console.print(f"  [dim]API Docs: http://{args.host}:{args.port}/docs[/dim]")
    console.print()
    uvicorn.run("policyflow.main:app", host=args.host, port=args.port,
                reload=args.reload, log_level="info")


# ══════════════════════════════════════════════════════════════════════
# report — full-screen CLI Dashboard
# ══════════════════════════════════════════════════════════════════════

def cmd_report(args) -> None:
    from . import db
    from .dashboard_tui import run_dashboard

    days = parse_duration(args.since)
    if args.by_day:
        _daily_view(days)
        return

    # Launch full-screen TUI dashboard
    run_dashboard(db, days)


def _daily_view(days: int) -> None:
    from . import db
    daily = db.query_daily_costs(days)
    if not daily:
        console.print("[dim](no data)[/dim]")
        return

    console.print("[bold]每日成本柱状图[/bold]\n")
    all_vals = [d["actual_cost"] for d in daily] + [d["compared_cost"] for d in daily]
    max_v = max(all_vals) if all_vals else 1
    bar_w = 40

    for d in daily:
        day = d["day"][5:]
        aw = max(1, int(d["actual_cost"] / max_v * bar_w))
        cw = max(1, int(d["compared_cost"] / max_v * bar_w))
        console.print(f"  [dim]{day}[/dim]  [green]{'#'*aw}[/green] ${d['actual_cost']:.2f}")
        console.print(f"       [red]{'#'*cw}[/red] ${d['compared_cost']:.2f}\n")

    total_actual = sum(d["actual_cost"] for d in daily)
    total_compared = sum(d["compared_cost"] for d in daily)
    console.print(f"  [bold]合计  实际 ${total_actual:.2f}  |  对比 ${total_compared:.2f}  |  节省 ${total_compared - total_actual:.2f}[/bold]")
    console.print()


# ══════════════════════════════════════════════════════════════════════
# classify
# ══════════════════════════════════════════════════════════════════════

def cmd_classify(args) -> None:
    from .config import Config
    from .router import Router
    from .models import ChatCompletionRequest, Message

    config = Config()

    async def _run():
        router = Router(config)
        await router.initialize()
        try:
            req = ChatCompletionRequest(
                model="gpt-4o",
                messages=[Message(role="user", content=args.prompt)],
            )
            decision = await router.route(req)

            console.print(_logo(), style="bold cyan")
            console.print("  [bold cyan]路由测试[/bold cyan]\n")

            table = Table(box=box.SIMPLE, border_style="dim", show_header=False)
            table.add_column("k", style="cyan", width=12)
            table.add_column("v", style="white")
            table.add_row("匹配策略", decision.policy.name if decision.policy else "none")
            table.add_row("路由方法", decision.method)
            table.add_row("目标模型", decision.target_model)
            table.add_row("相似度", f"{decision.score:.3f}")
            if decision.policy:
                provider = config.get_model_provider(decision.target_model)
                if provider:
                    cfg = config.get_provider_config(provider)
                    table.add_row("供应商", f"{provider} ({cfg['base_url']})")
            console.print(table)
        finally:
            await router.close()

    asyncio.run(_run())


# ══════════════════════════════════════════════════════════════════════
# export
# ══════════════════════════════════════════════════════════════════════

def cmd_export(args) -> None:
    from . import db
    days = parse_duration(args.since)
    rows = db.query_export(days)

    if args.output == "-":
        out = sys.stdout
    else:
        out = open(args.output, "w", encoding="utf-8", newline="")

    try:
        if args.format == "csv" and rows:
            writer = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        elif rows:
            json.dump(rows, out, indent=2, ensure_ascii=False, default=str)
        else:
            console.print("[dim](no data)[/dim]")
    finally:
        if out is not sys.stdout:
            out.close()

    if args.output != "-":
        console.print(f"[green]导出 {len(rows)} 条记录到 {args.output}[/green]")


# ══════════════════════════════════════════════════════════════════════
# optimize
# ══════════════════════════════════════════════════════════════════════

def cmd_optimize(args) -> None:
    from .config import Config
    from .proxy import UpstreamProxy
    from .optimizer import generate_optimizations

    days = parse_duration(args.since)
    config = Config()
    proxy = UpstreamProxy(config)

    async def _run():
        console.print(_logo(), style="bold cyan")
        console.print("  [bold cyan]AI 优化分析[/bold cyan]\n")
        console.print("  [dim]正在分析日志数据...[/dim]\n")

        result = await generate_optimizations(config, proxy, days=days)
        await proxy.close()

        if not result.suggestions:
            console.print("  [dim]没有生成优化建议。[/dim]")
            return

        for i, s in enumerate(result.suggestions, 1):
            risk_style = {"low": "green", "medium": "yellow", "high": "red"}.get(s.risk, "dim")
            body = Text()
            body.append(f"{s.title}\n\n", style="bold white")
            body.append(f"类型: {s.kind}  |  风险: ", style="dim")
            body.append(f"{s.risk}  |  ", style=risk_style)
            body.append(f"预计每月节省: ${s.estimated_savings_monthly:.2f}\n\n", style="dim")
            body.append(f"{s.description}\n\n")
            if s.yaml_snippet:
                body.append(s.yaml_snippet, style="cyan")
                body.append("\n")
            console.print(Panel(body, border_style=risk_style, title=f"建议 {i}"))

        console.print(f"\n  [bold]>> 汇总: 预计每月节省 ${result.total_estimated_savings:.2f}[/bold]")
        console.print(f"  [dim](dry-run 模式，未修改文件)[/dim]\n")

    asyncio.run(_run())
