"""cc-usage 命令行入口.

用法:
    cc-usage sync [--force]            # 同步 JSONL 数据到 SQLite
    cc-usage today                     # 今日终端报告
    cc-usage week                      # 本周终端报告
    cc-usage month                     # 本月终端报告
    cc-usage report [--range RANGE] [--open]
                                       # 生成 HTML 报告
"""
from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from . import __version__
from .aggregate import (
    resolve_range,
    now_in_sh,
    date_range_span,
)
from .db import connect, sync, get_meta
from .parser import default_projects_dir
from .report_html import render as render_html
from .report_text import render as render_text


def default_db_path() -> Path:
    return Path.home() / ".claude" / "ccusage.db"


def default_output_dir() -> Path:
    return Path.home() / ".claude" / "ccusage-output"


def cmd_sync(args: argparse.Namespace) -> int:
    projects_dir = Path(args.projects_dir).expanduser()
    db_path = Path(args.db).expanduser()
    if not projects_dir.exists():
        print(f"❌ 找不到目录: {projects_dir}", file=sys.stderr)
        return 1
    conn = connect(db_path)
    try:
        sync(conn, projects_dir, force=args.force, verbose=True)
        # 统计
        total = conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
        models = conn.execute("SELECT COUNT(DISTINCT model) FROM usage").fetchone()[0]
        span = date_range_span(conn)
        print(
            f"✅ 数据库: {db_path}\n"
            f"   总记录: {total:,} 条 | 模型: {models} 个\n"
            f"   范围: {span[0]} ~ {span[1]}"
        )
    finally:
        conn.close()
    return 0


def _print_report(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        print("⚠️  数据库不存在, 先运行: cc-usage sync", file=sys.stderr)
        return 1
    conn = connect(db_path)
    try:
        dr = resolve_range(args.range)
        print(render_text(conn, dr))
    finally:
        conn.close()
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        print("⚠️  数据库不存在, 先运行: cc-usage sync", file=sys.stderr)
        return 1
    conn = connect(db_path)
    try:
        dr = resolve_range(args.range)
        out_dir = Path(args.output).expanduser() if args.output else default_output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        # all 范围用实际数据范围命名, 更友好
        if args.range == "all":
            span = date_range_span(conn)
            tag = f"{span[0]}-to-{span[1]}" if span[0] else "all"
        else:
            tag = f"{dr.start}-to-{dr.end}"
        fname = f"usage-{tag}.html"
        out_path = out_dir / fname
        render_html(conn, dr, out_path)
        print(f"✅ 报告已生成:\n   {out_path}")
        if args.open:
            url = out_path.resolve().as_uri()
            print(f"   正在浏览器打开...")
            webbrowser.open(url)
        else:
            print(f"   用 --open 自动打开, 或:\n   open '{out_path}'")
    finally:
        conn.close()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        print("数据库不存在. 运行 `cc-usage sync` 初始化.")
        return 0
    conn = connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
        models = conn.execute("SELECT COUNT(DISTINCT model) FROM usage").fetchone()[0]
        files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        span = date_range_span(conn)
        last = get_meta(conn, "last_sync")
        print(f"cc-usage v{__version__}")
        print(f"数据库:    {db_path}")
        print(f"最后同步:  {last or '(无)'}")
        print(f"数据范围:  {span[0]} ~ {span[1]}" if span[0] else "数据范围:  (空)")
        print(f"文件数:    {files}")
        print(f"记录数:    {total:,}")
        print(f"模型数:    {models}")
        print(f"当前时间:  {now_in_sh().strftime('%Y-%m-%d %H:%M:%S')} (Asia/Shanghai)")
    finally:
        conn.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cc-usage",
        description="🎯 Claude Code 用量统计 — 扫描本地会话, 统计 token 用量.",
    )
    p.add_argument("--version", action="version", version=f"cc-usage {__version__}")
    p.add_argument("--db", default=str(default_db_path()), help="SQLite 路径")
    sub = p.add_subparsers(dest="cmd")

    # sync
    sp = sub.add_parser("sync", help="同步 JSONL 到数据库")
    sp.add_argument("--force", action="store_true", help="强制全量重解析")
    sp.add_argument(
        "--projects-dir",
        default=str(default_projects_dir()),
        help="Claude projects 目录",
    )
    sp.set_defaults(func=cmd_sync)

    # today / week / month 直接走 _print_report
    for rng in ("today", "week", "month"):
        sp = sub.add_parser(rng, help=f"{rng} 终端报告")
        sp.set_defaults(func=_print_report, range=rng)

    # report (html)
    sp = sub.add_parser("report", help="生成 HTML 报告")
    sp.add_argument(
        "--range", "-r",
        choices=["today", "week", "month", "all"],
        default="week",
    )
    sp.add_argument("--output", "-o", help="输出目录")
    sp.add_argument("--open", action="store_true", help="生成后用浏览器打开")
    sp.set_defaults(func=cmd_report)

    # status
    sp = sub.add_parser("status", help="查看数据库状态")
    sp.set_defaults(func=cmd_status)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        # 默认: 如果数据库存在则 today, 否则提示 sync
        if default_db_path().exists():
            args = parser.parse_args(["today"] + (argv or []))
        else:
            parser.print_help()
            print("\n💡 第一次用? 运行: cc-usage sync")
            return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
