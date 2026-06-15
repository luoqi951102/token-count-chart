"""SQLite 存储层 + 增量同步.

设计:
- usage 表存每条 assistant 记录 (含 source_file 用于重解析时清理)
- files 表记录已解析文件的 mtime+size, 未变化则跳过
- 改动文件: 先 DELETE WHERE source_file=? 再重新 INSERT
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from .parser import UsageRecord, file_signature, iter_jsonl_files, parse_file

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    local_date TEXT NOT NULL,
    local_hour INTEGER NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_context INTEGER NOT NULL DEFAULT 0,
    msg_count INTEGER NOT NULL DEFAULT 1,
    session_id TEXT,
    cwd TEXT,
    project TEXT,
    source_file TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_date ON usage(local_date);
CREATE INDEX IF NOT EXISTS idx_usage_date_model ON usage(local_date, model);
CREATE INDEX IF NOT EXISTS idx_usage_model ON usage(model);
CREATE INDEX IF NOT EXISTS idx_usage_timestamp ON usage(timestamp);

CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    records INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _local_parts(ts_utc: str) -> tuple[str, int]:
    """UTC ISO 时间 -> (上海日期 YYYY-MM-DD, 小时 0-23)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    SH = ZoneInfo("Asia/Shanghai")
    dt = datetime.fromisoformat(ts_utc.replace("Z", "+00:00"))
    local = dt.astimezone(SH)
    return local.strftime("%Y-%m-%d"), local.hour


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def sync(
    conn: sqlite3.Connection,
    projects_dir: Path,
    force: bool = False,
    verbose: bool = True,
) -> dict:
    """增量同步所有 JSONL 文件到数据库.

    返回统计 dict: {scanned, new, updated, skipped, records, errors}
    """
    cur = conn.cursor()
    stats = {
        "scanned": 0,
        "new": 0,
        "updated": 0,
        "skipped": 0,
        "records": 0,
        "errors": 0,
    }

    known_files: dict[str, tuple[float, int]] = {}
    if not force:
        for path, mtime, size, _ in cur.execute(
            "SELECT path, mtime, size, records FROM files"
        ).fetchall():
            known_files[path] = (mtime, size)

    seen_paths = set()

    for filepath, project in iter_jsonl_files(projects_dir):
        stats["scanned"] += 1
        path_str = str(filepath)
        seen_paths.add(path_str)

        try:
            mtime, size = file_signature(filepath)
        except OSError:
            stats["errors"] += 1
            continue

        prev = known_files.get(path_str)
        if prev and not force and prev == (mtime, size):
            stats["skipped"] += 1
            continue

        # 清理旧记录 (如果是更新)
        if prev is not None:
            cur.execute("DELETE FROM usage WHERE source_file = ?", (path_str,))
            stats["updated"] += 1
        else:
            stats["new"] += 1

        rows = []
        for rec in parse_file(filepath, project):
            local_date, local_hour = _local_parts(rec.timestamp)
            total = (
                rec.input_tokens
                + rec.cache_creation_input_tokens
                + rec.cache_read_input_tokens
            )
            rows.append(
                (
                    rec.timestamp,
                    local_date,
                    local_hour,
                    rec.model,
                    rec.input_tokens,
                    rec.cache_creation_input_tokens,
                    rec.cache_read_input_tokens,
                    rec.output_tokens,
                    total,
                    1,
                    rec.session_id,
                    rec.cwd,
                    rec.project,
                    rec.source_file,
                )
            )

        if rows:
            cur.executemany(
                """INSERT INTO usage
                (timestamp, local_date, local_hour, model,
                 input_tokens, cache_creation_input_tokens,
                 cache_read_input_tokens, output_tokens, total_context,
                 msg_count, session_id, cwd, project, source_file)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            stats["records"] += len(rows)

        cur.execute(
            """INSERT INTO files (path, mtime, size, records)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET
                 mtime=excluded.mtime, size=excluded.size, records=excluded.records""",
            (path_str, mtime, size, len(rows)),
        )

    # 清理已删除的文件 (数据库里有, 但磁盘上没了)
    if known_files:
        deleted = set(known_files.keys()) - seen_paths
        for path_str in deleted:
            cur.execute("DELETE FROM usage WHERE source_file = ?", (path_str,))
            cur.execute("DELETE FROM files WHERE path = ?", (path_str,))
            stats["updated"] += 1

    conn.commit()

    # 写入同步时间
    from datetime import datetime, timezone

    cur.execute(
        "INSERT INTO meta(key,value) VALUES('last_sync',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (datetime.now(timezone.utc).isoformat(),),
    )
    conn.commit()

    if verbose:
        print(
            f"扫描 {stats['scanned']} 个文件 | "
            f"新增 {stats['new']} | 更新 {stats['updated']} | "
            f"跳过 {stats['skipped']} | 写入 {stats['records']} 条记录"
        )
    return stats


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute(
        "SELECT value FROM meta WHERE key=?", (key,)
    ).fetchone()
    return row[0] if row else default
