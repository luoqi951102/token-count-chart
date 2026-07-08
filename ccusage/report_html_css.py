"""HTML 仪表盘的 CSS (作为普通字符串常量, 非 f-string, 花括号原样).

由 report_html._build_html 拼接进 <style>. 包含:
- 深色 (默认) / 浅色主题, 全部走 CSS 变量
- 玻璃拟态卡片 + 渐变光晕背景 (鼠标视差)
- Hero signature: 超大数字 + sparkline + 脉冲点
- Range tab / 主题切换按钮
- 游戏化指标卡 (streak / 环比 / 工作日周末 / 最卷一天)
- reveal 滚动入场动画 + reduced-motion 降级
"""

CSS = r"""
:root {
  --bg: #07070d;
  --bg2: #0d0d1a;
  --card: rgba(22, 22, 38, 0.72);
  --card-solid: #16162a;
  --card-border: rgba(255, 255, 255, 0.08);
  --text: #e8e8f0;
  --text-dim: #8a8aa3;
  --text-faint: #5a5a70;
  --accent: #a78bfa;
  --accent2: #22d3ee;
  --accent3: #f472b6;
  /* ECharts 主题变量 (JS themeColors 读取) */
  --axis: rgba(255, 255, 255, 0.10);
  --axis-label: #8a8aa3;
  --split: rgba(255, 255, 255, 0.05);
  --tooltip-bg: rgba(20, 20, 35, 0.95);
  --tooltip-border: rgba(255, 255, 255, 0.12);
  --calendar-empty: #15152a;
  --calendar-border: #07070d;
  /* 鼠标视差 (-0.5 ~ 0.5) */
  --mx: 0;
  --my: 0;
}
[data-theme="light"] {
  --bg: #f5f5fa;
  --bg2: #ececf2;
  --card: rgba(255, 255, 255, 0.78);
  --card-solid: #ffffff;
  --card-border: rgba(0, 0, 0, 0.08);
  --text: #181826;
  --text-dim: #565670;
  --text-faint: #9a9ab0;
  --accent: #7c3aed;
  --accent2: #0891b2;
  --accent3: #db2777;
  --axis: rgba(0, 0, 0, 0.12);
  --axis-label: #565670;
  --split: rgba(0, 0, 0, 0.06);
  --tooltip-bg: rgba(255, 255, 255, 0.97);
  --tooltip-border: rgba(0, 0, 0, 0.10);
  --calendar-empty: #e6e6ee;
  --calendar-border: #f5f5fa;
}

* { box-sizing: border-box; margin: 0; padding: 0; }
html, body {
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display",
               "PingFang SC", "Microsoft YaHei", sans-serif;
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
  transition: background .4s, color .4s;
}
body {
  background:
    radial-gradient(ellipse 80% 60% at calc(20% + var(--mx)*42px) calc(0% + var(--my)*42px), rgba(167,139,250,0.20), transparent 60%),
    radial-gradient(ellipse 70% 50% at calc(82% + var(--mx)*-32px) calc(8% + var(--my)*32px), rgba(34,211,238,0.16), transparent 60%),
    radial-gradient(ellipse 60% 50% at calc(50% + var(--mx)*24px) calc(100% + var(--my)*-24px), rgba(244,114,182,0.12), transparent 60%),
    var(--bg);
  background-attachment: fixed;
}
[data-theme="light"] body {
  background:
    radial-gradient(ellipse 80% 60% at calc(20% + var(--mx)*42px) calc(0% + var(--my)*42px), rgba(124,58,237,0.12), transparent 60%),
    radial-gradient(ellipse 70% 50% at calc(82% + var(--mx)*-32px) calc(8% + var(--my)*32px), rgba(8,145,178,0.10), transparent 60%),
    radial-gradient(ellipse 60% 50% at calc(50% + var(--mx)*24px) calc(100% + var(--my)*-24px), rgba(219,39,119,0.08), transparent 60%),
    var(--bg);
  background-attachment: fixed;
}
.wrap { max-width: 1440px; margin: 0 auto; padding: 40px 28px 80px; }

/* ============ HERO ============ */
.hero { text-align: center; padding: 16px 0 28px; }
.hero .badge {
  display: inline-block; padding: 6px 14px; border-radius: 999px;
  background: rgba(167,139,250,0.12); border: 1px solid rgba(167,139,250,0.3);
  color: var(--accent); font-size: 12px; letter-spacing: 1px; margin-bottom: 16px;
}
.hero h1 {
  font-size: 42px; font-weight: 800; letter-spacing: -1px;
  background: linear-gradient(135deg, #a78bfa 0%, #f472b6 35%, #22d3ee 70%, #34d399 100%);
  background-size: 200% 200%;
  -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
  animation: shimmer 6s ease-in-out infinite;
}
@keyframes shimmer { 0%,100% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } }
.hero .meta { color: var(--text-dim); font-size: 14px; margin-top: 10px; }
.hero .meta b { color: var(--accent2); font-weight: 600; }

/* Hero signature: 超大数字 + sparkline + 脉冲 */
.hero-stats {
  display: flex; align-items: center; justify-content: center;
  gap: 56px; margin: 26px 0 6px; flex-wrap: wrap;
}
.hero-total { text-align: left; }
.hero-label {
  font-size: 12px; letter-spacing: 1px; text-transform: uppercase;
  color: var(--text-dim); margin-bottom: 4px;
}
.hero-num {
  font-size: clamp(48px, 7vw, 82px); font-weight: 900; letter-spacing: -2.5px; line-height: 1;
  background: linear-gradient(135deg, #a78bfa, #f472b6 50%, #22d3ee);
  -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
  font-variant-numeric: tabular-nums;
}
.hero-sub { font-size: 13px; color: var(--text-faint); margin-top: 8px; }
.hero-spark { width: 260px; height: 76px; }
.hero-pulse { display: flex; align-items: center; gap: 9px; font-size: 13px; color: var(--text-dim); }
.pulse-dot {
  width: 10px; height: 10px; border-radius: 50%; background: #34d399;
  box-shadow: 0 0 0 0 rgba(52,211,153,0.6);
  animation: pulse 2.2s infinite;
}
@keyframes pulse {
  0%   { box-shadow: 0 0 0 0 rgba(52,211,153,0.6); }
  70%  { box-shadow: 0 0 0 14px rgba(52,211,153,0); }
  100% { box-shadow: 0 0 0 0 rgba(52,211,153,0); }
}

/* toolbar: range tabs + actions */
.toolbar {
  display: flex; align-items: center; justify-content: center;
  gap: 16px; margin-top: 24px; flex-wrap: wrap;
}
.range-tabs {
  display: inline-flex; gap: 4px; padding: 5px;
  background: var(--card); border: 1px solid var(--card-border);
  border-radius: 14px; backdrop-filter: blur(20px);
}
.range-tabs button {
  padding: 9px 20px; border: none; background: transparent;
  color: var(--text-dim); font-size: 13px; font-weight: 600; font-family: inherit;
  border-radius: 10px; cursor: pointer; transition: color .25s, background .25s, box-shadow .25s;
}
.range-tabs button:hover { color: var(--text); }
.range-tabs button.active {
  color: #fff;
  background: linear-gradient(135deg, #a78bfa, #f472b6);
  box-shadow: 0 4px 18px rgba(167,139,250,0.45);
}

/* source 切换器: 复用 range-tabs 结构, 渐变换成青绿系区分 */
.source-tabs {
  display: inline-flex; gap: 4px; padding: 5px;
  background: var(--card); border: 1px solid var(--card-border);
  border-radius: 14px; backdrop-filter: blur(20px);
}
.source-tabs button {
  padding: 9px 18px; border: none; background: transparent;
  color: var(--text-dim); font-size: 13px; font-weight: 600; font-family: inherit;
  border-radius: 10px; cursor: pointer; transition: color .25s, background .25s, box-shadow .25s;
}
.source-tabs button:hover { color: var(--text); }
.source-tabs button.active {
  color: #fff;
  background: linear-gradient(135deg, #22d3ee, #34d399);
  box-shadow: 0 4px 18px rgba(34,211,238,0.45);
}

/* ============ KPI ============ */
.kpi-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 16px; margin-bottom: 28px; }
@media (max-width: 900px) { .kpi-grid { grid-template-columns: repeat(3, 1fr); } }
@media (max-width: 500px) { .kpi-grid { grid-template-columns: repeat(2, 1fr); } }
.kpi {
  background: var(--card); backdrop-filter: blur(20px);
  border: 1px solid var(--card-border); border-radius: 16px;
  padding: 22px 18px; position: relative; overflow: hidden;
  transition: transform .25s, border-color .25s;
}
.kpi:hover { transform: translateY(-3px); border-color: rgba(255,255,255,0.18); }
.kpi::before { content: ""; position: absolute; top: 0; left: 0; right: 0; height: 2px; background: var(--grad); }
.kpi::after {
  content: ""; position: absolute; top: -40px; right: -40px; width: 120px; height: 120px;
  background: var(--grad); filter: blur(50px); opacity: 0.25;
}
.kpi .label { font-size: 12px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
.kpi .value {
  font-size: 28px; font-weight: 800; letter-spacing: -0.5px;
  background: var(--grad); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
  font-variant-numeric: tabular-nums;
}
.kpi .sub { font-size: 11px; color: var(--text-faint); margin-top: 4px; }

/* ============ 游戏化指标 ============ */
.streak-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 16px; margin-bottom: 28px; }
@media (max-width: 1200px) { .streak-grid { grid-template-columns: repeat(3, 1fr); } }
@media (max-width: 900px) { .streak-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 500px) { .streak-grid { grid-template-columns: 1fr; } }
.streak-card {
  background: var(--card); backdrop-filter: blur(20px);
  border: 1px solid var(--card-border); border-radius: 16px;
  padding: 20px 22px; position: relative; overflow: hidden;
  transition: transform .25s, border-color .25s;
}
.streak-card:hover { transform: translateY(-3px); border-color: rgba(255,255,255,0.16); }
.streak-card::before {
  content: ""; position: absolute; top: 0; left: 0; width: 3px; height: 100%;
  background: var(--sc-grad, linear-gradient(180deg, #a78bfa, #22d3ee));
}
.sc-label {
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.6px;
  color: var(--text-dim); margin-bottom: 10px; display: flex; align-items: center; gap: 7px;
}
.sc-main { font-size: 32px; font-weight: 800; line-height: 1; font-variant-numeric: tabular-nums; }
.sc-main small { font-size: 14px; font-weight: 600; color: var(--text-dim); margin-left: 2px; }
.sc-sub { font-size: 12px; color: var(--text-faint); margin-top: 8px; }
.sc-fire { color: #fb923c; }
.sc-up { color: #34d399; }
.sc-down { color: #fb7185; }
.sc-flat { color: var(--text-dim); }
.sc-bar {
  display: flex; height: 8px; border-radius: 4px; overflow: hidden;
  margin-top: 12px; background: rgba(255,255,255,0.06);
}
[data-theme="light"] .sc-bar { background: rgba(0,0,0,0.06); }
.sc-bar .seg-w { background: linear-gradient(90deg, #60a5fa, #a78bfa); }
.sc-bar .seg-e { background: linear-gradient(90deg, #f472b6, #fbbf24); }
.sc-bar .seg-c { background: linear-gradient(90deg, #a78bfa, #f472b6); }
.sc-bar .seg-z { background: linear-gradient(90deg, #22d3ee, #34d399); }

/* ============ CARD 网格 ============ */
.grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 20px; margin-bottom: 20px; }
.card {
  background: var(--card); backdrop-filter: blur(20px);
  border: 1px solid var(--card-border); border-radius: 18px;
  padding: 22px; position: relative; transition: border-color .25s;
}
.card:hover { border-color: rgba(255,255,255,0.14); }
[data-theme="light"] .card:hover { border-color: rgba(0,0,0,0.14); }
.card h2 { font-size: 15px; font-weight: 700; color: var(--text); margin-bottom: 4px; display: flex; align-items: center; gap: 10px; }
.card h2 .dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: linear-gradient(135deg, var(--accent), var(--accent2));
  box-shadow: 0 0 12px var(--accent);
}
.card .desc { color: var(--text-faint); font-size: 12px; margin-bottom: 16px; }
.col-12 { grid-column: span 12; }
.col-8 { grid-column: span 8; }
.col-6 { grid-column: span 6; }
.col-4 { grid-column: span 4; }
@media (max-width: 900px) { .col-12, .col-8, .col-6, .col-4 { grid-column: span 12; } }
.chart { width: 100%; }
.empty-hint { color: var(--text-faint); text-align: center; padding: 60px 0; font-size: 13px; }

/* ============ TABLE ============ */
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead th {
  text-align: left; color: var(--text-dim); font-weight: 600; font-size: 11px;
  text-transform: uppercase; letter-spacing: 0.5px; padding: 10px 12px;
  border-bottom: 1px solid var(--card-border); position: sticky; top: 0;
  background: var(--card-solid);
}
thead th.num { text-align: right; }
tbody td { padding: 11px 12px; border-bottom: 1px solid rgba(255,255,255,0.04); }
[data-theme="light"] tbody td { border-bottom: 1px solid rgba(0,0,0,0.04); }
tbody td.num { text-align: right; font-variant-numeric: tabular-nums; font-feature-settings: "tnum"; }
tbody tr { transition: background .2s; cursor: pointer; }
tbody tr:hover { background: rgba(255,255,255,0.03); }
[data-theme="light"] tbody tr:hover { background: rgba(0,0,0,0.03); }
tbody tr.focused { background: rgba(167,139,250,0.10); }
.model-tag { display: inline-flex; align-items: center; gap: 8px; font-weight: 600; }
.model-tag .swatch { width: 10px; height: 10px; border-radius: 3px; box-shadow: 0 0 8px currentColor; }
.pct-bar {
  display: inline-block; width: 60px; height: 6px; background: rgba(255,255,255,0.06);
  border-radius: 3px; overflow: hidden; margin-left: 8px; vertical-align: middle;
}
[data-theme="light"] .pct-bar { background: rgba(0,0,0,0.06); }
.pct-fill { height: 100%; border-radius: 3px; }
.total-row td {
  font-weight: 700; border-top: 2px solid var(--card-border); border-bottom: none !important;
  color: var(--accent); padding-top: 14px; cursor: default;
}
.total-row td:hover { background: transparent !important; }

footer {
  text-align: center; color: var(--text-faint); font-size: 12px;
  padding: 30px 0 0; border-top: 1px solid var(--card-border); margin-top: 30px;
}
footer code {
  background: rgba(255,255,255,0.06); padding: 2px 8px; border-radius: 4px;
  color: var(--accent2); font-size: 11px;
}
[data-theme="light"] footer code { background: rgba(0,0,0,0.06); }

/* ============ 按钮 ============ */
.actions { display: flex; gap: 14px; justify-content: center; flex-wrap: wrap; }
.btn-icon {
  width: 42px; height: 42px; border-radius: 12px;
  border: 1px solid var(--card-border); background: var(--card); backdrop-filter: blur(20px);
  color: var(--text); font-size: 18px; cursor: pointer;
  display: inline-flex; align-items: center; justify-content: center;
  transition: transform .25s cubic-bezier(.34,1.56,.64,1), border-color .25s;
}
.btn-icon:hover { transform: translateY(-3px); border-color: rgba(255,255,255,0.2); }
[data-theme="light"] .btn-icon:hover { border-color: rgba(0,0,0,0.2); }
.btn {
  position: relative; padding: 11px 22px; border-radius: 14px;
  border: 1px solid var(--card-border); background: var(--card); backdrop-filter: blur(20px);
  color: var(--text); font-size: 13px; font-weight: 600; font-family: inherit;
  cursor: pointer; overflow: hidden;
  transition: transform .25s cubic-bezier(.34,1.56,.64,1), border-color .25s, box-shadow .3s;
  display: inline-flex; align-items: center; gap: 9px; letter-spacing: 0.3px;
}
.btn::before { content: ""; position: absolute; top: 0; left: 0; right: 0; height: 2px; background: var(--bgrad); }
.btn::after { content: ""; position: absolute; inset: 0; background: var(--bgrad); opacity: 0; transition: opacity .3s; z-index: -1; }
.btn:hover { transform: translateY(-3px); border-color: rgba(255,255,255,0.2); box-shadow: 0 10px 32px var(--bshadow); }
[data-theme="light"] .btn:hover { border-color: rgba(0,0,0,0.2); }
.btn:hover::after { opacity: 0.10; }
.btn:active { transform: translateY(-1px); }
.btn:disabled { opacity: 0.55; cursor: progress; transform: none !important; box-shadow: none !important; }
.btn .icon { font-size: 15px; filter: drop-shadow(0 0 6px var(--bshadow)); }
.btn .label { background: var(--bgrad); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }
.btn-png { --bgrad: linear-gradient(135deg, #a78bfa 0%, #f472b6 100%); --bshadow: rgba(167,139,250,0.35); }
.btn-pdf { --bgrad: linear-gradient(135deg, #22d3ee 0%, #60a5fa 100%); --bshadow: rgba(34,211,238,0.35); }
.btn .spinner {
  width: 13px; height: 13px; border: 2px solid rgba(255,255,255,0.15);
  border-top-color: var(--accent); border-radius: 50%; animation: spin .7s linear infinite; display: inline-block;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ============ 导出 loading overlay ============ */
#export-overlay {
  position: fixed; inset: 0; background: rgba(7,7,13,0.78); backdrop-filter: blur(10px);
  display: none; align-items: center; justify-content: center; z-index: 9999;
  flex-direction: column; gap: 18px;
}
#export-overlay.show { display: flex; }
#export-overlay .ring {
  width: 44px; height: 44px; border: 3px solid rgba(167,139,250,0.15);
  border-top-color: #a78bfa; border-radius: 50%; animation: spin .7s linear infinite;
}
#export-overlay .text { color: #8a8aa3; font-size: 14px; letter-spacing: 0.5px; }

/* ============ Toast ============ */
#toast {
  position: fixed; bottom: 32px; left: 50%;
  transform: translateX(-50%) translateY(120px);
  background: rgba(20,20,35,0.95); border: 1px solid rgba(255,255,255,0.12);
  border-radius: 14px; padding: 13px 26px; color: #e8e8f0;
  font-size: 14px; font-weight: 600; opacity: 0;
  transition: transform .35s cubic-bezier(.34,1.56,.64,1), opacity .35s;
  z-index: 10000; backdrop-filter: blur(20px); box-shadow: 0 12px 40px rgba(0,0,0,0.5);
}
#toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }

/* ============ reveal 滚动入场 ============ */
.reveal {
  opacity: 0; transform: translateY(28px);
  transition: opacity .7s cubic-bezier(.22,1,.36,1), transform .7s cubic-bezier(.22,1,.36,1);
  will-change: opacity, transform;
}
.reveal.in { opacity: 1; transform: translateY(0); }

/* ============ 无障碍: 减少动效 ============ */
@media (prefers-reduced-motion: reduce) {
  .reveal { opacity: 1 !important; transform: none !important; transition: none !important; }
  .hero h1, .pulse-dot, .btn .spinner, #export-overlay .ring { animation: none !important; }
  * { scroll-behavior: auto !important; }
}
"""
