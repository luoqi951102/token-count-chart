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
from .db import connect, sync, sync_zcode, get_meta, backfill_provider, reconcile_providers, dedupe_claude_rows
from .parser import default_projects_dir, default_zcode_db
from .report_html import render as render_html
from .report_text import render as render_text


def default_db_path() -> Path:
    return Path.home() / ".claude" / "ccusage.db"


def default_output_dir() -> Path:
    return Path.home() / ".claude" / "ccusage-output"


def cmd_sync(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser()
    only = getattr(args, "only", None) or "all"
    conn = connect(db_path)
    try:
        if only in ("all", "claude"):
            projects_dir = Path(args.projects_dir).expanduser()
            if not projects_dir.exists():
                print(f"❌ 找不到 Claude projects 目录: {projects_dir}", file=sys.stderr)
                if only == "claude":
                    return 1
            else:
                sync(conn, projects_dir, force=args.force, verbose=True)
        if only in ("all", "zcode"):
            zcode_db = Path(getattr(args, "zcode_db", None) or default_zcode_db()).expanduser()
            sync_zcode(conn, zcode_db, verbose=True)
        # 统计
        total = conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
        models = conn.execute("SELECT COUNT(DISTINCT model) FROM usage").fetchone()[0]
        by_source = dict(
            conn.execute(
                "SELECT source, COUNT(*) FROM usage GROUP BY source"
            ).fetchall()
        )
        span = date_range_span(conn)
        src_str = " · ".join(f"{s}: {c:,}" for s, c in sorted(by_source.items()))
        print(
            f"✅ 数据库: {db_path}\n"
            f"   总记录: {total:,} 条 | 模型: {models} 个\n"
            f"   来源: {src_str or '(空)'}\n"
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
        source = getattr(args, "source", "all")
        print(render_text(conn, dr, source))
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
        source = getattr(args, "source", "all")
        out_dir = Path(args.output).expanduser() if args.output else default_output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        # all 范围用实际数据范围命名, 更友好
        if args.range == "all":
            span = date_range_span(conn)
            tag = f"{span[0]}-to-{span[1]}" if span[0] else "all"
        else:
            tag = f"{dr.start}-to-{dr.end}"
        if source != "all":
            tag += f"-{source}"
        fname = f"usage-{tag}.html"
        out_path = out_dir / fname
        render_html(conn, dr, out_path, source=source)
        print(f"✅ 报告已生成:\n   {out_path}")
        if args.open:
            url = out_path.resolve().as_uri()
            print(f"   正在浏览器打开...")
            webbrowser.open(url)
        else:
            print(f"   用 --open 自动打开, 或:\n   open '{out_path}'")
        # 维护 latest.html 软链, 方便快速访问
        _refresh_latest(out_dir, out_path)
    finally:
        conn.close()
    return 0


def _refresh_latest(out_dir: Path, newest: Path) -> None:
    """更新 latest.html 软链, 永远指向最新的报告."""
    latest = out_dir / "latest.html"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(newest.name)
    except OSError:
        # 软链失败 (如无权限), 退化为拷贝
        import shutil
        shutil.copy2(newest, latest)


def cmd_open(args: argparse.Namespace) -> int:
    """一键打开最近一次生成的报告. 可选 --fresh 先刷新数据."""
    import time

    db_path = Path(args.db).expanduser()
    out_dir = Path(args.output).expanduser() if args.output else default_output_dir()
    target: Path | None = None

    if args.fresh:
        # 同步 + 重新生成
        if not db_path.exists():
            print("⚠️  数据库不存在, 先运行: cc-usage sync", file=sys.stderr)
            return 1
        print("🔄 同步最新数据...")
        conn = connect(db_path)
        try:
            sync(conn, default_projects_dir(), verbose=False)
            sync_zcode(conn, default_zcode_db(), verbose=False)
            dr = resolve_range(args.range)
            source = getattr(args, "source", "all")
            out_dir.mkdir(parents=True, exist_ok=True)
            if args.range == "all":
                span = date_range_span(conn)
                tag = f"{span[0]}-to-{span[1]}"
            else:
                tag = f"{dr.start}-to-{dr.end}"
            if source != "all":
                tag += f"-{source}"
            target = out_dir / f"usage-{tag}.html"
            render_html(conn, dr, target, source=source)
            _refresh_latest(out_dir, target)
            print(f"✅ 已刷新: {target.name}")
        finally:
            conn.close()
    else:
        # 直接打开已有的最新报告
        htmls = sorted(
            out_dir.glob("usage-*.html"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not htmls:
            print("📂 还没有任何报告, 正在生成首份...")
            if not db_path.exists():
                cmd_sync(argparse.Namespace(
                    db=args.db, force=False,
                    projects_dir=str(default_projects_dir()),
                ))
            conn = connect(db_path)
            try:
                dr = resolve_range("all")
                out_dir.mkdir(parents=True, exist_ok=True)
                span = date_range_span(conn)
                target = out_dir / f"usage-{span[0]}-to-{span[1]}.html"
                render_html(conn, dr, target)
                _refresh_latest(out_dir, target)
            finally:
                conn.close()
        else:
            target = htmls[0]

    # 两条路径都汇合到这里: 打开浏览器
    url = target.resolve().as_uri()
    age = time.time() - target.stat().st_mtime
    age_str = (
        f"{int(age // 60)} 分钟前" if age < 3600
        else f"{int(age // 3600)} 小时前" if age < 86400
        else f"{int(age // 86400)} 天前"
    )
    print(f"🚀 打开: {target.name}  (生成于 {age_str})")
    webbrowser.open(url)
    if not args.fresh:
        print(f"   💡 想要最新数据? 用: cc-usage open --fresh")
    return 0


def cmd_backfill_provider(args: argparse.Namespace) -> int:
    """扫 Claude JSONL 的 message.id 指纹，回填空 provider 历史 Claude 行."""
    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        print("数据库不存在. 运行 `cc-usage sync` 初始化.")
        return 1
    conn = connect(db_path)
    try:
        stats = backfill_provider(
            conn,
            Path(args.projects_dir).expanduser(),
            dry_run=args.dry_run,
            verbose=True,
        )
        print()
        print(
            f"扫描 {stats['scanned']} | 命中 {stats['matched']} | "
            f"更新 {stats['updated']} | 跳过 {stats['skipped_tagged']} | "
            f"未匹配 {stats['unmatched']}"
        )
        if args.dry_run:
            print("\n(Dry-run 模式: 未写入. 去掉 --dry-run 实际执行回填)")
    finally:
        conn.close()
    return 0


def cmd_reconcile_providers(args: argparse.Namespace) -> int:
    """双信号源回填 Claude 历史 provider: msgid 指纹 + 路由时间窗."""
    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        print("数据库不存在. 运行 `cc-usage sync` 初始化.")
        return 1
    if args.only_msgid and args.only_route:
        print("❌ --only-msgid 与 --only-route 互斥。请二选一或都不指定（双信号源）.")
        return 2
    prefer = args.prefer
    if prefer not in ("strict", "msgid", "route"):
        print(f"❌ --prefer 仅支持 strict / msgid / route，收到 {prefer!r}")
        return 2
    conn = connect(db_path)
    try:
        stats = reconcile_providers(
            conn,
            Path(args.projects_dir).expanduser(),
            dry_run=args.dry_run,
            only_msgid=args.only_msgid,
            only_route=args.only_route,
            prefer=prefer,
            verbose=True,
        )
        print()
        print(
            f"扫描 {stats['scanned']} | verified={stats['verified']} "
            f"msgid_only={stats['msgid_only']} route_only={stats['route_only']} "
            f"conflict={stats['conflict']} (prefer={prefer} 写入 {stats['conflict_written']}) "
            f"unmatched={stats['unmatched']} | 更新 {stats['updated']} | 跳过 {stats['skipped_tagged']}"
        )
        if args.dry_run:
            print("\n(Dry-run 模式: 未写入 DB. 去掉 --dry-run 实际执行.)")
    finally:
        conn.close()
    return 0


def cmd_dedupe(args: argparse.Namespace) -> int:
    """去掉 usage 表里 Claude 同 (source_file, timestamp) 的重复行."""
    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        print("数据库不存在. 运行 `cc-usage sync` 初始化.")
        return 1
    conn = connect(db_path)
    try:
        stats = dedupe_claude_rows(conn, dry_run=args.dry_run, verbose=True)
        if args.dry_run:
            print("\n(Dry-run 模式: 未写入. 去掉 --dry-run 实际执行)")
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
        by_source = dict(
            conn.execute(
                "SELECT source, COUNT(*) FROM usage GROUP BY source"
            ).fetchall()
        )
        print(f"cc-usage v{__version__}")
        print(f"数据库:    {db_path}")
        print(f"最后同步:  {last or '(无)'}")
        print(f"数据范围:  {span[0]} ~ {span[1]}" if span[0] else "数据范围:  (空)")
        print(f"文件数:    {files}")
        print(f"记录数:    {total:,}")
        src_str = " · ".join(f"{s}: {c:,}" for s, c in sorted(by_source.items()))
        print(f"来源:      {src_str or '(空)'}")
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
    sp.add_argument(
        "--only",
        choices=["claude", "zcode", "all"],
        default="all",
        help="只同步指定来源 (默认全部)",
    )
    sp.add_argument(
        "--zcode-db",
        default=None,
        help="ZCode 数据库路径 (默认 ~/.zcode/cli/db/db.sqlite)",
    )
    sp.set_defaults(func=cmd_sync)

    # today / week / month 直接走 _print_report
    for rng in ("today", "week", "month"):
        sp = sub.add_parser(rng, help=f"{rng} 终端报告")
        sp.add_argument(
            "--source",
            choices=["all", "claude", "zcode"],
            default="all",
            help="只看某个工具的用量",
        )
        sp.set_defaults(func=_print_report, range=rng)

    # report (html)
    sp = sub.add_parser("report", help="生成 HTML 报告")
    sp.add_argument(
        "--range", "-r",
        choices=["today", "week", "last_week", "month", "all"],
        default="week",
    )
    sp.add_argument(
        "--source",
        choices=["all", "claude", "zcode"],
        default="all",
        help="只看某个工具的用量",
    )
    sp.add_argument("--output", "-o", help="输出目录")
    sp.add_argument("--open", action="store_true", help="生成后用浏览器打开")
    sp.set_defaults(func=cmd_report)

    # open (一键打开最近报告)
    sp = sub.add_parser("open", help="一键打开最近一次的 HTML 报告")
    sp.add_argument("--fresh", action="store_true", help="先同步数据并重新生成")
    sp.add_argument(
        "--range", "-r",
        choices=["today", "week", "last_week", "month", "all"],
        default="all",
    )
    sp.add_argument(
        "--source",
        choices=["all", "claude", "zcode"],
        default="all",
        help="只看某个工具的用量",
    )
    sp.add_argument("--output", "-o", help="输出目录")
    sp.set_defaults(func=cmd_open)

    # status
    sp = sub.add_parser("status", help="查看数据库状态")
    sp.set_defaults(func=cmd_status)

    # backfill-provider（一次性历史回填）
    sp = sub.add_parser(
        "backfill-provider",
        help="用 Claude JSONL 的 message.id 指纹回填空 provider 历史 Claude 行",
    )
    sp.add_argument(
        "--projects-dir",
        default=str(default_projects_dir()),
        help="Claude projects 目录",
    )
    sp.add_argument(
        "--dry-run",
        action="store_true",
        help="只展示回填分布，不实际写入",
    )
    sp.set_defaults(func=cmd_backfill_provider)

    # reconcile-providers（双信号源）
    sp = sub.add_parser(
        "reconcile-providers",
        help="双信号源回填空 provider: msgid 指纹 + 路由时间窗（VSCode log + settings.json）",
    )
    sp.add_argument(
        "--projects-dir",
        default=str(default_projects_dir()),
        help="Claude projects 目录",
    )
    sp.add_argument(
        "--dry-run",
        action="store_true",
        help="只展示决策分布和冲突，不实际写入",
    )
    sp.add_argument(
        "--only-msgid",
        action="store_true",
        help="只用 msgid 指纹（不用路由窗），等同 backfill-provider",
    )
    sp.add_argument(
        "--only-route",
        action="store_true",
        help="只用路由时间窗（忽略 msgid 指纹）",
    )
    sp.add_argument(
        "--prefer",
        choices=["strict", "msgid", "route"],
        default="msgid",
        help="冲突解决策略：strict 留空 / msgid 优先指纹（默认）/ route 优先路由",
    )
    sp.set_defaults(func=cmd_reconcile_providers)

    # dedupe（修复历史重复行）
    sp = sub.add_parser(
        "dedupe",
        help="去掉 Claude 同 (source_file, timestamp) 的重复行（修复历史多次 sync 累积的翻倍）",
    )
    sp.add_argument(
        "--dry-run",
        action="store_true",
        help="只展示重复分布和修复后总量，不实际删除",
    )
    sp.set_defaults(func=cmd_dedupe)

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
