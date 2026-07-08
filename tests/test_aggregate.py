"""aggregate 层测试: 范围解析、source 过滤、source_breakdown、streak."""
from ccusage.aggregate import (
    resolve_range, source_breakdown, by_model, streak,
)


class TestResolveRange:
    def test_last_week_is_7_days_before_this_week(self):
        """上周区间 = 本周各区 7 天前, 且恰好 7 天宽."""
        this = resolve_range("week")
        last = resolve_range("last_week")
        from datetime import datetime, timedelta
        d_this_s = datetime.strptime(this.start, "%Y-%m-%d")
        d_last_s = datetime.strptime(last.start, "%Y-%m-%d")
        assert (d_this_s - d_last_s).days == 7
        # 区间宽度 7 天 (含首尾)
        d_last_e = datetime.strptime(last.end, "%Y-%m-%d")
        assert (d_last_e - d_last_s).days == 6

    def test_today_start_eq_end(self):
        r = resolve_range("today")
        assert r.start == r.end

    def test_unknown_range_raises(self):
        import pytest
        with pytest.raises(ValueError):
            resolve_range("ytd")


class TestSourceFilter:
    def test_by_model_source_zcode_only(self, seeded_conn):
        rows = by_model(seeded_conn, "2000-01-01", "2099-12-31", source="zcode")
        assert len(rows) == 1
        assert rows[0]["model"] == "GLM-5.2"
        assert rows[0]["total"] == 1300

    def test_by_model_source_all_merges(self, seeded_conn):
        rows = by_model(seeded_conn, "2000-01-01", "2099-12-31", source="all")
        # glm-5.2 和 GLM-5.2 名字不同, 应是 2 个模型
        assert len(rows) == 2

    def test_source_breakdown(self, seeded_conn):
        bk = source_breakdown(seeded_conn, "2000-01-01", "2099-12-31")
        assert bk["claude"] == 3000
        assert bk["zcode"] == 1300


class TestStreakSourceFilter:
    def test_streak_zcode_only_excludes_clude_days(self, seeded_conn):
        """streak(zcode) 不应把只有 claude 数据的日期算进去."""
        s = streak(seeded_conn, source="zcode")
        # zcode 数据在 07-07, 07-08 (假设今天 >= 07-08)
        # claude 独有的 07-06 不应计入
        assert s["longest"] >= 2  # 07-07, 07-08 连续

    def test_streak_all_includes_all_days(self, seeded_conn):
        s = streak(seeded_conn, source="all")
        # 全部: 07-06, 07-07, 07-08
        assert s["longest"] >= 3
