"""炫酷 HTML 报告生成器.

基于 ECharts (CDN), 深色仪表盘风格, 包含:
- KPI 卡片 (玻璃拟态)
- 日历热力图 (GitHub 风格)
- 每日堆叠柱状图 (按模型)
- 模型分布环形图
- 趋势折线 (渐变填充)
- 每周分组柱状
- 时段雷达图
- 明细表格

输出单文件 HTML, 离线可看 (除 CDN).
"""
from __future__ import annotations

import html
import json
import sqlite3
from datetime import datetime
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
    weekly_by_model,
)


# 模型配色板 - 鲜艳渐变色, 用于所有图表
PALETTE = [
    "#a78bfa",  # 紫
    "#22d3ee",  # 青
    "#34d399",  # 翠绿
    "#fbbf24",  # 琥珀
    "#f472b6",  # 粉
    "#60a5fa",  # 蓝
    "#fb7185",  # 玫瑰
    "#a3e635",  # 柠檬
    "#f97316",  # 橙
    "#2dd4bf",  # 蓝绿
    "#c084fc",  # 浅紫
    "#facc15",  # 黄
]


def model_color_map(models: list[str]) -> dict[str, str]:
    """给模型列表分配稳定的颜色."""
    return {m: PALETTE[i % len(PALETTE)] for i, m in enumerate(sorted(models))}


def fmt_num(n: int) -> str:
    return f"{n:,}"


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def render(
    conn: sqlite3.Connection,
    dr: DateRange,
    output_path: Path,
) -> Path:
    """生成 HTML 报告, 返回文件路径."""
    # ===== 拉取所有数据 =====
    model_rows = by_model(conn, dr.start, dr.end)
    daily_rows = daily_by_model(conn, dr.start, dr.end)
    daily_total_rows = daily_totals(conn, dr.start, dr.end)
    weekly_rows = weekly_by_model(conn, dr.start, dr.end)
    hourly_rows = hourly_distribution(conn, dr.start, dr.end)
    projects = active_projects(conn, dr.start, dr.end, limit=8)

    all_models = sorted({m["model"] for m in model_rows})
    cmap = model_color_map(all_models)

    # ===== KPI =====
    total_tokens = sum(m["total"] for m in model_rows)
    total_input = sum(m["input"] for m in model_rows)
    total_output = sum(m["output"] for m in model_rows)
    total_cache = sum(m["cache_write"] + m["cache_read"] for m in model_rows)
    total_msgs = sum(m["msgs"] for m in model_rows)
    active_days = len(daily_total_rows)

    data_span = date_range_span(conn)

    # ===== 日历热力图数据 =====
    # 用全量数据画热力图 (即使当前范围是 week/month), 更好看
    all_daily = daily_totals(conn, "2000-01-01", "2099-12-31")
    calendar_data = [
        {"name": d["date"], "value": d["total"]} for d in all_daily
    ]

    # ===== 每日堆叠柱状图 =====
    dates_sorted = sorted({d["date"] for d in daily_rows})
    daily_series = []
    for model in all_models:
        series_data = []
        for date in dates_sorted:
            v = next(
                (
                    r["total"]
                    for r in daily_rows
                    if r["date"] == date and r["model"] == model
                ),
                0,
            )
            series_data.append(v)
        daily_series.append(
            {
                "name": model,
                "type": "bar",
                "stack": "total",
                "emphasis": {"focus": "series"},
                "data": series_data,
                "itemStyle": {
                    "color": cmap[model],
                    "borderRadius": [3, 3, 0, 0],
                },
                "barWidth": "60%",
            }
        )

    # ===== 模型分布饼图 =====
    pie_data = [
        {"name": m["model"], "value": m["total"]} for m in model_rows
    ]

    # ===== 趋势折线 =====
    trend_dates = [d["date"] for d in daily_total_rows]
    trend_input = [d["input"] for d in daily_total_rows]
    trend_output = [d["output"] for d in daily_total_rows]

    # ===== 每周分组柱状 =====
    weeks_sorted = sorted({w["week"] for w in weekly_rows})
    weekly_series = []
    for model in all_models:
        sdata = []
        for wk in weeks_sorted:
            v = next(
                (
                    r["total"]
                    for r in weekly_rows
                    if r["week"] == wk and r["model"] == model
                ),
                0,
            )
            sdata.append(v)
        weekly_series.append(
            {
                "name": model,
                "type": "bar",
                "data": sdata,
                "itemStyle": {
                    "color": cmap[model],
                    "borderRadius": [4, 4, 0, 0],
                },
            }
        )

    # ===== 时段雷达 =====
    hmap = {h["hour"]: h["total"] for h in hourly_rows}
    radar_indicator = [
        {"name": f"{h:02d}", "max": max(hmap.values()) if hmap else 1}
        for h in range(24)
    ]
    radar_values = [hmap.get(h, 0) for h in range(24)]

    # ===== 项目分布 =====
    proj_names = [p["project"] for p in projects]
    proj_values = [p["total"] for p in projects]

    # ===== 表格行 =====
    table_rows = []
    for i, m in enumerate(model_rows):
        pct = (m["total"] / total_tokens * 100) if total_tokens else 0
        table_rows.append(
            {
                "model": m["model"],
                "color": cmap[m["model"]],
                "input": m["input"],
                "cache_write": m["cache_write"],
                "cache_read": m["cache_read"],
                "output": m["output"],
                "total": m["total"],
                "msgs": m["msgs"],
                "pct": round(pct, 1),
            }
        )

    # 序列化给前端
    payload = {
        "range_label": dr.label,
        "range_start": dr.start,
        "range_end": dr.end,
        "generated_at": now_in_sh().strftime("%Y-%m-%d %H:%M:%S"),
        "data_span": f"{data_span[0]} ~ {data_span[1]}" if data_span[0] else "N/A",
        "kpi": {
            "total_tokens": total_tokens,
            "total_input": total_input,
            "total_output": total_output,
            "total_cache": total_cache,
            "total_msgs": total_msgs,
            "model_count": len(all_models),
            "active_days": active_days,
        },
        "calendar": calendar_data,
        "daily": {
            "dates": dates_sorted,
            "series": daily_series,
        },
        "pie": pie_data,
        "trend": {
            "dates": trend_dates,
            "input": trend_input,
            "output": trend_output,
        },
        "weekly": {
            "weeks": weeks_sorted,
            "series": weekly_series,
        },
        "radar": {
            "indicator": radar_indicator,
            "values": radar_values,
        },
        "projects": {
            "names": proj_names,
            "values": proj_values,
        },
        "models": all_models,
        "colors": cmap,
        "table": table_rows,
        "fmt": {
            "total_tokens_str": fmt_tokens(total_tokens),
            "total_input_str": fmt_tokens(total_input),
            "total_output_str": fmt_tokens(total_output),
            "total_cache_str": fmt_tokens(total_cache),
        },
    }

    html_content = _build_html(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_content, encoding="utf-8")
    return output_path


# ============ HTML 模板 ============


def _build_html(p: dict) -> str:
    payload_json = json.dumps(p, ensure_ascii=False)
    title = html.escape(f"Claude Code 用量 · {p['range_label']}")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
  :root {{
    --bg: #07070d;
    --bg2: #0d0d1a;
    --card: rgba(22, 22, 38, 0.72);
    --card-border: rgba(255, 255, 255, 0.08);
    --card-hover: rgba(30, 30, 50, 0.85);
    --text: #e8e8f0;
    --text-dim: #8a8aa3;
    --text-faint: #5a5a70;
    --accent: #a78bfa;
    --accent2: #22d3ee;
    --accent3: #f472b6;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display",
                 "PingFang SC", "Microsoft YaHei", sans-serif;
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
  }}
  body {{
    background:
      radial-gradient(ellipse 80% 60% at 20% 0%, rgba(167,139,250,0.18), transparent 60%),
      radial-gradient(ellipse 70% 50% at 80% 10%, rgba(34,211,238,0.15), transparent 60%),
      radial-gradient(ellipse 60% 50% at 50% 100%, rgba(244,114,182,0.10), transparent 60%),
      var(--bg);
    background-attachment: fixed;
  }}
  .wrap {{
    max-width: 1440px;
    margin: 0 auto;
    padding: 40px 28px 80px;
  }}
  /* HEADER */
  .hero {{
    text-align: center;
    padding: 20px 0 36px;
  }}
  .hero .badge {{
    display: inline-block;
    padding: 6px 14px;
    border-radius: 999px;
    background: rgba(167,139,250,0.12);
    border: 1px solid rgba(167,139,250,0.3);
    color: var(--accent);
    font-size: 12px;
    letter-spacing: 1px;
    margin-bottom: 18px;
  }}
  .hero h1 {{
    font-size: 42px;
    font-weight: 800;
    letter-spacing: -1px;
    background: linear-gradient(135deg,
      #a78bfa 0%, #f472b6 35%, #22d3ee 70%, #34d399 100%);
    background-size: 200% 200%;
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
    animation: shimmer 6s ease-in-out infinite;
  }}
  @keyframes shimmer {{
    0%,100% {{ background-position: 0% 50%; }}
    50% {{ background-position: 100% 50%; }}
  }}
  .hero .meta {{
    color: var(--text-dim);
    font-size: 14px;
    margin-top: 10px;
  }}
  .hero .meta b {{ color: var(--accent2); font-weight: 600; }}

  /* KPI */
  .kpi-grid {{
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    gap: 16px;
    margin-bottom: 28px;
  }}
  @media (max-width: 900px) {{
    .kpi-grid {{ grid-template-columns: repeat(3, 1fr); }}
  }}
  @media (max-width: 500px) {{
    .kpi-grid {{ grid-template-columns: repeat(2, 1fr); }}
  }}
  .kpi {{
    background: var(--card);
    backdrop-filter: blur(20px);
    border: 1px solid var(--card-border);
    border-radius: 16px;
    padding: 22px 18px;
    position: relative;
    overflow: hidden;
    transition: transform .25s, border-color .25s;
  }}
  .kpi:hover {{
    transform: translateY(-3px);
    border-color: rgba(255,255,255,0.18);
  }}
  .kpi::before {{
    content: "";
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: var(--grad);
  }}
  .kpi::after {{
    content: "";
    position: absolute;
    top: -40px; right: -40px;
    width: 120px; height: 120px;
    background: var(--grad);
    filter: blur(50px);
    opacity: 0.25;
  }}
  .kpi .label {{
    font-size: 12px;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 8px;
  }}
  .kpi .value {{
    font-size: 28px;
    font-weight: 800;
    background: var(--grad);
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
    letter-spacing: -0.5px;
  }}
  .kpi .sub {{
    font-size: 11px;
    color: var(--text-faint);
    margin-top: 4px;
  }}

  /* CARD */
  .grid {{
    display: grid;
    grid-template-columns: repeat(12, 1fr);
    gap: 20px;
    margin-bottom: 20px;
  }}
  .card {{
    background: var(--card);
    backdrop-filter: blur(20px);
    border: 1px solid var(--card-border);
    border-radius: 18px;
    padding: 22px;
    position: relative;
    transition: border-color .25s;
  }}
  .card:hover {{ border-color: rgba(255,255,255,0.14); }}
  .card h2 {{
    font-size: 15px;
    font-weight: 700;
    color: var(--text);
    margin-bottom: 4px;
    display: flex;
    align-items: center;
    gap: 10px;
  }}
  .card h2 .dot {{
    width: 8px; height: 8px;
    border-radius: 50%;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    box-shadow: 0 0 12px var(--accent);
  }}
  .card .desc {{
    color: var(--text-faint);
    font-size: 12px;
    margin-bottom: 16px;
  }}
  .col-12 {{ grid-column: span 12; }}
  .col-8 {{ grid-column: span 8; }}
  .col-6 {{ grid-column: span 6; }}
  .col-4 {{ grid-column: span 4; }}
  @media (max-width: 900px) {{
    .col-12,.col-8,.col-6,.col-4 {{ grid-column: span 12; }}
  }}
  .chart {{ width: 100%; }}

  /* TABLE */
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }}
  thead th {{
    text-align: left;
    color: var(--text-dim);
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    padding: 10px 12px;
    border-bottom: 1px solid var(--card-border);
    position: sticky;
    top: 0;
  }}
  thead th.num {{ text-align: right; }}
  tbody td {{
    padding: 11px 12px;
    border-bottom: 1px solid rgba(255,255,255,0.04);
  }}
  tbody td.num {{
    text-align: right;
    font-variant-numeric: tabular-nums;
    font-feature-settings: "tnum";
  }}
  tbody tr:hover {{ background: rgba(255,255,255,0.025); }}
  .model-tag {{
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-weight: 600;
  }}
  .model-tag .swatch {{
    width: 10px; height: 10px;
    border-radius: 3px;
    box-shadow: 0 0 8px currentColor;
  }}
  .pct-bar {{
    display: inline-block;
    width: 60px;
    height: 6px;
    background: rgba(255,255,255,0.06);
    border-radius: 3px;
    overflow: hidden;
    margin-left: 8px;
    vertical-align: middle;
  }}
  .pct-fill {{
    height: 100%;
    border-radius: 3px;
  }}
  .total-row td {{
    font-weight: 700;
    border-top: 2px solid var(--card-border);
    border-bottom: none !important;
    color: var(--accent);
    padding-top: 14px;
  }}

  footer {{
    text-align: center;
    color: var(--text-faint);
    font-size: 12px;
    padding: 30px 0 0;
    border-top: 1px solid var(--card-border);
    margin-top: 30px;
  }}
  footer code {{
    background: rgba(255,255,255,0.06);
    padding: 2px 8px;
    border-radius: 4px;
    color: var(--accent2);
    font-size: 11px;
  }}
</style>
</head>
<body>
<div class="wrap">

  <div class="hero">
    <div class="badge">◆ CLAUDE CODE USAGE</div>
    <h1>{title}</h1>
    <div class="meta">
      数据范围 <b id="m-span"></b> · 生成于 <b id="m-gen"></b> · Asia/Shanghai
    </div>
  </div>

  <div class="kpi-grid" id="kpi"></div>

  <div class="grid">
    <div class="card col-12">
      <h2><span class="dot"></span>用量热力图</h2>
      <div class="desc">每天的总 Token 用量 (颜色越亮 = 用量越大)</div>
      <div id="chart-calendar" class="chart" style="height:240px"></div>
    </div>
  </div>

  <div class="grid">
    <div class="card col-8">
      <h2><span class="dot"></span>每日 Token · 按模型堆叠</h2>
      <div class="desc">区间内每天的 Token 用量, 按模型分层</div>
      <div id="chart-daily" class="chart" style="height:380px"></div>
    </div>
    <div class="card col-4">
      <h2><span class="dot"></span>模型分布</h2>
      <div class="desc">区间内各模型总 Token 占比</div>
      <div id="chart-pie" class="chart" style="height:380px"></div>
    </div>
  </div>

  <div class="grid">
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

  <div class="grid">
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

  <div class="grid">
    <div class="card col-12">
      <h2><span class="dot"></span>模型明细</h2>
      <div class="desc">区间内每个模型的完整 Token 统计</div>
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

  <footer>
    Generated by <code>cc-usage</code> · 数据来自 <code>~/.claude/projects</code>
  </footer>
</div>

<script>
const P = {payload_json};

// ============ 格式化 ============
function fmtTok(n) {{
  if (n >= 1e9) return (n/1e9).toFixed(2) + 'B';
  if (n >= 1e6) return (n/1e6).toFixed(2) + 'M';
  if (n >= 1e3) return (n/1e3).toFixed(1) + 'K';
  return String(n);
}}
function fmtNum(n) {{ return n.toLocaleString('en-US'); }}

// ============ 公共主题 ============
const TOOLTIP = {{
  backgroundColor: 'rgba(20,20,35,0.95)',
  borderColor: 'rgba(255,255,255,0.12)',
  borderWidth: 1,
  textStyle: {{ color: '#e8e8f0', fontSize: 12 }},
  extraCssText: 'backdrop-filter:blur(10px);box-shadow:0 8px 30px rgba(0,0,0,0.4);'
}};
const AXIS_LINE = {{ lineStyle: {{ color: 'rgba(255,255,255,0.1)' }} }};
const AXIS_LABEL = {{ color: '#8a8aa3', fontSize: 11 }};
const SPLIT_LINE = {{ lineStyle: {{ color: 'rgba(255,255,255,0.05)' }} }};

// ============ HEADER meta ============
document.getElementById('m-span').textContent = P.data_span;
document.getElementById('m-gen').textContent = P.generated_at;

// ============ KPI ============
const kpiData = [
  {{ label: '总 Token', value: fmtTok(P.kpi.total_tokens), sub: fmtNum(P.kpi.total_tokens),
     grad: 'linear-gradient(135deg,#a78bfa,#f472b6)' }},
  {{ label: '输入', value: fmtTok(P.kpi.total_input), sub: 'prompt tokens',
     grad: 'linear-gradient(135deg,#22d3ee,#60a5fa)' }},
  {{ label: '输出', value: fmtTok(P.kpi.total_output), sub: 'completion tokens',
     grad: 'linear-gradient(135deg,#34d399,#a3e635)' }},
  {{ label: '缓存', value: fmtTok(P.kpi.total_cache), sub: 'read + write',
     grad: 'linear-gradient(135deg,#fbbf24,#f97316)' }},
  {{ label: '消息数', value: fmtNum(P.kpi.total_msgs), sub: 'assistant turns',
     grad: 'linear-gradient(135deg,#fb7185,#f472b6)' }},
  {{ label: '模型 / 活跃天', value: P.kpi.model_count + ' / ' + P.kpi.active_days,
     sub: 'models / active days', grad: 'linear-gradient(135deg,#c084fc,#22d3ee)' }}
];
document.getElementById('kpi').innerHTML = kpiData.map(k => `
  <div class="kpi" style="--grad:${{k.grad}}">
    <div class="label">${{k.label}}</div>
    <div class="value" data-target="${{k.value}}">${{k.value}}</div>
    <div class="sub">${{k.sub}}</div>
  </div>
`).join('');

// 数字滚动动画 (简单版: 字符逐个淡入)
document.querySelectorAll('.kpi .value').forEach((el, i) => {{
  el.style.opacity = 0;
  el.style.transform = 'translateY(8px)';
  el.style.transition = 'opacity .6s, transform .6s';
  setTimeout(() => {{
    el.style.opacity = 1;
    el.style.transform = 'translateY(0)';
  }}, 80 * i + 100);
}});

// ============ Chart 1: Calendar heatmap ============
(function(){{
  if (!P.calendar.length) return;
  const chart = echarts.init(document.getElementById('chart-calendar'));
  const values = P.calendar.map(d => d.value);
  const max = Math.max(...values, 1);
  chart.setOption({{
    tooltip: {{ ...TOOLTIP, formatter: p => `
      <b>${{p.value[0]}}</b><br/>
      总用量: <b style="color:#a78bfa">${{fmtTok(p.value[1])}}</b><br/>
      ${{fmtNum(p.value[1])}} tokens
    ` }},
    visualMap: {{
      min: 0, max: max,
      calculable: false,
      orient: 'horizontal',
      left: 'center',
      bottom: 0,
      inRange: {{ color: ['#1a1a2e','#3d2b5e','#6b2d7d','#a78bfa','#f472b6','#fbbf24'] }},
      textStyle: {{ color: '#8a8aa3', fontSize: 10 }},
      itemWidth: 12, itemHeight: 120
    }},
    calendar: {{
      top: 30,
      left: 60,
      right: 30,
      cellSize: ['auto', 16],
      range: P.calendar.map(d => d.name),
      itemStyle: {{
        borderWidth: 2,
        borderColor: '#07070d',
        color: '#15152a'
      }},
      yearLabel: {{ show: false }},
      dayLabel: {{ color: '#5a5a70', fontSize: 10 }},
      monthLabel: {{ color: '#8a8aa3', fontSize: 11 }},
      splitLine: {{ show: false }}
    }},
    series: [{{
      type: 'heatmap',
      coordinateSystem: 'calendar',
      data: P.calendar.map(d => [d.name, d.value]),
      progressive: 2000
    }}]
  }});
  window.addEventListener('resize', () => chart.resize());
}})();

// ============ Chart 2: Daily stacked bar ============
(function(){{
  const dom = document.getElementById('chart-daily');
  if (!P.daily.dates.length) {{ dom.innerHTML = '<div style="color:#5a5a70;text-align:center;padding:60px">区间内无数据</div>'; return; }}
  const chart = echarts.init(dom);
  chart.setOption({{
    tooltip: {{ ...TOOLTIP, trigger: 'axis', axisPointer: {{ type: 'shadow' }},
      formatter: params => {{
        let s = `<b>${{params[0].axisValue}}</b><br/>`;
        let total = 0;
        params.reverse().forEach(p => {{
          if (p.value) {{ s += `${{p.marker}}${{p.seriesName}}: <b>${{fmtTok(p.value)}}</b><br/>`; total += p.value; }}
        }});
        s += `<br/><b style="color:#a78bfa">合计: ${{fmtTok(total)}}</b>`;
        return s;
      }}
    }},
    legend: {{
      type: 'scroll',
      top: 0,
      textStyle: {{ color: '#8a8aa3', fontSize: 11 }},
      pageTextStyle: {{ color: '#8a8aa3' }}
    }},
    grid: {{ left: 50, right: 20, top: 40, bottom: 30 }},
    xAxis: {{
      type: 'category',
      data: P.daily.dates,
      axisLine, axisLabel,
      axisTick: {{ show: false }}
    }},
    yAxis: {{ type: 'value', axisLine: {{ show: false }}, axisLabel,
      splitLine, axisTick: {{ show: false }}, axisLabel: {{ ...axisLabel, formatter: v => fmtTok(v) }} }},
    series: P.daily.series,
    animationDuration: 1000,
    animationEasing: 'cubicOut'
  }});
  window.addEventListener('resize', () => chart.resize());
}})();

// ============ Chart 3: Pie (donut) ============
(function(){{
  if (!P.pie.length) return;
  const chart = echarts.init(document.getElementById('chart-pie'));
  chart.setOption({{
    tooltip: {{ ...TOOLTIP,
      formatter: p => `<b>${{p.name}}</b><br/>${{fmtTok(p.value)}} (${{p.percent}}%)`
    }},
    legend: {{ type: 'scroll', bottom: 0, textStyle: {{ color: '#8a8aa3', fontSize: 10 }},
      pageTextStyle: {{ color: '#8a8aa3' }} }},
    series: [{{
      type: 'pie',
      radius: ['45%', '70%'],
      center: ['50%', '45%'],
      avoidLabelOverlap: true,
      itemStyle: {{
        borderColor: '#07070d',
        borderWidth: 3,
        borderRadius: 6
      }},
      label: {{
        show: true, position: 'center',
        formatter: '{{总用量}}\\n{{d|' + fmtTok(P.kpi.total_tokens) + '}}',
        color: '#e8e8f0', fontSize: 12,
        rich: {{ d: {{ fontSize: 22, fontWeight: 'bold', color: '#a78bfa' }} }}
      }},
      emphasis: {{
        label: {{ show: true, fontSize: 14 }},
        itemStyle: {{ shadowBlur: 20, shadowColor: 'rgba(167,139,250,0.5)' }}
      }},
      data: P.pie,
      animationType: 'scale',
      animationDuration: 800
    }}]
  }});
  window.addEventListener('resize', () => chart.resize());
}})();

// ============ Chart 4: Trend (input/output) ============
(function(){{
  const dom = document.getElementById('chart-trend');
  if (!P.trend.dates.length) return;
  const chart = echarts.init(dom);
  const mkArea = (color) => ({{
    color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
      {{ offset: 0, color: color + 'aa' }},
      {{ offset: 1, color: color + '05' }}
    ])
  }});
  chart.setOption({{
    tooltip: {{ ...TOOLTIP, trigger: 'axis',
      formatter: params => {{
        let s = `<b>${{params[0].axisValue}}</b><br/>`;
        params.forEach(p => s += `${{p.marker}}${{p.seriesName}}: <b>${{fmtTok(p.value)}}</b><br/>`);
        return s;
      }}
    }},
    legend: {{ top: 0, textStyle: {{ color: '#8a8aa3' }} }},
    grid: {{ left: 50, right: 20, top: 40, bottom: 30 }},
    xAxis: {{ type: 'category', boundaryGap: false, data: P.trend.dates,
      axisLine, axisLabel, axisTick: {{ show: false }} }},
    yAxis: {{ type: 'value', axisLine: {{ show: false }},
      splitLine, axisLabel: {{ ...axisLabel, formatter: v => fmtTok(v) }} }},
    series: [
      {{ name: '输入', type: 'line', smooth: true, symbol: 'circle', symbolSize: 6,
        data: P.trend.input, lineStyle: {{ width: 2, color: '#22d3ee' }},
        itemStyle: {{ color: '#22d3ee' }}, areaStyle: mkArea('#22d3ee') }},
      {{ name: '输出', type: 'line', smooth: true, symbol: 'circle', symbolSize: 6,
        data: P.trend.output, lineStyle: {{ width: 2, color: '#34d399' }},
        itemStyle: {{ color: '#34d399' }}, areaStyle: mkArea('#34d399') }}
    ]
  }});
  window.addEventListener('resize', () => chart.resize());
}})();

// ============ Chart 5: Weekly grouped bar ============
(function(){{
  const dom = document.getElementById('chart-weekly');
  if (!P.weekly.weeks.length) return;
  const chart = echarts.init(dom);
  chart.setOption({{
    tooltip: {{ ...TOOLTIP, trigger: 'axis', axisPointer: {{ type: 'shadow' }},
      formatter: params => {{
        let s = `<b>周起始: ${{params[0].axisValue}}</b><br/>`;
        params.forEach(p => {{ if (p.value) s += `${{p.marker}}${{p.seriesName}}: <b>${{fmtTok(p.value)}}</b><br/>`; }});
        return s;
      }}
    }},
    legend: {{ type: 'scroll', top: 0, textStyle: {{ color: '#8a8aa3', fontSize: 10 }} }},
    grid: {{ left: 50, right: 20, top: 40, bottom: 30 }},
    xAxis: {{ type: 'category', data: P.weekly.weeks, axisLine, axisLabel, axisTick: {{ show: false }} }},
    yAxis: {{ type: 'value', axisLine: {{ show: false }}, splitLine,
      axisLabel: {{ ...axisLabel, formatter: v => fmtTok(v) }} }},
    series: P.weekly.series
  }});
  window.addEventListener('resize', () => chart.resize());
}})();

// ============ Chart 6: Radar (hourly) ============
(function(){{
  if (!P.radar.values.some(v => v > 0)) return;
  const chart = echarts.init(document.getElementById('chart-radar'));
  chart.setOption({{
    tooltip: {{ ...TOOLTIP,
      formatter: p => `时段 <b>${{p.dimensionIndex.toString().padStart(2,'0')}}时</b><br/>用量: <b>${{fmtTok(p.value)}}</b>`
    }},
    radar: {{
      indicator: P.radar.indicator,
      center: ['50%', '52%'],
      radius: '68%',
      shape: 'polygon',
      axisName: {{ color: '#8a8aa3', fontSize: 10 }},
      splitLine: {{ lineStyle: {{ color: 'rgba(255,255,255,0.08)' }} }},
      splitArea: {{ areaStyle: {{ color: ['rgba(167,139,250,0.02)','rgba(167,139,250,0.05)'] }} }},
      axisLine: {{ lineStyle: {{ color: 'rgba(255,255,255,0.1)' }} }}
    }},
    series: [{{
      type: 'radar',
      data: [{{
        value: P.radar.values,
        name: '时段用量',
        areaStyle: {{ color: new echarts.graphic.RadialGradient(0.5, 0.5, 1, [
          {{ offset: 0, color: 'rgba(167,139,250,0.05)' }},
          {{ offset: 1, color: 'rgba(244,114,182,0.45)' }}
        ]) }},
        lineStyle: {{ width: 2, color: '#f472b6' }},
        itemStyle: {{ color: '#f472b6' }}
      }}]
    }}]
  }});
  window.addEventListener('resize', () => chart.resize());
}})();

// ============ Chart 7: Projects bar ============
(function(){{
  if (!P.projects.names.length) return;
  const chart = echarts.init(document.getElementById('chart-proj'));
  chart.setOption({{
    tooltip: {{ ...TOOLTIP,
      formatter: p => `<b>${{p.name}}</b><br/>${{fmtTok(p.value)}} tokens` }},
    grid: {{ left: 10, right: 30, top: 10, bottom: 20, containLabel: true }},
    xAxis: {{ type: 'value', axisLine: {{ show: false }}, splitLine,
      axisLabel: {{ ...axisLabel, formatter: v => fmtTok(v) }} }},
    yAxis: {{ type: 'category', data: P.projects.names.slice().reverse(),
      axisLine, axisLabel: {{ ...axisLabel, fontSize: 11 }}, axisTick: {{ show: false }} }},
    series: [{{
      type: 'bar',
      data: P.projects.values.slice().reverse().map((v, i) => ({{
        value: v,
        itemStyle: {{
          borderRadius: [0, 6, 6, 0],
          color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
            {{ offset: 0, color: '#a78bfa' }},
            {{ offset: 1, color: '#22d3ee' }}
          ])
        }}
      }})),
      barWidth: '55%',
      label: {{ show: true, position: 'right', color: '#e8e8f0',
        fontSize: 11, formatter: p => fmtTok(p.value) }}
    }}]
  }});
  window.addEventListener('resize', () => chart.resize());
}})();

// ============ Table ============
(function(){{
  const tbody = document.getElementById('tbody');
  let totalAll = 0;
  P.table.forEach(r => totalAll += r.total);
  tbody.innerHTML = P.table.map(r => `
    <tr>
      <td>
        <span class="model-tag">
          <span class="swatch" style="background:${{r.color}};color:${{r.color}}"></span>
          ${{r.model}}
        </span>
      </td>
      <td class="num">${{fmtNum(r.input)}}</td>
      <td class="num" style="color:#8a8aa3">${{fmtNum(r.cache_write)}}</td>
      <td class="num" style="color:#8a8aa3">${{fmtNum(r.cache_read)}}</td>
      <td class="num">${{fmtNum(r.output)}}</td>
      <td class="num"><b style="color:${{r.color}}">${{fmtNum(r.total)}}</b></td>
      <td class="num">${{fmtNum(r.msgs)}}</td>
      <td class="num">
        ${{r.pct}}%
        <span class="pct-bar"><span class="pct-fill"
          style="width:${{r.pct}}%;background:${{r.color}}"></span></span>
      </td>
    </tr>
  `).join('') + `
    <tr class="total-row">
      <td>TOTAL</td>
      <td class="num">${{fmtNum(P.table.reduce((a,r)=>a+r.input,0))}}</td>
      <td class="num">${{fmtNum(P.table.reduce((a,r)=>a+r.cache_write,0))}}</td>
      <td class="num">${{fmtNum(P.table.reduce((a,r)=>a+r.cache_read,0))}}</td>
      <td class="num">${{fmtNum(P.table.reduce((a,r)=>a+r.output,0))}}</td>
      <td class="num">${{fmtNum(totalAll)}}</td>
      <td class="num">${{fmtNum(P.table.reduce((a,r)=>a+r.msgs,0))}}</td>
      <td class="num">100%</td>
    </tr>
  `;
}})();
</script>
</body>
</html>
"""
