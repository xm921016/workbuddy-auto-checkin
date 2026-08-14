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
import base64
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
BANNER_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "banner_state.json")
# refresh token 缓存文件（云端工作区临时文件，已 gitignore，绝不入库）
REFRESH_TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".refresh_token")


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


def check_banner(token, uid):
    """
    检查活动横幅（banner），判断是否出现新的可参与活动。
    返回 (new_activity, info)：
      - (True, info)   发现新活动（与上次不同）
      - (False, None)  无变化或无活动
      - (None, msg)    检查异常
    状态记录在 banner_state.json，避免重复提醒。
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Domain": X_DOMAIN,
        "X-User-Id": uid,
        "X-IDE-Type": "WorkBuddy",
        "X-IDE-Name": "WorkBuddy",
        "X-Product": "WorkBuddy",
        "User-Agent": UA,
    }
    try:
        resp = requests.get(API_BASE + "/activity/workbuddy/banner",
                            headers=headers, timeout=20)
        body = resp.json()
        if resp.status_code != 200 or body.get("code") != 0 or not body.get("data"):
            log(f"WARN banner 接口无数据: HTTP {resp.status_code}")
            return None, f"banner 接口无数据: HTTP {resp.status_code}"
        raw = body["data"]
        activity_id = str(raw.get("activity_id", "") or "")
        content = raw.get("banner_content", "") or ""
        action = raw.get("action", {}) or {}
        online = raw.get("activity_online_status", False)
        # 可自动领取的活动特征：有 action 且 action 是 open_url/switch_model 之外可操作类型，
        # 或 status 非 None 且 online。这里只做通用记录，是否值得提醒由监控脚本判断。
        info = {
            "activity_id": activity_id,
            "banner_content": content,
            "action": action,
            "online": online,
            "detail_link": raw.get("detail_link", "") or "",
        }
        last = {}
        if os.path.exists(BANNER_STATE_FILE):
            try:
                with open(BANNER_STATE_FILE, "r", encoding="utf-8") as f:
                    last = json.load(f)
            except Exception:
                last = {}
        last_id = str(last.get("activity_id", "") or "")
        last_content = str(last.get("banner_content", "") or "")
        # 更新状态
        state = {"activity_id": activity_id, "banner_content": content,
                 "action": action, "checked_at": datetime.datetime.now().isoformat()}
        with open(BANNER_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        if not activity_id:
            log("banner 无活动 ID")
            return False, None
        if activity_id == last_id and content == last_content:
            log(f"banner 无新活动（当前: {content[:40] or '(空)'}）")
            return False, None
        if content == last_content:
            # ID 变了但内容相同（横幅重建等），只更新状态不打扰
            log(f"banner ID 变化但内容相同，仅更新状态不提醒（{content[:40]}）")
            return False, None
        log(f"发现新活动横幅: {content}")
        return True, info
    except Exception as e:
        log(f"ERROR banner 检查异常: {e}")
        return None, str(e)


def build_banner_email_body(info: dict) -> str:
    """根据 banner 信息生成新活动提醒邮件正文"""
    action = info.get("action", {}) or {}
    action_type = str(action.get("type", "") or "")
    action_label = str(action.get("label", "") or "")
    type_map = {
        "switch_model": "模型福利（免费切换/限免模型）",
        "open_url": "可参与活动（需打开链接参与）",
        "open_activity": "可参与活动（需打开链接参与）",
        "receive_gift": "可领取礼包",
    }
    type_desc = type_map.get(action_type, "其他类型（请到 WorkBuddy 客户端查看）")
    if action_type in ("open_url", "open_activity"):
        manual = "需要人工参与：请打开详情链接或 WorkBuddy 客户端按提示操作"
    elif action_type == "switch_model":
        manual = "无需人工参与：为模型福利，通常自动生效"
    else:
        manual = "无法自动判断：请到 WorkBuddy 客户端查看该活动详情"
    detail = info.get("detail_link", "") or "（无）"
    return (
        "发现新的 WorkBuddy 活动横幅！\n\n"
        f"活动内容: {info.get('banner_content', '') or '(空)'}\n"
        f"活动ID: {info.get('activity_id', '') or '(无)'}\n"
        f"动作类型: {type_desc}（{action_type or '(空)'}）\n"
        f"操作按钮: {action_label or '(无)'}\n"
        f"活动在线: {'是' if info.get('online') else '否'}\n"
        f"详情链接: {detail}\n\n"
        f"是否需人工参与: {manual}\n\n"
        f"时间: {datetime.datetime.now()}"
    )


def decode_jwt_exp(token):
    """从 JWT 中解码过期时间（秒级时间戳），失败返回 None"""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        info = json.loads(base64.urlsafe_b64decode(payload))
        return info.get("exp")
    except Exception:
        return None


def try_refresh_token(refresh_tok, uid):
    """
    用 refresh token 调 /v2/plugin/auth/token/refresh 换新 token（已实测调通）。
    返回 (True, auth_dict) 或 (False, err_msg)。
    """
    headers = {
        "X-Refresh-Token": refresh_tok,
        "X-Auth-Refresh-Source": "plugin",
        "Content-Type": "application/json",
        "X-Domain": X_DOMAIN,
        "User-Agent": UA,
    }
    if uid:
        headers["X-User-Id"] = uid
    try:
        resp = requests.post(API_BASE + "/plugin/auth/token/refresh",
                             headers=headers, json={}, timeout=20)
        body = resp.json()
    except Exception as e:
        return False, f"请求异常: {e}"
    if resp.status_code != 200 or not body.get("data") or not body["data"].get("accessToken"):
        return False, f"HTTP {resp.status_code}: {json.dumps(body, ensure_ascii=False)[:300]}"
    return True, body["data"]


def load_refresh_token():
    """读取 refresh token：缓存文件优先，其次环境变量 WB_REFRESH_TOKEN。返回 (token, source)"""
    if os.path.exists(REFRESH_TOKEN_FILE):
        try:
            with open(REFRESH_TOKEN_FILE, "r", encoding="utf-8") as f:
                tok = f.read().strip()
            if tok:
                return tok, "cache"
        except Exception:
            pass
    tok = os.environ.get("WB_REFRESH_TOKEN", "").strip()
    if tok:
        return tok, "env"
    return "", ""


def save_refresh_token(tok):
    """持久化最新 refresh token（供云端缓存、下次运行使用）"""
    try:
        with open(REFRESH_TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(tok)
        return True
    except Exception as e:
        log(f"WARN 保存 refresh token 失败: {e}")
        return False


def get_access_token():
    """
    获取有效 access token（自动续期优先）：
    1. 有 refresh token -> 调用刷新接口换新 access token（并持久化新的 refresh token）
    2. 刷新失败或没有 refresh token -> 退回 WB_ACCESS_TOKEN / 本地登录态文件
    返回 (token, uid, source)
    """
    refresh_tok, rt_source = load_refresh_token()
    if refresh_tok:
        uid = os.environ.get("WB_USER_ID", "").strip()
        ok, result = try_refresh_token(refresh_tok, uid)
        if ok:
            new_access = result.get("accessToken", "")
            new_refresh = result.get("refreshToken", "")
            if new_refresh:
                save_refresh_token(new_refresh)
            if new_access:
                days = (result.get("refreshExpiresIn") or 0) // 86400
                log(f"✅ token 自动续期成功（来源 {rt_source}，新 refresh 有效期约 {days} 天）")
                return new_access, uid, "refresh"
        else:
            log(f"WARN token 自动续期失败（来源 {rt_source}）: {result}")
    # 退回原逻辑（环境变量 WB_ACCESS_TOKEN 或本地登录态）
    return get_token()


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
    parser.add_argument("--check-banner", action="store_true", help="检查活动横幅是否有新活动（不领取）")
    args = parser.parse_args()

    token, uid, source = get_access_token()
    if not token:
        log("ERROR 未找到 token（自动续期失败且 WB_ACCESS_TOKEN/本地登录态均无）")
        append_log({"event": "no_token", "ok": False, "msg": "no token found"})
        sys.exit(1)
    log(f"token 来源: {source} | uid: {uid or '(未提供)'}")

    # token 到期兜底提醒：自动续期正常时新 token 有 60 天，不会触发；仅续期失效/未配置时才提醒
    exp = decode_jwt_exp(token)
    if exp:
        days_left = (exp - int(time.time())) // 86400
        if days_left <= 7:
            log(f"WARN token 剩余 {days_left} 天，发送到期提醒")
            append_log({"event": "token_expiring", "ok": True, "days_left": days_left})
            send_email("⚠️ WorkBuddy Token 即将过期",
                       f"WorkBuddy 登录 token 即将过期（剩余 {days_left} 天）。\n\n"
                       f"自动续期未能完成或未启用。解决办法：\n"
                       f"1. 打开 WorkBuddy 客户端确认已登录（会自动刷新本地会话）\n"
                       f"2. 若云端未配置 WB_REFRESH_TOKEN，联系助手更新 GitHub Secret\n\n"
                       f"当前 token 到期时间: {datetime.datetime.fromtimestamp(exp).strftime('%Y-%m-%d %H:%M')}\n"
                       f"发送时间: {datetime.datetime.now()}")

    # 0. 活动横幅检查模式
    if args.check_banner:
        new_activity, info = check_banner(token, uid)
        if new_activity is None:
            log("banner 检查异常")
            append_log({"event": "banner_error", "ok": False, "msg": info})
            send_email("⚠️ WorkBuddy 活动监控异常",
                       f"检查活动横幅时发生异常:\n{info}\n时间: {datetime.datetime.now()}")
            sys.exit(2)
        if new_activity:
            print(f"NEW_ACTIVITY: {json.dumps(info, ensure_ascii=False)}", flush=True)
            append_log({"event": "banner_new", "ok": True, "info": info})
            send_email("🎉 WorkBuddy 新活动提醒", build_banner_email_body(info))
            sys.exit(10)  # 10 = 发现新活动
        append_log({"event": "banner_nochange", "ok": True})
        sys.exit(0)

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
