#!/bin/sh
# 读取并清理会话日志，查看当前会话状态（去除 ANSI，可读）。
# 用法: screen.sh <session-dir> [尾部行数]
#   不给行数则输出全部；给了则只看尾部 N 行（看最新状态时更方便）。
SD="$1"
N="$2"
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
LOG="$SD/session.log"

if [ ! -f "$LOG" ]; then
  echo "错误：找不到会话日志 $LOG" >&2
  exit 1
fi

if [ -n "$N" ]; then
  tail -n "$N" "$LOG" | python3 "$DIR/clean_ansi.py"
else
  python3 "$DIR/clean_ansi.py" < "$LOG"
fi
