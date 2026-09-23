#!/usr/bin/env python3
"""常驻 pty 代理：持有一个交互式命令（如 ssh），让它跨多次调用存活。

为什么需要它：普通的一次性 shell 调用（每次都是新进程）无法维持一个交互式
远程会话——命令一结束连接就断。这个代理在后台常驻，用伪终端(pty)持有目标
命令，把输出实时写进 <session-dir>/session.log，并从命名管道
<session-dir>/session.in 读入你要发送的按键/命令。这样只需登录一次
（包括只用一个动态口令），之后所有操作都在同一个活会话里完成。

用法：
    python3 pty_proxy.py --session-dir /path/to/workdir -- <命令> [参数...]

例：
    python3 pty_proxy.py --session-dir ./sess -- \
        ssh -o IdentitiesOnly=yes -i ~/.ssh/id_rsa user@bastion -p22022

务必用 run_in_background（或 nohup ... &）启动它，让它常驻。
"""
import os, pty, select, sys, argparse, signal, time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session-dir", required=True, help="存放 session.log / session.in 的工作目录")
    ap.add_argument("cmd", nargs=argparse.REMAINDER, help="-- 之后是要持有的交互式命令")
    args = ap.parse_args()

    cmd = args.cmd
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("错误：请在 -- 之后给出要运行的命令", file=sys.stderr)
        sys.exit(2)

    sd = os.path.abspath(args.session_dir)
    os.makedirs(sd, exist_ok=True)
    log_path = os.path.join(sd, "session.log")
    fifo_path = os.path.join(sd, "session.in")

    if os.path.exists(fifo_path):
        os.remove(fifo_path)
    os.mkfifo(fifo_path)
    open(log_path, "wb").close()  # 清空日志，本次会话从头记录
    os.environ.setdefault("TERM", "xterm")

    pid, master = pty.fork()
    if pid == 0:
        # 子进程：已连到 pty slave，直接 exec 目标命令
        os.execvp(cmd[0], cmd)
        os._exit(127)

    # 写下代理自身 PID，供 stop.sh 精确终止（比 pkill -f 模式匹配可靠，尤其 macOS）
    pid_path = os.path.join(sd, "session.pid")
    with open(pid_path, "w") as pf:
        pf.write(str(os.getpid()))

    def _terminate(signum, frame):
        # 收到终止信号：抛 SystemExit 让主循环退出，由 finally 统一、稳妥地结束子进程
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)

    logf = open(log_path, "ab", buffering=0)
    fifo_fd = os.open(fifo_path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        while True:
            try:
                rlist, _, _ = select.select([master, fifo_fd], [], [], 60)
            except (InterruptedError, OSError):
                continue
            if master in rlist:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                logf.write(data)
            if fifo_fd in rlist:
                cmd_in = os.read(fifo_fd, 65536)
                if cmd_in:
                    os.write(master, cmd_in)
                else:
                    # 写端关闭后重开读端，等待下一次写入
                    os.close(fifo_fd)
                    fifo_fd = os.open(fifo_path, os.O_RDONLY | os.O_NONBLOCK)
    finally:
        # 稳妥结束子进程：先 TERM，最多等 ~3 秒，仍在则 KILL 兜底
        # （交互式 shell 可能忽略 TERM；ssh 等一般会正常响应 TERM 迅速退出）
        _reaped = False
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
        for _ in range(30):
            try:
                wpid, _ = os.waitpid(pid, os.WNOHANG)
                if wpid == pid:
                    _reaped = True
                    break
            except ChildProcessError:
                _reaped = True
                break
            except Exception:
                break
            time.sleep(0.1)
        if not _reaped:
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except Exception:
                pass
        logf.write(b"\n>>> SESSION ENDED <<<\n")
        logf.close()
        try:
            os.remove(pid_path)
        except Exception:
            pass


if __name__ == "__main__":
    main()
