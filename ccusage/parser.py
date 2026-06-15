"""解析 Claude Code 会话 JSONL 文件, 提取 token 用量记录.

每个 ~/.claude/projects/<project>/<sessionId>.jsonl 文件是一个会话,
其中 type=='assistant' 的行包含 message.usage 字段, 是我们的数据源.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass
class UsageRecord:
    """单条 assistant 消息的用量记录."""

    timestamp: str  # ISO UTC, e.g. 2026-05-29T09:05:31.140Z
    model: str
    input_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    output_tokens: int
    session_id: str
    cwd: str
    project: str
    source_file: str


# 跳过这些非真实模型
_IGNORED_MODELS = {"<synthetic>", "", None}


def default_projects_dir() -> Path:
    """返回默认的 Claude Code projects 目录."""
    return Path.home() / ".claude" / "projects"


def iter_jsonl_files(projects_dir: Path) -> Iterator[tuple[Path, str]]:
    """遍历所有会话 JSONL 文件, yield (filepath, project_name)."""
    if not projects_dir.exists():
        return
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        for f in sorted(project_dir.glob("*.jsonl")):
            yield f, project_dir.name


def parse_file(filepath: Path, project: str) -> Iterator[UsageRecord]:
    """解析单个 JSONL 会话文件, yield UsageRecord 流.

    容错: 跳过空行、JSON 解析失败、非 assistant 行、缺 usage 的行.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(d, dict) or d.get("type") != "assistant":
                    continue
                msg = d.get("message") or {}
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage")
                if not isinstance(usage, dict) or not usage:
                    continue
                model = msg.get("model")
                if model in _IGNORED_MODELS:
                    continue
                ts = d.get("timestamp")
                if not ts:
                    continue
                yield UsageRecord(
                    timestamp=ts,
                    model=str(model),
                    input_tokens=int(usage.get("input_tokens") or 0),
                    cache_creation_input_tokens=int(
                        usage.get("cache_creation_input_tokens") or 0
                    ),
                    cache_read_input_tokens=int(
                        usage.get("cache_read_input_tokens") or 0
                    ),
                    output_tokens=int(usage.get("output_tokens") or 0),
                    session_id=str(d.get("sessionId") or ""),
                    cwd=str(d.get("cwd") or ""),
                    project=project,
                    source_file=str(filepath),
                )
    except (OSError, UnicodeDecodeError):
        return


def file_signature(filepath: Path) -> tuple[float, int]:
    """返回文件的 (mtime, size), 用于增量同步去重."""
    stat = filepath.stat()
    return stat.st_mtime, stat.st_size
