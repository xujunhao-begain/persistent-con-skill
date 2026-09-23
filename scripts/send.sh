#!/bin/sh
# 向持久会话发送一条命令（或一段输入），并回读本次产生的新增输出（已清理）。
#
# 用法: send.sh <session-dir> "<要发送的内容>" [等待秒数] [-y] [--force-dangerous] [--note "..."]
#   - 内容末尾会自动补一个回车符 \r（远程 TUI/readline 通常只认 CR，不认 \n）
#   - 等待秒数默认 3；命令越慢应给越久
#   - -y / --confirmed      ：声明「用户已明确同意这条操作」，放行 CONFIRM 级命令
#   - --force-dangerous     ：放行 BLOCK 级红线命令；必须与 -y 同时给，
#                             且**只有用户亲口指定该命令时**才允许使用
#   - --note "<用户原话>"    ：把用户的同意原话记进 audit.log，便于收工时复盘
#   - --                    ：其后的参数一律按位置参数处理（要发送的内容本身长得像选项时用）
#
# 风险闸门（本脚本是所有远端操作的唯一出口，所以闸门放在这里）：
#   发送前先过 risk_check.py 分级——SAFE 直接放行；CONFIRM 未带 -y 则拒发（退出码 3）；
#   BLOCK 未带 --force-dangerous -y 则拒发（退出码 4）。闸门自身异常也拒发（fail closed）。
#   每次发送都追加一行到 $SD/audit.log（纯数字密码/口令自动脱敏）。
#
# 也可用来发密码/动态口令（登录密码留空时，由用户当场提供后用本脚本发入）
# 注意：密码会回显进 session.log（本地文件），如需保密请自行清理该文件。
SD=""
CONTENT=""
WAIT=""
CONFIRMED=0
FORCE=0
NOTE=""

ENDOPTS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --)                 ENDOPTS=1;   shift; continue ;;
    -y|--confirmed)     [ "$ENDOPTS" -eq 0 ] && { CONFIRMED=1; shift; continue; } ;;
    --force-dangerous)  [ "$ENDOPTS" -eq 0 ] && { FORCE=1;     shift; continue; } ;;
    --note)             [ "$ENDOPTS" -eq 0 ] && { NOTE="$2";   shift 2; continue; } ;;
  esac
  case "$1" in
    *)
      if   [ -z "$SD" ];      then SD="$1"
      elif [ -z "$CONTENT" ]; then CONTENT="$1"
      elif [ -z "$WAIT" ];    then WAIT="$1"
      fi
      shift ;;
  esac
done
WAIT="${WAIT:-3}"

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SKILL_DIR=$(dirname "$DIR")
LOG="$SD/session.log"
FIFO="$SD/session.in"
AUDIT="$SD/audit.log"

[ -n "$SD" ] || { echo "用法: send.sh <session-dir> \"<内容>\" [等待秒数] [-y] [--force-dangerous]" >&2; exit 2; }

if [ ! -p "$FIFO" ]; then
  echo "错误：找不到会话管道 $FIFO —— 会话可能未启动或已结束" >&2
  exit 1
fi

# ---------------------------------------------------------------- 风险闸门
# 命令走 stdin 传给 risk_check（不进 argv，避免多一处泄露口令/命令的地方）
GATE=$(printf '%s' "$CONTENT" | python3 "$DIR/risk_check.py" --stdin --skill-dir "$SKILL_DIR" 2>&1)
RC=$?
LEVEL=$(printf '%s\n' "$GATE" | head -1 | sed 's/^RISK: //')

case "$RC" in
  0) ;;                                                  # SAFE：放行
  10)
    if [ "$CONFIRMED" -ne 1 ]; then
      echo "$GATE" >&2
      echo "" >&2
      echo "拒发：这条命令是 CONFIRM 级（破坏性但常规）操作，尚未取得用户同意。" >&2
      echo "请先把「要做什么 / 影响什么 / 怎么回滚」讲给用户，拿到明确同意后重发并加 -y：" >&2
      echo "  sh \"\$SK/scripts/send.sh\" \"\$SD\" '<命令>' $WAIT -y --note '<用户同意的原话>'" >&2
      exit 3
    fi ;;
  20)
    if [ "$CONFIRMED" -ne 1 ] || [ "$FORCE" -ne 1 ]; then
      echo "$GATE" >&2
      echo "" >&2
      echo "拒发：这条命令是 BLOCK 级红线操作。agent 不得自行发起。" >&2
      echo "若确实是用户亲口指定要执行它，请复述影响、取得同意后加 -y --force-dangerous 重发；" >&2
      echo "否则请给出更安全的替代方案（如先备份、缩小删除范围、改用 systemctl reload）。" >&2
      exit 4
    fi ;;
  *)
    echo "$GATE" >&2
    echo "拒发：风险闸门执行异常（退出码 $RC），按 fail-closed 处理，不发送。" >&2
    exit 2 ;;
esac

# ---------------------------------------------------------------- 审计留痕
# 纯数字（6-8 位）按动态口令脱敏；其余原样记录，供收工时汇总"这次到底改了什么"。
REDACTED=$(printf '%s' "$CONTENT" | sed -E 's/^[0-9]{6,8}$/<REDACTED-OTP>/')
TS=$(date '+%Y-%m-%dT%H:%M:%S')
printf '%s LEVEL=%s CONFIRMED=%d FORCE=%d CMD=%s%s\n' \
  "$TS" "$LEVEL" "$CONFIRMED" "$FORCE" "$REDACTED" \
  "$([ -n "$NOTE" ] && printf ' NOTE=%s' "$NOTE")" >> "$AUDIT" 2>/dev/null

# ---------------------------------------------------------------- 实际发送
OFF=$(wc -c < "$LOG")
printf '%s\r' "$CONTENT" > "$FIFO"
python3 -c "import time;time.sleep($WAIT)"
tail -c +$((OFF + 1)) "$LOG" | python3 "$DIR/clean_ansi.py"
