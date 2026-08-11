# WorkBuddy 每日自动签到

自动领取 WorkBuddy（CodeBuddy）每日积分，通过 GitHub Actions 云端每天定时运行，无需电脑开机。

## 原理

WorkBuddy 客户端的「Buddy 加油站 / 每日签到」本质是带 Token 的 HTTP 请求，无需模拟点击：

| 接口 | 方法 | 说明 |
|---|---|---|
| `/v2/billing/meter/checkin-status` | POST | 查询签到状态 |
| `/v2/billing/meter/daily-checkin` | POST | 领取每日签到 |

- 域名：`https://copilot.tencent.com/v2`（兼容 `https://www.codebuddy.cn/v2`）
- 鉴权：`Authorization: Bearer <accessToken>` + `X-User-Id` + `X-Domain: www.codebuddy.cn`

Token 从 WorkBuddy 桌面客户端本地登录态文件读取（Windows）：
`%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info`

## 本地运行

```bash
pip install -r requirements.txt
python workbuddy_checkin.py            # 正常领取
python workbuddy_checkin.py --check-only   # 只查询不领取
```

脚本自动从环境变量 `WB_ACCESS_TOKEN` 或本地登录态文件读取 token，结果写入 `checkin_log.jsonl`。

## GitHub Actions 云端运行

仓库需配置以下 Secrets（**token 绝不进代码仓库**）：

| Secret | 说明 |
|---|---|
| `WB_ACCESS_TOKEN` | 登录 accessToken（从本地 auth 文件获取） |
| `WB_USER_ID` | 用户 uid（可选） |

> 邮件告警无需 SMTP：由本地 WorkBuddy 自动化（每天 10:00）检查云端运行结果，失败/未运行时通过 WorkBuddy 智能邮箱（agent-mail）发送告警到收件邮箱。

默认每天 09:10（北京时间）自动运行；可在 Actions 页面手动触发（workflow_dispatch）。

## 邮件通知规则

**每天成功签到 → 不发邮件；未成功签到 → 发邮件说明原因。**

本地 WorkBuddy 自动化任务「WorkBuddy 签到监控与活动提醒」每天 10:00 运行两步检查：

**第一步 · 签到检查**（通过智能邮箱 agent-mail 发送）：

| 当天情况 | 邮件行为 |
|---|---|
| 已成功签到 | 不发邮件 |
| 签到活动未激活（active=false） | 发「ℹ️ 今日活动未激活」说明邮件 |
| 活动激活但未签到（云端失败/未运行） | 发「⚠️ 签到未运行/失败」告警邮件（含原因与修复建议） |
| 本地查询异常（token 失效等） | 发「⚠️ 签到监控异常」邮件 |

**第二步 · 新活动监控**（`python workbuddy_checkin.py --check-banner`）：

| 退出码 | 含义 | 邮件行为 |
|---|---|---|
| 10 | 发现新活动横幅 | 发「🎉 新活动提醒」邮件（含活动内容/类型/链接） |
| 0 | 无新活动 | 不发邮件 |
| 其他 | 检查异常 | 发「⚠️ 活动监控异常」邮件 |

banner 状态记录在本地 `banner_state.json`（已加入 .gitignore，不提交仓库），只有 activity_id 变化才触发提醒，避免重复邮件。

前置条件：在 WorkBuddy「更多 - 我的邮箱」开通智能邮箱（免费，已开通）。

## Token 失效处理

accessToken 有效期约 60 天（示例值至 2026-10-10）。失效后接口返回 401，需要重新从本地登录态文件获取新 token 并更新到 GitHub Secrets：
1. 打开 WorkBuddy 客户端确认已登录
2. 读取 `%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info` 中的 `auth.accessToken`
3. 更新仓库 Secret `WB_ACCESS_TOKEN`
