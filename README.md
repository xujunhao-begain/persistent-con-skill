# persistent-con-skill

一次登录，长久连接服务器。一个用于在 agent / CLI 环境中保持"跨多次操作都活着"的交互式远程 shell 会话的技能。

## 解决什么问题

普通的一次性 shell 调用每次都是独立的新进程——`ssh` 上去执行完，进程一结束连接就断。对多步交互式运维尤其致命：

- 每次 `ssh` 都要重新输密码，既慢又容易卡在认证流程里。
- 远程界面常是交互式 shell，需要"看到提示 → 再发下一步输入"的节奏。
- 多步运维（查状态 → 改配置 → 重启服务 → 盯日志）无法用无状态的单条命令表达。

**核心思路**：用一个常驻后台进程用伪终端（pty）持有这个交互式会话，把输出实时写进日志文件、从命名管道接收输入。会话在多次调用之间一直活着，登录一次（输一次密码）即可持续操作，直到主动收工。

> 为什么不是 `screen`/`tmux`？很多机器没装 tmux；老版本 `screen`（尤其 macOS 自带、被 `login` 包装的）`hardcopy` 抓不到屏幕内容，不可靠。自建 pty 代理没有这些依赖问题，且输出直接落文件、随时可读。

## 核心特性

- **一次登录，持续操作**：常驻 pty 代理持有会话，登录一次即可多次发送命令、读取输出，无需重复认证。
- **风险闸门分级**：所有发往远端的命令强制过 `risk_check.py` 分级——
  - `SAFE`（`ls`/`ps`/`df`/`tail`/`systemctl status`）直接发
  - `CONFIRM`（删文件、停服务、`kill`、装包、动数据库等）必须先取得用户明确同意
  - `BLOCK`（`rm -rf /`、格式化、重启主机、清审计痕迹等）一律拦下，不许自行发起
- **审计留痕**：每次发送都记录到 `audit.log`（时间 / 等级 / 是否已确认 / 命令原文，纯数字密码自动脱敏），收工可据此交账。
- **密码不落盘可选**：配置不写密码时会话停在密码提示上，由用户当场提供、经 `send.sh` 发入，密码不落盘。
- **多 agent 环境适配**：一键安装脚本自动识别 Trae-CN / Claude Code 环境，装到 global 或 project 级。

## 安装

### 一键安装（推荐）

```bash
sh install.sh              # 自动检测 Trae-CN / Claude Code 环境，装到 global
sh install.sh --project    # 装到当前项目（<项目>/.trae/skills/ 与 <项目>/.claude/）
sh install.sh --trae       # 只装 Trae-CN（global）
sh install.sh --claude     # 只装 Claude Code（global）
```

- **Trae-CN**：整目录装入 `~/.trae-cn/skills/persistent-con-skill/`，由 `SKILL.md` frontmatter 的 `description` 自动路由触发。
- **Claude Code**：`commands/remote-ops.md` 装入 `~/.claude/commands/remote-ops.md`（用户输入 `/remote-ops` 触发），其余装入 `~/.claude/skills/persistent-con-skill/`。

装完脚本会自动跑一次配置检测；未配置时按下方「配置」走。

### 手动安装

- Trae-CN：复制整个技能目录到上述位置即可（入口是 `SKILL.md`）。
- Claude Code：`commands/remote-ops.md` → `~/.claude/commands/remote-ops.md`；`scripts/` + `references/` + `config.example.json` → `~/.claude/skills/persistent-con-skill/`。

## 配置

技能不含任何真实连接信息——这些由用户首次使用时提供，写入本地的 `config.local.json`（已被 `.gitignore` 忽略，不随技能分发）。

复制 `config.example.json` 为 `config.local.json` 并填写：

| 字段 | 含义 | 备注 |
|---|---|---|
| `host` | 目标服务器域名或 IP | 必填 |
| `user` | 登录用户名 | 必填 |
| `port` | SSH 端口 | 必填（常见 22） |
| `password` | 登录密码 | 可选；填了自动登录，不填则停在密码提示上由用户当场提供 |
| `ready_prompt` | 登录成功判据正则 | 可选；如 shell 提示符 |
| `login_wait` | 超时秒数 | 可选，默认 10 |
| `risk_policy` | 风险闸门策略 | 可选；不配即用内置默认规则 |

检查配置是否就绪：

```bash
python3 scripts/check_config.py --skill-dir .
```

退出码 `0` 表示配置就绪；`2`/`3` 表示未配置或缺字段。

## 快速上手

```bash
# 1) 起会话（自动登录并常驻）
sh scripts/connect.sh "$SD"

# 2) 在活会话里做运维（SAFE 直接发）
sh scripts/send.sh "$SD" 'ps -ef | grep myjob | grep -v grep' 3

# 3) 看当前屏幕状态
sh scripts/screen.sh "$SD" 20

# 4) 收工：登出 + 终止代理 + 交账
sh scripts/send.sh "$SD" 'exit' 2
sh scripts/stop.sh "$SD"
grep -v 'LEVEL=SAFE' "$SD/audit.log"
```

> `$SD` 是可写工作目录，存放会话文件（`session.log`、`session.in`）。`$SK` 是技能脚本目录（即本仓库根）。

## 项目结构

```
persistent-con-skill/
├── SKILL.md                      # 技能入口与完整使用说明
├── install.sh                    # 一键安装脚本（适配 Trae-CN / Claude Code）
├── config.example.json           # 配置模板（复制为 config.local.json 后填写）
├── .gitignore                    # 忽略 config.local.json / session.dir / 日志等
├── commands/
│   └── remote-ops.md            # Claude Code 的 /remote-ops 命令
├── references/
│   └── risk-policy.md           # 完整风险规则目录与替代方案对照表
└── scripts/
    ├── check_config.py          # 配置检测（退出码 0/2/3）
    ├── connect.sh               # 起会话：后台拉起 pty 代理 + 自动登录
    ├── pty_proxy.py             # 常驻 pty 代理（持有交互式会话）
    ├── send.sh                  # 发送命令（强制走风险闸门）
    ├── risk_check.py            # 风险分级（SAFE/CONFIRM/BLOCK）
    ├── screen.sh                # 看当前屏幕尾部（已清理 ANSI）
    ├── wait_for.sh              # 等某提示出现再发下一步
    ├── stop.sh                  # 终止 pty 代理
    └── clean_ansi.py            # 清理 ANSI 转义 + UTF-8 容错
```

## 何时用 / 何时不用

- **用**：多步远程运维；账号密码登录服务器；要在远端依次跑一串命令、盯日志、管进程/服务/cron。
- **不用**：单条一次性命令且不需要保持状态（`ssh host 'df -h'` 直接跑就行，不必起会话）。

## 更多

完整的使用说明、操作约束、风险规则目录与替代方案对照表见 [SKILL.md](./SKILL.md) 与 [references/risk-policy.md](./references/risk-policy.md)。
