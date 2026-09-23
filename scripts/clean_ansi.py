#!/usr/bin/env python3
"""从 stdin 读取原始终端字节流，去除 ANSI 转义/OSC 序列与回车符，输出可读文本。

远程 TUI（堡垒机菜单、彩色提示符）会夹带大量光标定位/清屏/变色序列，
直接看 session.log 很难读。所有读日志的脚本都管道到这里做清理。
以 UTF-8 容错解码，避免中文/非法字节导致的 illegal byte sequence 报错。
"""
import sys, re

data = sys.stdin.buffer.read().decode("utf-8", "replace")
data = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", data)      # CSI 序列
data = re.sub(r"\x1b\][^\x07]*(\x07|\x1b\\)", "", data)  # OSC 序列（如设置标题）
data = data.replace("\r", "")
sys.stdout.write(data)
