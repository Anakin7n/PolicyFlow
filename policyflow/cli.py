"""PolicyFlow CLI — 命令行工具入口。

Commands:
    policyflow serve      启动服务
    policyflow report     成本报告
    policyflow classify   测试路由
    policyflow export     导出日志
    policyflow optimize   AI 优化建议
"""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="policyflow",
        description="PolicyFlow — 策略路由中间件 CLI",
    )
    subparsers = parser.add_subparsers(dest="command")

    # serve
    p_serve = subparsers.add_parser("serve", help="启动 PolicyFlow 服务")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true", default=False)

    # report
    p_report = subparsers.add_parser("report", help="查看成本报告")
    p_report.add_argument("--since", default="30d", help="统计周期，如 7d/30d")
    p_report.add_argument("--by-model", action="store_true")
    p_report.add_argument("--by-day", action="store_true")

    # classify
    p_classify = subparsers.add_parser("classify", help="测试策略路由")
    p_classify.add_argument("prompt", help="要测试的 prompt")

    # export
    p_export = subparsers.add_parser("export", help="导出日志数据")
    p_export.add_argument("--format", choices=["csv", "json"], default="csv")
    p_export.add_argument("--since", default="30d")
    p_export.add_argument("--output", default="-", help="输出文件，默认 stdout")

    # optimize
    p_opt = subparsers.add_parser("optimize", help="AI 优化建议")
    p_opt.add_argument("--since", default="30d")
    p_opt.add_argument("--dry-run", action="store_true", default=True,
                       help="仅打印建议（默认）")

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


# ── Stub implementations (filled in later phases) ────────────────────

def cmd_serve(args) -> None:
    """Start the PolicyFlow FastAPI server."""
    import uvicorn
    uvicorn.run("policyflow.main:app", host=args.host, port=args.port,
                reload=args.reload)


def cmd_report(args) -> None:
    """Print cost report."""
    print("(report 功能将在 Phase 6 实现)")


def cmd_classify(args) -> None:
    """Test policy routing for a prompt."""
    print("(classify 功能将在 Phase 6 实现)")


def cmd_export(args) -> None:
    """Export log data."""
    print("(export 功能将在 Phase 6 实现)")


def cmd_optimize(args) -> None:
    """Generate AI optimization suggestions."""
    print("(optimize 功能将在 Phase 6 实现)")
