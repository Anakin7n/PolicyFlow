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
import os
import sys
from datetime import datetime, timedelta


def parse_duration(s: str) -> int:
    """Convert '7d' / '30d' / '1m' to number of days."""
    s = s.strip().lower()
    if s.endswith("d"):
        return max(1, int(s[:-1]))
    if s.endswith("m"):
        return max(1, int(s[:-1]) * 30)
    return 30


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="policyflow",
        description="PolicyFlow — 策略路由中间件 CLI",
    )
    subparsers = parser.add_subparsers(dest="command")

    # ── serve ────────────────────────────────────────────────
    p = subparsers.add_parser("serve", help="启动 PolicyFlow 服务")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true", default=False)

    # ── report ───────────────────────────────────────────────
    p = subparsers.add_parser("report", help="查看成本分析报告")
    p.add_argument("--since", default="30d", help="统计周期 (7d/30d/1m)")
    p.add_argument("--by-model", action="store_true", help="按模型拆分")
    p.add_argument("--by-day", action="store_true", help="按日期拆分")

    # ── classify ─────────────────────────────────────────────
    p = subparsers.add_parser("classify", help="测试策略路由")
    p.add_argument("prompt", help="要测试的 prompt 文本")

    # ── export ───────────────────────────────────────────────
    p = subparsers.add_parser("export", help="导出日志数据")
    p.add_argument("--format", choices=["csv", "json"], default="csv")
    p.add_argument("--since", default="30d")
    p.add_argument("--output", default="-", help="输出文件 (默认 stdout)")

    # ── optimize ─────────────────────────────────────────────
    p = subparsers.add_parser("optimize", help="AI 优化建议")
    p.add_argument("--since", default="30d")
    p.add_argument("--dry-run", action="store_true", default=True,
                   help="仅打印建议，不修改文件 (默认)")

    args = parser.parse_args()

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
# Command implementations
# ══════════════════════════════════════════════════════════════════════

def cmd_serve(args) -> None:
    """Start the PolicyFlow FastAPI server."""
    import uvicorn
    print(f"PolicyFlow v0.5.0 starting at http://{args.host}:{args.port}")
    uvicorn.run("policyflow.main:app", host=args.host, port=args.port,
                reload=args.reload)


def cmd_report(args) -> None:
    """Print cost analysis report."""
    from . import db
    days = parse_duration(args.since)

    if args.by_day:
        data = db.query_daily_costs(days)
        if not data:
            print("(no data)")
            return
        print(f"\n  每日成本 (最近 {days} 天)")
        print(f"  {'日期':<12} {'请求数':>8} {'实际花费':>10} {'对比花费':>10} {'节省':>10}")
        print(f"  {'-'*50}")
        total_actual = 0
        total_compared = 0
        for r in data:
            saved = r["compared_cost"] - r["actual_cost"]
            print(f"  {r['day']:<12} {r['requests']:>8} ${r['actual_cost']:>9.2f} ${r['compared_cost']:>9.2f} ${saved:>9.2f}")
            total_actual += r["actual_cost"]
            total_compared += r["compared_cost"]
        print(f"  {'─'*50}")
        print(f"  {'合计':<12} {'':>8} ${total_actual:>9.2f} ${total_compared:>9.2f} ${total_compared - total_actual:>9.2f}")
        return

    summary = db.query_summary(days)
    policies = db.query_policy_breakdown(days)
    cascade = db.query_cascade_stats(days)

    print(f"\n  PolicyFlow 成本报告")
    print(f"  {'─'*50}")
    print(f"  周期:      最近 {days} 天")
    print(f"  总请求:    {summary['total_requests']:,}")
    print(f"  总花费:    ${summary['total_cost']:,.2f}")
    s = summary["saved_amount"]
    pct = summary["saved_pct"]
    print(f"  如果全用 Pro: ${summary['compared_cost']:,.2f}")
    print(f"  节省:      ${s:,.2f} ({pct}%)")

    if args.by_model:
        _report_by_model(days)
    else:
        print(f"\n  按策略拆分")
        print(f"  {'策略':<25} {'请求数':>6} {'花费':>8} {'节省':>8} {'占比':>6}")
        print(f"  {'-'*55}")
        for p in policies:
            saved = p["saved"]
            print(f"  {p['policy']:<25} {p['requests']:>6} ${p['cost']:>7.2f} ${saved:>7.2f} {p['pct']:>5.1f}%")

        print(f"\n  级联统计")
        print(f"  便宜模型尝试: {cascade['total_requests']}")
        print(f"  验证通过:     {cascade['direct_success']} ({cascade['direct_pct']}%)")
        print(f"  升级到更强:   {cascade['cascade_attempts']} ({cascade['cascade_pct']}%)")
        if cascade["failed"]:
            print(f"  失败:         {cascade['failed']}")

    # Optimization tips (heuristic, for quick feedback)
    tips = []
    if pct < 10:
        tips.append("节省比例偏低 (<10%)，建议检查策略是否过于保守")
    cpct = cascade["cascade_pct"]
    if cpct > 15:
        tips.append(f"级联升级率偏高 ({cpct}%)，部分策略的便宜模型可能不够胜任")
    high = next((p for p in policies if p["pct"] > 60), None)
    if high:
        tips.append(f"策略「{high['policy']}」占了 {high['pct']}% 成本，建议检查优化空间")
    if tips:
        print(f"\n  >> 优化提示")
        for t in tips:
            print(f"  - {t}")
    print()


def _report_by_model(days: int) -> None:
    """Print report grouped by routed model."""
    from . import db
    conn = db.get_db()
    rows = conn.execute(
        """SELECT routed_model,
                  COUNT(*) as requests,
                  COALESCE(SUM(estimated_cost), 0) as cost
           FROM requests
           WHERE timestamp >= date('now', ? || ' days')
           GROUP BY routed_model ORDER BY cost DESC""",
        (f"-{days}",),
    ).fetchall()
    conn.close()
    if not rows:
        print("(no data)")
        return
    print(f"\n  按模型拆分")
    print(f"  {'模型':<30} {'请求数':>6} {'花费':>8}")
    print(f"  {'-'*46}")
    for r in rows:
        print(f"  {r['routed_model']:<30} {r['requests']:>6} ${r['cost']:>7.2f}")


def cmd_classify(args) -> None:
    """Test policy routing for a prompt."""
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
            policy_name = decision.policy.name if decision.policy else "none"
            print(f"  匹配策略: {policy_name}")
            print(f"  路由方法: {decision.method}")
            print(f"  目标模型: {decision.target_model}")
            print(f"  相似度:   {decision.score:.3f}")
            if decision.policy:
                provider = config.get_model_provider(decision.target_model)
                if provider:
                    cfg = config.get_provider_config(provider)
                    print(f"  供应商:   {provider} ({cfg['base_url']})")
        finally:
            await router.close()

    asyncio.run(_run())


def cmd_export(args) -> None:
    """Export log data to CSV or JSON."""
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
            print("(no data)", file=out)
    finally:
        if out is not sys.stdout:
            out.close()

    if args.output != "-":
        print(f"导出 {len(rows)} 条记录到 {args.output}")


def cmd_optimize(args) -> None:
    """Generate AI optimization suggestions."""
    from .config import Config
    from .proxy import UpstreamProxy
    from .optimizer import generate_optimizations

    days = parse_duration(args.since)
    config = Config()
    proxy = UpstreamProxy(config)

    async def _run():
        result = await generate_optimizations(config, proxy, days=days)
        await proxy.close()

        if not result.suggestions:
            print("\n  没有生成优化建议。")
            if result.raw_response:
                print(f"  原始响应: {result.raw_response[:500]}")
            return

        print(f"\n  AI 优化建议 (分析最近 {result.period})")
        print(f"  {'='*60}")
        for i, s in enumerate(result.suggestions, 1):
            print(f"\n  ┌─ 建议 {i}: {s.title} ({s.risk} risk)")
            print(f"  ├─ 类型: {s.kind}")
            print(f"  ├─ 说明: {s.description}")
            if s.yaml_snippet:
                print(f"  ├─ YAML 片段:")
                for line in s.yaml_snippet.strip().split("\n"):
                    print(f"  │  {line}")
            print(f"  └─ 预计每月节省: ${s.estimated_savings_monthly:.2f}")

        print(f"\n  {'='*60}")
        print(f"  >> 汇总: 执行以上建议，预计每月节省 ${result.total_estimated_savings:.2f}")
        if args.dry_run:
            print(f"  (dry-run 模式，未修改文件)")
        print()

    asyncio.run(_run())
