---
description: 查看 Claude Code 用量统计 (token by model)
---

你想查看 Claude Code 的用量统计。请根据参数 `$ARGUMENTS` 执行对应的命令，把**标准输出原样展示给用户**（保持终端彩色和表格格式，用代码块包起来），并在最后附上简短说明。

参数解析规则（忽略大小写、去除前后空格）：
- 空参数、`today` → 执行 `cc-usage today`，展示今日每个模型的 token 用量
- `week` → 执行 `cc-usage week`，展示本周统计
- `month` → 执行 `cc-usage month`，展示本月统计
- `report` → 执行 `cc-usage report --range week --open`，生成炫酷的 HTML 报告并用浏览器打开；告诉用户文件路径
- `report month` / `report all` → 执行 `cc-usage report --range month --open` 或 `--range all --open`
- `sync` → 执行 `cc-usage sync`，刷新数据库
- 其他无法识别的参数 → 提示支持的用法：`/use [today|week|month|report [today|week|month|all]|sync]`

注意：
1. 如果提示「数据库不存在」或「⚠️」，先静默执行一次 `cc-usage sync`（首次约 10 秒），然后再执行用户原本要的命令。
2. 终端输出里有 ANSI 颜色码时，包在 ```bash 代码块里展示即可，不要额外加自己的解释，让数字说话。
3. 最后用一句话告诉用户：想看炫酷图表可以运行 `/use report`。
