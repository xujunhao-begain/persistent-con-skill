---
description: 持久远程运维：账号密码登录服务器，在一个常驻会话里持续发命令、读输出、干净收工。所有远端操作强制过风险闸门（CONFIRM 需用户明确同意，BLOCK 红线一律拦下）。
argument-hint: [要做什么，如：连服务器看下 nginx 状态]
---

# 持久远程运维会话

用户诉求: $ARGUMENTS

## 第 0 步：定位技能目录

`$SK` 在以下位置中取第一个存在的（下文 shell 片段里的 `$SK` 都指它）：

1. `$PWD/.claude/skills/persistent-con-skill`
2. `~/.claude/skills/persistent-con-skill`

都不存在则提示用户在本技能目录下运行 `sh install.sh --claude` 完成安装。

会话工作目录 `$SD`：用临时目录（如 `mktemp -d` 结果下建 `sess` 子目录），存放
`session.log` / `session.in` / `audit.log`。

## 第 1 步：配置检查

```bash
python3 "$SK/scripts/check_config.py" --skill-dir "$SK"
```

- 退出码 `0` → 就绪，进第 2 步。
- 退出码 `2`/`3` → **首次配置引导**：向用户收集 `host` / `user` / `port`（默认 22）/
  `password`（可选），复制 `config.example.json` → `config.local.json` 填入后重跑确认 `0`。
  - `password` 默认建议**不写入**（每次当场向用户要，密码不落盘；持久会话本来也只输一次）。
    写入则 `connect.sh` 全自动登录，但密码会明文存在本地配置文件里，须由用户明确选择。
  - 可选 `ready_prompt`（登录成功判据正则，如 shell 提示符）；不配则按超时无失败判定。

## 第 2 步：起会话（自动登录）

`connect.sh` 是**前台**调用：它后台拉起常驻 pty 代理 → 等密码提示 → 发密码（若配置了）
→ 判定登录结果后自己退出，**会话继续常驻**：

```bash
sh "$SK/scripts/connect.sh" "$SD"
```

- 配了 `password`：输出 `OK: 会话已建立并常驻` 即成功。
- 没配 `password`：会停在密码提示并打印提示语——**立即向用户索要密码，拿到后瞬时发出**：

  ```bash
  sh "$SK/scripts/send.sh" "$SD" '<用户当场给的密码>' 4
  ```

- 失败时 `connect.sh` 打印日志尾部诊断（Permission denied / 连接失败 / 主机指纹变更等），
  按提示处理后重试。

## 第 3 步：在活会话里操作

**所有远端操作必须走 `send.sh`**（发送前强制过风险闸门 + 写审计日志）：

```bash
sh "$SK/scripts/send.sh" "$SD" '<命令>' 3                                # SAFE，直接发
sh "$SK/scripts/send.sh" "$SD" '<命令>' 3 -y --note '<用户同意的原话>'    # CONFIRM，须先取得同意
sh "$SK/scripts/screen.sh" "$SD" 40                                     # 看当前屏幕（清理 ANSI）
sh "$SK/scripts/wait_for.sh" "$SD" '<提示模式>' 15                       # 等提示出现再发下一步
python3 "$SK/scripts/risk_check.py" '<命令>'                             # 判级预演（不发送）
```

风险闸门三级（拒发不是脚本坏了，是故意拦下的）：

| 等级 | 典型操作 | 你要做什么 | 拒发退出码 |
|---|---|---|---|
| `SAFE` | `ls`/`ps`/`df`/`tail`/`systemctl status` | 直接发 | — |
| `CONFIRM` | 删文件、停/重启服务、`kill`、装包、切账户 | 先把**命令原文 + 影响面 + 回滚方式**讲给用户，取得**本轮对话**的明确同意后带 `-y` 重发；不许换写法绕过 | 3 |
| `BLOCK` | `rm -rf /`、格式化、`reboot`、清 history/日志 | **不得自行发起**，给出更安全替代方案；仅用户亲口指定该命令才可 `-y --force-dangerous` | 4 |

动手前先看清：删前 `ls -la`/`du -sh`，改前 `cp` 备份，`kill` 前 `ps` 确认属主，
`DELETE`/`UPDATE` 前先 `SELECT count(*)`。完整规则与替代方案见
`$SK/references/risk-policy.md`。

## 第 4 步：收工

```bash
sh "$SK/scripts/send.sh" "$SD" 'exit' 2          # 登出远程 shell
sh "$SK/scripts/stop.sh" "$SD"                   # 终止本地 pty 代理
grep -v 'LEVEL=SAFE' "$SD/audit.log"             # 取出有副作用的操作，向用户交账
```

收工汇报要写清：**改了什么、起停了什么进程、留下哪些常驻任务（pid 与停止命令）、
哪些操作没有回滚手段**。

## 关键约束与常见坑

- **一律走 `send.sh`**，别直接写 `$SD/session.in`——绕过闸门与审计，约束形同虚设。
- 回车由 `send.sh` 自动补 `\r`（远程只认 CR）；等提示出现再发下一步，别盲发。
- 需要 sleep 用 `python3 -c "import time;time.sleep(N)"`（前台 `sleep` 常被 agent 环境拦截）。
- 日志出现 `>>> SESSION ENDED <<<` 表示远端已断开，需重新起会话（重新要密码）。
- 长跑任务用 `nohup ... &` 脱离会话，并告知用户 pid 与停止命令。
- 逐字符回显是正常终端回显，不是卡住；等一下读结果即可。
