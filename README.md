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

**本地零文件、零定时任务**：每日签到由 GitHub Actions 云端完成，邮件由云端通过 SMTP 直接发送。

仓库需配置以下 Secrets（**token 绝不进代码仓库**）：

| Secret | 说明 |
|---|---|
| `WB_ACCESS_TOKEN` | 账号1 登录 accessToken（从本地 auth 文件获取，**有 WB_REFRESH_TOKEN 时可不更新**） |
| `WB_REFRESH_TOKEN` | 账号1 刷新令牌（从本地 auth 文件获取，用于**自动续期**，强烈建议配置） |
| `WB_USER_ID` | 账号1 用户 uid（可选） |
| `WB_ACCESS_TOKEN_2` | **账号2** 登录 accessToken（多账号签到，可选） |
| `WB_REFRESH_TOKEN_2` | **账号2** 刷新令牌（多账号签到，可选） |
| `WB_USER_ID_2` | **账号2** 用户 uid（可选） |
| `SMTP_HOST` | SMTP 服务器（如 smtp.qq.com） |
| `SMTP_PORT` | SMTP 端口（如 465） |
| `SMTP_USER` | 发件邮箱账号 |
| `SMTP_PASS` | 发件邮箱授权码（非登录密码） |
| `MAIL_TO` | 收件邮箱 |

> 多账号签到：脚本会依次处理账号1（`WB_ACCESS_TOKEN`/本地登录态）和账号2（`WB_ACCESS_TOKEN_2`），
> 每个账号使用独立的 refresh token 缓存（`.refresh_token` / `.refresh_token_2`），互不干扰。
> 邮件主题会带 `[账号N]` 前缀，方便区分是哪个账号的提醒。
> 如需第 3、4 个账号，照此规则添加 `WB_ACCESS_TOKEN_3`、`WB_REFRESH_TOKEN_3` 等即可（脚本自动发现）。

> **自动续期原理**：脚本每天运行时若检测到 `WB_REFRESH_TOKEN`，会调用 `POST /v2/plugin/auth/token/refresh` 换取新的 accessToken（有效期 60 天）和新的 refreshToken（有效期 90 天），并自动持久化——**token 永不过期，无需手动更新**。刷新失败时会自动退回 `WB_ACCESS_TOKEN` 并发出「⚠️ Token 即将过期」提醒邮件。

默认每天 01:30（北京时间）自动运行（参考同账号贴吧/网易云项目实测：私有免费仓库凌晨时段定时触发稳定、仅延迟较大），每次执行：

**每日签到**：成功不发邮件；未激活/失败时通过 SMTP 发邮件说明原因。**不发送任何活动推广类邮件。**

可在 Actions 页面手动触发（workflow_dispatch）。

## 邮件通知规则

**所有邮件由云端（GitHub Actions）通过 SMTP 直接发送，本地不运行任何东西。只发送签到状态相关邮件，不发送活动广告。**

| 当天情况 | 邮件行为 |
|---|---|
| 已成功签到 | 不发邮件 |
| 签到活动未激活（active=false） | 发「ℹ️ 今日活动未激活」说明邮件 |
| 查询状态异常 / 领取异常 | 发「⚠️ 签到失败」告警邮件（含原因） |
| token 即将过期（≤7 天） | 发「⚠️ Token 即将过期」提醒邮件（自动续期失效时才出现） |

## 本地手动运行（可选）

如需临时手动检查，可在本地执行（本地不配置 SMTP 环境变量时不会发邮件，仅打印结果）：

```bash
python workbuddy_checkin.py --check-only     # 只查签到状态
```

## Token 失效处理

**配置了 `WB_REFRESH_TOKEN` 后无需处理**——脚本每天自动续期，token 不会过期。

仅当**自动续期失效**（例如 refresh token 也失效，需重新登录）时：
1. 打开 WorkBuddy 客户端确认已登录（会自动刷新本地会话）
2. 从 `%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info` 读取新的 `auth.refreshToken`
3. 更新仓库 Secret `WB_REFRESH_TOKEN`（和 `WB_ACCESS_TOKEN`）

此时云端会自动检测并发送「⚠️ Token 即将过期」提醒邮件。
