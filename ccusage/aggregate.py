"""用量数据聚合层: 日/周/月维度, 模型维度, 时段维度.

所有日期分桶都基于 Asia/Shanghai 时区.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

SH = ZoneInfo("Asia/Shanghai")
RangeKey = Literal["today", "week", "last_week", "month", "all"]


@dataclass
class DateRange:
    start: str  # YYYY-MM-DD (含)
    end: str  # YYYY-MM-DD (含)
    label: str  # 展示用


def now_in_sh() -> datetime:
    return datetime.now(SH)


def resolve_range(rng: RangeKey | str) -> DateRange:
    """把 today/week/last_week/month/all 解析成具体的日期区间."""
    today = now_in_sh()
    if rng == "today":
        d = today.strftime("%Y-%m-%d")
        return DateRange(d, d, f"今日 {d}")
    if rng == "week":
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        return DateRange(
            monday.strftime("%Y-%m-%d"),
            sunday.strftime("%Y-%m-%d"),
            f"本周 {monday.strftime('%m-%d')} ~ {sunday.strftime('%m-%d')}",
        )
    if rng == "last_week":
        this_monday = today - timedelta(days=today.weekday())
        monday = this_monday - timedelta(days=7)
        sunday = monday + timedelta(days=6)
        return DateRange(
            monday.strftime("%Y-%m-%d"),
            sunday.strftime("%Y-%m-%d"),
            f"上周 {monday.strftime('%m-%d')} ~ {sunday.strftime('%m-%d')}",
        )
    if rng == "month":
        first = today.replace(day=1)
        # 月末
        if today.month == 12:
            nxt = today.replace(year=today.year + 1, month=1, day=1)
        else:
            nxt = today.replace(month=today.month + 1, day=1)
        last = nxt - timedelta(days=1)
        return DateRange(
            first.strftime("%Y-%m-%d"),
            last.strftime("%Y-%m-%d"),
            f"{first.strftime('%Y-%m')}",
        )
    if rng == "all":
        return DateRange("2000-01-01", "2099-12-31", "全部历史")
    raise ValueError(f"未知 range: {rng}")


def _week_of(date_str: str) -> str:
    """给定 YYYY-MM-DD, 返回所在周的周一日期 (作为周标识)."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    monday = dt - timedelta(days=dt.weekday())
    return monday.strftime("%Y-%m-%d")


# ---------- 查询函数 ----------


def daily_by_model(
    conn: sqlite3.Connection, start: str, end: str, source: str = "all"
) -> list[dict]:
    """按 (日期, 模型) 聚合. 返回行列表. source 可选过滤 'claude'/'zcode'."""
    rows = conn.execute(
        """
        SELECT local_date, model,
               SUM(input_tokens) AS inp,
               SUM(cache_creation_input_tokens) AS cw,
               SUM(cache_read_input_tokens) AS cr,
               SUM(output_tokens) AS outp,
               SUM(total_context) AS total,
               SUM(msg_count) AS msgs
        FROM usage
        WHERE local_date BETWEEN ? AND ?
          AND (source = ? OR ? = 'all')
        GROUP BY local_date, model
        ORDER BY local_date, total DESC
        """,
        (start, end, source, source),
    ).fetchall()
    return [
        {
            "date": r[0],
            "model": r[1],
            "input": r[2],
            "cache_write": r[3],
            "cache_read": r[4],
            "output": r[5],
            "total": r[6],
            "msgs": r[7],
        }
        for r in rows
    ]


def by_model(
    conn: sqlite3.Connection, start: str, end: str, source: str = "all"
) -> list[dict]:
    """按模型聚合区间内总量. source 可选过滤."""
    rows = conn.execute(
        """
        SELECT model,
               SUM(input_tokens),
               SUM(cache_creation_input_tokens),
               SUM(cache_read_input_tokens),
               SUM(output_tokens),
               SUM(total_context),
               SUM(msg_count)
        FROM usage
        WHERE local_date BETWEEN ? AND ?
          AND (source = ? OR ? = 'all')
        GROUP BY model
        ORDER BY SUM(total_context) DESC
        """,
        (start, end, source, source),
    ).fetchall()
    return [
        {
            "model": r[0],
            "input": r[1],
            "cache_write": r[2],
            "cache_read": r[3],
            "output": r[4],
            "total": r[5],
            "msgs": r[6],
        }
        for r in rows
    ]


def daily_totals(
    conn: sqlite3.Connection, start: str, end: str, source: str = "all"
) -> list[dict]:
    """按日期聚合总量 (不分模型). source 可选过滤."""
    rows = conn.execute(
        """
        SELECT local_date,
               SUM(input_tokens),
               SUM(cache_creation_input_tokens),
               SUM(cache_read_input_tokens),
               SUM(output_tokens),
               SUM(total_context),
               SUM(msg_count)
        FROM usage
        WHERE local_date BETWEEN ? AND ?
          AND (source = ? OR ? = 'all')
        GROUP BY local_date
        ORDER BY local_date
        """,
        (start, end, source, source),
    ).fetchall()
    return [
        {
            "date": r[0],
            "input": r[1],
            "cache_write": r[2],
            "cache_read": r[3],
            "output": r[4],
            "total": r[5],
            "msgs": r[6],
        }
        for r in rows
    ]


def weekly_by_model(
    conn: sqlite3.Connection, start: str, end: str, source: str = "all"
) -> list[dict]:
    """按 (周, 模型) 聚合. 周用周一日期标识. source 透传给 daily_by_model."""
    daily = daily_by_model(conn, start, end, source)
    bucket: dict[tuple[str, str], dict] = {}
    for row in daily:
        wk = _week_of(row["date"])
        key = (wk, row["model"])
        if key not in bucket:
            bucket[key] = {
                "week": wk,
                "model": row["model"],
                "input": 0,
                "cache_write": 0,
                "cache_read": 0,
                "output": 0,
                "total": 0,
                "msgs": 0,
            }
        b = bucket[key]
        for k in ("input", "cache_write", "cache_read", "output", "total", "msgs"):
            b[k] += row[k]
    return sorted(bucket.values(), key=lambda x: (x["week"], -x["total"]))


def hourly_distribution(
    conn: sqlite3.Connection, start: str, end: str, source: str = "all"
) -> list[dict]:
    """按时段 (0-23 小时) 聚合, 看一天里什么时段用得多. source 可选过滤."""
    rows = conn.execute(
        """
        SELECT local_hour, SUM(total_context), SUM(msg_count)
        FROM usage
        WHERE local_date BETWEEN ? AND ?
          AND (source = ? OR ? = 'all')
        GROUP BY local_hour
        ORDER BY local_hour
        """,
        (start, end, source, source),
    ).fetchall()
    return [{"hour": r[0], "total": r[1], "msgs": r[2]} for r in rows]


def date_range_span(conn: sqlite3.Connection) -> tuple[str | None, str | None]:
    """返回数据库中实际数据的最早/最晚日期."""
    row = conn.execute(
        "SELECT MIN(local_date), MAX(local_date) FROM usage"
    ).fetchone()
    return row[0], row[1]


def active_projects(
    conn: sqlite3.Connection, start: str, end: str, limit: int = 10,
    source: str = "all",
) -> list[dict]:
    """按项目目录 (cwd) 聚合, 看在哪些项目里用得多. source 可选过滤."""
    import os

    home = os.path.expanduser("~")
    rows = conn.execute(
        """
        SELECT cwd, SUM(total_context), SUM(msg_count)
        FROM usage
        WHERE local_date BETWEEN ? AND ?
          AND (source = ? OR ? = 'all')
        GROUP BY cwd
        ORDER BY SUM(total_context) DESC
        LIMIT ?
        """,
        (start, end, source, source, limit),
    ).fetchall()
    out = []
    for cwd, total, msgs in rows:
        if not cwd:
            name = "(unknown)"
        elif cwd == home:
            name = "~"
        elif cwd.startswith(home + "/"):
            name = "~" + cwd[len(home):]
        else:
            name = cwd
        out.append({"project": name, "total": total or 0, "msgs": msgs or 0})
    return out


# ---------- 游戏化指标 ----------


def streak(conn: sqlite3.Connection, source: str = "all") -> dict:
    """连续打卡天数.

    current: 从今天往前数的连续天数 (今天没数据则从昨天起算, 更友好);
    longest: 历史最长连续; active_today: 今天是否有用量.
    source 可选过滤 'claude'/'zcode'.
    """
    rows = conn.execute(
        "SELECT DISTINCT local_date FROM usage "
        "WHERE (source = ? OR ? = 'all') ORDER BY local_date",
        (source, source),
    ).fetchall()
    dates = sorted(r[0] for r in rows)
    if not dates:
        return {"current": 0, "longest": 0, "active_today": False}

    date_set = set(dates)
    today = now_in_sh().date()
    today_str = today.strftime("%Y-%m-%d")
    active_today = today_str in date_set

    # current: 若今天没用, 从昨天起算 (深夜看不会是 0)
    cursor = today if active_today else today - timedelta(days=1)
    current = 0
    while cursor.strftime("%Y-%m-%d") in date_set:
        current += 1
        cursor -= timedelta(days=1)

    # longest: 扫排序日期, 找最长连续段
    longest = 0
    run = 0
    prev = None
    for d in dates:
        dt = datetime.strptime(d, "%Y-%m-%d").date()
        if prev is not None and (dt - prev).days == 1:
            run += 1
        else:
            run = 1
        longest = max(longest, run)
        prev = dt

    return {"current": current, "longest": longest, "active_today": active_today}


def weekday_vs_weekend(
    conn: sqlite3.Connection, start: str, end: str
) -> dict:
    """工作日 vs 周末的用量对比 (total + 覆盖天数).

    strftime('%w', 'YYYY-MM-DD'): 0=周日, 6=周六.
    """
    rows = conn.execute(
        """
        SELECT
            CASE WHEN strftime('%w', local_date) IN ('0','6')
                 THEN 'weekend' ELSE 'weekday' END AS kind,
            SUM(total_context),
            COUNT(DISTINCT local_date)
        FROM usage
        WHERE local_date BETWEEN ? AND ?
        GROUP BY kind
        """,
        (start, end),
    ).fetchall()
    res = {
        "weekday": {"total": 0, "days": 0},
        "weekend": {"total": 0, "days": 0},
    }
    for kind, total, days in rows:
        if kind in res:
            res[kind]["total"] = total or 0
            res[kind]["days"] = days or 0
    return res


def peak_day(
    conn: sqlite3.Connection, start: str, end: str, source: str = "all"
) -> dict | None:
    """区间内用量最大的那一天. 无数据返回 None. source 可选过滤."""
    row = conn.execute(
        """
        SELECT local_date, SUM(total_context), SUM(msg_count)
        FROM usage
        WHERE local_date BETWEEN ? AND ?
          AND (source = ? OR ? = 'all')
        GROUP BY local_date
        ORDER BY SUM(total_context) DESC
        LIMIT 1
        """,
        (start, end, source, source),
    ).fetchone()
    if not row or not row[1]:
        return None
    return {"date": row[0], "total": row[1], "msgs": row[2] or 0}


def source_breakdown(
    conn: sqlite3.Connection, start: str, end: str
) -> dict:
    """按数据来源 (claude/zcode) 聚合 token, 给占比卡用."""
    rows = conn.execute(
        """
        SELECT source, COALESCE(SUM(total_context), 0)
        FROM usage
        WHERE local_date BETWEEN ? AND ?
        GROUP BY source
        """,
        (start, end),
    ).fetchall()
    res = {"claude": 0, "zcode": 0}
    for src, total in rows:
        if src in res:
            res[src] = total
    return res


def week_over_week(conn: sqlite3.Connection, source: str = "all") -> dict:
    """本周 vs 上周总量环比. delta_pct: 涨跌百分比 (上周为 0 则 None).
    source 可选过滤 'claude'/'zcode'.
    """
    this = resolve_range("week")
    last_start = (
        datetime.strptime(this.start, "%Y-%m-%d") - timedelta(days=7)
    ).strftime("%Y-%m-%d")
    last_end = (
        datetime.strptime(this.end, "%Y-%m-%d") - timedelta(days=7)
    ).strftime("%Y-%m-%d")

    def _sum(s: str, e: str) -> int:
        r = conn.execute(
            "SELECT COALESCE(SUM(total_context), 0) FROM usage "
            "WHERE local_date BETWEEN ? AND ? AND (source = ? OR ? = 'all')",
            (s, e, source, source),
        ).fetchone()
        return r[0] or 0

    this_week = _sum(this.start, this.end)
    last_week = _sum(last_start, last_end)
    delta_pct = (
        round((this_week - last_week) / last_week * 100, 1) if last_week else None
    )
    return {
        "this_week": this_week,
        "last_week": last_week,
        "last_start": last_start,
        "last_end": last_end,
        "delta_pct": delta_pct,
    }
