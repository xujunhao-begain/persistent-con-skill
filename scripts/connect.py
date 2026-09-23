#!/usr/bin/env python3
"""读配置起常驻会话，并自动完成账号密码登录。

由 connect.sh 调用；也可直接: python3 connect.py <session-dir> [skill-dir]

一个前台调用完成四件事：
  1. 校验配置，后台拉起 pty_proxy 常驻持有 `ssh user@host`（脱离本进程存活）；
  2. 轮询会话日志，等到密码提示出现；
  3. 把 config.local.json 里的 password 写进会话（只发这一次）；password 缺省时
     停在这一步不动——提示 agent 向用户当场索要密码、用 send.sh 发入（密码不落盘）；
  4. 确认登录结果：成功则本进程退出、会话继续常驻；失败则终止代理、打印已清理的
     日志尾部作诊断，退出码非 0。

登录失败的判据（日志中出现即失败，立即终止并诊断）：
    Permission denied / Connection refused / Connection timed out /
    Could not resolve hostname / REMOTE HOST IDENTIFICATION HAS CHANGED /
    Host key verification failed / Connection reset / No route to host
成功判据：配了 ready_prompt 则等它出现；未配则发出密码后 login_wait 秒内无失败迹象。
"""
import os, re, sys, time, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check_config as cc  # noqa: E402

PROMPT_WAIT = 30          # 等密码提示出现的最长时间（秒）；连接类失败有自己的签名会更早触发
PASSWORD_PAT = re.compile(r"[Pp]assword\s*:")
FAIL_SIGNATURES = [
    "Permission denied",
    "Connection refused",
    "Connection timed out",
    "Could not resolve hostname",
    "REMOTE HOST IDENTIFICATION HAS CHANGED",
    "Host key verification failed",
    "Connection reset",
    "No route to host",
]


def read_log(sd):
    try:
        with open(os.path.join(sd, "session.log"), "rb") as f:
            return f.read().decode("utf-8", "replace")
    except FileNotFoundError:
        return ""


def tail_clean(sd, n=2000):
    """取日志尾部 N 字节并清理 ANSI，供诊断输出。"""
    data = read_log(sd).encode("utf-8")[-n:]
    if not data.strip():
        return "(日志为空)"
    p = subprocess.run([sys.executable, os.path.join(HERE, "clean_ansi.py")],
                       input=data, capture_output=True)
    return p.stdout.decode("utf-8", "replace").strip() or "(日志为空)"


def kill_proxy(proc):
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def fail(sd, proc, why, hint=""):
    kill_proxy(proc)
    print(f"登录失败: {why}", file=sys.stderr)
    if hint:
        print(hint, file=sys.stderr)
    print("---- 会话日志尾部 ----", file=sys.stderr)
    print(tail_clean(sd), file=sys.stderr)
    sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print("用法: connect.py <session-dir> [skill-dir]", file=sys.stderr)
        sys.exit(2)
    sd = os.path.abspath(sys.argv[1])
    skill_dir = sys.argv[2] if len(sys.argv) > 2 else cc.skill_root()

    cfg, err = cc.load_config(skill_dir)
    if err:
        print(f"错误: 配置不可用 ({err[0]}) —— 请先完成首次配置引导 "
              f"(python3 {skill_dir}/scripts/check_config.py)", file=sys.stderr)
        sys.exit(2)
    miss = cc.missing_fields(cfg)
    if miss:
        print(f"错误: 配置缺字段: {', '.join(miss)} —— 请先完成首次配置引导", file=sys.stderr)
        sys.exit(3)

    fifo = os.path.join(sd, "session.in")
    os.makedirs(sd, exist_ok=True)

    # 1) 后台拉起 pty_proxy（新会话脱离本进程；本脚本退出后会话继续常驻）
    proxy = os.path.join(HERE, "pty_proxy.py")
    proc = subprocess.Popen(
        [sys.executable, proxy, "--session-dir", sd, "--"] + cc.ssh_argv(cfg),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)

    # 2) 等密码提示出现（连接类失败有专属签名，会更早触发）
    deadline = time.time() + PROMPT_WAIT
    while time.time() < deadline:
        if proc.poll() is not None:
            fail(sd, proc, "ssh 进程已退出（未等到密码提示）")
        log = read_log(sd)
        for sig in FAIL_SIGNATURES:
            if sig in log:
                hint = {"REMOTE HOST IDENTIFICATION HAS CHANGED":
                        "主机指纹与本地记录不符（主机重装或中间人）。人工核实后清理 "
                        "~/.ssh/known_hosts 里该主机的旧记录，再重连。",
                        "Permission denied":
                        "服务器拒绝了认证。确认 user 正确；若是密码错误，"
                        "更正 config.local.json 里的 password 后重试。"}.get(sig, "")
                fail(sd, proc, f"日志出现失败签名 `{sig}`", hint)
        if PASSWORD_PAT.search(log):
            break
        time.sleep(0.5)
    else:
        fail(sd, proc, f"{PROMPT_WAIT}s 内未出现密码提示", "确认 host/port 可达、该账户走的是密码认证。")

    # 3) 发密码（或停在提示上交给用户）
    if not cc.has_password(cfg):
        print("会话已停在密码提示上（密码未配置，不落盘）。")
        print("请向用户当场索要密码，然后立即发送（回车自动补）：")
        print(f'  sh "{skill_dir}/scripts/send.sh" "{sd}" \'<用户当场给的密码>\' 4')
        sys.exit(0)

    try:
        fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
    except OSError as e:
        fail(sd, proc, f"会话管道不可写 ({e})")
    try:
        os.write(fd, (str(cfg["password"]) + "\r").encode("utf-8"))
    finally:
        os.close(fd)

    # 4) 确认登录结果
    # 注意：不能用"密码提示是否再出现"判断成败——回显里本来就有 Password: 字样。
    # 失败交给专属签名（Permission denied 等），成功见下：ready_prompt 或超时无失败。
    ready = cfg.get("ready_prompt")
    login_wait = int(cfg.get("login_wait", 10) or 10)
    deadline = time.time() + login_wait
    while True:
        if proc.poll() is not None:
            fail(sd, proc, "ssh 进程已退出（密码发出后）", "多为密码错误或服务器强制断开，检查 password 是否正确。")
        log = read_log(sd)
        if "Permission denied" in log:
            fail(sd, proc, "密码被拒绝（Permission denied）",
                 "更正 config.local.json 里的 password 后重试；注意键盘布局/特殊字符转义。")
        if ready:
            try:
                if re.search(ready, log):
                    break
            except re.error as e:
                fail(sd, proc, f"ready_prompt 正则无效: {e}")
            if time.time() >= deadline:
                fail(sd, proc, f"{login_wait}s 内未出现 ready_prompt `{ready}`",
                     "已发密码且无失败签名——若确认登录成功，放宽该正则或改用 login_wait 超时判定。")
        elif time.time() >= deadline:
            break
        time.sleep(0.5)

    print("OK: 会话已建立并常驻（本进程退出后继续存活）。")
    print(f"之后所有远端操作都用 send.sh 发往该会话；看当前屏幕: screen.sh \"{sd}\" 40")
    if not ready:
        print("提示: 未配置 ready_prompt，本次按「超时无失败」判定成功——"
              f"用 screen.sh 复核一眼更稳妥: sh \"{skill_dir}/scripts/screen.sh\" \"{sd}\" 15")
    print(f"收工: 先在会话里 exit 登出，再 sh \"{skill_dir}/scripts/stop.sh\" \"{sd}\"")
    sys.exit(0)


if __name__ == "__main__":
    main()
