"""HTML 仪表盘的 JS (作为原始字符串常量, 花括号与反斜杠原样).

由 report_html._build_html 拼接进 <script>, 前面已有 `const P = {...}`.
架构:
- 数据源: P.ranges[today|week|month|all], 每个 range 同构 (kpi/daily/pie/...)
- 切 range = 换数据源 + 所有图表 setOption(opt, true) 重绘 + count-up 重触发
- 主题: 读 CSS 变量 (themeColors), 切主题重绘图表配色
- count-up / reveal / 鼠标视差 / 导出兼容 (forceFinalState)
"""

JS = r"""
const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const charts = {};
let currentRange = P.range_default || 'week';
let focusedModel = null;
let exporting = false;

// ============ 格式化 ============
function fmtTok(n) {
  n = +n || 0;
  if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return String(Math.round(n));
}
function fmtNum(n) { return (+n || 0).toLocaleString('en-US'); }

// ============ 主题: 读 CSS 变量 ============
function themeColors() {
  const cs = getComputedStyle(document.documentElement);
  const g = (v) => cs.getPropertyValue(v).trim();
  return {
    text: g('--text'), dim: g('--text-dim'), faint: g('--text-faint'),
    accent: g('--accent'), accent2: g('--accent2'),
    axisLabel: g('--axis-label'), axis: g('--axis'), split: g('--split'),
    tipBg: g('--tooltip-bg'), tipBorder: g('--tooltip-border'),
    calEmpty: g('--calendar-empty'), calBorder: g('--calendar-border'),
    cardSolid: g('--card-solid'),
  };
}
function tooltip() {
  const c = themeColors();
  return {
    backgroundColor: c.tipBg, borderColor: c.tipBorder, borderWidth: 1,
    textStyle: { color: c.text, fontSize: 12 },
    extraCssText: 'backdrop-filter:blur(10px);box-shadow:0 8px 30px rgba(0,0,0,0.4);'
  };
}
function axisLine() { return { lineStyle: { color: themeColors().axis } }; }
function axisLabel() { return { color: themeColors().axisLabel, fontSize: 11 }; }
function splitLine() { return { lineStyle: { color: themeColors().split } }; }

// ============ count-up ============
const countHandles = new WeakMap();
function animateCount(el, raw, fmt) {
  if (!el) return;
  raw = +raw || 0;
  if (prefersReduced || exporting) { el.textContent = fmt(raw); return; }
  const prev = countHandles.get(el);
  if (prev) cancelAnimationFrame(prev);
  const dur = 1100, t0 = performance.now();
  function step(now) {
    if (exporting) { el.textContent = fmt(raw); return; }
    const p = Math.min((now - t0) / dur, 1);
    const e = 1 - Math.pow(1 - p, 3); // easeOutCubic
    el.textContent = fmt(raw * e);
    if (p < 1) countHandles.set(el, requestAnimationFrame(step));
  }
  countHandles.set(el, requestAnimationFrame(step));
}

// ============ 图表 option 构造 ============
function buildCalendar() {
  if (!P.calendar.length) return null;
  const c = themeColors();
  const values = P.calendar.map(d => d.value);
  const max = Math.max(...values, 1);
  const calDates = P.calendar.map(d => d.name).sort();
  return {
    tooltip: Object.assign({}, tooltip(), {
      formatter: p => `<b>${p.value[0]}</b><br/>总用量: <b style="color:#a78bfa">${fmtTok(p.value[1])}</b><br/>${fmtNum(p.value[1])} tokens`
    }),
    visualMap: {
      min: 0, max: max, calculable: false, orient: 'horizontal', left: 'center', bottom: 0,
      inRange: { color: ['#1a1a2e', '#3d2b5e', '#6b2d7d', '#a78bfa', '#f472b6', '#fbbf24'] },
      textStyle: { color: c.dim, fontSize: 10 }, itemWidth: 12, itemHeight: 120
    },
    calendar: {
      top: 30, left: 60, right: 30, cellSize: ['auto', 16],
      range: calDates.length ? [calDates[0], calDates[calDates.length - 1]] : [],
      itemStyle: { borderWidth: 2, borderColor: c.calBorder, color: c.calEmpty },
      yearLabel: { show: false }, dayLabel: { color: c.dim, fontSize: 10 },
      monthLabel: { color: c.dim, fontSize: 11 }, splitLine: { show: false }
    },
    series: [{
      type: 'heatmap', coordinateSystem: 'calendar',
      data: P.calendar.map(d => [d.name, d.value]), progressive: 2000
    }]
  };
}

function buildDaily(r) {
  if (!r.daily.dates.length) return null;
  const c = themeColors();
  return {
    tooltip: Object.assign({}, tooltip(), {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: params => {
        let s = `<b>${params[0].axisValue}</b><br/>`, total = 0;
        params.reverse().forEach(p => { if (p.value) { s += `${p.marker}${p.seriesName}: <b>${fmtTok(p.value)}</b><br/>`; total += p.value; } });
        return s + `<br/><b style="color:#a78bfa">合计: ${fmtTok(total)}</b>`;
      }
    }),
    legend: { type: 'scroll', top: 0, textStyle: { color: c.dim, fontSize: 11 }, pageTextStyle: { color: c.dim } },
    grid: { left: 50, right: 20, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: r.daily.dates, axisLine: axisLine(), axisLabel: axisLabel(), axisTick: { show: false } },
    yAxis: { type: 'value', axisLine: { show: false }, splitLine: splitLine(), axisTick: { show: false }, axisLabel: Object.assign({}, axisLabel(), { formatter: v => fmtTok(v) }) },
    series: r.daily.series, animationDuration: 900, animationEasing: 'cubicOut'
  };
}

function buildPie(r) {
  if (!r.pie.length) return null;
  const c = themeColors();
  return {
    tooltip: Object.assign({}, tooltip(), { formatter: p => `<b>${p.name}</b><br/>${fmtTok(p.value)} (${p.percent}%)` }),
    legend: { type: 'scroll', bottom: 0, textStyle: { color: c.dim, fontSize: 10 }, pageTextStyle: { color: c.dim } },
    series: [{
      type: 'pie', radius: ['45%', '70%'], center: ['50%', '45%'], avoidLabelOverlap: true,
      itemStyle: { borderColor: c.calBorder, borderWidth: 3, borderRadius: 6 },
      label: {
        show: true, position: 'center', color: c.text,
        formatter: () => '{a|总用量}\n{d|' + fmtTok(r.kpi.total_tokens) + '}',
        rich: { a: { fontSize: 12, color: c.dim, lineHeight: 18 }, d: { fontSize: 24, fontWeight: 'bold', color: '#a78bfa' } }
      },
      emphasis: { label: { show: true, fontSize: 14 }, itemStyle: { shadowBlur: 20, shadowColor: 'rgba(167,139,250,0.5)' } },
      data: r.pie, animationType: 'scale', animationDuration: 800
    }]
  };
}

function buildTrend(r) {
  if (!r.trend.dates.length) return null;
  const c = themeColors();
  const mkArea = color => ({ color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: color + 'aa' }, { offset: 1, color: color + '05' }]) });
  return {
    tooltip: Object.assign({}, tooltip(), {
      trigger: 'axis', formatter: params => {
        let s = `<b>${params[0].axisValue}</b><br/>`;
        params.forEach(p => s += `${p.marker}${p.seriesName}: <b>${fmtTok(p.value)}</b><br/>`);
        return s;
      }
    }),
    legend: { top: 0, textStyle: { color: c.dim } },
    grid: { left: 50, right: 20, top: 40, bottom: 30 },
    xAxis: { type: 'category', boundaryGap: false, data: r.trend.dates, axisLine: axisLine(), axisLabel: axisLabel(), axisTick: { show: false } },
    yAxis: { type: 'value', axisLine: { show: false }, splitLine: splitLine(), axisLabel: Object.assign({}, axisLabel(), { formatter: v => fmtTok(v) }) },
    series: [
      { name: '输入', type: 'line', smooth: true, symbol: 'circle', symbolSize: 6, data: r.trend.input, lineStyle: { width: 2, color: '#22d3ee' }, itemStyle: { color: '#22d3ee' }, areaStyle: mkArea('#22d3ee') },
      { name: '输出', type: 'line', smooth: true, symbol: 'circle', symbolSize: 6, data: r.trend.output, lineStyle: { width: 2, color: '#34d399' }, itemStyle: { color: '#34d399' }, areaStyle: mkArea('#34d399') }
    ]
  };
}

function buildWeekly(r) {
  if (!r.weekly.weeks.length) return null;
  const c = themeColors();
  return {
    tooltip: Object.assign({}, tooltip(), {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: params => {
        let s = `<b>周起始: ${params[0].axisValue}</b><br/>`;
        params.forEach(p => { if (p.value) s += `${p.marker}${p.seriesName}: <b>${fmtTok(p.value)}</b><br/>`; });
        return s;
      }
    }),
    legend: { type: 'scroll', top: 0, textStyle: { color: c.dim, fontSize: 10 }, pageTextStyle: { color: c.dim } },
    grid: { left: 50, right: 20, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: r.weekly.weeks, axisLine: axisLine(), axisLabel: axisLabel(), axisTick: { show: false } },
    yAxis: { type: 'value', axisLine: { show: false }, splitLine: splitLine(), axisLabel: Object.assign({}, axisLabel(), { formatter: v => fmtTok(v) }) },
    series: r.weekly.series
  };
}

function buildRadar(r) {
  if (!r.radar.values.some(v => v > 0)) return null;
  const c = themeColors();
  return {
    tooltip: Object.assign({}, tooltip(), { formatter: p => `时段 <b>${String(p.dimensionIndex).padStart(2, '0')}时</b><br/>用量: <b>${fmtTok(p.value)}</b>` }),
    radar: {
      indicator: r.radar.indicator, center: ['50%', '52%'], radius: '68%', shape: 'polygon',
      axisName: { color: c.dim, fontSize: 10 },
      splitLine: { lineStyle: { color: c.axis } },
      splitArea: { areaStyle: { color: ['rgba(167,139,250,0.02)', 'rgba(167,139,250,0.05)'] } },
      axisLine: { lineStyle: { color: c.axis } }
    },
    series: [{
      type: 'radar',
      data: [{
        value: r.radar.values, name: '时段用量',
        areaStyle: { color: new echarts.graphic.RadialGradient(0.5, 0.5, 1, [{ offset: 0, color: 'rgba(167,139,250,0.05)' }, { offset: 1, color: 'rgba(244,114,182,0.45)' }]) },
        lineStyle: { width: 2, color: '#f472b6' }, itemStyle: { color: '#f472b6' }
      }]
    }]
  };
}

function buildProj(r) {
  if (!r.projects.names.length) return null;
  const c = themeColors();
  return {
    tooltip: Object.assign({}, tooltip(), { formatter: p => `<b>${p.name}</b><br/>${fmtTok(p.value)} tokens` }),
    grid: { left: 10, right: 30, top: 10, bottom: 20, containLabel: true },
    xAxis: { type: 'value', axisLine: { show: false }, splitLine: splitLine(), axisLabel: Object.assign({}, axisLabel(), { formatter: v => fmtTok(v) }) },
    yAxis: { type: 'category', data: r.projects.names.slice().reverse(), axisLine: axisLine(), axisLabel: Object.assign({}, axisLabel(), { fontSize: 11 }), axisTick: { show: false } },
    series: [{
      type: 'bar', barWidth: '55%',
      data: r.projects.values.slice().reverse().map(v => ({
        value: v,
        itemStyle: { borderRadius: [0, 6, 6, 0], color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [{ offset: 0, color: '#a78bfa' }, { offset: 1, color: '#22d3ee' }]) }
      })),
      label: { show: true, position: 'right', color: c.text, fontSize: 11, formatter: p => fmtTok(p.value) }
    }]
  };
}

// ============ 渲染各区域 ============
function renderMeta(r) {
  document.getElementById('m-span').textContent = r.label;
  document.getElementById('m-gen').textContent = P.generated_at;
}

function renderKpi(r, animate) {
  const k = r.kpi;
  const data = [
    { label: '总 Token', raw: k.total_tokens, val: fmtTok(k.total_tokens), sub: fmtNum(k.total_tokens) + ' tokens', grad: 'linear-gradient(135deg,#a78bfa,#f472b6)', anim: 'tok' },
    { label: '输入', raw: k.total_input, val: fmtTok(k.total_input), sub: 'prompt tokens', grad: 'linear-gradient(135deg,#22d3ee,#60a5fa)', anim: 'tok' },
    { label: '输出', raw: k.total_output, val: fmtTok(k.total_output), sub: 'completion tokens', grad: 'linear-gradient(135deg,#34d399,#a3e635)', anim: 'tok' },
    { label: '缓存', raw: k.total_cache, val: fmtTok(k.total_cache), sub: 'read + write', grad: 'linear-gradient(135deg,#fbbf24,#f97316)', anim: 'tok' },
    { label: '消息数', raw: k.total_msgs, val: fmtNum(k.total_msgs), sub: 'assistant turns', grad: 'linear-gradient(135deg,#fb7185,#f472b6)', anim: 'num' },
    { label: '模型 / 活跃天', val: k.model_count + ' / ' + k.active_days, sub: 'models / active days', grad: 'linear-gradient(135deg,#c084fc,#22d3ee)', anim: '' }
  ];
  document.getElementById('kpi').innerHTML = data.map(d => `
    <div class="kpi" style="--grad:${d.grad}">
      <div class="label">${d.label}</div>
      <div class="value" data-raw="${d.raw || 0}" data-anim="${d.anim}">${d.val}</div>
      <div class="sub">${d.sub}</div>
    </div>`).join('');
  if (animate) {
    document.querySelectorAll('.kpi .value').forEach(el => {
      const a = el.dataset.anim, raw = parseFloat(el.dataset.raw);
      if (a === 'tok') animateCount(el, raw, fmtTok);
      else if (a === 'num') animateCount(el, raw, fmtNum);
    });
  }
}

function renderTable(r) {
  const tbody = document.getElementById('tbody');
  const totalAll = r.table.reduce((a, x) => a + x.total, 0);
  tbody.innerHTML = r.table.map(x => `
    <tr data-model="${x.model}">
      <td><span class="model-tag"><span class="swatch" style="background:${x.color};color:${x.color}"></span>${x.model}</span></td>
      <td class="num">${fmtNum(x.input)}</td>
      <td class="num" style="color:var(--text-dim)">${fmtNum(x.cache_write)}</td>
      <td class="num" style="color:var(--text-dim)">${fmtNum(x.cache_read)}</td>
      <td class="num">${fmtNum(x.output)}</td>
      <td class="num"><b style="color:${x.color}">${fmtNum(x.total)}</b></td>
      <td class="num">${fmtNum(x.msgs)}</td>
      <td class="num">${x.pct}%<span class="pct-bar"><span class="pct-fill" style="width:${x.pct}%;background:${x.color}"></span></span></td>
    </tr>`).join('') + `
    <tr class="total-row">
      <td>TOTAL</td>
      <td class="num">${fmtNum(r.table.reduce((a, x) => a + x.input, 0))}</td>
      <td class="num">${fmtNum(r.table.reduce((a, x) => a + x.cache_write, 0))}</td>
      <td class="num">${fmtNum(r.table.reduce((a, x) => a + x.cache_read, 0))}</td>
      <td class="num">${fmtNum(r.table.reduce((a, x) => a + x.output, 0))}</td>
      <td class="num">${fmtNum(totalAll)}</td>
      <td class="num">${fmtNum(r.table.reduce((a, x) => a + x.msgs, 0))}</td>
      <td class="num">100%</td>
    </tr>`;
  tbody.querySelectorAll('tr[data-model]').forEach(tr => {
    tr.addEventListener('click', () => focusModel(tr.dataset.model));
  });
}

function renderSparkline() {
  const dom = document.getElementById('spark');
  if (!dom || !P.sparkline.length) return;
  if (!charts.spark) charts.spark = echarts.init(dom);
  charts.spark.setOption({
    grid: { left: 0, right: 0, top: 4, bottom: 0 },
    xAxis: { type: 'category', show: false, boundaryGap: false, data: P.sparkline.map(d => d.name) },
    yAxis: { type: 'value', show: false },
    tooltip: { show: false },
    series: [{
      type: 'line', smooth: true, symbol: 'none', data: P.sparkline.map(d => d.value),
      lineStyle: { width: 2.5, color: '#a78bfa' },
      areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(167,139,250,0.50)' }, { offset: 1, color: 'rgba(167,139,250,0.02)' }]) }
    }]
  }, true);
}

function animateHero(r) {
  const el = document.getElementById('hero-num');
  el.dataset.raw = r.kpi.total_tokens;
  animateCount(el, r.kpi.total_tokens, fmtTok);
  document.getElementById('hero-sub').textContent = `${fmtNum(r.kpi.total_tokens)} tokens · ${r.kpi.active_days} 天 · ${r.kpi.model_count} 个模型`;
}

function renderStreak() {
  const g = P.gamification;
  const cards = [];
  const st = g.streak;
  cards.push(`<div class="streak-card" style="--sc-grad:linear-gradient(180deg,#fb923c,#f472b6)">
    <div class="sc-label">🔥 连续打卡</div>
    <div class="sc-main sc-fire">${st.current}<small> 天</small></div>
    <div class="sc-sub">最长 ${st.longest} 天 · ${st.active_today ? '今日已用 ✓' : '今日暂无'}</div>
  </div>`);
  const w = g.wow;
  const dp = w.delta_pct;
  const cls = dp === null ? 'sc-flat' : (dp > 0 ? 'sc-up' : dp < 0 ? 'sc-down' : 'sc-flat');
  const arrow = dp === null ? '—' : (dp > 0 ? '▲' : dp < 0 ? '▼' : '–');
  cards.push(`<div class="streak-card" style="--sc-grad:linear-gradient(180deg,#34d399,#22d3ee)">
    <div class="sc-label">📈 本周环比</div>
    <div class="sc-main ${cls}">${dp === null ? '—' : arrow + ' ' + Math.abs(dp) + '%'}</div>
    <div class="sc-sub">本周 ${fmtTok(w.this_week)} · 上周 ${fmtTok(w.last_week)}</div>
  </div>`);
  const ww = g.weekday_weekend;
  const tot = ww.weekday.total + ww.weekend.total;
  const wp = tot ? (ww.weekday.total / tot * 100) : 0;
  const wAvg = ww.weekday.days ? ww.weekday.total / ww.weekday.days : 0;
  const eAvg = ww.weekend.days ? ww.weekend.total / ww.weekend.days : 0;
  cards.push(`<div class="streak-card" style="--sc-grad:linear-gradient(180deg,#60a5fa,#a78bfa)">
    <div class="sc-label">🗓️ 工作日 / 周末</div>
    <div class="sc-main">${wp.toFixed(0)}<small>% 工作日</small></div>
    <div class="sc-bar"><div class="seg-w" style="width:${wp}%"></div><div class="seg-e" style="width:${100 - wp}%"></div></div>
    <div class="sc-sub">日均 ${fmtTok(wAvg)} · 周末日均 ${fmtTok(eAvg)}</div>
  </div>`);
  const pk = g.peak;
  cards.push(`<div class="streak-card" style="--sc-grad:linear-gradient(180deg,#fbbf24,#f97316)">
    <div class="sc-label">🏆 最卷的一天</div>
    <div class="sc-main">${pk ? fmtTok(pk.total) : '—'}</div>
    <div class="sc-sub">${pk ? pk.date + ' · ' + fmtNum(pk.msgs) + ' 条消息' : '暂无数据'}</div>
  </div>`);
  document.getElementById('streak-grid').innerHTML = cards.join('');
}

// ============ 图表重绘 / range 切换 ============
function redrawCharts() {
  const r = P.ranges[currentRange];
  const apply = (key, opt) => { if (charts[key]) { if (opt) charts[key].setOption(opt, true); else charts[key].clear(); } };
  apply('calendar', buildCalendar());
  apply('daily', buildDaily(r));
  apply('pie', buildPie(r));
  apply('trend', buildTrend(r));
  apply('weekly', buildWeekly(r));
  apply('radar', buildRadar(r));
  apply('proj', buildProj(r));
}

function setRange(key) {
  if (!P.ranges[key]) key = 'week';
  currentRange = key;
  focusedModel = null;
  const r = P.ranges[key];
  renderMeta(r);
  renderKpi(r, true);
  renderTable(r);
  redrawCharts();
  animateHero(r);
  document.querySelectorAll('.range-tabs button').forEach(b => b.classList.toggle('active', b.dataset.range === key));
  history.replaceState(null, '', '#range=' + key);
}

// ============ 模型聚焦 (点表格行) ============
function focusModel(name) {
  focusedModel = focusedModel === name ? null : name;
  ['daily', 'weekly'].forEach(key => {
    const ch = charts[key];
    if (!ch) return;
    P.models.forEach(m => {
      ch.dispatchAction({ type: focusedModel && m !== focusedModel ? 'legendUnSelect' : 'legendSelect', name: m });
    });
  });
  document.querySelectorAll('#tbody tr[data-model]').forEach(tr => {
    tr.classList.toggle('focused', !!(focusedModel && tr.dataset.model === focusedModel));
  });
}

// ============ 主题 ============
function syncThemeIcon() {
  const t = document.documentElement.getAttribute('data-theme') || 'dark';
  const icon = document.getElementById('theme-icon');
  if (icon) icon.textContent = t === 'light' ? '☀️' : '🌙';
}
function toggleTheme() {
  const cur = document.documentElement.getAttribute('data-theme') || 'dark';
  const next = cur === 'light' ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', next);
  try { localStorage.setItem('ccusage-theme', next); } catch (e) {}
  syncThemeIcon();
  redrawCharts();
  renderSparkline();
}

// ============ reveal / 视差 ============
function setupReveal() {
  const els = document.querySelectorAll('.reveal');
  if (prefersReduced || !('IntersectionObserver' in window)) { els.forEach(e => e.classList.add('in')); return; }
  els.forEach((el, i) => { el.style.transitionDelay = Math.min(i * 60, 360) + 'ms'; });
  const io = new IntersectionObserver(entries => {
    entries.forEach(e => { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } });
  }, { threshold: 0.12 });
  els.forEach(el => io.observe(el));
}
function setupParallax() {
  if (prefersReduced) return;
  let raf = null;
  window.addEventListener('mousemove', e => {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      document.body.style.setProperty('--mx', (e.clientX / window.innerWidth - 0.5).toFixed(3));
      document.body.style.setProperty('--my', (e.clientY / window.innerHeight - 0.5).toFixed(3));
      raf = null;
    });
  });
}

// ============ 导出 (兼容动画终态) ============
function forceFinalState(on) {
  exporting = on;
  document.querySelectorAll('.reveal').forEach(el => el.classList.add('in'));
  if (on) {
    document.querySelectorAll('.kpi .value, #hero-num').forEach(el => {
      const raw = parseFloat(el.dataset.raw);
      if (!isNaN(raw)) el.textContent = (el.dataset.anim === 'num') ? fmtNum(raw) : fmtTok(raw);
    });
  }
}
function fixGradientText(on) {
  const els = document.querySelectorAll('h1, .hero-num, .kpi .value, .btn .label');
  els.forEach(el => {
    if (on) { el.dataset.fill = el.style.webkitTextFillColor || ''; el.style.webkitTextFillColor = 'currentColor'; el.style.color = '#a78bfa'; }
    else { el.style.webkitTextFillColor = el.dataset.fill || ''; el.style.color = ''; }
  });
}
const exportOverlay = document.getElementById('export-overlay');
const exportText = document.getElementById('export-text');
const toastEl = document.getElementById('toast');
function showToast(msg) { toastEl.textContent = msg; toastEl.classList.add('show'); clearTimeout(showToast._t); showToast._t = setTimeout(() => toastEl.classList.remove('show'), 2800); }
function safeName() {
  const s = (P.data_start || P.range_start || '').replace(/[^0-9]/g, '');
  const e = (P.data_end || P.range_end || '').replace(/[^0-9]/g, '');
  return 'cc-usage-' + s + '-to-' + e;
}
async function capturePage() {
  exportOverlay.classList.add('show');
  document.querySelector('.actions').style.visibility = 'hidden';
  document.querySelector('.range-tabs').style.visibility = 'hidden';
  forceFinalState(true);
  fixGradientText(true);
  await new Promise(r => requestAnimationFrame(r));
  await new Promise(r => setTimeout(r, 90));
  try {
    const target = document.querySelector('.wrap');
    const canvas = await html2canvas(target, { backgroundColor: '#07070d', scale: 2, useCORS: true, allowTaint: false, logging: false, windowWidth: target.scrollWidth, width: target.scrollWidth, height: target.scrollHeight });
    return canvas;
  } finally {
    fixGradientText(false);
    forceFinalState(false);
    document.querySelector('.actions').style.visibility = '';
    const rt = document.querySelector('.range-tabs'); if (rt) rt.style.visibility = '';
    exportOverlay.classList.remove('show');
  }
}
async function exportPNG() {
  const btn = document.getElementById('btn-png'); if (btn.disabled) return;
  btn.disabled = true; const orig = btn.innerHTML;
  btn.innerHTML = '<span class="spinner"></span><span class="label">渲染中...</span>';
  exportText.textContent = '正在生成 PNG...';
  try {
    const canvas = await capturePage();
    canvas.toBlob(blob => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a'); a.href = url; a.download = safeName() + '.png';
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      showToast('✅ PNG 已下载');
    }, 'image/png');
  } catch (e) { console.error(e); showToast('❌ 导出失败: ' + (e.message || e)); }
  finally { btn.disabled = false; btn.innerHTML = orig; }
}
async function exportPDF() {
  const btn = document.getElementById('btn-pdf'); if (btn.disabled) return;
  btn.disabled = true; const orig = btn.innerHTML;
  btn.innerHTML = '<span class="spinner"></span><span class="label">渲染中...</span>';
  exportText.textContent = '正在生成 PDF...';
  try {
    const canvas = await capturePage();
    const jsPDF = window.jspdf.jsPDF;
    const pdf = new jsPDF({ orientation: 'p', unit: 'mm', format: 'a4' });
    const pageW = pdf.internal.pageSize.getWidth();
    const pageH = pdf.internal.pageSize.getHeight();
    const imgH = (canvas.height * pageW) / canvas.width;
    const imgData = canvas.toDataURL('image/png');
    let heightLeft = imgH, position = 0;
    pdf.addImage(imgData, 'PNG', 0, position, pageW, imgH, undefined, 'FAST');
    heightLeft -= pageH;
    while (heightLeft > 0) { position -= pageH; pdf.addPage(); pdf.addImage(imgData, 'PNG', 0, position, pageW, imgH, undefined, 'FAST'); heightLeft -= pageH; }
    pdf.save(safeName() + '.pdf');
    showToast('✅ PDF 已下载');
  } catch (e) { console.error(e); showToast('❌ 导出失败: ' + (e.message || e)); }
  finally { btn.disabled = false; btn.innerHTML = orig; }
}

// ============ 初始化 ============
(function init() {
  syncThemeIcon();
  const ids = { calendar: 'chart-calendar', daily: 'chart-daily', pie: 'chart-pie', trend: 'chart-trend', weekly: 'chart-weekly', radar: 'chart-radar', proj: 'chart-proj' };
  Object.keys(ids).forEach(k => { const dom = document.getElementById(ids[k]); if (dom) charts[k] = echarts.init(dom); });

  renderSparkline();
  const pulse = document.getElementById('hero-pulse-text');
  if (pulse) pulse.textContent = '已记录 ' + P.calendar.length + ' 天 · 连续 ' + P.gamification.streak.current + ' 天';
  renderStreak();

  const hashKey = (location.hash.match(/range=([a-z]+)/) || [])[1];
  setRange(P.ranges[hashKey] ? hashKey : P.range_default);

  setupReveal();
  setupParallax();
  document.querySelectorAll('.range-tabs button').forEach(b => b.addEventListener('click', () => setRange(b.dataset.range)));
  document.getElementById('theme-toggle').addEventListener('click', toggleTheme);
  document.getElementById('btn-png').addEventListener('click', exportPNG);
  document.getElementById('btn-pdf').addEventListener('click', exportPDF);
  window.addEventListener('resize', () => Object.keys(charts).forEach(k => charts[k] && charts[k].resize()));
})();
"""
