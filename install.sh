#!/usr/bin/env bash
# cc-usage 一键安装脚本
# 用法: curl -fsSL https://raw.githubusercontent.com/luoqi951102/token-count-chart/main/install.sh | bash
# 或克隆后: bash install.sh
set -euo pipefail

REPO="https://github.com/luoqi951102/token-count-chart.git"
INSTALL_DIR="${CCUSAGE_DIR:-$HOME/.cc-usage}"
BIN_DIR="${CCUSAGE_BIN:-$HOME/.local/bin}"

echo "🎯 cc-usage 安装"
echo "   安装目录: $INSTALL_DIR"
echo "   可执行目录: $BIN_DIR"
echo ""

# 1. clone 或更新
if [ -d "$INSTALL_DIR/.git" ]; then
  echo "📦 已存在, 拉取最新..."
  git -C "$INSTALL_DIR" pull --ff-only || echo "⚠️  pull 失败, 保留本地版本"
else
  echo "⬇️  克隆仓库..."
  if ! git clone --depth 1 "$REPO" "$INSTALL_DIR"; then
    echo "❌ 克隆失败, 请检查网络或确认 git 已安装"
    exit 1
  fi
fi

# 2. 软链到 PATH
mkdir -p "$BIN_DIR"
ln -sf "$INSTALL_DIR/scripts/cc-usage" "$BIN_DIR/cc-usage"
echo "🔗 软链: $BIN_DIR/cc-usage → $INSTALL_DIR/scripts/cc-usage"

# 3. PATH 检查
path_ok=0
case ":$PATH:" in
  *":$BIN_DIR:"*) path_ok=1 ;;
esac
if [ "$path_ok" = 0 ]; then
  echo "⚠️  $BIN_DIR 不在 PATH"
  echo "   请在 shell 配置 (~/.zshrc 或 ~/.bashrc) 加: export PATH=\"$BIN_DIR:\$PATH\""
fi

# 4. ccuf alias (zsh / bash)
shell_rc=""
case "${SHELL:-}" in
  */zsh)  shell_rc="$HOME/.zshrc" ;;
  */bash) shell_rc="$HOME/.bashrc" ;;
esac
if [ -n "$shell_rc" ] && ! grep -q 'alias ccuf=' "$shell_rc" 2>/dev/null; then
  {
    echo ""
    echo "# cc-usage 一键打开报告"
    echo 'alias ccuf="cc-usage open --fresh"'
  } >> "$shell_rc"
  echo "✍️  已加 ccuf alias 到 $shell_rc"
elif [ -n "$shell_rc" ]; then
  echo "✓ ccuf alias 已存在 ($shell_rc)"
fi

# 5. 验证
echo ""
echo "✅ 安装完成! 验证版本:"
export PATH="$BIN_DIR:$PATH"
cc-usage --version 2>/dev/null || echo "   (cc-usage 运行异常, 请检查 python3 是否可用)"

echo ""
echo "下一步:"
echo "  cc-usage sync     # 首次同步数据 (~2 秒)"
if [ "$path_ok" = 1 ]; then
  echo "  ccuf              # 一键打开报告"
else
  echo "  ccuf              # (重开终端或 source $shell_rc 后生效)"
fi
echo ""
echo "升级: 重新跑同一条 curl 命令即可 (会 git pull)."
echo "卸载: bash $INSTALL_DIR/uninstall.sh"
