#!/bin/sh
# 读 config.local.json 拼出堡垒机登录命令，并用 pty_proxy 常驻持有会话。
# 用 run_in_background（或 nohup ... &）调用，让它常驻。
#
# 用法: connect.sh <session-dir> [skill-dir]
#   skill-dir 默认为本脚本上级目录（技能根）。
# 配置的校验与登录命令组装都在 connect.py / check_config.py 内完成（单一真相源）。
SD="$1"
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)          # scripts/
SKILL_DIR="${2:-$(dirname "$DIR")}"

[ -n "$SD" ] || { echo "用法: connect.sh <session-dir> [skill-dir]" >&2; exit 2; }

exec python3 "$DIR/connect.py" "$SD" "$SKILL_DIR"
