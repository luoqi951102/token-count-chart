"""SQLite 存储层 + 增量同步.

设计:
- usage 表存每条用量记录 (Claude assistant 行 或 ZCode model_usage 行)
  · source 列区分来源: 'claude' / 'zcode'
  · ext_id 列存外部去重键 (ZCode 的 model_usage.id; Claude 留空, 靠 source_file 重解析清理)
  · provider 列存供应商标识:
      - ZCode 来自 model_usage.provider_id (UUID 或 builtin:xxx)
      - Claude 存当时的 ANTHROPIC_BASE_URL 原文 (e.g. "https://api.goodputai.cn");
        Claude JSONL 不记 baseURL, 所以只能 going-forward 打标,
        历史已入库的行维持空字符串, 不回溯改写.
      - Swift 端负责 baseURL → 友好名 的二级映射.
- files 表记录已解析的 Claude 文件 mtime+size, 未变化则跳过
- 改动文件: 先 DELETE WHERE source_file=? 再重新 INSERT
  · 但在 DELETE 前快照 (timestamp, provider) → INSERT 后按 timestamp 回填,
    避免「会话文件被 append 后整盘改写供应商」的脏数据.
- ZCode 同步: 按 completed_at 水位线增量, INSERT OR IGNORE 幂等
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterator

from .parser import UsageRecord, file_signature, iter_jsonl_files, parse_file


def _read_claude_base_url(settings_path: Path | None = None) -> str:
    """读 ~/.claude/settings.json 里的 ANTHROPIC_BASE_URL.

    CCM 切供应商时会把当前 baseURL 写到 env.ANTHROPIC_BASE_URL.
    用于对 Claude 新行做 going-forward 供应商打标. 读不出则返回空串.

    CCM 内置支持的 baseURL → 友好名映射 (Swift 端再做, 这里只存原文):
      https://api.z.ai/api/anthropic                                → 智谱官方·国际
      https://open.bigmodel.cn/api/anthropic                        → 智谱官方·国内
      https://api.deepseek.com/anthropic                            → DeepSeek
      https://api.moonshot.ai/anthropic                             → 月之暗面·国际
      https://api.moonshot.cn/anthropic                             → 月之暗面·国内
      https://coding-intl.dashscope.aliyuncs.com/apps/anthropic     → 通义千问·国际
      https://coding.dashscope.aliyuncs.com/apps/anthropic          → 通义千问·国内
      https://api.minimax.io/anthropic                              → Minimax·国际
      https://api.minimaxi.com/anthropic                            → Minimax·国内
      https://ark.cn-beijing.volces.com/api/coding                  → 火山引擎
      https://api.stepfun.ai/v1/anthropic                           → StepFun
      https://api.anthropic.com/                                    → Anthropic 官方
      (第三方代理如 api.goodputai.cn 不在 CCM 标准表, 走用户自定义别名)
    """
    p = settings_path or (Path.home() / ".claude" / "settings.json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(d, dict):
        return ""
    env = d.get("env")
    if not isinstance(env, dict):
        return ""
    url = env.get("ANTHROPIC_BASE_URL", "")
    return url if isinstance(url, str) else ""

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
    ext_id TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT ''
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

# 迁移: 给旧库补列. 幂等, 已存在则跳过.
_MIGRATIONS = [
    "ALTER TABLE usage ADD COLUMN source TEXT NOT NULL DEFAULT 'claude'",
    "ALTER TABLE usage ADD COLUMN ext_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE usage ADD COLUMN provider TEXT NOT NULL DEFAULT ''",
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
    # 旧库迁移: 补 source / ext_id / provider 列 (幂等)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(usage)").fetchall()}
    if "source" not in cols:
        conn.execute("ALTER TABLE usage ADD COLUMN source TEXT NOT NULL DEFAULT 'claude'")
    if "ext_id" not in cols:
        conn.execute("ALTER TABLE usage ADD COLUMN ext_id TEXT NOT NULL DEFAULT ''")
    if "provider" not in cols:
        conn.execute("ALTER TABLE usage ADD COLUMN provider TEXT NOT NULL DEFAULT ''")
    # 幂等: 确保 source 相关索引存在 (无论新旧库, 列已就位)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_source ON usage(source)")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_source_ext "
        "ON usage(source, ext_id) WHERE ext_id != ''"
    )
    # provider 维度索引（用于按供应商聚合）
    conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_provider ON usage(provider)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_model_provider ON usage(model, provider)")
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

    # 把当前 Claude settings.json 里的 baseURL 拿到, 用于给新行打标
    current_base_url = _read_claude_base_url()
    if verbose and current_base_url:
        print(f"🔑 Claude 当前 baseURL: {current_base_url}")

    # 累积「文件被 DELETE+INSERT 时, 该回填的旧 provider」
    # key: (source_file, timestamp) → value: 原 provider
    provider_snapshots: dict[tuple[str, str], str] = {}

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

        # 清理旧记录 (如果是更新): 先快照 (timestamp, provider) 再 DELETE,
        # 这样重 INSERT 后能根据 timestamp 把「已标注的 provider」原样回填,
        # 避免会话文件被 append 后整盘改写成当前 baseURL 的脏数据.
        if prev is not None:
            for ts, prov in cur.execute(
                "SELECT timestamp, provider FROM usage WHERE source_file = ?",
                (path_str,),
            ).fetchall():
                if prov:  # 只回填非空的 (历史空值由 baseURL 写新, 不保护)
                    provider_snapshots[(path_str, ts)] = prov
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
            # going-forward: 用当前 settings.json 的 baseURL 给新行打标
            # 但如果该 timestamp 在旧快照里有非空 provider, 说明上次已标好, 沿用旧值
            keep = provider_snapshots.get((path_str, rec.timestamp))
            provider_val = keep if keep else current_base_url
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
                    provider_val,
                )
            )

        if rows:
            cur.executemany(
                """INSERT INTO usage
                (timestamp, local_date, local_hour, model,
                 input_tokens, cache_creation_input_tokens,
                 cache_read_input_tokens, output_tokens, total_context,
                 msg_count, session_id, cwd, project, source_file, provider)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                   m.session_id, s.directory, m.provider_id
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
        session_id, directory, provider_id,
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
                ext_id, provider_id or "",
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
                 msg_count, session_id, cwd, project, source_file, source, ext_id, provider)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'','zcode',?,?)
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
