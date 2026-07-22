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


# MARK: - Provider 历史回填

# msg.id 格式 → provider baseURL 的指纹映射表（与 Swift 端 BaseURLProviderMap 对齐）
# 这些是 Claude Code 转发请求时各供应商响应里返回的 request_id 原文格式，
# 比任何其他 Claude JSONL 字段都更可靠地区分供应商。
#
# 已知格式（来自实测）：
#   ^021\d+                          火山引擎方舟 request_id（前缀 021 + 数字）
#                                    覆盖: deepseek-v4-flash, deepseek-v4-pro, doubao, minimax-m3
#   ^msg_01[A-Za-z0-9]{4,}           Anthropic 官方格式（用户确认经浙算 MaaS 代理）
#                                    覆盖: claude-opus-4-8
#   ^msg_[0-9a-f]{32}$               goodputai 量化部署格式（无连字符的 32 位 hex）
#                                    覆盖: glm-52-w4a8-kv / kvp
#   ^msg_\d{14,}                     goodputai 代理格式（msg_ + 14 位以上时间戳）
#                                    覆盖: glm-5.2 (90%)
#   ^msg_[0-9a-f]{8}-[0-9a-f]{4}-... 标准 UUID 格式 → 走 OpenAI 兼容代理
#                                     glm-*  → ai.zj-computility.com 浙算 MaaS
#                                     qwen*  → 通义千问 DashScope
#   ^chatcmpl-                       OpenAI 标准格式 → OpenAI 兼容代理
#                                     仅 qwen3 (22 条不确定)
_MSGID_PATTERNS = [
    # (compiled regex, provider baseURL, 备注)
    (r"^021\d+", "https://ark.cn-beijing.volces.com/api/coding", "火山方舟"),
    (r"^msg_01[A-Za-z0-9]{4,}", "https://ai.zj-computility.com/maas", "Anthropic 经浙算代理"),
    (r"^msg_[0-9a-f]{32}$", "https://api.goodputai.cn", "goodputai 量化部署"),
    (r"^msg_\d{14,}", "https://api.goodputai.cn", "goodputai 代理 msg_timestamp"),
]


def _classify_provider_from_msgid(mid: str, model: str) -> str:
    """根据 Claude JSONL message.id 格式推断供应商 baseURL。

    返回 baseURL 原文（写入 ccusage.db usage.provider 列），无匹配返回空串。
    对没匹配 msgid 但 model 已知的特殊情况，做 model 维度回退（少数边角）。
    """
    if not mid:
        return ""
    import re
    for pattern, url, _ in _MSGID_PATTERNS:
        if re.match(pattern, mid):
            return url
    # UUID 格式 → 按 model 区分 glm* vs qwen*
    if re.match(r"^msg_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", mid):
        if model.startswith("qwen"):
            return "https://coding.dashscope.aliyuncs.com/apps/anthropic"
        if model.startswith("glm"):
            return "https://ai.zj-computility.com/maas"
        return ""
    return ""


def backfill_provider(
    conn: sqlite3.Connection,
    projects_dir: Path,
    dry_run: bool = False,
    verbose: bool = True,
) -> dict:
    """扫所有 Claude JSONL 文件的 message.id 指纹，回填空 provider 历史 Claude 行。

    策略：
    - 读取每个 assistant 行的 (timestamp, message.id, model)
    - 按 msgid 指纹映射到 baseURL
    - 只更新 provider='' 的 Claude 行（已带标签的不动，避免覆盖新数据）
    - 用 timestamp 做 DB 关联键（Claude JSONL 里 timestamp 是稳定的）

    返回 {scanned, matched, updated, skipped_tagged, unmatched_models, before/after 分布}
    """
    import json

    stats = {
        "scanned": 0,         # JSONL 里扫到的 assistant 行数
        "matched": 0,         # msgid 指纹命中数
        "updated": 0,         # 实际 UPDATE 行数
        "skipped_tagged": 0,  # DB 里已有 provider 非空、跳过未改的行数
        "unmatched": 0,       # msgid 指纹未命中（仍未打标）的行数
        "dry_run": dry_run,
    }
    # timestamp → (baseURL, model)
    msgid_index: dict[str, tuple[str, str]] = {}
    # 没匹配到 msgid 的 (model, msgid_prefix) 统计，便于报告
    unmatched_seens: dict[tuple[str, str], int] = {}

    projects_path = Path(projects_dir)
    if not projects_path.exists():
        if verbose:
            print(f"⚠️  Claude projects 目录不存在: {projects_path}")
        return stats

    # 扫所有 JSONL
    for project_dir in sorted(projects_path.iterdir()):
        if not project_dir.is_dir():
            continue
        for f in sorted(project_dir.glob("*.jsonl")):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    for line in fp:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            d = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(d, dict) or d.get("type") != "assistant":
                            continue
                        msg = d.get("message")
                        if not isinstance(msg, dict):
                            continue
                        model = msg.get("model") or ""
                        if not model or model in {"<synthetic>", ""}:
                            continue
                        ts = d.get("timestamp", "")
                        if not ts:
                            continue
                        mid = msg.get("id", "")
                        stats["scanned"] += 1
                        url = _classify_provider_from_msgid(mid, model)
                        if url:
                            msgid_index[ts] = (url, model)
                            stats["matched"] += 1
                        else:
                            stats["unmatched"] += 1
                            prefix = mid[:24] if mid else "<empty>"
                            key = (model, prefix)
                            unmatched_seens[key] = unmatched_seens.get(key, 0) + 1
            except OSError:
                continue

    if verbose:
        print(
            f"扫描 JSONL {stats['scanned']} 条 assistant 行 | "
            f"msgid 指纹命中 {stats['matched']} | 未命中 {stats['unmatched']}"
        )

    # 把 msgid_index 转成 model→baseURL 分布（用于报告）
    model_url_counts: dict[tuple[str, str], int] = {}
    for ts, (url, model) in msgid_index.items():
        model_url_counts[(model, url)] = model_url_counts.get((model, url), 0) + 1

    if dry_run:
        if verbose:
            print("\n[Dry-run] 模式：不写入 DB，只展示将要更新的分布\n")
            print("=== (model, baseURL) 将会回填的分布 ===")
            for (m, u), n in sorted(model_url_counts.items(), key=lambda x: -x[1]):
                print(f"  {m:30s}  {u:55s}  {n:6d} 条")
            print()
            if unmatched_seens:
                print("=== 未匹配 msgid 指纹的 (model, msgid前缀) ===")
                for (m, p), n in sorted(unmatched_seens.items(), key=lambda x: -x[1]):
                    print(f"  {m:30s}  {p:30s}  {n:6d} 条")
        return stats

    # 批量 UPDATE：按 timestamp 走 WHERE
    # 只更新 provider='' 的 Claude 行
    cur = conn.cursor()
    for ts, (url, model) in msgid_index.items():
        # 看该 timestamp 对应的 Claude 行是否是空 provider
        row = cur.execute(
            "SELECT rowid FROM usage WHERE source='claude' AND timestamp=? AND provider=''",
            (ts,),
        ).fetchone()
        if row is None:
            # 要么已带 provider，要么不在 DB 里
            stats["skipped_tagged"] += 1
            continue
        cur.execute(
            "UPDATE usage SET provider=? WHERE source='claude' AND timestamp=? AND provider=''",
            (url, ts),
        )
        stats["updated"] += cur.rowcount

    conn.commit()

    if verbose:
        print(f"\n✓ 已回填 {stats['updated']} 行空 provider Claude 行")
        print(f"  跳过（已带标签或不在DB）: {stats['skipped_tagged']}")
        print()
        print("=== 回填后 Claude 各 (model, provider) 分布 ===")
        rows = cur.execute(
            """
            SELECT model,
                   CASE WHEN provider='' THEN '<空·未匹配>' ELSE provider END,
                   COUNT(*)
            FROM usage WHERE source='claude'
            GROUP BY model, 2 ORDER BY model, 3 DESC
            """
        ).fetchall()
        for m, p, n in rows:
            print(f"  {m:30s}  {p:55s}  {n:6d}")
        if stats["unmatched"] > 0 and unmatched_seens:
            print()
            print("=== 未匹配 msgid 指纹的可疑 (model, msgid前缀) ===")
            for (m, p), n in sorted(unmatched_seens.items(), key=lambda x: -x[1])[:10]:
                print(f"  {m:30s}  {p:30s}  {n:6d} 条")
    return stats


# MARK: - 路由窗提取（双信号源融合）
#
# VSCode Claude Code 扩展日志在每次启动时打印 ANTHROPIC_BASE_URL=...
# 每行带 UTC timestamp，这就是机器可读的供应商切换时间线。
# 但只覆盖 VSCode 跑过的会话；终端 Claude Code 的切换抓不到。
# 所以再叠加 settings.json 文件 mtime 作为补充信号：
#   每次 CCM 改 settings.json，触发的写入会更新 mtime。
# 我们用 mtime 作为该时刻的 baseURL 切换点。

import re as _re
from datetime import datetime, timezone as _tz
from pathlib import Path as _Path


def _parse_utc_iso(ts: str) -> datetime | None:
    """解析 'YYYY-MM-DDTHH:MM:SS.mmmZ' UTC ISO 字符串为 datetime."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _vclog_dir() -> _Path:
    """VSCode Claude Code 扩展日志目录."""
    return _Path.home() / "Library/Application Support/Code/logs"


def _extract_route_timeline_from_vclog() -> list[tuple[datetime, str]]:
    """扫 VSCode Claude Code 扩展日志，提取 (timestamp, baseURL) 切换点.

    日志按日期分目录: YYYYMMDDTHHMMSS/window<N>/exthost/Anthropic.claude-code/Claude VSCode.log
    每个文件内每行可能是:
      2026-07-22T06:27:18.386Z [DEBUG] ... ANTHROPIC_BASE_URL=https://api.xxx ...
      2026-07-22 14:27:21.133 [info] From claude: 2026-07-22T06:27:21.133Z [DEBUG] ... ANTHROPIC_BASE_URL=...

    匹配 UTC ISO timestamp + ANTHROPIC_BASE_URL=https://..., 按时间排序后返回.
    """
    pat_url = _re.compile(r"ANTHROPIC_BASE_URL=(https?://[^\s,;\"]+)")
    pat_utc = _re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+)Z")

    points: list[tuple[datetime, str]] = []
    log_dir = _vclog_dir()
    if not log_dir.exists():
        return points

    for log_file in log_dir.glob("*/window*/exthost/Anthropic.claude-code/Claude VSCode.log"):
        try:
            with open(log_file, "r", errors="ignore") as f:
                for line in f:
                    if "ANTHROPIC_BASE_URL=" not in line:
                        continue
                    m_url = pat_url.search(line)
                    if not m_url:
                        continue
                    url = m_url.group(1).rstrip(",;\"'")
                    m_ts = pat_utc.search(line)
                    if not m_ts:
                        continue
                    dt = _parse_utc_iso(m_ts.group(1) + "Z")
                    if dt is None:
                        continue
                    points.append((dt, url))
        except OSError:
            continue
    points.sort(key=lambda x: x[0])
    return points


def _extract_route_timeline_from_settings() -> list[tuple[datetime, str]]:
    """扫 ~/.claude/ 下所有 settings*.json 备份，提取 (mtime, baseURL) 切换点.

    CCM 切供应商时会 rewrite settings.json, 触发 mtime 更新.
    备份文件 (如 settings.json20260525) 是历史快照, mtime 即冻结时刻.
    """
    import os
    points: list[tuple[datetime, str]] = []
    claude_dir = _Path.home() / ".claude"

    candidates = []
    # 当前 settings.json
    main = claude_dir / "settings.json"
    if main.exists():
        candidates.append(main)
    # 备份文件: settings.json<yyyymmddHHMM> 或类似命名
    for f in claude_dir.iterdir():
        if not f.is_file():
            continue
        name = f.name
        if name.startswith("settings.json") and name != "settings.json":
            candidates.append(f)
        if name == "settings.local.json":
            candidates.append(f)

    for f in candidates:
        try:
            url = _read_claude_base_url(f)
            if not url:
                continue
            mtime = datetime.fromtimestamp(os.path.getmtime(f), tz=_tz.utc)
            points.append((mtime, url))
        except (OSError, ValueError):
            continue
    points.sort(key=lambda x: x[0])
    return points


def build_route_timeline(
    extra_points: list[tuple[datetime, str]] | None = None,
) -> list[tuple[datetime, datetime, str]]:
    """构造路由时间窗 [(start, end, baseURL), ...].

    融合 VSCode log + settings.json mtime 两源，按 timestamp 排序去重，
    相邻同 baseURL 合并，每个时间点的结束 = 下一个时间点的开始.
    最后一个结束 = datetime.now().

    Args:
        extra_points: 额外人工提供的切换点 (datetime, baseURL)，会与自动源合并排序

    Returns:
        时间窗列表，每个元素 (start_utc, end_utc, baseURL)
    """
    points: list[tuple[datetime, str]] = []
    points += _extract_route_timeline_from_vclog()
    points += _extract_route_timeline_from_settings()
    if extra_points:
        points += extra_points
    points.sort(key=lambda x: x[0])

    # 合并相邻同 baseURL
    merged_points: list[tuple[datetime, str]] = []
    for dt, url in points:
        if not merged_points or merged_points[-1][1] != url:
            merged_points.append((dt, url))
        else:
            # 同 baseURL, 更新时间戳到最新（避免同一供应商多次出现丢中间）
            pass

    if not merged_points:
        return []

    # 构造时间窗: [start_i, end_i) = [t_i, t_{i+1})
    windows: list[tuple[datetime, datetime, str]] = []
    now = datetime.now(tz=_tz.utc)
    for i, (start, url) in enumerate(merged_points):
        if i + 1 < len(merged_points):
            end = merged_points[i + 1][0]
        else:
            end = now
        windows.append((start, end, url))
    return windows


def _route_url_at(ts: datetime, windows: list[tuple[datetime, datetime, str]]) -> str:
    """二分查找 timestamp 落在哪个路由窗，返回对应 baseURL. 落不进任何窗返回空串."""
    import bisect
    starts = [w[0] for w in windows]
    idx = bisect.bisect_right(starts, ts) - 1
    if idx < 0:
        return ""
    start, end, url = windows[idx]
    if start <= ts < end:
        return url
    return ""


# MARK: - 双信号源回填

def reconcile_providers(
    conn: sqlite3.Connection,
    projects_dir: Path,
    dry_run: bool = False,
    only_msgid: bool = False,
    only_route: bool = False,
    prefer: str = "msgid",
    verbose: bool = True,
) -> dict:
    """双信号源回填 Claude 历史 provider.

    主信号: msg.id 格式指纹（请求级，精度高）
    辅信号: 路由窗（VSCode log + settings.json mtime 构造）

    prefer 三种模式:
    - 'strict': 主辅一致才写；冲突留空，记入 conflicts
    - 'msgid'  (默认): msgid 命中就用（哪怕与路由冲突），msgid 未命中才 fallback 到路由
    - 'route':   路由命中就用，路由未命中才 fallback 到 msgid
    only_msgid / only_route 互斥单信号模式，prefer 失效
    """
    stats = {
        "scanned": 0,
        "verified": 0,            # 主辅一致
        "msgid_only": 0,          # 仅 msgid 命中
        "route_only": 0,          # 仅路由窗命中
        "conflict": 0,            # 主辅冲突总数
        "conflict_written": 0,    # prefer=msgid/route 时冲突仍写入的数量
        "unmatched": 0,           # 都没命中
        "updated": 0,             # 实际写入 DB 的行
        "skipped_tagged": 0,      # DB 已有 provider 非空
        "prefer": prefer,
        "dry_run": dry_run,
    }

    # 1) 构造路由窗
    windows = build_route_timeline()
    windows_summary = [(w[0].isoformat(), w[1].isoformat(), w[2]) for w in windows]
    stats["route_windows"] = windows_summary
    if verbose:
        print(f"=== 路由时间窗（共 {len(windows)} 个，prefer = {prefer}）===")
        for s, e, u in windows:
            print(f"  {s.strftime('%Y-%m-%d %H:%M UTC')} ~ {e.strftime('%Y-%m-%d %H:%M UTC')}  →  {u}")
        print()

    # 2) 扫 JSONL 提取 (timestamp, model, msgid)
    import json
    msgid_index: dict[str, tuple[str, str]] = {}  # ts -> (model, msgid)
    projects_path = Path(projects_dir)
    if not projects_path.exists():
        if verbose:
            print(f"⚠️  Claude projects 目录不存在: {projects_path}")
        return stats

    for project_dir in sorted(projects_path.iterdir()):
        if not project_dir.is_dir():
            continue
        for f in sorted(project_dir.glob("*.jsonl")):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    for line in fp:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            d = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(d, dict) or d.get("type") != "assistant":
                            continue
                        msg = d.get("message")
                        if not isinstance(msg, dict):
                            continue
                        model = msg.get("model") or ""
                        if not model or model in {"<synthetic>", ""}:
                            continue
                        ts = d.get("timestamp", "")
                        if not ts:
                            continue
                        mid = msg.get("id", "")
                        stats["scanned"] += 1
                        msgid_index[ts] = (model, mid)
            except OSError:
                continue

    if verbose:
        print(f"扫描 JSONL {stats['scanned']} 条 assistant 行\n")

    # 3) 双信号源决策
    conflicts: list[tuple[str, str, str, str]] = []  # (ts, model, msgid_url, route_url)
    write_plan: list[tuple[str, str, str]] = []        # (ts, url_to_write, decision_tag)

    for ts, (model, mid) in msgid_index.items():
        ts_dt = _parse_utc_iso(ts)
        if ts_dt is None:
            continue
        msgid_url = "" if only_route else _classify_provider_from_msgid(mid, model)
        route_url = "" if only_msgid else _route_url_at(ts_dt, windows)

        # 单信号模式
        if only_msgid:
            if msgid_url:
                stats["msgid_only"] += 1
                write_plan.append((ts, msgid_url, "msgid_only"))
            else:
                stats["unmatched"] += 1
            continue
        if only_route:
            if route_url:
                stats["route_only"] += 1
                write_plan.append((ts, route_url, "route_only"))
            else:
                stats["unmatched"] += 1
            continue

        # 双信号源决策
        if msgid_url and route_url and msgid_url == route_url:
            stats["verified"] += 1
            write_plan.append((ts, msgid_url, "verified"))
        elif msgid_url and route_url and msgid_url != route_url:
            stats["conflict"] += 1
            conflicts.append((ts, model, msgid_url, route_url))
            if prefer == "msgid":
                write_plan.append((ts, msgid_url, "conflict_prefer_msgid"))
                stats["conflict_written"] += 1
            elif prefer == "route":
                write_plan.append((ts, route_url, "conflict_prefer_route"))
                stats["conflict_written"] += 1
            # strict 模式不写
        elif msgid_url and not route_url:
            stats["msgid_only"] += 1
            write_plan.append((ts, msgid_url, "msgid_only"))
        elif route_url and not msgid_url:
            stats["route_only"] += 1
            write_plan.append((ts, route_url, "route_only"))
        else:
            stats["unmatched"] += 1

    # 4) 生成 dry-run 报告或写库
    if dry_run:
        write_dist: dict[tuple[str, str], int] = {}
        for ts, url, tag in write_plan:
            m = msgid_index[ts][0]
            write_dist[(m, tag)] = write_dist.get((m, tag), 0) + 1
        if verbose:
            print("=== 决策分布 ===")
            print(f"  verified (主辅一致)        : {stats['verified']}   ✅ 写入")
            print(f"  msgid_only (仅指纹命中)    : {stats['msgid_only']}   ✅ 写入")
            print(f"  route_only (仅路由命中)    : {stats['route_only']}   ✅ 写入")
            print(f"  conflict (主辅冲突)        : {stats['conflict']}")
            print(f"    ↳ prefer={prefer} 写入   : {stats['conflict_written']}   {'✅' if stats['conflict_written'] else '❌'}")
            print(f"  unmatched (都未命中)       : {stats['unmatched']}   ❌ 留空")
            print()
            print("=== 将要写入的 (model, decision) 分布 ===")
            for (m, t), n in sorted(write_dist.items(), key=lambda x: (-x[1], x[0])):
                print(f"  {m:30s}  {t:22s}  {n:6d}")
            print()
            if conflicts:
                from collections import Counter as _C
                conflict_by_model = _C()
                for ts, m, mu, ru in conflicts:
                    conflict_by_model[(m, mu, ru)] += 1
                print(f"=== 冲突聚合（按 model+msgid_url+route_url，共 {len(conflicts)} 条）===")
                for (m, mu, ru), n in sorted(conflict_by_model.items(), key=lambda x: -x[1])[:25]:
                    if prefer == "msgid": chosen = mu
                    elif prefer == "route": chosen = ru
                    else: chosen = "<skip>"
                    print(f"  {m:22s}  msgid={mu:48s} route={ru:48s}  n={n:5d}  → 写 {chosen}")
                print()
            print("(Dry-run 模式: 未写入 DB. 去掉 --dry-run 实际执行)")
        return stats

    # 实际写入
    cur = conn.cursor()
    for ts, url, _tag in write_plan:
        row = cur.execute(
            "SELECT rowid FROM usage WHERE source='claude' AND timestamp=? AND provider=''",
            (ts,),
        ).fetchone()
        if row is None:
            stats["skipped_tagged"] += 1
            continue
        cur.execute(
            "UPDATE usage SET provider=? WHERE source='claude' AND timestamp=? AND provider=''",
            (url, ts),
        )
        stats["updated"] += cur.rowcount
    conn.commit()

    if verbose:
        print(f"\n✓ 已更新 {stats['updated']} 行空 provider Claude 行")
        print(f"  跳过（已带标签或不在 DB）: {stats['skipped_tagged']}")
        print(f"  决策明细: verified={stats['verified']} msgid_only={stats['msgid_only']} "
              f"route_only={stats['route_only']} conflict={stats['conflict']} "
              f"(prefer={prefer} 写入 {stats['conflict_written']}) unmatched={stats['unmatched']}")
        if conflicts and prefer == "strict":
            print(f"\n⚠️  {len(conflicts)} 条主辅冲突未写入（strict 模式保留空）:")
            for ts, m, mu, ru in conflicts[:30]:
                print(f"  {ts}  {m:22s}  msgid→{mu}  vs  route→{ru}")
            if len(conflicts) > 30:
                print(f"  ... 还有 {len(conflicts) - 30} 条")
    return stats


# MARK: - 去重（修复历史多次 sync 累积的重复行）

def dedupe_claude_rows(
    conn: sqlite3.Connection,
    dry_run: bool = False,
    verbose: bool = True,
) -> dict:
    """去掉 usage 表里 Claude 同 (source_file, timestamp) 的重复行.

    背景: 早期 sync 在某些 force=True 路径里 DELETE+INSERT 不对称,
    导致同一 assistant 行被入库 2-8 次。token 数被相应放大，
    会让浮窗"今日总量"等指标虚高。

    策略: 保留每个 (source_file, timestamp) 的最小 rowid 行（最早插入的那条）,
          其余 Claude 重复行删除. ZCode 走 (source, ext_id) UNIQUE 索引, 无重复, 不动.

    返回:
    {
      before_rows, after_rows, deleted_rows,
      before_total_tokens, after_total_tokens,
      dup_groups_by_count: {2: N, 3: N, ...}
    }
    """
    stats = {
        "before_rows": 0,
        "after_rows": 0,
        "deleted_rows": 0,
        "before_total_tokens": 0,
        "after_total_tokens": 0,
        "dry_run": dry_run,
        "dup_groups_by_count": {},
    }

    cur = conn.cursor()

    # 1) 当前总量
    stats["before_rows"] = cur.execute(
        "SELECT COUNT(*) FROM usage WHERE source='claude'"
    ).fetchone()[0]
    stats["before_total_tokens"] = cur.execute(
        "SELECT COALESCE(SUM(total_context + output_tokens), 0) FROM usage"
    ).fetchone()[0]

    # 2) 重复倍率分布
    rows = cur.execute("""
        SELECT n, COUNT(*) AS groups FROM (
          SELECT COUNT(*) AS n FROM usage WHERE source='claude'
          GROUP BY source_file, timestamp
        ) GROUP BY n ORDER BY n
    """).fetchall()
    stats["dup_groups_by_count"] = {n: g for n, g in rows}

    # 3) 待删除行数（仅模拟）
    to_delete = cur.execute("""
        SELECT COUNT(*) FROM usage
        WHERE source='claude' AND rowid NOT IN (
          SELECT MIN(rowid) FROM usage WHERE source='claude'
          GROUP BY source_file, timestamp
        )
    """).fetchone()[0]
    stats["deleted_rows"] = to_delete

    if verbose:
        print(f"=== Claude 重复倍率分布 ===")
        total_dup_groups = sum(g for n, g in rows if n > 1)
        for n, g in rows:
            mark = "❌ 重复" if n > 1 else "✓ 唯一"
            print(f"  倍率 n={n}: {g:6d} 组  {mark}")
        print(f"  (其中 {total_dup_groups} 组有重复，总重复行约 {to_delete:,d})")
        print()
        print(f"=== 修复影响 ===")
        print(f"  Claude 行数         : {stats['before_rows']:,d} -> {stats['before_rows'] - to_delete:,d}")
        print(f"  待删除（重复）     : {to_delete:,d}")
        print(f"  总 token (全表)   : {stats['before_total_tokens']:,d} ({stats['before_total_tokens']/1e9:.3f}B)")

    if dry_run:
        # 模拟去重后的 token 数
        after_total = cur.execute("""
            SELECT COALESCE(SUM(total_context + output_tokens), 0) FROM usage
            WHERE (source='claude' AND rowid IN (
                    SELECT MIN(rowid) FROM usage WHERE source='claude'
                    GROUP BY source_file, timestamp
                  )) OR source != 'claude'
        """).fetchone()[0]
        stats["after_total_tokens"] = after_total
        stats["after_rows"] = stats["before_rows"] - to_delete
        if verbose:
            print(f"  去重后 token      : {after_total:,d} ({after_total/1e9:.3f}B)")
            print(f"  (Dry-run 模式: 未删除. 去掉 --dry-run 实际执行)")
        return stats

    # 4) 实际去重：DELETE 重复行（保留最小 rowid）
    # 用临时表收集最小 rowid，再 DELETE 不在其中的行
    cur.execute("""
        CREATE TEMP TABLE IF NOT EXISTS _keep_rowids AS
        SELECT MIN(rowid) AS rid FROM usage WHERE source='claude'
        GROUP BY source_file, timestamp
    """)
    deleted = cur.execute("""
        DELETE FROM usage WHERE source='claude' AND rowid NOT IN (SELECT rid FROM _keep_rowids)
    """).rowcount
    cur.execute("DROP TABLE _keep_rowids")
    conn.commit()

    # 5) 修复后统计
    stats["after_rows"] = cur.execute(
        "SELECT COUNT(*) FROM usage WHERE source='claude'"
    ).fetchone()[0]
    stats["after_total_tokens"] = cur.execute(
        "SELECT COALESCE(SUM(total_context + output_tokens), 0) FROM usage"
    ).fetchone()[0]
    stats["deleted_rows"] = deleted

    if verbose:
        print(f"\n✓ 已删除 {deleted:,d} 行重复 Claude 行")
        print(f"  Claude 行数       : {stats['before_rows']:,d} → {stats['after_rows']:,d}")
        print(f"  总 token (全表) : {stats['before_total_tokens']:,d} → {stats['after_total_tokens']:,d}")
        print(f"                   ({stats['before_total_tokens']/1e9:.3f}B → {stats['after_total_tokens']/1e9:.3f}B)")
    return stats
