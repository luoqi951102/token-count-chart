# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`cc-usage` is a zero-dependency (pure Python stdlib) CLI that tallies token usage from local Claude Code sessions (`~/.claude/projects/*/*.jsonl`), aggregates by day/week/month/model in Asia/Shanghai time, and prints a colored terminal report or renders a single-file ECharts HTML dashboard.

It exists because the user routes Claude Code through third-party routers (`claude-code-switch`) using domestic models (qwen/glm/deepseek) that the official `ccusage` npm package doesn't recognize. **Do not introduce third-party runtime dependencies** — stdlib-only is a hard design constraint. Optional dev/test deps go in `requirements.txt` only.

## Commands

The CLI is invoked via the `scripts/cc-usage` wrapper (symlinked to `~/.local/bin/cc-usage`), which resolves its own symlink to find the project root and prepends it to `PYTHONPATH` before running `python3 -m ccusage.cli`.

```bash
# Run directly during development (no install needed):
PYTHONPATH=. python3 -m ccusage.cli <command>

cc-usage sync                 # Incremental parse of JSONL → SQLite (run first)
cc-usage today | week | month # Terminal report for a window
cc-usage status               # DB row/file/model counts, last sync, data span
cc-usage report --range {today|week|month|all} [--open]   # HTML dashboard to ~/.claude/ccusage-output/
cc-usage open [--fresh]       # Open most recent report; --fresh re-syncs + regenerates first
cc-usage sync --force         # Re-parse every file (ignore mtime cache)
```

There are no tests, lint, or build steps configured yet (`tests/` is empty). When adding tests, use pytest.

The Claude Code slash command `/use` (`commands/use.md`, copied into `~/.claude/commands/`) wraps these CLI calls and tells Claude to show raw stdout verbatim.

## Architecture — the data pipeline

Data flows one direction through clearly separated layers, each in its own module:

```
~/.claude/projects/*/*.jsonl
        │  parser.py     — JSONL → UsageRecord stream (only type=='assistant' rows with message.usage)
        ▼
   SQLite (~/.claude/ccusage.db, WAL mode)        db.py
        │                      · usage table: one row per assistant message
        │                      · files table: mtime+size signature per source file (incremental sync)
        │                      · meta table: last_sync timestamp
        │  aggregate.py — SQL GROUP BY into day/model/hour/week/project dicts
        ▼
   report_text.py (ANSI terminal table)  |  report_html.py (single-file ECharts dashboard)
        ▼
   stdout / ~/.claude/ccusage-output/usage-<tag>.html (+ latest.html symlink)
```

`cli.py` is the only orchestrator — it opens one SQLite connection and threads it through `aggregate` + `report_*`. The aggregate functions take `(conn, start, end)` and return plain `list[dict]`; both reporters consume the same dict shape (`input`, `cache_write`, `cache_read`, `output`, `total`, `msgs`).

### Three things you must know before changing code

1. **Timezone is bucketed at parse time, not query time.** `db.py:_local_parts()` converts each record's UTC `timestamp` to Asia/Shanghai and stores `local_date` + `local_hour` columns. All aggregation filters on `local_date BETWEEN ? AND ?`. `ZoneInfo("Asia/Shanghai")` is hardcoded in both `db.py` and `aggregate.py` (`SH` constant). Changing the timezone means touching both.

2. **Incremental sync keys off `(mtime, size)`, not content.** `db.py:sync()` skips files whose signature in the `files` table matches; changed files are DELETE-then-INSERT by `source_file`; files that vanished from disk are pruned. `--force` bypasses the signature check and re-parses everything. The model `<synthetic>` is filtered out in `parser.py`.

3. **`report_html.py` is ~1000 lines, ~80% of it is one HTML template string** (`_build_html`) containing inline CSS + JS that drives ECharts from a CDN. The Python side (`render`) just collects aggregate dicts into one `p` dict and injects it as JSON. When tweaking the dashboard, edit `_build_html`'s template and the JS option objects — the Python data-shaping in `render()` maps 1:1 to the chart configs below it.

### Key conventions

- Date ranges are `DateRange(start, end, label)` with inclusive `YYYY-MM-DD` boundaries; `"all"` is represented as `"2000-01-01".."2099-12-31"` (see `aggregate.resolve_range`).
- `report_html.py` always draws the calendar heatmap from the **full** history regardless of the selected range, for visual continuity.
- `latest.html` in the output dir is a symlink to the newest report, refreshed on every report generation (`cli._refresh_latest`); it falls back to a file copy if symlinks fail.
- All user-facing strings (CLI output, terminal report, HTML) are in Chinese.
