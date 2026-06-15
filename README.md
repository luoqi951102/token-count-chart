# 🎯 cc-usage · Claude Code 用量统计

扫描本地 `~/.claude/projects/*/*.jsonl`，统计每个模型每天消耗的 token（input / cache_creation / cache_read / output），输出**终端彩色报告**和**炫酷的 HTML 仪表盘**。

> 零依赖（纯 Python 标准库）· 增量同步 · Asia/Shanghai 时区分桶 · 单文件 HTML 离线可看

---

## ✨ 功能

- 📊 **多维统计**：每个模型每日的 input / cache_write / cache_read / output / 总上下文 / 消息数
- 📅 **多时间窗**：今日 / 本周 / 本月 / 全部历史
- 🎨 **炫酷 HTML 仪表盘**：日历热力图、堆叠柱状图、环形饼图、趋势折线、周对比、雷达图、项目排行
- ⚡ **增量同步**：首次 ~2 秒解析全量，之后每次 <1 秒（按文件 mtime 去重）
- 🌏 **时区正确**：UTC 时间戳转 Asia/Shanghai 后再按天分桶
- 💻 **终端彩色输出**：ANSI 表格 + 进度条

---

## 🚀 快速开始

### 1. 全局安装（软链）

```bash
git clone <repo> ~/work/token-count
ln -sf ~/work/token-count/scripts/cc-usage ~/.local/bin/cc-usage
# （确保 ~/.local/bin 在 PATH 中）
```

### 2. 首次同步数据

```bash
cc-usage sync
```

扫描 `~/.claude/projects/` 下所有 JSONL，写入 SQLite。首次几秒，之后增量。

### 3. 查看统计

```bash
cc-usage today       # 今日终端报告
cc-usage week        # 本周
cc-usage month       # 本月
cc-usage status      # 数据库状态
```

### 4. 生成 HTML 仪表盘

```bash
cc-usage report --range all --open      # 全历史报告并打开浏览器
cc-usage report --range month --open    # 本月
cc-usage report --range week            # 本周（不自动打开）
```

报告输出到 `~/.claude/ccusage-output/`。

---

## 🤖 在 Claude Code 里使用

已部署 slash command `/use`：

```
/use              # 今日用量
/use week         # 本周
/use month        # 本月
/use report       # 生成本周 HTML 报告
/use report all   # 全历史报告
/use sync         # 刷新数据库
```

---

## 📂 项目结构

```
token-count/
├── ccusage/
│   ├── parser.py          # JSONL → UsageRecord 流
│   ├── db.py              # SQLite + 增量同步
│   ├── aggregate.py       # 日/周/月聚合（Asia/Shanghai）
│   ├── report_text.py     # 终端彩色表格
│   ├── report_html.py     # ECharts 仪表盘
│   └── cli.py             # 命令行入口
├── scripts/cc-usage       # 全局入口（软链到 ~/.local/bin）
├── commands/use.md        # → 复制到 ~/.claude/commands/
├── output/                # 本地 HTML（gitignore）
└── tests/
```

---

## 🛠️ 数据来源

| 字段 | 来源 |
|---|---|
| 时间 | JSONL 行的 `timestamp`（UTC） |
| 模型 | `message.model` |
| token | `message.usage.{input,cache_creation_input,cache_read_input,output}_tokens` |
| 会话 | `sessionId` |
| 项目目录 | `cwd` |

数据存储：`~/.claude/ccusage.db`（SQLite + WAL）。

---

## ❓ FAQ

**Q: 为什么不直接用官方 `ccusage` npm 包？**
A: 你走的是第三方路由（`claude-code-switch`），用的模型是 qwen3.6-plus / glm / deepseek 等国产模型，官方包不认这些名字，也没有炫酷仪表盘。

**Q: 会改 Claude Code 的文件吗？**
A: 不会。只读 `~/.claude/projects/`，自己写一份独立的 SQLite。

**Q: 数据多久更新一次？**
A: 想看最新就跑一次 `cc-usage sync`，秒级。或者 `/use sync`。

**Q: 能改时区吗？**
A: 当前写死 Asia/Shanghai。要改去 `ccusage/db.py` 和 `aggregate.py` 改 `ZoneInfo`。
