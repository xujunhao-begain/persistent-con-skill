#!/bin/sh
# install.sh — 自动检测环境并安装本技能到 Trae-CN 和/或 Claude Code
#
# 用法:
#   sh install.sh               # 自动检测环境，装到 global（检测到哪个装哪个，都检测到就都装）
#   sh install.sh --project     # 装到当前项目（<项目>/.trae/skills/ 与 <项目>/.claude/）
#   sh install.sh --trae        # 只装 Trae-CN（global）
#   sh install.sh --claude      # 只装 Claude Code（global）
#
# 安装布局:
#   Trae-CN     → 整目录装入 ~/.trae-cn/skills/persistent-con-skill/（项目级为 <项目>/.trae/skills/），
#                 入口 SKILL.md，按 description 自动路由触发
#   Claude Code → commands/remote-ops.md 装入 ~/.claude/commands/remote-ops.md（触发: /remote-ops）；
#                 scripts/ + references/ + config 装入 ~/.claude/skills/persistent-con-skill/
#
# 装完自动跑一次 check_config.py 检测配置状态（退出码 2 = 需要首次配置引导）。
# 技能脚本全部用 $(dirname "$0") 自解析路径，装在哪个位置都能找到自身，无需改代码。

SRC_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
NAME="persistent-con-skill"
MODE="global"
ONLY=""

for arg in "$@"; do
  case "$arg" in
    --project) MODE="project" ;;
    --trae)    ONLY="trae" ;;
    --claude)  ONLY="claude" ;;
    *) echo "未知参数: $arg（支持 --project / --trae / --claude）" >&2; exit 2 ;;
  esac
done

# 注意：Trae-CN 的 global 目录是 ~/.trae-cn，但项目级是 <项目>/.trae（不是 .trae-cn）
if [ "$MODE" = "project" ]; then
  TRAE_ROOT="$PWD/.trae"
  CLAUDE_ROOT="$PWD/.claude"
else
  TRAE_ROOT="$HOME/.trae-cn"
  CLAUDE_ROOT="$HOME/.claude"
fi
TRAE_DEST="$TRAE_ROOT/skills/$NAME"
CLAUDE_CMD="$CLAUDE_ROOT/commands"
CLAUDE_DEST="$CLAUDE_ROOT/skills/$NAME"

# ---------------------------------------------------------------- 工具函数
real() { CDPATH= cd -- "$1" 2>/dev/null && pwd; }

is_same() { [ "$(real "$1")" = "$(real "$2")" ]; }

copy_tree() {
  # copy_tree <src> <dest> — 复制并排除 git/缓存/系统杂项
  mkdir -p "$2"
  (cd "$1" && tar --exclude=.git --exclude=__pycache__ --exclude='*.pyc' \
       --exclude=.DS_Store -cf - .) | (cd "$2" && tar -xf -)
}

safe_rmtree() {
  # safe_rmtree <dir> — 只删非空名字的合法路径，防变量为空导致误删
  case "$1" in
    "" | "/" | "$HOME") echo "拒绝删除可疑路径: '$1'" >&2; exit 2 ;;
    *) rm -rf "$1" ;;
  esac
}

# ---------------------------------------------------------------- 安装动作
DETECT_DIR=""
INSTALLED=0

install_trae() {
  if is_same "$SRC_DIR" "$TRAE_DEST"; then
    echo "[Trae-CN] 源目录即安装目录，跳过复制: $TRAE_DEST"
  else
    safe_rmtree "$TRAE_DEST"
    copy_tree "$SRC_DIR" "$TRAE_DEST"
    echo "[Trae-CN] 已安装: $TRAE_DEST"
  fi
  chmod +x "$TRAE_DEST/scripts/"*.sh "$TRAE_DEST/scripts/"*.py 2>/dev/null
  DETECT_DIR="$TRAE_DEST"
  INSTALLED=1
}

install_claude() {
  if [ ! -f "$SRC_DIR/commands/remote-ops.md" ]; then
    echo "[Claude Code] 缺少 $SRC_DIR/commands/remote-ops.md，跳过" >&2
    return 1
  fi
  if ! is_same "$SRC_DIR" "$CLAUDE_DEST"; then
    safe_rmtree "$CLAUDE_DEST"
    mkdir -p "$CLAUDE_DEST"
    copy_tree "$SRC_DIR/scripts" "$CLAUDE_DEST/scripts"
    copy_tree "$SRC_DIR/references" "$CLAUDE_DEST/references"
    [ -f "$SRC_DIR/config.example.json" ] && cp "$SRC_DIR/config.example.json" "$CLAUDE_DEST/"
    # 已配置过的 config.local.json 一并带入，装完即可用
    [ -f "$SRC_DIR/config.local.json" ] && cp "$SRC_DIR/config.local.json" "$CLAUDE_DEST/"
  fi
  mkdir -p "$CLAUDE_CMD"
  cp "$SRC_DIR/commands/remote-ops.md" "$CLAUDE_CMD/remote-ops.md"
  chmod +x "$CLAUDE_DEST/scripts/"*.sh "$CLAUDE_DEST/scripts/"*.py 2>/dev/null
  echo "[Claude Code] 命令: $CLAUDE_CMD/remote-ops.md（使用: /remote-ops）"
  echo "[Claude Code] 脚本: $CLAUDE_DEST"
  DETECT_DIR="$CLAUDE_DEST"
  INSTALLED=1
}

# ---------------------------------------------------------------- 决定装哪
DO_TRAE=0
DO_CLAUDE=0
if [ "$ONLY" = "trae" ]; then
  DO_TRAE=1
elif [ "$ONLY" = "claude" ]; then
  DO_CLAUDE=1
elif [ "$MODE" = "project" ]; then
  DO_TRAE=1                                # 项目级 trae 约定固定为 .trae/skills/
  [ -d "$CLAUDE_ROOT" ] && DO_CLAUDE=1
else
  [ -d "$TRAE_ROOT" ] && DO_TRAE=1
  [ -d "$CLAUDE_ROOT" ] && DO_CLAUDE=1
fi

if [ "$DO_TRAE" -eq 0 ] && [ "$DO_CLAUDE" -eq 0 ]; then
  echo "未检测到 Trae-CN（$HOME/.trae-cn）或 Claude Code（$HOME/.claude）环境。"
  echo "请显式指定: sh install.sh --trae 或 sh install.sh --claude"
  exit 1
fi

if [ "$DO_TRAE" -eq 1 ]; then install_trae; fi
if [ "$DO_CLAUDE" -eq 1 ]; then install_claude; fi

# ---------------------------------------------------------------- 配置检测
echo ""
echo "=== 配置检测 ==="
if [ -n "$DETECT_DIR" ]; then
  python3 "$DETECT_DIR/scripts/check_config.py" --skill-dir "$DETECT_DIR"
  RC=$?
  if [ "$RC" -eq 2 ] || [ "$RC" -eq 3 ]; then
    echo ""
    echo "需要首次配置：向用户收集 host / user / port / password(可选) 后，"
    echo "把 config.example.json 复制为 config.local.json 填入，再确认:"
    echo "  python3 \"$DETECT_DIR/scripts/check_config.py\" --skill-dir \"$DETECT_DIR\""
  fi
fi

exit 0
