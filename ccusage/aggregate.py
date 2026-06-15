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
RangeKey = Literal["today", "week", "month", "all"]


@dataclass
class DateRange:
    start: str  # YYYY-MM-DD (含)
    end: str  # YYYY-MM-DD (含)
    label: str  # 展示用


def today_str() -> str:
    return datetime.now(SH).strftime("%Y-%m-%d")


def now_in_sh() -> datetime:
    return datetime.now(SH)


def resolve_range(rng: RangeKey | str) -> DateRange:
    """把 today/week/month/all 解析成具体的日期区间."""
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
    conn: sqlite3.Connection, start: str, end: str
) -> list[dict]:
    """按 (日期, 模型) 聚合. 返回行列表."""
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
        GROUP BY local_date, model
        ORDER BY local_date, total DESC
        """,
        (start, end),
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


def by_model(conn: sqlite3.Connection, start: str, end: str) -> list[dict]:
    """按模型聚合区间内总量."""
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
        GROUP BY model
        ORDER BY SUM(total_context) DESC
        """,
        (start, end),
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


def daily_totals(conn: sqlite3.Connection, start: str, end: str) -> list[dict]:
    """按日期聚合总量 (不分模型)."""
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
        GROUP BY local_date
        ORDER BY local_date
        """,
        (start, end),
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
    conn: sqlite3.Connection, start: str, end: str
) -> list[dict]:
    """按 (周, 模型) 聚合. 周用周一日期标识."""
    daily = daily_by_model(conn, start, end)
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
    conn: sqlite3.Connection, start: str, end: str
) -> list[dict]:
    """按时段 (0-23 小时) 聚合, 看一天里什么时段用得多."""
    rows = conn.execute(
        """
        SELECT local_hour, SUM(total_context), SUM(msg_count)
        FROM usage
        WHERE local_date BETWEEN ? AND ?
        GROUP BY local_hour
        ORDER BY local_hour
        """,
        (start, end),
    ).fetchall()
    return [{"hour": r[0], "total": r[1], "msgs": r[2]} for r in rows]


def date_range_span(conn: sqlite3.Connection) -> tuple[str | None, str | None]:
    """返回数据库中实际数据的最早/最晚日期."""
    row = conn.execute(
        "SELECT MIN(local_date), MAX(local_date) FROM usage"
    ).fetchone()
    return row[0], row[1]


def active_projects(
    conn: sqlite3.Connection, start: str, end: str, limit: int = 10
) -> list[dict]:
    """按项目目录 (cwd) 聚合, 看在哪些项目里用得多."""
    import os

    home = os.path.expanduser("~")
    rows = conn.execute(
        """
        SELECT cwd, SUM(total_context), SUM(msg_count)
        FROM usage
        WHERE local_date BETWEEN ? AND ?
        GROUP BY cwd
        ORDER BY SUM(total_context) DESC
        LIMIT ?
        """,
        (start, end, limit),
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
