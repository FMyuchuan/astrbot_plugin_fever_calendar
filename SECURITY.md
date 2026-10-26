# 安全说明

这个插件会持有**能直接登录云端账号的凭据**（`passToken` / `serviceToken` / `ssecurity`），
因此下面几条请务必留意。

## 凭据存在哪里

```
data/plugin_data/astrbot_plugin_fever_calendar/
  identities.json              身份 → 账号 的映射（0600）
  users/<账号>/session.json    该账号的登录态（0600）
  users/<账号>/login_qr.png    登录二维码，登录成功后自动删除
```

- 目录默认只有运行 AstrBot 的用户可读；**不要把 `plugin_data` 目录分享出去或提交进 git**
- 拿到 `session.json` 等于拿到你的账号，拿到 `login_qr.png` 等于能扫码登录你的账号
- 插件不需要账号密码，也不保存密码

## 请不要公开这些内容

提 issue、发截图、贴日志时，请先抹掉：

- `session.json` / `identities.json` 的任何内容
- 登录二维码图片或其 URL
- 完整的报错堆栈里带 `Cookie`、`ssecurity`、`token` 的行
- 你的账号数字 ID（如果需要说明问题，可写成 `1234****`）

## 权限建议

- `allowed_users` 留空表示**所有人**都能使用插件（各用各的账号）。只给自己或熟人用时，请填上发送者 ID
- 在群里使用时特别注意：群成员同样能触发 LLM 工具，名单不设就等于对他开放
- 不建议把插件装在公开群里

## 报告安全问题

发现凭据泄露、越权访问等问题，请**不要**直接开公开 issue，
通过仓库主页的私密渠道联系作者，或在 issue 里只描述现象、附上已脱敏的日志。

## 免责

本项目为非官方实现，依赖服务端私有协议，服务方随时可能变更导致功能失效；
使用可能违反服务方用户协议，风险由使用者自行承担。详见 [README](README.md)。
