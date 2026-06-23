#!/usr/bin/env bash
# cc-usage 卸载脚本
# 用法: bash uninstall.sh  (或: CCUSAGE_DIR=... bash uninstall.sh)
set -euo pipefail

INSTALL_DIR="${CCUSAGE_DIR:-$HOME/.cc-usage}"
BIN_DIR="${CCUSAGE_BIN:-$HOME/.local/bin}"

echo "🗑️  卸载 cc-usage"
rm -f "$BIN_DIR/cc-usage" && echo "✓ 已删除 $BIN_DIR/cc-usage"
rm -rf "$INSTALL_DIR" && echo "✓ 已删除 $INSTALL_DIR"

echo ""
echo "ℹ️  ccuf alias 保留在 shell 配置里 (不擅自改), 如需删除请手动从 ~/.zshrc 移除:"
echo '    alias ccuf="cc-usage open --fresh"'
