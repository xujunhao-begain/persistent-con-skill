#!/usr/bin/env python3
"""远程操作风险闸门：判定一条即将发往远端会话的命令属于什么风险等级。

被 send.sh 在**写入会话管道之前**调用——这是技能里唯一的"动手"出口，所以闸门放在这里
就能覆盖所有远端操作。判定结果决定 send.sh 是否放行：

    SAFE    (退出码 0)  —— 只读/无害，直接放行
    CONFIRM (退出码 10) —— 破坏性但常规；**必须先把影响讲清楚、拿到用户明确同意**，
                           再带 -y 重发
    BLOCK   (退出码 20) —— 灾难性/不可回滚/规避审计；agent 一律不得自行发起，
                           只有用户亲口指定该命令时才可带 --force-dangerous 放行

命令行用法:
    python3 risk_check.py "<命令>"            # 直接给命令
    python3 risk_check.py --stdin             # 从 stdin 读命令（避免口令/命令进 argv）
    python3 risk_check.py --list              # 打印规则目录
    [--skill-dir DIR]                         # 读 config.local.json 里的 risk_policy

设计要点：
- **引号内容先掩码**再匹配，避免 `grep "rm -rf" x.log` 这类"提到但没执行"的误判；
  少数规则（SQL、redis FLUSH）本身就写在引号里，单独标注 scan="raw" 对原文匹配。
- 命令按 `;` `&&` `||` `|` 换行 拆成片段逐段判定，一段命中就整条升级。
- `rm` 单独做**目标路径分析**（而不是只看有没有 -rf）：删到根级/系统目录、或删一个
  未展开的变量路径（`rm -rf $DIR/` 在变量为空时等于 `rm -rf /`）判 BLOCK，其余判 CONFIRM。
"""
import os, sys, re, json, shlex, argparse

# ---------------------------------------------------------------- 只读命令白名单
# 片段的首个命令属于这里时，若该片段没有输出重定向，就直接视为安全，不再做模式匹配。
READONLY_CMDS = {
    "ls", "ll", "cat", "head", "tail", "less", "more", "grep", "egrep", "zgrep", "wc",
    "awk", "cut", "sort", "uniq", "tr", "stat", "file", "readlink", "realpath", "basename",
    "dirname", "du", "df", "free", "uptime", "top", "htop", "vmstat", "iostat", "mpstat",
    "ps", "pgrep", "pstree", "lsof", "ss", "netstat", "ip", "ifconfig", "route", "ping",
    "dig", "nslookup", "host", "traceroute", "whoami", "id", "groups", "who", "w", "last",
    "date", "uname", "hostname", "hostnamectl", "env", "printenv", "echo", "pwd", "cd",
    "which", "type", "command", "whereis", "md5sum", "sha1sum", "sha256sum", "diff", "cmp",
    "history", "man", "help", "true", "false", "sleep", "tee",  # tee 无重定向时也只是转写
    "journalctl", "dmesg", "crontab", "systemctl", "docker", "kubectl", "git", "find",
}
# 上面几个"既能读也能写"的命令（systemctl/docker/kubectl/git/find/crontab/history/tee）
# 仍然要过模式匹配——用子命令区分读写，因此不能只靠首词放行。
DUAL_USE = {"journalctl", "crontab", "systemctl", "docker", "kubectl", "git", "find",
            "history", "tee", "ip"}

# ------------------------------------------------------------ rm 的根级保护路径
ROOT_LIKE = {
    "/", "/etc", "/var", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/boot", "/dev",
    "/proc", "/sys", "/home", "/root", "/opt", "/srv", "/mnt", "/media", "/run",
    "/var/log", "/var/lib", "/usr/local", "/usr/bin", "/etc/init.d", "~",
}

# ------------------------------------------------------------------------ 规则表
# (id, level, regex, why, scan)
#   scan: "masked" = 对引号已掩码的文本匹配（默认，避免"只是提到"的误判）
#         "raw"    = 对原文匹配（规则本身就写在引号里，如 SQL、redis FLUSH）
#         "both"   = 两者任一命中即算（配置里用户自定义的规则一律按此处理，宁可多报）
RULES = [
    # ======================= BLOCK：灾难性 / 不可回滚 / 规避审计 =======================
    ("wipe-filesystem", "BLOCK", r"\bmkfs(\.\w+)?\b|\bmke2fs\b|\bmkswap\b",
     "格式化文件系统 —— 整盘数据不可恢复", "masked"),
    ("dd-to-disk", "BLOCK", r"\bdd\b[^\n]*\bof=\s*/dev/(sd|nvme|vd|xvd|hd|disk|mapper)",
     "dd 直写块设备 —— 覆盖整块磁盘，不可恢复", "masked"),
    ("redirect-to-disk", "BLOCK", r">\s*/dev/(sd|nvme|vd|xvd|hd|disk)\w*",
     "重定向写入块设备 —— 破坏磁盘/分区表", "masked"),
    ("shred-disk", "BLOCK", r"\bshred\b[^\n]*/dev/",
     "对块设备做安全擦除 —— 不可恢复", "masked"),
    ("host-power", "BLOCK", r"\b(reboot|shutdown|poweroff|halt)\b|\binit\s+[06]\b|"
                            r"\bsystemctl\s+(reboot|poweroff|halt)\b",
     "重启/关机整台主机 —— 全量服务中断，且当前会话会立即断开", "masked"),
    ("firewall-flush", "BLOCK", r"\biptables\b[^\n]*\s-(F|X|Z)\b|"
                                r"\biptables\b[^\n]*-P\s+INPUT\s+DROP",
     "清空/默认拒绝防火墙规则 —— 极可能把自己锁在机器外面", "masked"),
    ("fork-bomb", "BLOCK", r":\s*\(\s*\)\s*\{.*\|\s*:\s*&.*\}\s*;\s*:",
     "fork 炸弹 —— 直接打死主机", "raw"),
    ("audit-evasion", "BLOCK",
     r"\bhistory\s+-c\b|>\s*~?/?\.?\w*bash_history|\brm\b[^\n]*\.bash_history|"
     r"\bunset\s+HISTFILE\b|\bexport\s+HISTFILE=\s*/dev/null",
     "清除命令历史 —— 规避审计，任何情况下都不应由 agent 主动执行", "masked"),
    ("log-wipe", "BLOCK", r"(>|\btruncate\b[^\n]*)\s*/var/log/|\brm\b[^\n]*/var/log/",
     "清空系统日志 —— 破坏审计与故障排查依据", "masked"),
    ("root-perm-recursive", "BLOCK",
     r"\bchmod\b[^\n]*\s-[a-zA-Z]*R[a-zA-Z]*\s[^\n]*\s/(\s|$|\*)|"
     r"\bchown\b[^\n]*\s-[a-zA-Z]*R[a-zA-Z]*\s[^\n]*\s/(\s|$|\*)|"
     r"\bchmod\b\s+(-[a-zA-Z]+\s+)*777\s+/(\s|$|\*)",
     "从根目录起递归改权限/属主 —— 系统级不可逆损坏", "masked"),
    ("find-delete-root", "BLOCK",
     r"\bfind\s+/(\s|\*)[^\n]*(-delete|-exec\s+rm)",
     "从根目录起批量删除 —— 影响面无法预估", "masked"),

    # ============================ CONFIRM：破坏性但常规 ============================
    ("service-stop", "CONFIRM",
     r"\bsystemctl\s+(stop|restart|disable|mask|kill)\b|"
     r"\bservice\s+\S+\s+(stop|restart)\b|\b(nginx|apache2|httpd)\s+-s\s+(stop|quit|reload)\b|"
     r"\bsupervisorctl\s+(stop|restart)\b",
     "停止/重启系统服务 —— 会造成线上中断", "masked"),
    ("service-start", "CONFIRM",
     r"\bsystemctl\s+(start|enable)\b|\bnohup\b[^\n]*&|\bdocker\s+run\b[^\n]*(-d|--detach)\b|"
     r"\bdocker(-|\s+)compose\s+up\b",
     "拉起常驻服务 —— 会占用端口/资源，起监听端口的服务前先确认端口未被占用", "masked"),
    ("process-kill", "CONFIRM",
     r"\bkill\s+(-(?!0\b)\w+\s+)?\d+|\b(killall|pkill)\b",
     "结束进程 —— 可能打断线上任务或误杀同名进程；先 ps 确认目标再动", "masked"),
    ("rm-any", "CONFIRM", r"\brm\b",
     "删除文件/目录 —— 远端无回收站", "masked"),
    ("inplace-edit", "CONFIRM",
     r"\bsed\b[^\n]*\s-[a-zA-Z]*i[a-zA-Z]*(\s|\.|'|\")|\bperl\b[^\n]*\s-[a-zA-Z]*i\b",
     "原地改文件 —— 无备份则改动不可回滚，建议先 cp 一份 .bak", "masked"),
    ("truncate-redirect", "CONFIRM",
     r"(^|[^>\w])>\s*(?!/dev/null)(/|\.{0,2}/|~/)\S+",
     "输出重定向覆盖已有文件 —— 单个 > 会清空原内容（追加请用 >>）", "masked"),
    ("write-system-path", "CONFIRM",
     r"(>|>>|\btee\b[^\n]*)\s*/(etc|usr|bin|sbin|boot|lib)/|\bvi?m?\s+/etc/",
     "写入系统目录 —— 影响全机行为", "masked"),
    ("perm-change", "CONFIRM",
     r"\bchmod\b|\bchown\b|\bchgrp\b|\bchattr\b|\bsetfacl\b",
     "修改权限/属主 —— 可能让服务读不到文件或过度放权", "masked"),
    ("user-cred", "CONFIRM",
     r"\b(useradd|adduser|userdel|usermod|groupadd|groupdel|passwd|chpasswd|visudo)\b|"
     r"/etc/(passwd|shadow|sudoers)|authorized_keys",
     "改动账户/凭据/免密登录 —— 属于持久化访问权限变更，必须用户明示", "masked"),
    ("crontab-write", "CONFIRM",
     r"\bcrontab\s+(-r\b|-\s*$|[^-\s]\S*)|>\s*/etc/cron|/etc/cron\.[a-z]+/",
     "改动定时任务 —— crontab -r 直接清空、crontab <file> 是整表覆盖（先 crontab -l 备份）", "masked"),
    ("package-mgmt", "CONFIRM",
     r"\b(apt|apt-get|yum|dnf|apk|zypper)\s+(install|remove|purge|upgrade|update|autoremove)\b|"
     r"\b(pip|pip3)\s+(install|uninstall)\b|\bnpm\s+(install|uninstall)\b[^\n]*(-g|--global)\b|"
     r"\brpm\s+-(i|e|U)\b",
     "增删/升级系统或全局包 —— 可能改变依赖、影响其它服务", "masked"),
    ("remote-exec", "CONFIRM",
     r"\b(curl|wget)\b[^\n]*\|\s*(sudo\s+)?(ba)?sh\b|\b(curl|wget)\b[^\n]*\|\s*python",
     "下载即执行远程脚本 —— 内容不可审计，先落盘看一眼再跑", "masked"),
    ("container-destroy", "CONFIRM",
     r"\bdocker\s+(rm|rmi|stop|kill|system\s+prune|volume\s+rm|image\s+prune)\b|"
     r"\bdocker(-|\s+)compose\s+down\b|\bkubectl\s+(delete|drain|scale)\b",
     "销毁容器/镜像/卷或删除 K8s 资源 —— 数据与服务同时受影响", "masked"),
    ("db-destructive", "CONFIRM",
     r"\bdrop\s+(table|database|schema|index)\b|\btruncate\s+table\b|\bdelete\s+from\b|"
     r"\balter\s+table\b|\bflushall\b|\bflushdb\b|\bdb\.\w+\.drop\(",
     "破坏性数据库操作 —— 数据不可恢复；DELETE/UPDATE 务必带 WHERE 并先 SELECT 计数", "raw"),
    ("git-destructive", "CONFIRM",
     r"\bgit\s+(reset\s+--hard|clean\s+-[a-zA-Z]*[fd]|checkout\s+--?\s*\.|"
     r"push\s+[^\n]*(--force|-f)\b|branch\s+-D)\b",
     "丢弃本地改动或强推远端 —— 未提交内容/远端历史会丢", "masked"),
    ("sync-delete", "CONFIRM",
     r"\brsync\b[^\n]*--delete\b|\bscp\b|\bsftp\b",
     "同步/传输文件 —— --delete 会删掉目标端多余文件，传输也会覆盖同名文件", "masked"),
    ("mount-swap", "CONFIRM",
     r"\b(mount|umount|swapoff|swapon|fdisk|parted|lvremove|vgremove|pvremove)\b",
     "改动挂载/分区/卷 —— 可能导致数据不可访问", "masked"),
    ("fw-security", "CONFIRM",
     r"\biptables\b|\bufw\b|\bfirewall-cmd\b|\bsetenforce\b|\bnft\b",
     "改动防火墙/SELinux —— 可能中断访问或降低安全水位", "masked"),
    ("archive-extract-root", "CONFIRM",
     r"\btar\b[^\n]*-[a-zA-Z]*x[a-zA-Z]*[^\n]*-C\s*/(\s|$|etc|usr|opt)",
     "解压到系统目录 —— 归档内容会覆盖同名文件", "masked"),
    ("dd-any", "CONFIRM", r"\bdd\s+if=",
     "dd 块级写入 —— 目标写错就是不可逆覆盖", "masked"),
    ("log-vacuum", "CONFIRM", r"\bjournalctl\b[^\n]*--vacuum|\blogrotate\s+-f\b",
     "清理/轮转日志 —— 历史日志会被删除", "masked"),
    ("privilege-escalation", "CONFIRM",
     r"\bsudo\s+su\b|\bsu\s+-\b|\bsudo\s+-i\b",
     "切换到更高权限账户 —— 之后所有命令都以该身份执行，请确认目标账户是用户指定的", "masked"),
]

# 少数规则天然跨越 shell 分隔符（`curl ... | sh`、`nohup ... &`），按片段切开就都匹配不上，
# 因此这些规则**额外**对整条命令再匹配一次。
CROSS_SEGMENT_RULES = {"remote-exec", "service-start", "fork-bomb"}

LEVEL_ORDER = {"SAFE": 0, "CONFIRM": 10, "BLOCK": 20}
EXIT_CODE = {"SAFE": 0, "CONFIRM": 10, "BLOCK": 20}


def mask_quoted(s):
    """把引号内的内容替换成等长的占位符，保持字符串长度与偏移不变。

    这样 `grep "rm -rf /" app.log`（只是提到，并没执行）不会被 rm 规则误判，
    而命令结构（管道、重定向、分隔符）仍然完整可见。
    """
    out = []
    quote = None
    for ch in s:
        if quote:
            if ch == quote:
                quote = None
                out.append(ch)
            else:
                out.append("\x00")
        elif ch in "'\"":
            quote = ch
            out.append(ch)
        else:
            out.append(ch)
    return "".join(out)


def split_segments(masked, raw):
    """按 shell 分隔符切片段；掩码串与原文等长，故同一组偏移可同时切两份。"""
    spans, start = [], 0
    for m in re.finditer(r"(\|\||&&|;|\||\n|&)", masked):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(masked)))
    segs = []
    for a, b in spans:
        mseg, rseg = masked[a:b].strip(), raw[a:b].strip()
        if rseg:
            segs.append((rseg, mseg))
    return segs


def leading_cmd(masked_seg):
    """取片段的首个命令名（跳过 sudo / 环境变量赋值）。"""
    toks = re.findall(r"\S+", masked_seg)
    for t in toks:
        if "=" in t and not t.startswith("-") and re.match(r"^\w+=", t):
            continue                      # FOO=bar cmd
        if t in ("sudo", "nohup", "time", "env", "nice", "ionice", "exec"):
            continue
        return os.path.basename(t.strip("\x00"))
    return ""


def analyze_rm(raw_seg, protected):
    """rm 的目标路径分析：返回 (level, why) 或 None（不是 rm）。"""
    try:
        toks = shlex.split(raw_seg)
    except ValueError:
        toks = raw_seg.split()
    if not toks:
        return None
    idx = next((i for i, t in enumerate(toks) if os.path.basename(t) == "rm"), None)
    if idx is None:
        return None

    targets = [t for t in toks[idx + 1:] if not t.startswith("-")]
    extra_roots = set(protected)
    roots = ROOT_LIKE | extra_roots
    for t in targets:
        # 未展开的变量路径：`rm -rf $DIR/` 在变量为空时等价于 `rm -rf /`
        if re.match(r"^\$\{?\w+\}?/", t) or re.match(r"^\$\{?\w+\}?$", t):
            return ("BLOCK", f"删除目标是未展开的变量路径 `{t}` —— 变量为空时会退化成 rm -rf /；"
                             "请先 echo 出实际路径、由用户确认后用绝对路径重发")
        norm = re.sub(r"/\*+$", "", t.rstrip("/")) or "/"
        norm = os.path.expanduser(norm) if norm.startswith("~") is False else norm
        if norm in extra_roots or t.rstrip("/") in extra_roots:
            return ("BLOCK", f"删除目标 `{t}` 是配置里声明的受保护路径（risk_policy.protected_paths）"
                             " —— 整体删除影响面过大")
        if norm in roots or t.rstrip("/") in roots or t in ("*", "/*"):
            return ("BLOCK", f"删除目标是根级/系统目录 `{t}` —— 系统不可恢复")
    return None


def load_policy(skill_dir=None):
    """读 config.local.json 的 risk_policy（可选）。没有配置就用内置默认。"""
    skill_dir = skill_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(skill_dir, "config.local.json")
    pol = {"enabled": True, "protected_paths": [], "block_patterns": [],
           "confirm_patterns": [], "allow_patterns": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        raw = cfg.get("risk_policy") or {}
        if isinstance(raw, dict):
            # 类型不对的项直接忽略（check_config.py 会把这些配置错误报给用户），
            # 避免把字符串当列表遍历导致规则被悄悄削弱
            if isinstance(raw.get("enabled"), bool):
                pol["enabled"] = raw["enabled"]
            for k in ("protected_paths", "block_patterns", "confirm_patterns", "allow_patterns"):
                v = raw.get(k)
                if isinstance(v, list):
                    pol[k] = [x for x in v if isinstance(x, str)]
    except Exception:
        pass
    return pol


def classify(cmd, policy=None):
    """返回 (level, hits)。hits 为 [(rule_id, level, why, 命中片段)]。"""
    pol = policy or {"enabled": True, "protected_paths": [], "block_patterns": [],
                     "confirm_patterns": [], "allow_patterns": []}
    if not pol.get("enabled", True):
        return "SAFE", []

    for pat in pol.get("allow_patterns") or []:          # 用户显式豁免的已知安全写法
        try:
            if re.search(pat, cmd):
                return "SAFE", [("allow-pattern", "SAFE", f"命中配置里的豁免规则 {pat}", cmd)]
        except re.error:
            pass

    masked = mask_quoted(cmd)
    hits, level = [], "SAFE"

    def bump(rid, lv, why, seg):
        nonlocal level
        hits.append((rid, lv, why, seg))
        if LEVEL_ORDER[lv] > LEVEL_ORDER[level]:
            level = lv

    extra = ([(f"cfg-block-{i}", "BLOCK", p, f"命中配置里声明的红线规则 {p}", "both")
              for i, p in enumerate(pol.get("block_patterns") or [])] +
             [(f"cfg-confirm-{i}", "CONFIRM", p, f"命中配置里声明的需确认规则 {p}", "both")
              for i, p in enumerate(pol.get("confirm_patterns") or [])])

    for raw_seg, masked_seg in split_segments(masked, cmd):
        head = leading_cmd(masked_seg)
        redirect = re.search(r"(^|[^>\w])>{1,2}\s*(?!/dev/null)\S", masked_seg)
        readonly = head in READONLY_CMDS and head not in DUAL_USE and not redirect
        if readonly:
            continue

        rm_verdict = analyze_rm(raw_seg, pol.get("protected_paths") or [])
        if rm_verdict:
            bump("rm-root-target", rm_verdict[0], rm_verdict[1], raw_seg)

        for rid, lv, pat, why, scan in [(r[0], r[1], r[2], r[3], r[4]) for r in RULES] + \
                                       [(e[0], e[1], e[2], e[3], e[4]) for e in extra]:
            if scan == "both":
                targets = (masked_seg, raw_seg)
            elif scan == "raw":
                targets = (raw_seg,)
            else:
                targets = (masked_seg,)
            try:
                if any(re.search(pat, t, re.IGNORECASE) for t in targets):
                    bump(rid, lv, why, raw_seg)
            except re.error:
                continue

    # 跨片段规则：对整条命令再走一遍（管道/后台符号把命令切开后就匹配不到了）
    for rid, lv, pat, why, scan in RULES:
        if rid not in CROSS_SEGMENT_RULES:
            continue
        target = cmd if scan == "raw" else masked
        try:
            if re.search(pat, target, re.IGNORECASE):
                bump(rid, lv, why, cmd.strip())
        except re.error:
            continue
    return level, hits


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("command", nargs="?", default=None)
    ap.add_argument("--stdin", action="store_true", help="从 stdin 读命令（不进 argv）")
    ap.add_argument("--list", action="store_true", help="打印规则目录")
    ap.add_argument("--skill-dir", default=None)
    args = ap.parse_args()

    if args.list:
        print("风险规则目录（BLOCK=红线，CONFIRM=需用户明确同意）\n")
        for rid, lv, _pat, why, _scan in RULES:
            print(f"  [{lv:7}] {rid:24} {why}")
        print("\nrm 另有目标路径分析：删根级/系统目录或未展开变量路径 → BLOCK，其余 → CONFIRM。")
        print("可在 config.local.json 的 risk_policy 里扩展 protected_paths / "
              "block_patterns / confirm_patterns / allow_patterns。")
        sys.exit(0)

    cmd = sys.stdin.read() if args.stdin else args.command
    if cmd is None:
        ap.error("需要 <命令> 或 --stdin")
    cmd = cmd.rstrip("\r\n")

    level, hits = classify(cmd, load_policy(args.skill_dir))
    print(f"RISK: {level}")
    seen = set()
    for rid, lv, why, seg in sorted(hits, key=lambda h: -LEVEL_ORDER[h[1]]):
        if (rid, seg) in seen:
            continue
        seen.add((rid, seg))
        print(f"- [{lv}][{rid}] {why}")
        print(f"  命中片段: {seg}")
    if level == "CONFIRM":
        print("ASK: 把上面的操作、影响面、回滚方式原样讲给用户，取得**明确同意**后带 -y 重发。")
    elif level == "BLOCK":
        print("ASK: 这是红线操作，agent 不得自行发起。只有用户亲口指定要执行这条命令时，"
              "才可带 --force-dangerous -y 放行；否则请提出更安全的替代方案。")
    sys.exit(EXIT_CODE[level])


if __name__ == "__main__":
    main()
