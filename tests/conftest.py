"""pytest 公共 fixture: 内存 SQLite + 预置数据."""
import sqlite3
from pathlib import Path

import pytest

from ccusage.db import connect


@pytest.fixture
def conn():
    """内存数据库 (每个测试独立), 已建表 + 迁移完成."""
    c = connect(Path(":memory:"))
    yield c
    c.close()


def _insert(conn, *, date, hour, model, total, source="claude", ext_id=""):
    """测试辅助: 插一条 usage 记录."""
    conn.execute(
        """INSERT INTO usage
        (timestamp, local_date, local_hour, model,
         input_tokens, cache_creation_input_tokens,
         cache_read_input_tokens, output_tokens, total_context,
         msg_count, source_file, source, ext_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,'',?,?)""",
        (f"{date}T00:00:00Z", date, hour, model,
         total, 0, 0, 0, total, 1, source, ext_id),
    )


@pytest.fixture
def seeded_conn(conn):
    """带 3 天混合数据的连接: 2026-07-06 ~ 07-08, claude + zcode."""
    _insert(conn, date="2026-07-06", hour=10, model="glm-5.2", total=1000, source="claude")
    _insert(conn, date="2026-07-07", hour=14, model="glm-5.2", total=2000, source="claude")
    _insert(conn, date="2026-07-07", hour=15, model="GLM-5.2", total=500, source="zcode", ext_id="zc_1")
    _insert(conn, date="2026-07-08", hour=9, model="GLM-5.2", total=800, source="zcode", ext_id="zc_2")
    conn.commit()
    yield conn
