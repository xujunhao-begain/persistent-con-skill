---
name: persistent-con-skill
description: >
  在需要保持一个"跨多次操作都活着"的交互式远程 shell 会话时使用——用账号密码登录远程
  服务器后，在同一个常驻会话里持续发命令、读输出、最后干净收工，避免每一步都重新登录。
  用一个常驻的 pty 代理持有会话，让你登录一次就能持续操作，直到主动收工。所有发往
  远端的命令都强制过一道风险闸门：破坏性操作必须先取得用户明确同意，灾难性操作
  （删根目录、格式化、重启主机、清审计痕迹等）一律拦下、不许自行发起。只要用户提到：
  连服务器做运维、SSH 登录服务器、"保持会话别断开"、要在远程机上依次跑一串命令、
  管理远程 cron/进程/服务、或抱怨"每步都要重新登录/重输密码"，就应主动使用本技能——
  即使没明说"持久会话"四个字。不适用于单条一次性、无需保持状态的 SSH 命令
  （那种直接 ssh 一把跑完即可）。
---

# 持久远程运维会话 (persistent-con-skill)

## 这个技能解决什么问题

普通的一次性 shell 调用每次都是**独立的新进程**——你 `ssh` 上去执行完，进程一结束
连接就断了。对于需要**多步交互**的运维尤其致命：

- 每次 `ssh` 都要重新输密码，既慢又容易卡在认证流程里。
- 远程界面常是**交互式 shell**，需要"看到提示 → 再发下一步输入"的节奏，而不是一次性
  把命令塞进去。
- 多步运维（先查状态、再改配置、再重启服务、最后盯日志）无法用无状态的单条命令表达。

核心思路：**用一个常驻后台进程用伪终端(pty)持有这个交互式会话**，把它的输出实时写进
日志文件、从命名管道接收你的输入。这样会话在多次调用之间一直活着，登录一次
（输一次密码）即可持续操作，直到你主动收工。

> 为什么不是 `screen`/`tmux`？很多机器没装 tmux；老版本 `screen`（尤其 macOS 自带、
> 被 `login` 包装的）`hardcopy` 抓不到屏幕内容，不可靠。自建 pty 代理没有这些依赖问题，
> 且输出直接落文件、随时可读。

## 安装（适配多 agent 环境）

### 一键安装（推荐）

```bash
sh install.sh              # 自动检测 Trae-CN / Claude Code 环境，装到 global
sh install.sh --project    # 装到当前项目（<项目>/.trae/skills/ 与 <项目>/.claude/）
sh install.sh --trae       # 只装 Trae-CN（global）
sh install.sh --claude     # 只装 Claude Code（global）
```

- **Trae-CN**：整目录装入 `~/.trae-cn/skills/persistent-con-skill/`（项目级为
  `<项目>/.trae/skills/`），由本文件 frontmatter 的 `description` 自动路由触发。
- **Claude Code**：`commands/remote-ops.md` 装入 `~/.claude/commands/remote-ops.md`
  （用户输入 `/remote-ops` 触发），`scripts/`、`references/`、配置装入
  `~/.claude/skills/persistent-con-skill/`。

装完脚本会自动跑一次配置检测；未配置时按下方「首次启用：配置引导」走。
两环境共用同一套 `scripts/` 与 `config.local.json` 字段；技能脚本全部用
`$(dirname "$0")` 自解析路径，装在哪都能正确找到自身。

### 手动安装

- Trae-CN：复制整个技能目录到上述位置即可（入口是本 SKILL.md）。
- Claude Code：`commands/remote-ops.md` → `~/.claude/commands/remote-ops.md`；
  `scripts/` + `references/` + `config.example.json` → `~/.claude/skills/persistent-con-skill/`。

## 何时用 / 何时不用

- **用**：多步远程运维；账号密码登录服务器；要在远端依次跑一串命令、盯日志、管进程/
  服务/cron。
- **不用**：单条一次性命令且不需要保持状态（`ssh host 'df -h'` 直接跑就行，不必起会话）。

## 工作目录与脚本位置

- 需要一个**可写工作目录**存放会话文件（`session.log`、`session.in`）。用调用方的临时/
  scratchpad 目录即可，下面记作 `$SD`。
- 本技能脚本在自身 `scripts/` 目录下，下面记作 `$SK`（即本 SKILL.md 所在目录）。
  技能通过软链接使用时，用软链接解析后的实际路径。

## 首次启用：配置引导（必做）

技能**不含任何真实连接信息**——这些由用户首次使用时提供，写入本地的
`config.local.json`（已被 `.gitignore` 忽略，不随技能分发）。**每次要连接前，先检查配置**：

```bash
python3 "$SK/scripts/check_config.py" --skill-dir "$SK"
```

- 退出码 `0`：配置就绪，直接进入下面的"快速上手"。
- 退出码 `2`/`3`（未配置 / 缺字段）：**走配置引导**——用 `AskUserQuestion` 或直接询问，
  向用户收集下列信息，然后把 `config.example.json` 复制为 `config.local.json` 并填入，
  再重新 `check_config.py` 确认通过。

**要向用户索要的信息**（对照 `config.example.json`）：

| 字段 | 含义 | 备注 |
|---|---|---|
| `host` | 目标服务器域名或 IP | 必填 |
| `user` | 登录用户名 | 必填 |
| `port` | SSH 端口 | 必填（常见 22，也有非 22 的） |
| `password` | 登录密码 | 可选；填了则自动登录，不填则停在密码提示上由用户当场提供 |
| `ready_prompt` | 登录成功的判据正则 | 可选；如 shell 提示符。不配则按超时无失败判定成功 |
| `login_wait` | 超时秒数 | 可选，默认 10 |
| `risk_policy` | 风险闸门策略（可选） | 不配即用内置默认规则；可加本环境的受保护路径与自定义红线，见 `references/risk-policy.md` |

引导时的关键提醒（帮用户避免踩坑）：
- **密码是否写入配置由用户决定**：写入了则全自动登录（方便但密码存在本地文件里，
  请确保文件权限受限、不提交）；不写入则每次停在密码提示上，由用户当场提供（更安全，
  密码不落盘）。默认推荐**不写入密码**——更安全，且持久会话本来也只需输一次。
- 只需引导一次；之后配置常驻，除非用户换环境或改密码。

## 默认用户引导流程（首选交互节奏）

配置就绪后，**默认按下面的节奏引导用户**，不要自作主张直接动手：

1. **先起会话**（见"快速上手 1"）：`connect.sh` 会后台拉起会话。
   - 若配了 `password`：`connect.sh` 自动完成密码登录，成功后会话常驻，直接进入运维。
   - 若**没配 `password`**：`connect.sh` 会停在密码提示上并打印提示语——此时**立即向用户
     索要密码，拿到后用 `send.sh` 瞬时发入**（不要先问密码再慢慢连——虽然密码不像动态口令
     那样会过期，但让用户等着也是体验差）。
2. **登录成功后先看状态**：用 `screen.sh` 看一眼当前屏幕，确认确实登录到了目标机器。
3. **再询问用户接下来的动作**：问清楚"要做什么？"，拿到明确回答后再开始操作。

> 若用户在一开始就已经说清要做哪些事，可跳过第 2 步的确认，直接开始操作；
> 但只要用户没说清楚目标，就走完整默认引导（起会话 → 确认登录 → 问要做什么）。

## 服务器操作约束（红线与确认门 · 必读）

一旦登录成功，你握着的是一个**生产环境的 shell**，后续每条命令都不再需要密码。
所以技能不靠"记得小心"，而是把约束写进唯一的出口：**所有发往远端的内容都必须走 `send.sh`**，
`send.sh` 在写入会话管道前强制调用 `risk_check.py` 分级。

| 等级 | 典型操作 | 你要做什么 | 拒发退出码 |
|---|---|---|---|
| `SAFE` | `ls` / `ps` / `df` / `tail` / `systemctl status` | 直接发 | — |
| `CONFIRM` | 删文件、停/重启服务、`kill`、`sed -i`、装包、动数据库、改权限、切账户 | **先取得用户明确同意**，再带 `-y` 重发 | 3 |
| `BLOCK` | `rm -rf /`、格式化、`dd` 写盘、重启主机、`iptables -F`、清 history/日志 | **不得自行发起**，改提安全替代方案 | 4 |

```bash
# 被拒发时：先讲清楚，拿到用户明确同意，再带 -y（--note 把授权原话记进审计日志）
sh "$SK/scripts/send.sh" "$SD" 'rm -rf /data/app/tmp' 3 -y --note '用户确认：tmp 可清'

# 想先看判级而不发送
python3 "$SK/scripts/risk_check.py" 'systemctl restart nginx'
python3 "$SK/scripts/risk_check.py" --list          # 规则全目录
```

**几条不可协商的行为约束**：

1. **CONFIRM 不许绕过**：被拒发时不要换写法、不要拆成小命令规避、不要直接补 `-y`。先把
   **命令原文 / 影响面（哪台机、哪些路径、是否中断线上）/ 回滚方式**讲给用户；没有回滚手段
   必须明说。同意必须落在**这一轮对话**里——上次的同意不能延用到这次另一条命令。
2. **BLOCK 是红线**：命中时正确反应是**换更安全的做法**（清盘先 `du -sh` 定位再逐个删、
   生效用 `systemctl reload` 而非 `reboot`、防火墙只增删具体规则），而不是申请放行。
   只有用户**亲口写出那条命令**并确认，才可 `-y --force-dangerous`。
3. **动手前先看清**：删前 `ls -la`/`du -sh`，改前 `cp` 备份，`kill` 前 `ps` 确认 pid 属主，
   同步前 `rsync --dry-run`，`DELETE`/`UPDATE` 前先跑同条件 `SELECT count(*)` 把行数给用户看。
4. **不越出交代的边界**：只在用户指定的路径下写；不主动改 `/etc`、不主动提权、不在生产机上
   装包；`authorized_keys` / `passwd` / `sudoers` 一律先问。
5. **长跑任务要交底**：用 `nohup ... &` 脱离会话，并明确告知用户"进程会在收工后继续跑，
   pid 是 X，停它用 Y"。不告知的常驻进程等于留坑。
6. **收工要交账**：每次发送都记在 `$SD/audit.log`（时间 / 等级 / 是否已确认 / 命令原文，
   纯数字密码自动脱敏）。收工时据此汇报改动清单：

   ```bash
   grep -v 'LEVEL=SAFE' "$SD/audit.log"     # 只看有副作用的操作
   ```

> 绕过闸门的路子（直接 `printf ... > "$SD/session.in"`）确实存在，但**不要用**——绕过去
> 约束和审计一起失效。闸门看的是命令文本，`bash deploy.sh` 里面写了什么它看不见，
> 所以跑来源不明的脚本前先 `cat` 出来看。
> 完整规则目录、替代方案对照表、按环境定制策略（`risk_policy`）见 **`references/risk-policy.md`**。

## 快速上手（四步）

### 1) 起会话：自动登录并常驻

```bash
sh "$SK/scripts/connect.sh" "$SD"
```

`connect.sh` 是一个前台调用，它完成：后台拉起 pty 代理 → 等密码提示 → 发密码（若配置了）
→ 确认登录结果。成功后本进程退出、会话继续常驻。

- 若**配了 `password`**：自动完成登录，看到 `OK: 会话已建立并常驻` 即可。
- 若**没配 `password`**：会停在密码提示上，打印提示语让你向用户索要密码。拿到密码后：

  ```bash
  sh "$SK/scripts/send.sh" "$SD" '<用户当场给的密码>' 4
  ```

  发出后用 `screen.sh` 确认是否登录成功。

> **通用场景**（非账号密码、或想自定义登录命令）：直接用 pty_proxy 持有任意交互式命令：
> ```bash
> python3 "$SK/scripts/pty_proxy.py" --session-dir "$SD" -- <你的交互式登录命令>
> ```

### 2) 在活会话里做运维

登录后就是普通远程 shell，直接发命令、读回显：

```bash
sh "$SK/scripts/send.sh" "$SD" 'ps -ef | grep myjob | grep -v grep' 3   # SAFE，直接发
sh "$SK/scripts/send.sh" "$SD" 'sudo su - targetuser' 3 -y              # CONFIRM：切账户须用户明示
```

> 发之前想不清风险等级就先问闸门：`python3 "$SK/scripts/risk_check.py" '<命令>'`。
> 被拒发（退出码 3/4）说明这条操作需要先跟用户对齐——见上面「服务器操作约束」。

**常用辅助命令**：

```bash
sh "$SK/scripts/screen.sh" "$SD" 40                    # 看当前屏幕尾部 40 行（已清理 ANSI）
sh "$SK/scripts/wait_for.sh" "$SD" '某提示符' 15        # 等某提示出现再发下一步
```

### 3) 看状态、确认结果

```bash
sh "$SK/scripts/screen.sh" "$SD" 20                    # 看最新状态
```

`send.sh` 每次发送后会回读本次新增的输出（已清理 ANSI），你能立刻看到结果。
想看**完整屏幕**用 `screen.sh`。

### 4) 收工：干净退出 + 终止代理

先在会话里登出，再终止本地代理：

```bash
sh "$SK/scripts/send.sh" "$SD" 'exit' 2                # 登出远程 shell
sh "$SK/scripts/stop.sh" "$SD"                         # 终止 pty 代理
grep -v 'LEVEL=SAFE' "$SD/audit.log"                  # 取出本次所有有副作用的操作，向用户交账
```

收工汇报里要写清：**改了什么、起停了什么进程、留下哪些常驻任务（pid 与停止命令）、
哪些操作没有回滚手段**。

> 长跑任务想在你收工后继续，起它时用 `nohup ... &` 脱离会话——这样登出/断开不会杀它。

## 交互细节与常见坑（都是实战踩出来的）

- **回车用 `\r` 不是 `\n`**：TUI/readline 只认 CR，`send.sh` 已自动处理。**一律走 `send.sh`，
  别直接写 FIFO**（`printf ... > "$SD/session.in"`）——那样绕过风险闸门与审计日志，
  技能的操作约束就形同虚设。
- **等提示再发，别盲发**：需要等某个提示出现再发下一步时，用 `wait_for.sh` 等真正的输入提示串，
  不要盲目 `sleep` 后发。
- **前台 `sleep` 常被 agent 环境拦截**：本技能所有延时都用 `python3 -c "import time;time.sleep(N)"`。
  你自己需要等待时也照此办理。
- **读日志要清理 ANSI 且用 UTF-8 容错**：直接 `cat`/`sed` 处理带中文和转义的日志会报
  `illegal byte sequence`。统一走 `clean_ansi.py`（`send.sh`/`screen.sh` 已内置）。
- **逐字符回显 ≠ 卡住**：`send.sh` 发送时日志里可能看到内容被逐字符回显，这是正常的终端
  回显，等一下读结果即可。
- **闸门拒发不是脚本坏了**：`send.sh` 退出码 3/4 是**故意**拦下的，表示这条操作需要先跟用户
  对齐（3=需明确同意，4=红线）。别改脚本、别绕管道，按「服务器操作约束」处理。
- **会话意外结束的标志**：日志出现 `>>> SESSION ENDED <<<` 表示远端已断开或代理已退出，
  需要重新起会话（会重新要密码）。
- **密码登录失败的常见原因**：`Permission denied` 多为密码错误或用户名不对；
  `Connection refused/timed out` 多为 host/port 不通或防火墙拦截；
  `REMOTE HOST IDENTIFICATION HAS CHANGED` 为主机指纹变了（重装/中间人），需清理
  `~/.ssh/known_hosts` 里该主机的旧记录后重连。
