"""ccusage — Claude Code 用量统计工具.

扫描 ~/.claude/projects/*/*.jsonl 中的 assistant 记录, 提取每个模型的
token 用量 (input / cache_creation / cache_read / output), 按天/周/月
聚合, 输出终端表格和炫酷的 HTML 报告.
"""

__version__ = "1.0.0"
