"""SQLite 存储层 + 增量同步.

设计:
- usage 表存每条用量记录 (Claude assistant 行 或 ZCode model_usage 行)
  · source 列区分来源: 'claude' / 'zcode'
  · ext_id 列存外部去重键 (ZCode 的 model_usage.id; Claude 留空, 靠 source_file 重解析清理)
- files 表记录已解析的 Claude 文件 mtime+size, 未变化则跳过
- 改动文件: 先 DELETE WHERE source_file=? 再重新 INSERT
- ZCode 同步: 按 completed_at 水位线增量, INSERT OR IGNORE 幂等
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

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
    source_file TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'claude',
    ext_id TEXT NOT NULL DEFAULT ''
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

# 迁移: 给旧库 (无 source/ext_id 列) 补列. 幂等, 已存在则跳过.
_MIGRATIONS = [
    "ALTER TABLE usage ADD COLUMN source TEXT NOT NULL DEFAULT 'claude'",
    "ALTER TABLE usage ADD COLUMN ext_id TEXT NOT NULL DEFAULT ''",
]


def _local_parts(ts_utc: str) -> tuple[str, int]:
    """UTC ISO 时间 -> (上海日期 YYYY-MM-DD, 小时 0-23)."""
    from datetime import datetime
    from .aggregate import SH
    dt = datetime.fromisoformat(ts_utc.replace("Z", "+00:00"))
    local = dt.astimezone(SH)
    return local.strftime("%Y-%m-%d"), local.hour


def _local_parts_epoch(ms_epoch: int) -> tuple[str, int]:
    """毫秒级 epoch -> (上海日期 YYYY-MM-DD, 小时 0-23)."""
    from datetime import datetime, timezone
    from .aggregate import SH
    dt = datetime.fromtimestamp(ms_epoch / 1000, tz=timezone.utc).astimezone(SH)
    return dt.strftime("%Y-%m-%d"), dt.hour


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    # 旧库迁移: 补 source / ext_id 列 (幂等)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(usage)").fetchall()}
    if "source" not in cols:
        conn.execute("ALTER TABLE usage ADD COLUMN source TEXT NOT NULL DEFAULT 'claude'")
    if "ext_id" not in cols:
        conn.execute("ALTER TABLE usage ADD COLUMN ext_id TEXT NOT NULL DEFAULT ''")
    # 幂等: 确保 source 相关索引存在 (无论新旧库, 列已就位)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_source ON usage(source)")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_source_ext "
        "ON usage(source, ext_id) WHERE ext_id != ''"
    )
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


def sync_zcode(
    conn: sqlite3.Connection,
    zcode_db: Path,
    verbose: bool = True,
) -> dict:
    """从 ZCode 的 db.sqlite 增量同步 model_usage.

    增量策略: 只取 completed_at > 水位线的已完次记录, 按 (source, ext_id)
    幂等插入. 水位线存 meta['zcode_last_completed_at'].

    返回 {new, skipped, errors}
    """
    stats = {"new": 0, "skipped": 0, "errors": 0}
    if not zcode_db.exists():
        if verbose:
            print(f"⏭️  ZCode 数据库不存在, 跳过: {zcode_db}")
        return stats

    # 读 ZCode 库 (immutable 只读: 不碰 -wal/-shm, 避免 ZCode 持锁时打开失败)
    # 退路: immutable 失败 (如 WAL 未 checkpoint) 再降级普通只读
    src = None
    for uri in (f"file:{zcode_db}?immutable=1", f"file:{zcode_db}?mode=ro"):
        try:
            src = sqlite3.connect(uri, uri=True)
            src.execute("SELECT COUNT(*) FROM model_usage").fetchone()
            break
        except sqlite3.Error:
            if src:
                src.close()
                src = None
    if src is None:
        if verbose:
            print(f"⚠️  ZCode 库暂时读不了 (可能正被占用), 跳过: {zcode_db}")
        return stats

    try:
        # 水位线: 上次同步过的最大 completed_at
        watermark = int(get_meta(conn, "zcode_last_completed_at") or 0)
        rows = src.execute(
            """
            SELECT m.id, m.started_at, m.completed_at, m.model_id,
                   m.input_tokens, m.cache_creation_input_tokens,
                   m.cache_read_input_tokens, m.output_tokens,
                   m.computed_total_tokens, m.tool_call_count,
                   m.session_id, s.directory
            FROM model_usage m
            LEFT JOIN session s ON m.session_id = s.id
            WHERE m.completed_at > ? AND m.status = 'completed'
            ORDER BY m.completed_at
            """,
            (watermark,),
        ).fetchall()
    finally:
        src.close()

    if not rows:
        if verbose:
            print(f"✓ ZCode 无新增 (水位线 {watermark})")
        return stats

    new_watermark = watermark
    batch = []  # 收集后批量插入 (与 Claude 路径 executemany 一致)
    for (
        ext_id, started_at, completed_at, model_id,
        inp, cw, cr, outp, total, tool_count,
        session_id, directory,
    ) in rows:
        # 时间用 started_at 分桶 (用户实际开始用的时间)
        try:
            local_date, local_hour = _local_parts_epoch(started_at)
        except (ValueError, OSError):
            stats["errors"] += 1
            continue
        ts_iso = _epoch_ms_to_iso(started_at)
        batch.append(
            (
                ts_iso, local_date, local_hour, model_id,
                inp or 0, cw or 0, cr or 0, outp or 0, total or 0,
                1, session_id or "", directory or "", directory or "",
                ext_id,
            )
        )
        if completed_at and completed_at > new_watermark:
            new_watermark = completed_at

    # 批量 INSERT OR IGNORE, 靠 (source, ext_id) 唯一索引去重
    if batch:
        before = conn.execute(
            "SELECT COUNT(*) FROM usage WHERE source='zcode'"
        ).fetchone()[0]
        try:
            conn.executemany(
                """
                INSERT OR IGNORE INTO usage
                (timestamp, local_date, local_hour, model,
                 input_tokens, cache_creation_input_tokens,
                 cache_read_input_tokens, output_tokens, total_context,
                 msg_count, session_id, cwd, project, source_file, source, ext_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'','zcode',?)
                """,
                batch,
            )
            after = conn.execute(
                "SELECT COUNT(*) FROM usage WHERE source='zcode'"
            ).fetchone()[0]
            inserted = after - before
            stats["new"] = inserted
            stats["skipped"] = len(batch) - inserted
        except sqlite3.Error:
            stats["errors"] += len(batch)

    conn.commit()
    if new_watermark > watermark:
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('zcode_last_completed_at',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(new_watermark),),
        )
        conn.commit()

    if verbose:
        print(
            f"✓ ZCode: 新增 {stats['new']} | 跳过 {stats['skipped']} "
            f"| 错误 {stats['errors']} (水位线 {watermark}→{new_watermark})"
        )
    return stats


def _epoch_ms_to_iso(ms: int) -> str:
    """毫秒 epoch -> UTC ISO 字符串 (与 Claude jsonl 的 timestamp 同格式)."""
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}Z"


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute(
        "SELECT value FROM meta WHERE key=?", (key,)
    ).fetchone()
    return row[0] if row else default
