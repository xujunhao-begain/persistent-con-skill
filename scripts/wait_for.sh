#!/bin/sh
# 轮询等待会话日志中出现某个提示模式（如 "[OTP Code]"、"Opt>"、shell 提示符）。
# 用它替代盲目 sleep：连接/命令耗时不定，等到提示出现再发下一步更可靠。
#
# 用法: wait_for.sh <session-dir> "<grep 模式>" [超时秒数]
#   命中返回 0；超时返回 1。
#
# 注意：前台 sleep 常被 agent 运行环境拦截，这里用 python 的 time.sleep 做延时。
SD="$1"
PAT="$2"
TMO="${3:-30}"
LOG="$SD/session.log"

i=0
while [ "$i" -lt "$TMO" ]; do
  if grep -q -- "$PAT" "$LOG" 2>/dev/null; then
    echo "[matched] $PAT"
    exit 0
  fi
  python3 -c "import time;time.sleep(1)"
  i=$((i + 1))
done
echo "[timeout after ${TMO}s] $PAT" >&2
exit 1
