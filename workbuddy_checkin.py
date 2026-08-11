# -*- coding: utf-8 -*-
"""
WorkBuddy 每日自动签到脚本
===========================
调用 WorkBuddy(CodeBuddy) 官方接口领取每日积分。

接口（已实测调通）:
    POST https://copilot.tencent.com/v2/billing/meter/checkin-status  查询签到状态
    POST https://copilot.tencent.com/v2/billing/meter/daily-checkin   领取每日签到
鉴权:
    Authorization: Bearer <accessToken>
    X-User-Id: <uid>
    X-Domain: www.codebuddy.cn

Token 来源（按优先级）:
    1. 环境变量 WB_ACCESS_TOKEN / WB_REFRESH_TOKEN（GitHub Actions 注入 Secrets）
    2. 本地 WorkBuddy 登录态文件（Windows 桌面客户端）:
       %LOCALAPPDATA%/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info
       （注意：token 不进代码仓库）

运行:
    python workbuddy_checkin.py
    python workbuddy_checkin.py --check-only   # 只查询不领取
"""
import argparse
import datetime
import json
import os
import smtplib
import ssl
import sys
import time
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

try:
    import requests
except ImportError:
    print("缺少依赖 requests，请先: pip install requests")
    sys.exit(1)

API_BASE = "https://copilot.tencent.com/v2"
X_DOMAIN = "www.codebuddy.cn"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (WorkBuddy-Checkin)"

# 本地 WorkBuddy 桌面端登录态文件（Windows）
LOCAL_AUTH_FILE = os.path.join(
    os.environ.get("LOCALAPPDATA", r"C:\Users\%s\AppData\Local" % os.environ.get("USERNAME", "")),
    "CodeBuddyExtension", "Data", "Public", "auth", "workbuddy-desktop.info",
)

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkin_log.jsonl")


def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def append_log(record: dict):
    """追加一条 JSONL 日志"""
    try:
        record["ts"] = datetime.datetime.now().isoformat()
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        log(f"WARN 写日志失败: {e}")


def get_token():
    """获取 token：环境变量优先，其次本地登录态文件"""
    token = os.environ.get("WB_ACCESS_TOKEN", "").strip()
    uid = os.environ.get("WB_USER_ID", "").strip()
    if token:
        return token, uid, "env"

    if os.path.exists(LOCAL_AUTH_FILE):
        try:
            with open(LOCAL_AUTH_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            auth = data.get("auth", {})
            token = auth.get("accessToken", "")
            uid = data.get("account", {}).get("uid", "")
            if token:
                return token, uid, "local"
        except Exception as e:
            log(f"WARN 读取本地登录态失败: {e}")

    return "", "", ""


def api_post(path, token, uid, payload=None, timeout=20):
    """调用 copilot.tencent.com 接口"""
    url = API_BASE + path
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Domain": X_DOMAIN,
        "User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if uid:
        headers["X-User-Id"] = uid
    resp = requests.post(url, headers=headers, json=payload or {}, timeout=timeout)
    try:
        body = resp.json()
    except Exception:
        body = {"code": resp.status_code, "msg": resp.text[:300]}
    return resp.status_code, body


def send_email(subject, body, config=None):
    """通过 SMTP 发送邮件（失败不抛异常，只记录）"""
    cfg = config or {}
    host = cfg.get("smtp_host") or os.environ.get("SMTP_HOST", "")
    user = cfg.get("smtp_user") or os.environ.get("SMTP_USER", "")
    password = cfg.get("smtp_pass") or os.environ.get("SMTP_PASS", "")
    to = cfg.get("mail_to") or os.environ.get("MAIL_TO", "")
    if not (host and user and password and to):
        log("WARN 邮件配置不完整，跳过发送")
        return False
    port_str = cfg.get("smtp_port") or os.environ.get("SMTP_PORT", "")
    try:
        port = int(port_str) if port_str else 465
    except (TypeError, ValueError):
        port = 465
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = formataddr((str(Header("WorkBuddy签到", "utf-8")), user))
        msg["To"] = to
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, timeout=30, context=context) as server:
            server.login(user, password)
            server.sendmail(user, [to], msg.as_string())
        log(f"OK 已发送邮件到 {to}: {subject}")
        return True
    except Exception as e:
        log(f"ERROR 邮件发送失败: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="WorkBuddy 每日自动签到")
    parser.add_argument("--check-only", action="store_true", help="只查询签到状态，不领取")
    args = parser.parse_args()

    token, uid, source = get_token()
    if not token:
        log("ERROR 未找到 token（环境变量 WB_ACCESS_TOKEN 或本地登录态文件均无）")
        append_log({"event": "no_token", "ok": False, "msg": "no token found"})
        sys.exit(1)
    log(f"token 来源: {source} | uid: {uid or '(未提供)'}")

    # 1. 查询签到状态（正确接口：checkin-activity-status；checkin-status 仅作兜底）
    try:
        code, status = api_post("/billing/meter/checkin-activity-status", token, uid)
        log(f"checkin-activity-status -> HTTP {code}, code={status.get('code')}")
        if code != 200 or status.get("code") != 0:
            # 兜底：尝试旧接口
            log("WARN 活动接口异常，尝试 checkin-status 兜底")
            code, status = api_post("/billing/meter/checkin-status", token, uid)
            log(f"checkin-status -> HTTP {code}, code={status.get('code')}")
        if code != 200 or status.get("code") != 0:
            log(f"ERROR 查询状态失败: {json.dumps(status, ensure_ascii=False)[:300]}")
            append_log({"event": "status_fail", "ok": False, "msg": status})
            send_email("⚠️ WorkBuddy签到失败：查询状态异常",
                       f"查询签到状态失败\nHTTP {code}\n响应: {json.dumps(status, ensure_ascii=False)[:500]}\n时间: {datetime.datetime.now()}")
            sys.exit(1)
        data = status.get("data", {})
    except Exception as e:
        log(f"ERROR 查询状态异常: {e}")
        append_log({"event": "status_error", "ok": False, "msg": str(e)})
        send_email("⚠️ WorkBuddy签到失败：网络异常", f"查询签到状态时发生异常:\n{e}\n时间: {datetime.datetime.now()}")
        sys.exit(1)

    # 打印状态摘要
    summary = {
        "active": data.get("active", False),
        "today_checked_in": data.get("today_checked_in", False),
        "streak_days": data.get("streak_days", 0),
        "daily_credit": data.get("daily_credit", 0),
        "today_credit": data.get("today_credit", 0),
        "total_credits": data.get("total_credits", 0),
        "activity_name": data.get("activity_name", ""),
        "start_time": data.get("start_time", ""),
        "end_time": data.get("end_time", ""),
        "claim_button_text": data.get("claim_button_text", ""),
        "action_button": data.get("action_button", {}),
    }
    log("状态摘要: " + json.dumps(summary, ensure_ascii=False))

    if args.check_only:
        log("--check-only 模式，不领取")
        append_log({"event": "check_only", "ok": True, "summary": summary})
        sys.exit(0)

    # 2. 判断是否需要领取
    if not data.get("active", False):
        log("INFO 当前签到活动未激活（active=false），跳过领取")
        append_log({"event": "inactive", "ok": True, "summary": summary})
        # 用户要求：没成功签到就发邮件说明原因
        send_email("ℹ️ WorkBuddy 签到：今日活动未激活",
                   f"今日签到活动当前未激活，未能领取积分。\n\n状态摘要:\n{json.dumps(summary, ensure_ascii=False, indent=2)}\n\n时间: {datetime.datetime.now()}")
        sys.exit(0)

    if data.get("today_checked_in", False):
        log("INFO 今日已签到，无需重复领取")
        append_log({"event": "already", "ok": True, "summary": summary})
        sys.exit(0)

    # 3. 执行每日签到
    log("正在领取每日签到...")
    try:
        code, result = api_post("/billing/meter/daily-checkin", token, uid)
        log(f"daily-checkin -> HTTP {code}, code={result.get('code')} msg={result.get('msg')}")
        if code == 200 and result.get("code") == 0:
            log("✅ 签到成功！")
            append_log({"event": "success", "ok": True, "data": result.get("data"), "summary": summary})
            # 用户要求：成功签到不发邮件
            sys.exit(0)
        else:
            # 已签到/活动未开始等业务错误码
            msg = result.get("msg", "")
            log(f"INFO 领取未成功(可能已签到或未开放): {msg}")
            append_log({"event": "claim_skip", "ok": True, "code": result.get("code"), "msg": msg})
            # 用户要求：没成功签到就发邮件说明原因
            send_email("⚠️ WorkBuddy 签到：领取未成功",
                       f"领取每日签到未成功，原因：{msg}\n\n状态摘要:\n{json.dumps(summary, ensure_ascii=False, indent=2)}\n\n时间: {datetime.datetime.now()}")
            sys.exit(0)
    except Exception as e:
        log(f"ERROR 领取异常: {e}")
        append_log({"event": "claim_error", "ok": False, "msg": str(e)})
        send_email("⚠️ WorkBuddy签到失败：领取异常", f"领取每日签到时发生异常:\n{e}\n时间: {datetime.datetime.now()}")
        sys.exit(1)


if __name__ == "__main__":
    main()
