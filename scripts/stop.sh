#!/bin/sh
# 收工：终止某个会话的 pty 代理进程。
# 用法: stop.sh <session-dir>
#
# 建议顺序：先用 send.sh 在会话里逐层干净退出（如堡垒机的 exit/exit/q），
# 让远端正常登出，再调用本脚本清理本地代理进程。
# 用 nohup/后台脱离了会话的远端任务不受影响，会继续运行。
SD="$1"
[ -n "$SD" ] || { echo "用法: stop.sh <session-dir>" >&2; exit 2; }
PIDFILE="$SD/session.pid"

if [ -f "$PIDFILE" ]; then
  PID=$(cat "$PIDFILE" 2>/dev/null)
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    kill -TERM "$PID" 2>/dev/null
    echo "已终止会话代理 (pid=$PID): $SD"
    exit 0
  fi
  echo "会话代理已不在运行（pidfile 残留，清理）: $SD"
  rm -f "$PIDFILE"
  exit 0
fi

# 回退：无 pidfile 时尝试模式匹配（可能已随远端登出自行退出）
ABS=$(CDPATH= cd -- "$SD" 2>/dev/null && pwd || echo "$SD")
if pkill -f "pty_proxy.py --session-dir $ABS" 2>/dev/null; then
  echo "已终止会话代理（模式匹配）: $SD"
else
  echo "未发现运行中的会话代理（可能已自行退出）: $SD"
fi
