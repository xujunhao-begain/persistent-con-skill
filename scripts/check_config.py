#!/usr/bin/env python3
"""技能配置的单一真相源：加载/校验配置，并据配置组装 SSH 登录命令。

既作命令行工具（供 agent 判断是否需要走首次配置引导），也作模块供 connect.py 复用，
避免"读配置 + 拼登录命令"的逻辑散落多处、彼此漂移。

命令行用法:
    python3 check_config.py [--skill-dir <技能根目录>]
    - 找不到 config.local.json  -> 退出码 2（需要首次配置引导）
    - 找到但缺必填字段          -> 退出码 3（列出缺失字段）
    - 完整                      -> 退出码 0（打印登录方式预览）

必填字段: host / user / port。password 可缺省：缺省（或留空）时技能会停在密码提示上，
由用户当场提供、经 send.sh 发入（密码不落盘）；填写则登录全自动。
"""
import os, sys, re, json, argparse

REQUIRED = ["host", "user", "port"]


def skill_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config(skill_dir=None):
    """返回 (cfg, err)。err 为 None 表示成功；否则为 (类型, 详情)。"""
    skill_dir = skill_dir or skill_root()
    path = os.path.join(skill_dir, "config.local.json")
    if not os.path.exists(path):
        return None, ("NOT_CONFIGURED", path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), None
    except Exception as e:
        return None, ("INVALID", str(e))


def missing_fields(cfg):
    return [k for k in REQUIRED if not cfg.get(k)]


def has_password(cfg):
    return bool(cfg.get("password"))


def check_risk_policy(cfg):
    """校验可选的 risk_policy 段，返回告警列表（不影响就绪判定，只提示）。

    闸门本身有内置默认规则，配置缺失时照样生效；这里只挡住"配错导致闸门形同虚设"的情况。
    """
    warns = []
    pol = cfg.get("risk_policy")
    if pol is None:
        return ["提示: 未配置 risk_policy —— 使用内置默认风险规则（已覆盖常见红线）。"]
    if not isinstance(pol, dict):
        return ["警告: risk_policy 必须是对象，已忽略，回退到内置默认规则。"]
    if pol.get("enabled") is False:
        warns.append("警告: risk_policy.enabled=false —— **风险闸门已整体关闭**，"
                     "高危命令将不再拦截。除非你清楚后果，建议改回 true。")
    for key in ("protected_paths", "block_patterns", "confirm_patterns", "allow_patterns"):
        val = pol.get(key)
        if val is None:
            continue
        if not isinstance(val, list) or any(not isinstance(x, str) for x in val):
            warns.append(f"警告: risk_policy.{key} 必须是字符串数组，已忽略该项。")
            continue
        if key.endswith("patterns"):
            for pat in val:
                try:
                    re.compile(pat)
                except re.error as e:
                    warns.append(f"警告: risk_policy.{key} 里的正则无效，已忽略: {pat} ({e})")
    if pol.get("allow_patterns"):
        warns.append("提示: allow_patterns 是闸门豁免口子（整条命令命中即放行）—— "
                     "请确认每条都写得足够窄。")
    return warns


def ssh_argv(cfg):
    """由配置组装 SSH 登录命令（token 列表），check_config 预览与 connect 实连共用。

    本技能是账号密码直连，默认注入：
    - PubkeyAuthentication=no + PreferredAuthentications=password,keyboard-interactive：
      强制走密码路径，不让 ssh 先试一堆公钥拖慢登录、在日志里留下无关提示。
    - NumberOfPasswordPrompts=1：密码错了立即失败退出，connect.py 才能可靠识别
      "密码被拒"，而不是再次弹提示导致状态混乱。
    - StrictHostKeyChecking=accept-new：首次连接自动记录主机指纹、不再交互式问 yes/no；
      指纹变化（主机重装/中间人）仍会拒绝连接，由 connect.py 识别并明确报错。

    可选 cfg['ssh_opts']（字符串或列表）插在默认之前。ssh 对同一 -o 选项取**首次**出现的
    值，所以在 ssh_opts 里自带上面任一选项即可覆盖默认。
    """
    extra = cfg.get("ssh_opts") or []
    if isinstance(extra, str):
        extra = extra.split()
    defaults = [
        "-o", "PubkeyAuthentication=no",
        "-o", "PreferredAuthentications=password,keyboard-interactive",
        "-o", "NumberOfPasswordPrompts=1",
        "-o", "StrictHostKeyChecking=accept-new",
    ]
    return ["ssh", *list(extra), *defaults,
            f"{cfg['user']}@{cfg['host']}", "-p" + str(cfg["port"])]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill-dir", default=skill_root())
    args = ap.parse_args()

    cfg, err = load_config(args.skill_dir)
    if err and err[0] == "NOT_CONFIGURED":
        print("NOT_CONFIGURED: 未找到 config.local.json —— 需要首次配置引导。")
        print(f"预期路径: {err[1]}")
        print("请向用户索要必要信息后写入该文件（字段见 config.example.json）。")
        sys.exit(2)
    if err:
        print(f"INVALID: config.local.json 解析失败: {err[1]}")
        sys.exit(3)

    miss = missing_fields(cfg)
    if miss:
        print(f"INCOMPLETE: 缺少必填字段: {', '.join(miss)}")
        sys.exit(3)

    print("OK: 配置就绪。")
    print("登录命令预览: " + " ".join(ssh_argv(cfg)))
    if has_password(cfg):
        print("登录方式: 自动 —— password 已配置，connect.sh 会自动完成密码登录。")
    else:
        print("登录方式: 交互 —— password 未配置，会话会停在密码提示上，"
              "由用户当场提供密码后用 send.sh 发入（密码不落盘）。")
    rp = cfg.get("ready_prompt")
    print("成功判据: {}".format(
        f"日志出现 ready_prompt 正则 `{rp}`" if rp else
        f"发出密码后 {cfg.get('login_wait', 10)} 秒内无失败迹象（可用 screen.sh 人工复核）"))
    for w in check_risk_policy(cfg):
        print(w)
    print("风险闸门: 所有 send.sh 发送前强制分级（规则目录: "
          "python3 scripts/risk_check.py --list）")
    sys.exit(0)


if __name__ == "__main__":
    main()
