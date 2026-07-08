"""report_text 测试: 防 Bug 1 回归 (脏日期不崩) + source 标注."""
from ccusage.aggregate import DateRange
from ccusage.report_text import render


class TestReportTextRobustness:
    def test_dirty_date_does_not_crash(self, seeded_conn):
        """Bug 1 回归: 异常日期格式不应让 render 崩溃.

        注入一条 local_date 在查询区间内的脏数据, 确保它会走到日期解析逻辑,
        验证 except 分支不再因 wd 未定义而 UnboundLocalError.
        """
        seeded_conn.execute(
            "INSERT INTO usage (timestamp, local_date, local_hour, model, "
            "input_tokens, cache_creation_input_tokens, cache_read_input_tokens, "
            "output_tokens, total_context, msg_count, source_file, source) "
            "VALUES ('x','2026-07-07',0,'m',1,0,0,1,2,1,'','claude')"
        )
        seeded_conn.commit()
        dr = DateRange("2026-07-06", "2026-07-08", "测试")
        # 关键: 不应抛 UnboundLocalError, 正常返回字符串
        out = render(seeded_conn, dr)
        assert isinstance(out, str)
        assert "2026-07-07" in out

    def test_source_all_shows_breakdown(self, seeded_conn):
        """source=all 时标题区显示 Claude/ZCode 占比."""
        dr = DateRange("2000-01-01", "2099-12-31", "全部")
        out = render(seeded_conn, dr, source="all")
        assert "来源" in out

    def test_source_zcode_filters(self, seeded_conn):
        """source=zcode 只显示 zcode 模型."""
        dr = DateRange("2000-01-01", "2099-12-31", "全部")
        out = render(seeded_conn, dr, source="zcode")
        assert "ZCode" in out
