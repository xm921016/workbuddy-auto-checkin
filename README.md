# WorkBuddy 每日自动签到

自动领取 WorkBuddy（CodeBuddy）每日积分，通过 GitHub Actions 云端每天定时运行，无需电脑开机。

## 原理

WorkBuddy 客户端的「Buddy 加油站 / 每日签到」本质是带 Token 的 HTTP 请求，无需模拟点击：

| 接口 | 方法 | 说明 |
|---|---|---|
| `/v2/billing/meter/checkin-activity-status` | POST | 查询签到状态（主用，旧接口 checkin-status 作兜底） |
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

**本地零文件、零定时任务**：签到与新活动监控全部由 GitHub Actions 云端完成，邮件由云端通过 SMTP 直接发送。

仓库需配置以下 Secrets（**token 绝不进代码仓库**）：

| Secret | 说明 |
|---|---|
| `WB_ACCESS_TOKEN` | 登录 accessToken（从本地 auth 文件获取） |
| `WB_USER_ID` | 用户 uid（可选） |
| `SMTP_HOST` | SMTP 服务器（如 smtp.qq.com） |
| `SMTP_PORT` | SMTP 端口（如 465） |
| `SMTP_USER` | 发件邮箱账号 |
| `SMTP_PASS` | 发件邮箱授权码（非登录密码） |
| `MAIL_TO` | 收件邮箱 |

默认每天 09:10（北京时间）自动运行，每次执行两步：
1. **每日签到**：成功不发邮件；未激活/失败时通过 SMTP 发邮件说明原因
2. **新活动监控**：检查活动横幅，发现新活动或检查异常时发提醒邮件；运行后自动把最新状态提交回仓库 `banner_state.json`（去重基准，避免重复提醒）

可在 Actions 页面手动触发（workflow_dispatch）。

## 邮件通知规则

**所有邮件由云端（GitHub Actions）通过 SMTP 直接发送，本地不运行任何东西。**

**签到检查**（`python workbuddy_checkin.py`）：

| 当天情况 | 邮件行为 |
|---|---|
| 已成功签到 | 不发邮件 |
| 签到活动未激活（active=false） | 发「ℹ️ 今日活动未激活」说明邮件 |
| 查询状态异常 / 领取异常 | 发「⚠️ 签到失败」告警邮件（含原因） |

**新活动监控**（`python workbuddy_checkin.py --check-banner`）：

| 退出码 | 含义 | 邮件行为 |
|---|---|---|
| 10 | 发现新活动横幅 | 发「🎉 新活动提醒」邮件（含活动内容/类型/链接） |
| 0 | 无新活动 | 不发邮件 |
| 2 或其他 | 检查异常 | 发「⚠️ 活动监控异常」邮件 |

banner 状态记录在仓库 `banner_state.json`（由 workflow 每次运行后自动提交更新），只有 activity_id 变化才触发提醒，避免重复邮件。

## 本地手动运行（可选）

如需临时手动检查，可在本地执行（本地不配置 SMTP 环境变量时不会发邮件，仅打印结果）：

```bash
python workbuddy_checkin.py --check-only     # 只查签到状态
python workbuddy_checkin.py --check-banner   # 查活动横幅
```

## Token 失效处理

accessToken 有效期约 60 天（示例值至 2026-10-10）。失效后接口返回 401，需要重新从本地登录态文件获取新 token 并更新到 GitHub Secrets：
1. 打开 WorkBuddy 客户端确认已登录
2. 读取 `%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info` 中的 `auth.accessToken`
3. 更新仓库 Secret `WB_ACCESS_TOKEN`
