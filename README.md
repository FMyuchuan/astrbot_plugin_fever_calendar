# fever-calendar

[![CI](https://github.com/FMyuchuan/astrbot_plugin_fever_calendar/actions/workflows/ci.yml/badge.svg)](https://github.com/FMyuchuan/astrbot_plugin_fever_calendar/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-black.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.5.0-black.svg)](CHANGELOG.md)

在聊天里读写云日历 —— 一个 **AstrBot 插件** + 一份**协议实现文档**。

> 非官方项目，与设备厂商无关联、未获授权或背书。

## 它能做什么

某发烧厂的云日历没有公开 API，手机之外无法编辑日程。本项目通过自研实现打通了这条链路：

```
passToken ──► serviceToken + ssecurity
                ├── 签名 = Base64(SHA1("METHOD&path&k=v…&ssecurity"))
                ├── 游标 = AES(ssecurity) 自造 token
                └── 响应 = AES/CBC/PKCS5 解密 → JSON
```

在此之上封装成 AstrBot 插件：

| 能力 | 说明 |
|---|---|
| 多用户 | 每人扫码登录自己的发烧账号，各管各的日历，互相看不到 |
| 跨平台 | 同一个人换平台用 `/calendar bind` 挂到同一个账号，不必重新登录 |
| 查看 | 今天 / 明天 / 本周 / 关键词搜索 |
| 新建 | 提醒、闹钟、倒计时、重复规则 |
| 修改 | 支持「整个系列」与「仅这一次」 |
| 删除 | 二次确认；同样支持单次实例 |
| 导出 | 标准 iCalendar（含 `RECURRENCE-ID`） |
| 自然语言 | 5 个 LLM 工具，“明天三点提醒我交作业” |

## 安装

插件文件就在仓库根目录，可直接作为插件目录使用：

```bash
cd AstrBot/data/plugins
git clone https://github.com/FMyuchuan/astrbot_plugin_fever_calendar.git
# 安装依赖：httpx、pycryptodome、segno
```

重启 AstrBot，然后在聊天里发 `/calendar login` 扫码登录——每个人各自登录自己的账号，
登录态按人保存在 `data/plugin_data/` 里，不需要往配置里填任何令牌。

## 常用指令

```
/calendar                          帮助
/calendar login                    扫码登录（每个用户各登各的）
/calendar today | tomorrow | week  看日程
/calendar add 例会 明天 15:00 --remind 10 --repeat weekly
/calendar delete 2                 删除（会先要确认码）
```

### 多用户与跨平台

- 默认按「平台 + 发送者 ID」划分账号：每个人只看到自己登录的那本日历
- 想在 QQ 和 Telegram 上用同一个账号：在 A 平台发 `/calendar bind` 拿到短码，
  到 B 平台发 `/calendar bind <短码>`，两边就指向同一份登录态（10 分钟内有效）
- 不需要「共享日历」：共享日历解决的是多人看同一本，这里只是把「你」的两个身份并起来

完整用法见 [README.plugin.md](README.plugin.md)，
指令与工具的完整定义见 [docs/05-指令与工具设计.md](docs/05-指令与工具设计.md)。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/01-协议说明.md](docs/01-协议说明.md) | 登录、签名、AES、游标、记录字段、错误码 |
| [docs/02-侦察过程.md](docs/02-侦察过程.md) | 从 0 摸清协议的过程记录 |
| [docs/03-项目结构与路线图.md](docs/03-项目结构与路线图.md) | 代码地图与后续计划 |
| [docs/04-AstrBot插件方案.md](docs/04-AstrBot插件方案.md) | 插件整体设计 |
| [docs/05-指令与工具设计.md](docs/05-指令与工具设计.md) | 指令与 LLM 工具定义 |
| [CHANGELOG.md](CHANGELOG.md) | 版本变更记录 |
| [SECURITY.md](SECURITY.md) | 凭据存放、权限建议、如何报告安全问题 |

协议文档只描述行为与数据格式，不包含任何厂商代码、SDK 或反编译产物。

## 边界与免责

- 仅用于操作**使用者本人**的账号数据；不提供批量导出、多账号管理、验证码绕过等能力
- 依赖客户端私有协议实现，**服务方随时可能变更，届时功能会失效**
- 使用可能违反服务方用户协议，**风险由使用者自行承担**
- 文中出现的产品与服务名称仅用于描述互操作，商标归各自所有者
- 本项目不含任何官方 SDK、APK 或反编译产物

## 许可证

[MIT](LICENSE)
