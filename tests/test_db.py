"""db 层测试: ZCode 增量同步幂等性."""
import sqlite3
from pathlib import Path

from ccusage.db import connect, sync_zcode


def _make_zcode_db(path: Path):
    """造一个最小 ZCode schema + 3 条 model_usage 的假库."""
    c = sqlite3.connect(str(path))
    c.executescript("""
        CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT);
        CREATE TABLE model_usage (
            id TEXT PRIMARY KEY, session_id TEXT, started_at INTEGER,
            completed_at INTEGER, model_id TEXT, status TEXT,
            input_tokens INTEGER, output_tokens INTEGER,
            cache_creation_input_tokens INTEGER, cache_read_input_tokens INTEGER,
            computed_total_tokens INTEGER, tool_call_count INTEGER
        );
        INSERT INTO session VALUES ('s1', '/home/test/proj');
        INSERT INTO model_usage VALUES
            ('zc_a','s1',1783000000000,1783000001000,'GLM-5.2','completed',
             100,10,0,0,110,1),
            ('zc_b','s1',1783000086000,1783000087000,'GLM-5.2','completed',
             200,20,0,0,220,2),
            ('zc_c','s1',1783000172000,1783000173000,'GLM-5.2','cancelled',
             999,0,0,0,999,0);
    """)
    c.commit()
    c.close()


class TestSyncZcode:
    def test_first_sync_inserts_completed_only(self, tmp_path):
        """首次同步: 只收 completed, cancelled 跳过."""
        zc = tmp_path / "zcode.db"
        _make_zcode_db(zc)
        conn = connect(tmp_path / "usage.db")
        stats = sync_zcode(conn, zc, verbose=False)
        assert stats["new"] == 2  # zc_a, zc_b (zc_c cancelled 跳过)
        assert stats["errors"] == 0
        conn.close()

    def test_second_sync_is_idempotent(self, tmp_path):
        """第二次同步无新增: 水位线之后没新数据 → new=0."""
        zc = tmp_path / "zcode.db"
        _make_zcode_db(zc)
        conn = connect(tmp_path / "usage.db")
        sync_zcode(conn, zc, verbose=False)
        stats2 = sync_zcode(conn, zc, verbose=False)
        assert stats2["new"] == 0
        conn.close()

    def test_missing_db_skips_gracefully(self, tmp_path):
        """ZCode 库不存在时不报错, 返回零."""
        conn = connect(tmp_path / "usage.db")
        stats = sync_zcode(conn, tmp_path / "nope.db", verbose=False)
        assert stats["new"] == 0
        assert stats["errors"] == 0
        conn.close()
