"""终端文本报告: 彩色表格输出."""
from __future__ import annotations

import sqlite3
from datetime import datetime

from .aggregate import (
    DateRange,
    active_projects,
    by_model,
    daily_totals,
    date_range_span,
    hourly_distribution,
    now_in_sh,
    source_breakdown,
)


# ANSI 颜色
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"
    BRED = "\033[1;31m"
    BGREEN = "\033[1;32m"
    BYELLOW = "\033[1;33m"
    BBLUE = "\033[1;34m"
    BMAGENTA = "\033[1;35m"
    BCYAN = "\033[1;36m"


def fmt_tokens(n: int) -> str:
    """1234567 -> 1.23M ; 12345 -> 12.3K."""
    if n is None:
        return "-"
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def fmt_num(n: int) -> str:
    return f"{n:,}"


# 模型 -> 颜色 (循环取色, 保证区分度)
_MODEL_COLORS = [
    C.CYAN, C.MAGENTA, C.GREEN, C.YELLOW, C.BLUE,
    C.RED, C.BCYAN, C.BMAGENTA, C.BGREEN, C.BYELLOW,
]


def model_color(model: str, idx: int) -> str:
    return _MODEL_COLORS[idx % len(_MODEL_COLORS)]


def render(conn: sqlite3.Connection, dr: DateRange, source: str = "all") -> str:
    """渲染一个区间的终端报告. source 可选过滤 'claude'/'zcode'."""
    out = []

    # 标题
    out.append("")
    out.append(
        f"{C.BOLD}{C.BCYAN}╭─────────────────────────────────────────────────╮{C.RESET}"
    )
    src_tag = {"all": "全部", "claude": "Claude", "zcode": "ZCode"}.get(source, source)
    title = f"Claude/ZCode 用量 · {dr.label} · {src_tag}"
    out.append(
        f"{C.BOLD}{C.BCYAN}│ {C.BMAGENTA}{title:<47}{C.BCYAN}│{C.RESET}"
    )
    out.append(
        f"{C.BOLD}{C.BCYAN}╰─────────────────────────────────────────────────╯{C.RESET}"
    )
    if dr.start != dr.end:
        out.append(
            f"{C.DIM}  区间: {dr.start} ~ {dr.end}"
            f"  ·  时区: Asia/Shanghai{C.RESET}"
        )
    # 占比标注 (仅 source=all 时显示)
    if source == "all":
        bk = source_breakdown(conn, dr.start, dr.end)
        tot_bk = bk["claude"] + bk["zcode"]
        if tot_bk:
            cp = bk["claude"] / tot_bk * 100
            out.append(
                f"{C.DIM}  来源: Claude {cp:.0f}% · ZCode {100 - cp:.0f}%{C.RESET}"
            )
    out.append("")

    # 拉数据
    models = by_model(conn, dr.start, dr.end, source)
    daily = daily_totals(conn, dr.start, dr.end, source)
    if not models:
        out.append(f"{C.DIM}  该区间内暂无数据.{C.RESET}")
        out.append("")
        return "\n".join(out)

    # ---- KPI ----
    total_tokens = sum(m["total"] for m in models)
    total_input = sum(m["input"] for m in models)
    total_output = sum(m["output"] for m in models)
    total_msgs = sum(m["msgs"] for m in models)
    active_days = len(daily)

    kpi = [
        ("总 Token", fmt_tokens(total_tokens), C.BMAGENTA),
        ("输入", fmt_tokens(total_input), C.CYAN),
        ("输出", fmt_tokens(total_output), C.GREEN),
        ("消息数", fmt_num(total_msgs), C.YELLOW),
        ("模型数", str(len(models)), C.BLUE),
        ("活跃天", str(active_days), C.RED),
    ]
    kpi_line = "  ".join(
        f"{C.DIM}{label}{C.RESET} {color}{val}{C.RESET}"
        for label, val, color in kpi
    )
    out.append(f"  {kpi_line}")
    out.append("")

    # ---- 模型明细表 ----
    out.append(f"  {C.BOLD}按模型{C.RESET}")
    out.append(
        f"  {C.DIM}{'模型':<24} {'输入':>9} {'缓存写':>9} {'缓存读':>9} "
        f"{'输出':>9} {'总上下文':>10} {'消息':>7}{C.RESET}"
    )
    out.append(f"  {C.DIM}{'-'*83}{C.RESET}")
    for i, m in enumerate(models):
        color = model_color(m["model"], i)
        name = m["model"][:22] if len(m["model"]) <= 22 else m["model"][:21] + "…"
        out.append(
            f"  {color}{name:<24}{C.RESET}"
            f"{fmt_tokens(m['input']):>11} "
            f"{C.DIM}{fmt_tokens(m['cache_write']):>10}{C.RESET} "
            f"{C.DIM}{fmt_tokens(m['cache_read']):>10}{C.RESET} "
            f"{fmt_tokens(m['output']):>10} "
            f"{C.BOLD}{fmt_tokens(m['total']):>11}{C.RESET} "
            f"{fmt_num(m['msgs']):>8}"
        )
    # 合计行
    out.append(f"  {C.DIM}{'-'*83}{C.RESET}")
    out.append(
        f"  {C.BOLD}{'TOTAL':<24}{C.RESET}"
        f"{fmt_tokens(total_input):>11} "
        f"{C.DIM}{fmt_tokens(sum(m['cache_write'] for m in models)):>10}{C.RESET} "
        f"{C.DIM}{fmt_tokens(sum(m['cache_read'] for m in models)):>10}{C.RESET} "
        f"{fmt_tokens(total_output):>10} "
        f"{C.BOLD}{fmt_tokens(total_tokens):>11}{C.RESET} "
        f"{fmt_num(total_msgs):>8}"
    )
    out.append("")

    # ---- 每日趋势 ----
    out.append(f"  {C.BOLD}每日总量{C.RESET}")
    out.append(
        f"  {C.DIM}{'日期':<12} {'总Token':>10} {'输入':>9} {'输出':>9} "
        f"{'消息':>7}  分布{C.RESET}"
    )
    out.append(f"  {C.DIM}{'-'*70}{C.RESET}")
    max_total = max((d["total"] for d in daily), default=1) or 1
    for d in daily:
        bar_len = int(30 * d["total"] / max_total) if max_total else 0
        bar = "█" * bar_len
        # 星期几
        try:
            wd = datetime.strptime(d["date"], "%Y-%m-%d").weekday()
            wdn = ["一", "二", "三", "四", "五", "六", "日"][wd]
        except Exception:
            wd = 0
            wdn = "?"
        weekend = C.RED if wd >= 5 else C.RESET
        out.append(
            f"  {weekend}{d['date']} 周{wdn}{C.RESET} "
            f"{C.BOLD}{fmt_tokens(d['total']):>11}{C.RESET} "
            f"{fmt_tokens(d['input']):>10} "
            f"{fmt_tokens(d['output']):>10} "
            f"{fmt_num(d['msgs']):>8}  "
            f"{C.MAGENTA}{bar}{C.RESET}"
        )
    out.append("")

    # ---- 项目分布 (top 5) ----
    projects = active_projects(conn, dr.start, dr.end, limit=5)
    if projects:
        out.append(f"  {C.BOLD}Top 项目{C.RESET}")
        max_p = max((p["total"] for p in projects), default=1) or 1
        for p in projects:
            bar_len = int(20 * p["total"] / max_p) if max_p else 0
            bar = "▆" * bar_len
            name = p["project"][:34]
            out.append(
                f"  {C.BLUE}{name:<36}{C.RESET} "
                f"{C.BOLD}{fmt_tokens(p['total']):>10}{C.RESET} "
                f"{C.DIM}{fmt_num(p['msgs'])} msgs{C.RESET} "
                f"{C.CYAN}{bar}{C.RESET}"
            )
        out.append("")

    # ---- 时段分布 ----
    hours = hourly_distribution(conn, dr.start, dr.end)
    if hours:
        hmap = {h["hour"]: h["total"] for h in hours}
        out.append(f"  {C.BOLD}时段分布{C.RESET}")
        max_h = max(hmap.values()) if hmap else 1
        # 分两行显示 0-11, 12-23
        for start_h in (0, 12):
            line = "  "
            for h in range(start_h, start_h + 12):
                v = hmap.get(h, 0)
                bar_len = int(15 * v / max_h) if max_h and v else 0
                bar = "▅" * bar_len
                line += f"{C.DIM}{h:02d}{C.RESET}{C.YELLOW}{bar:<16}{C.RESET}"
            out.append(line)
        out.append("")

    out.append(
        f"{C.DIM}  生成于 {now_in_sh().strftime('%Y-%m-%d %H:%M:%S')}  "
        f"(Asia/Shanghai){C.RESET}"
    )
    out.append("")
    return "\n".join(out)
