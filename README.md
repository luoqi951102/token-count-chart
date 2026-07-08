# 🎯 cc-usage · Claude Code / ZCode 用量统计

同时扫描 **Claude Code**（`~/.claude/projects/*/*.jsonl`）和 **ZCode**（`~/.zcode/cli/db/db.sqlite`）的会话数据，统计每个模型每天消耗的 token（input / cache_creation / cache_read / output），输出**终端彩色报告**和**炫酷的 HTML 仪表盘**，看板支持一键切换两个工具的用量对比。

> 零依赖（纯 Python 标准库）· 双源增量同步 · Asia/Shanghai 时区分桶 · 单文件 HTML 离线可看

---

## ✨ 功能

- 📊 **多维统计**：每个模型每日的 input / cache_write / cache_read / output / 总上下文 / 消息数
- 📅 **多时间窗**：今日 / 本周 / 上周 / 本月 / 全部历史
- 🔀 **双工具来源切换**：看板顶部「全部 / Claude / ZCode」全局切换器 + Claude/ZCode 占比卡，一键对比两个工具的用量
- 🎨 **炫酷 HTML 仪表盘**：7 图表 + Hero 超大 count-up + 滚动入场动效 + 游戏化指标(连续打卡 / 周环比 / 工作日周末 / 最卷一天 / Claude/ZCode 占比)+ 前端 range×source 一键切换 + 明暗主题 + 模型聚焦
- ⚡ **增量同步**：Claude 按文件 mtime+size 去重，ZCode 按 completed_at 水位线增量，首次几秒、之后秒级
- 🌏 **时区正确**：UTC 时间戳转 Asia/Shanghai 后再按天分桶
- 💻 **终端彩色输出**：ANSI 表格 + 进度条
- 🧪 **测试覆盖**：pytest 关键路径测试（范围解析 / source 过滤 / 幂等同步 / 防崩）

---

## 🚀 快速开始

### 方式一：一键安装（推荐）

```bash
curl -fsSL https://raw.githubusercontent.com/luoqi951102/token-count-chart/main/install.sh | bash
```

装完重开终端（或 `source ~/.zshrc`），然后 `cc-usage sync` 同步数据、`ccuf` 一键打开报告。卸载：`bash ~/.cc-usage/uninstall.sh`。升级：重跑同一条 curl 命令即可。

### 方式二：手动软链

```bash
git clone <repo> ~/work/token-count
ln -sf ~/work/token-count/scripts/cc-usage ~/.local/bin/cc-usage
# （确保 ~/.local/bin 在 PATH 中）
```

### 2. 首次同步数据

```bash
cc-usage sync                        # 同步 Claude + ZCode 双源
cc-usage sync --only zcode           # 只同步 ZCode
cc-usage sync --only claude --force  # 强制全量重解析 Claude
```

Claude 扫描 `~/.claude/projects/` 下所有 JSONL，ZCode 读取 `~/.zcode/cli/db/db.sqlite` 的 `model_usage` 表，统一写入 SQLite。首次几秒，之后增量。

### 3. 查看统计

```bash
cc-usage today                 # 今日终端报告（双源合计）
cc-usage today --source zcode  # 只看今日 ZCode 用量
cc-usage week                  # 本周
cc-usage month                 # 本月
cc-usage status                # 数据库状态（含来源分布）
```

### 4. 生成 HTML 仪表盘

```bash
cc-usage report --range all --open           # 全历史报告并打开浏览器
cc-usage report --range last_week --open     # 上周
cc-usage report --range month --source zcode # 本月只看 ZCode
cc-usage open --fresh                        # 一键刷新并打开（双源同步）
```

报告输出到 `~/.claude/ccusage-output/`。看板顶部「全部 / Claude / ZCode」可随时切换对比。

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
│   ├── parser.py          # JSONL → UsageRecord 流 + ZCode 库定位
│   ├── db.py              # SQLite + 增量同步（Claude mtime / ZCode 水位线）
│   ├── aggregate.py       # 日/周/月聚合（Asia/Shanghai，支持 source 过滤）
│   ├── report_text.py     # 终端彩色表格
│   ├── report_html.py     # ECharts 仪表盘（source×range 二维预计算）
│   ├── report_html_css.py # 仪表盘 CSS
│   ├── report_html_js.py  # 仪表盘 JS（range/source 切换 + 占比卡）
│   └── cli.py             # 命令行入口
├── scripts/cc-usage       # 全局入口（软链到 ~/.local/bin）
├── commands/use.md        # → 复制到 ~/.claude/commands/
├── tests/                 # pytest 测试（14 用例）
├── output/                # 本地 HTML（gitignore）
└── requirements.txt       # 可选 dev 依赖（pytest）
```

---

## 🛠️ 数据来源

| 字段 | Claude Code | ZCode |
|---|---|---|
| 存储形式 | `~/.claude/projects/*/*.jsonl` | `~/.zcode/cli/db/db.sqlite` |
| 时间 | JSONL 行的 `timestamp`（UTC ISO） | `model_usage.started_at`（毫秒 epoch） |
| 模型 | `message.model` | `model_usage.model_id` |
| token | `message.usage.{input,cache_creation_input,cache_read_input,output}_tokens` | 同名字段 |
| 项目目录 | `cwd` | JOIN `session.directory` |

数据存储：`~/.claude/ccusage.db`（SQLite + WAL，`source` 列区分来源）。

### 🧪 测试

```bash
pip install -r requirements.txt   # 安装 pytest
PYTHONPATH=. python3 -m pytest tests/ -v
```

---

## ❓ FAQ

**Q: 为什么不直接用官方 `ccusage` npm 包？**
A: 你走的是第三方路由（`claude-code-switch` / ZCode），用的模型是 qwen / glm / deepseek 等国产模型，官方包不认这些名字，也没有双工具对比的炫酷仪表盘。

**Q: 支持 ZCode 吗？**
A: 支持。自动读取 `~/.zcode/cli/db/db.sqlite` 的 `model_usage` 表，与 Claude Code 数据统一落库，看板可一键切换对比。没有装 ZCode 也不影响，会自动跳过。

**Q: 会改 Claude Code / ZCode 的文件吗？**
A: 不会。只读它们的数据目录，自己写一份独立的 SQLite。

**Q: 数据多久更新一次？**
A: 想看最新就跑一次 `cc-usage sync`，秒级。或者 `ccuf`（= `cc-usage open --fresh`）。

**Q: 能改时区吗？**
A: 当前写死 Asia/Shanghai。时区常量 `SH` 定义在 `aggregate.py`，`db.py` 复用它，改一处即可。
