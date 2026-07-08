"""炫酷 HTML 报告生成器 (单文件, 离线可看, 仅依赖 ECharts/html2canvas/jspdf CDN).

本模块只负责: 拉数据 -> 组装 payload -> 拼接 HTML 骨架.
样式与脚本已抽到 report_html_css.py / report_html_js.py (各自 <800 行).

数据契约 (前端零聚合, 切 range 只换数据源):
- payload.range_default : 初始高亮的 range key
- payload.ranges[key]   : today/week/month/all 四档, 同构
- payload.calendar      : 全量日历热力图 (不随 range 变)
- payload.sparkline     : 近 14 天 (Hero 迷你趋势)
- payload.gamification  : streak / 周环比 / 工作日周末 / 最卷一天 (全量)
"""
from __future__ import annotations

import html
import json
import sqlite3
from pathlib import Path

from .aggregate import (
    DateRange,
    active_projects,
    by_model,
    daily_by_model,
    daily_totals,
    date_range_span,
    hourly_distribution,
    now_in_sh,
    peak_day,
    resolve_range,
    source_breakdown,
    streak,
    weekday_vs_weekend,
    weekly_by_model,
    week_over_week,
)
from .report_html_css import CSS
from .report_html_js import JS

# 全量范围 (与 aggregate.resolve_range('all') 一致)
_ALL_START = "2000-01-01"
_ALL_END = "2099-12-31"

# 模型配色板 - 鲜艳渐变色, 用于所有图表
PALETTE = [
    "#a78bfa", "#22d3ee", "#34d399", "#fbbf24", "#f472b6",
    "#60a5fa", "#fb7185", "#a3e635", "#f97316", "#2dd4bf",
    "#c084fc", "#facc15",
]


def model_color_map(models: list[str]) -> dict[str, str]:
    """给模型列表分配稳定的颜色 (按名字排序, 跨 range 一致)."""
    return {m: PALETTE[i % len(PALETTE)] for i, m in enumerate(sorted(models))}


def _build_range(
    conn: sqlite3.Connection, dr: DateRange, cmap: dict[str, str],
    source: str = "all",
) -> dict:
    """为一个 (DateRange, source) 跑全部聚合, 组装成前端同构结构.

    daily/weekly series 用 dict 预索引取值 (修原 O(N^2) 嵌套扫描).
    """
    model_rows = by_model(conn, dr.start, dr.end, source)
    daily_rows = daily_by_model(conn, dr.start, dr.end, source)
    daily_total_rows = daily_totals(conn, dr.start, dr.end, source)
    weekly_rows = weekly_by_model(conn, dr.start, dr.end, source)
    hourly_rows = hourly_distribution(conn, dr.start, dr.end, source)
    projects = active_projects(conn, dr.start, dr.end, limit=8, source=source)

    # 用全局模型顺序配色, 保证切 range 时同一模型颜色不变
    models_in_range = sorted(cmap.keys())
    total_tokens = sum(m["total"] for m in model_rows)
    total_input = sum(m["input"] for m in model_rows)
    total_output = sum(m["output"] for m in model_rows)
    total_cache = sum(m["cache_write"] + m["cache_read"] for m in model_rows)
    total_msgs = sum(m["msgs"] for m in model_rows)

    # 每日堆叠柱: 预索引 (date, model) -> total
    dates_sorted = sorted({d["date"] for d in daily_rows})
    daily_lookup = {(d["date"], d["model"]): d["total"] for d in daily_rows}
    daily_series = [
        {
            "name": model, "type": "bar", "stack": "total",
            "emphasis": {"focus": "series"},
            "data": [daily_lookup.get((date, model), 0) for date in dates_sorted],
            "itemStyle": {"color": cmap[model], "borderRadius": [3, 3, 0, 0]},
            "barWidth": "60%",
        }
        for model in models_in_range
    ]

    pie_data = [{"name": m["model"], "value": m["total"]} for m in model_rows]

    trend = {
        "dates": [d["date"] for d in daily_total_rows],
        "input": [d["input"] for d in daily_total_rows],
        "output": [d["output"] for d in daily_total_rows],
    }

    # 每周分组柱: 预索引 (week, model) -> total
    weeks_sorted = sorted({w["week"] for w in weekly_rows})
    weekly_lookup = {(w["week"], w["model"]): w["total"] for w in weekly_rows}
    weekly_series = [
        {
            "name": model, "type": "bar",
            "data": [weekly_lookup.get((wk, model), 0) for wk in weeks_sorted],
            "itemStyle": {"color": cmap[model], "borderRadius": [4, 4, 0, 0]},
        }
        for model in models_in_range
    ]

    hmap = {h["hour"]: h["total"] for h in hourly_rows}
    hmax = max(hmap.values()) if hmap else 1
    radar = {
        "indicator": [{"name": f"{h:02d}", "max": hmax} for h in range(24)],
        "values": [hmap.get(h, 0) for h in range(24)],
    }

    projects_d = {
        "names": [p["project"] for p in projects],
        "values": [p["total"] for p in projects],
    }

    table = []
    for m in model_rows:
        pct = (m["total"] / total_tokens * 100) if total_tokens else 0
        table.append({
            "model": m["model"], "color": cmap[m["model"]],
            "input": m["input"], "cache_write": m["cache_write"],
            "cache_read": m["cache_read"], "output": m["output"],
            "total": m["total"], "msgs": m["msgs"], "pct": round(pct, 1),
        })

    kpi = {
        "total_tokens": total_tokens, "total_input": total_input,
        "total_output": total_output, "total_cache": total_cache,
        "total_msgs": total_msgs,
        "model_count": len({m["model"] for m in model_rows}),
        "active_days": len(daily_total_rows),
    }

    return {
        "label": dr.label, "start": dr.start, "end": dr.end,
        "kpi": kpi,
        "daily": {"dates": dates_sorted, "series": daily_series},
        "pie": pie_data, "trend": trend,
        "weekly": {"weeks": weeks_sorted, "series": weekly_series},
        "radar": radar, "projects": projects_d, "table": table,
    }


def _infer_range_key(dr: DateRange) -> str:
    """从 DateRange 反推 today/week/last_week/month/all key (用于初始高亮 tab)."""
    for key in ("today", "week", "last_week", "month", "all"):
        r = resolve_range(key)
        if r.start == dr.start and r.end == dr.end:
            return key
    return "week"


_RANGE_KEYS = ("today", "week", "last_week", "month", "all")
_SOURCE_KEYS = ("all", "claude", "zcode")


def render(
    conn: sqlite3.Connection, dr: DateRange, output_path: Path,
    source: str = "all",
) -> Path:
    """生成 HTML 报告, 返回文件路径.

    source: 报告初始高亮的来源 ('all'/'claude'/'zcode'). 前端三个 source 都可切.
    """
    # 全局配色: 基于全量 (all source) 模型, 跨 source/range 稳定
    all_model_rows = by_model(conn, _ALL_START, _ALL_END)
    all_models = sorted({m["model"] for m in all_model_rows})
    cmap = model_color_map(all_models)

    # 日历热力图 + sparkline: 每个 source 一份 (前端切 source 时日历也跟着变)
    calendars = {}
    sparklines = {}
    for sk in _SOURCE_KEYS:
        daily = daily_totals(conn, _ALL_START, _ALL_END, sk)
        cd = [{"name": d["date"], "value": d["total"]} for d in daily]
        calendars[sk] = cd
        sparklines[sk] = cd[-14:]

    # ranges: 按 source × range 预计算 (前端切 source/range 都是零聚合换数据源)
    ranges = {
        sk: {rk: _build_range(conn, resolve_range(rk), cmap, sk) for rk in _RANGE_KEYS}
        for sk in _SOURCE_KEYS
    }

    # 游戏化指标: 每个 source 一份 (切到 zcode 时打卡/环比应是 zcode 自己的)
    gamification = {}
    for sk in _SOURCE_KEYS:
        gamification[sk] = {
            "streak": streak(conn, sk),
            "wow": week_over_week(conn, sk),
            "weekday_weekend": weekday_vs_weekend(conn, _ALL_START, _ALL_END),
            "peak": peak_day(conn, _ALL_START, _ALL_END, sk),
        }

    span = date_range_span(conn)
    payload = {
        "range_default": _infer_range_key(dr),
        "source_default": source if source in _SOURCE_KEYS else "all",
        "range_start": dr.start,
        "range_end": dr.end,
        "data_start": span[0] or dr.start,
        "data_end": span[1] or dr.end,
        "generated_at": now_in_sh().strftime("%Y-%m-%d %H:%M:%S"),
        "calendars": calendars,
        "sparklines": sparklines,
        "gamification": gamification,
        "models": all_models,
        "colors": cmap,
        "ranges": ranges,
    }

    html_content = _build_html(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_content, encoding="utf-8")
    return output_path


# ============ HTML 骨架 (唯一的 f-string, 花括号需转义) ============


def _build_html(p: dict) -> str:
    payload_json = json.dumps(p, ensure_ascii=False)
    sd = p["source_default"]
    rd = p["range_default"]
    title = html.escape(
        f"Claude Code / ZCode 用量 · {p['ranges'][sd][rd]['label']}"
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<script>(function(){{try{{var t=localStorage.getItem('ccusage-theme')||'dark';document.documentElement.setAttribute('data-theme',t);}}catch(e){{}}}})();</script>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/jspdf@2.5.1/dist/jspdf.umd.min.js"></script>
<style>
{CSS}
</style>
</head>
<body>
<div class="wrap">

  <header class="hero reveal">
    <div class="badge">◆ CLAUDE CODE / ZCODE USAGE</div>
    <h1>{title}</h1>
    <div class="meta">数据范围 <b id="m-span"></b> · 生成于 <b id="m-gen"></b> · Asia/Shanghai</div>
    <div class="hero-stats">
      <div class="hero-total">
        <div class="hero-label">区间总用量</div>
        <div class="hero-num" id="hero-num" data-raw="0">0</div>
        <div class="hero-sub" id="hero-sub"></div>
      </div>
      <div id="spark" class="hero-spark"></div>
      <div class="hero-pulse"><span class="pulse-dot"></span><span id="hero-pulse-text">连续打卡</span></div>
    </div>
    <div class="toolbar">
      <div class="source-tabs" id="source-tabs">
        <button type="button" data-source="all">全部</button>
        <button type="button" data-source="claude">Claude</button>
        <button type="button" data-source="zcode">ZCode</button>
      </div>
      <div class="range-tabs" id="range-tabs">
        <button type="button" data-range="today">今日</button>
        <button type="button" data-range="week">本周</button>
        <button type="button" data-range="last_week">上周</button>
        <button type="button" data-range="month">本月</button>
        <button type="button" data-range="all">全部</button>
      </div>
      <div class="actions">
        <button id="theme-toggle" class="btn-icon" type="button" title="切换明暗主题"><span id="theme-icon">🌙</span></button>
        <button id="btn-png" class="btn btn-png" type="button"><span class="icon">📸</span><span class="label">导出 PNG</span></button>
        <button id="btn-pdf" class="btn btn-pdf" type="button"><span class="icon">📄</span><span class="label">导出 PDF</span></button>
      </div>
    </div>
  </header>

  <div class="streak-grid reveal" id="streak-grid"></div>
  <div class="kpi-grid reveal" id="kpi"></div>

  <div class="grid reveal">
    <div class="card col-12">
      <h2><span class="dot"></span>用量热力图</h2>
      <div class="desc">每天的总 Token 用量 (颜色越亮 = 用量越大)</div>
      <div id="chart-calendar" class="chart" style="height:240px"></div>
    </div>
  </div>

  <div class="grid reveal">
    <div class="card col-8">
      <h2><span class="dot"></span>每日 Token · 按模型堆叠</h2>
      <div class="desc">每天的 Token 用量, 按模型分层 · 点明细表模型行可聚焦</div>
      <div id="chart-daily" class="chart" style="height:380px"></div>
    </div>
    <div class="card col-4">
      <h2><span class="dot"></span>模型分布</h2>
      <div class="desc">各模型总 Token 占比</div>
      <div id="chart-pie" class="chart" style="height:380px"></div>
    </div>
  </div>

  <div class="grid reveal">
    <div class="card col-6">
      <h2><span class="dot"></span>输入 / 输出趋势</h2>
      <div class="desc">每日的输入与输出 Token (渐变填充)</div>
      <div id="chart-trend" class="chart" style="height:320px"></div>
    </div>
    <div class="card col-6">
      <h2><span class="dot"></span>每周用量 · 按模型</h2>
      <div class="desc">按周聚合, 分组柱状对比</div>
      <div id="chart-weekly" class="chart" style="height:320px"></div>
    </div>
  </div>

  <div class="grid reveal">
    <div class="card col-6">
      <h2><span class="dot"></span>活跃时段</h2>
      <div class="desc">24 小时内的使用分布 (雷达图)</div>
      <div id="chart-radar" class="chart" style="height:340px"></div>
    </div>
    <div class="card col-6">
      <h2><span class="dot"></span>Top 项目</h2>
      <div class="desc">在哪些项目目录里用得最多</div>
      <div id="chart-proj" class="chart" style="height:340px"></div>
    </div>
  </div>

  <div class="grid reveal">
    <div class="card col-12">
      <h2><span class="dot"></span>模型明细</h2>
      <div class="desc">每个模型的完整 Token 统计 · 点击行可聚焦该模型 (再点取消)</div>
      <div style="max-height:480px;overflow:auto;margin-top:8px">
        <table id="tbl">
          <thead>
            <tr>
              <th>模型</th>
              <th class="num">输入</th>
              <th class="num">缓存写</th>
              <th class="num">缓存读</th>
              <th class="num">输出</th>
              <th class="num">总上下文</th>
              <th class="num">消息</th>
              <th class="num">占比</th>
            </tr>
          </thead>
          <tbody id="tbody"></tbody>
        </table>
      </div>
    </div>
  </div>

  <footer>Generated by <code>cc-usage</code> · 数据来自 <code>~/.claude/projects</code></footer>
</div>

<div id="export-overlay">
  <div class="ring"></div>
  <div class="text" id="export-text">正在渲染, 请稍候...</div>
</div>
<div id="toast"></div>

<script>
const P = {payload_json};
{JS}
</script>
</body>
</html>
"""
