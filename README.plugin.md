# 发烧云日历 · AstrBot 插件

在聊天里管理发烧云日历：查看、新建、修改、删除，支持重复事件、单次实例、闹钟/倒计时提醒。

底层协议是自研逆向的（没有官方 API），实现细节见兄弟项目 `mi-calendar/docs/`。

## 安装

1. 把本目录放进 `AstrBot/data/plugins/`
2. 在 WebUI 插件页安装依赖（`requirements.txt`：httpx、pycryptodome、segno）
3. 在聊天里发 `/calendar login`，用发烧手机扫码

不需要账号密码，也不需要往配置里填令牌：扫码拿到的凭据只落在该用户自己的
`users/<账号>/session.json` 里，之后由插件静默续期。

## 指令

| 指令 | 作用 |
|---|---|
| `/calendar help` | 用法速查 |
| `/calendar login` / `logout` | 扫码登录自己的账号 / 清除本机登录态 |
| `/calendar bind` | 生成短码，在别的平台输入即可共用同一个账号 |
| `/calendar today` `/calendar tomorrow` `/calendar week` | 列日程 |
| `/calendar search <关键词>` | 标题搜索 |
| `/calendar show <序号>` | 单条详情 |
| `/calendar add <标题> <时间> [选项]` | 新建 |
| `/calendar update <序号> [选项]` | 修改 |
| `/calendar delete <序号>` → `/calendar confirm <token>` | 删除（二次确认） |
| `/calendar export` | 导出 ics |
| `/calendar status` | 账号、记录数、已绑定的平台 |

### 添加示例

```
/calendar add 小组会议 明天 14:00-15:00 --remind 10 --alarm on
/calendar add 抢课 后天 09:00 --remind 30 --countdown on --alarm on
/calendar add 例会 周五 19:00 --repeat weekly
```

选项：

- `--remind N`：提前 N 分钟
- `--alarm on|off`、`--countdown on|off`：两个独立开关，可同时开
- `--repeat daily|weekly|monthly` 或标准 `FREQ=...`
- `--title X`（改的时候用）
- `--this-only`（重复事件只改/删这一次）

## 结构

```
main.py            指令入口（Star 插件）
core/
  accounts.py      多用户：身份绑定、每账号独立登录态与缓存
  crypto.py        签名 + AES 游标
  session.py       登录/续期/令牌落盘
  pdc.py           记录读写（异步）
  service.py       业务层：重复展开、范围筛选、增删改
  ics.py           导出 RFC 5545
selftest.py        脱离 AstrBot 的自测（要登录态）
scripts/rebrand.py 统一替换厂商隐晦词（python scripts/rebrand.py <新词>）
tests/smoke.py     离线冒烟测试（账号隔离 + 重复规则 + 导出），CI 会跑
.github/workflows  推 main / PR 时自动跑冒烟测试
```

### 数据目录

```
data/plugin_data/astrbot_plugin_fever_calendar/
  identities.json          身份 → 账号 的映射（0600）
  users/<账号>/session.json 各自的登录态（0600）
  users/<账号>/login_qr.png 登录二维码，登录成功后自动删除
  users/<账号>/calendar_export.ics 导出的日历
```

一个平台身份一个账号；用 `/calendar bind` 可以把多个平台并到同一个账号。

## 自测

```bash
python tests/smoke.py       # 离线，不需要账号
python selftest.py          # 只读：登录 + 今天/明天/本周 + 导出
python selftest.py --write  # 额外验证 建→改→删
```

## 自然语言（LLM 工具）

插件注册了 5 个工具，模型支持 function calling 时可直接对话：

| 你说 | 调用 |
|---|---|
| “我今天有什么安排” | `list_calendar_events` |
| “明天三点提醒我交作业，提前十分钟” | `create_calendar_event` |
| “把交作业的提醒改成半小时” | `update_calendar_event` |
| “把交作业删了” | `delete_calendar_event` → 返回确认码，需再回 `/calendar confirm <token>` |
| “日历能用吗”“登录了吗” | `calendar_status` |

工具返回的是结构化文本（`OK …` / `NOT_FOUND …` / `AMBIGUOUS …` / `NEEDS_CONFIRM …`），由模型转成自然语言回复。

## 已知限制

- 首次登录：聊天里发 `/calendar login` 扫码，每人一份，互不影响
- 单次实例的「改/删」依赖例外记录（`originalServerId`），已在手机端验证
- `/calendar sync`、`/calendar nextmonth` 还只是设计稿，未实现
- 导出 ics 不含 `ATTENDEE`/`ORGANIZER`（发烧侧是数字账号，转 mailto 会失真）

## 部署到服务器（实测流程）

```bash
# 1. 打包（排除 __pycache__ 与 data/）
tar -czf plugin.tar.gz --exclude=__pycache__ --exclude=data astrbot_plugin_fever_calendar

# 2. 上传解压到数据卷挂载的 plugins 目录
scp plugin.tar.gz HOST:/tmp/
ssh HOST "tar -xzf /tmp/plugin.tar.gz -C /path/to/AstrBot/data/plugins"

# 3. 重启容器（AstrBot 只在启动时加载插件）
podman restart astrbot   # 或 docker restart
```

然后在聊天里发 `/calendar login` 扫码即可，不需要配置任何令牌。

### 三个容易踩的点

1. **只有 passToken 登不上**：`serviceLogin` 必须带 `userId` / `cUserId` cookie，缺了会返回 `code=70016`。扫码登录会把三者一起拿到，手工填令牌才需要凑齐。
2. **`ready` 不能用 ssecurity 判断**：ssecurity 是登录后才有的，用它判断会形成「未登录→不登录→永远未登录」的死循环。
3. **AES key 要现算**：`ssecurity` 为空时算出来的 key 是空的，PdcClient 每次用到时再取。

### 排查命令

```bash
podman logs -f astrbot | grep -i fever       # 插件日志
podman exec -it astrbot ls /AstrBot/data/plugins   # 确认插件在容器里
```
