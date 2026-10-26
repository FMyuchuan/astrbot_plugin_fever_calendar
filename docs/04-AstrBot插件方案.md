# 封装成 AstrBot 插件：方案设计

> 依据：AstrBot 官方插件文档（[最小实例](https://docs.astrbot.app/dev/star/guides/simple.html)、[插件配置](https://docs.astrbot.app/dev/star/guides/plugin-config.html)、[调用 AI / Tool](https://docs.astrbot.app/dev/star/guides/ai.html)、[存储](https://docs.astrbot.app/dev/star/guides/storage.html)、[插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)）与社区同类插件（如 [astrbot_plugin_zectrix](https://github.com/zhangkai542322/astrbot_plugin_zectrix) 待办类插件）。

---

## 一、同类插件的架构共性

调研下来，AstrBot 插件基本都遵循这套骨架：

```
astrbot_plugin_xxx/
├── metadata.yaml          # name / display_name / desc / version / author
├── _conf_schema.json      # 配置项 Schema，WebUI 自动生成配置面板
├── requirements.txt       # 额外依赖（pip 安装）
├── main.py                # 必须叫 main.py，内含继承 Star 的插件类
└── README.md
```

关键约定：

| 约定 | 说明 |
|---|---|
| 插件类 | `class XxxPlugin(Star)`，`__init__(self, context: Context, config: AstrBotConfig)` |
| 指令 | `@filter.command("名字")`，handler 为 `async def f(self, event: AstrMessageEvent)` |
| 回复 | `yield event.plain_result("...")`，图片用 `event.image_result(...)` |
| 给 LLM 用的工具 | `@filter.llm_tool(name="xxx")`，**参数从 docstring 的 `Args:` 解析**，类型写在括号里：`arg(string): 描述` |
| 配置 | `_conf_schema.json` 定义，AstrBot 落到 `data/config/<plugin>_config.json` |
| 持久化 | 小数据用 `self.get_kv_data/put_kv_data`；大文件放 `data/plugin_data/<plugin>/` |
| 网络 | **官方明确要求不要用 `requests`**，用 `aiohttp` / `httpx` 等异步库 |
| 生命周期 | `async def terminate(self)` 做清理（停后台任务、关 client） |

社区同类插件（待办类）额外做了两件事，值得借鉴：

1. **自然语言入口**：注册 `llm_tool`，让用户说"明天下午三点提醒我开会"就能建日程；
2. **配置化凭据**：API Key 放 `_conf_schema.json` 的 `secret: true` 字段，而不是写死在代码里。

---

## 二、我们的特殊性

和普通插件最大的不同是**鉴权**：发烧云没有 API Key，只有 `passToken`/`serviceToken`/`ssecurity` 这套要自己维护的会话。

因此插件必须包含：

1. 首次登录（扫码，产出 `passToken`）
2. 静默续期（`passToken` → `serviceToken`，我们已验证可用）
3. 令牌持久化（存 `data/plugin_data/<plugin>/session.json`，**不进配置文件**，避免被 WebUI 明文展示）
4. 过期兜底（`401` 时提示重新扫码，而不是崩掉）

另外现有代码是**同步 `requests` + 本地文件**，移植时需要改成 `httpx.AsyncClient` + 异步文件 IO，并把 AES 换成 `cryptography` 或 `pycryptodome`（写进 requirements）。

---

## 三、插件结构

```
astrbot_plugin_fever_calendar/
├── metadata.yaml
├── _conf_schema.json
├── requirements.txt          # httpx, pycryptodome
├── main.py                   # Star 入口：指令 + llm_tool + 后台同步任务
├── core/
│   ├── crypto.py             # 签名 Base64(SHA1(...)) / AES-CBC token 加解密
│   ├── session.py            # 登录、续期、令牌落盘（async）
│   ├── pdc.py                # records 增删改查 + limit/syncToken 游标
│   ├── ics.py                # 导出 .ics
│   └── store.py              # 本地缓存 records.json / sync_state.json
└── README.md
```

`core/` 直接由现在的 `mi_calendar_api.py` / `mi_pull.py` / `mi_write.py` 改写而来，逻辑不变，只换 IO 层。

---

## 四、用户可见能力

### 4.1 指令

| 指令 | 作用 |
|---|---|
| `/calendar login` | 发二维码，手机扫码后自动落盘令牌 |
| `/calendar today`、`/calendar tomorrow`、`/calendar week` | 列日程（标题/时间/提醒） |
| `/calendar search <关键词>` | 标题搜索 |
| `/calendar add <标题> <时间>` | 新建，可选 `remind=15`、`--alarm on/off`、`--countdown on/off`、`--repeat weekly` |
| `/calendar update <序号> remind=15` | 改提醒分钟数 / 闹钟开关 / 倒计时开关 / 重复规则 / 时间 / 标题 |
| `/calendar delete <序号>` | 删除（二次确认） |
| `/calendar export` | 回一个 `.ics` 文件 |
| `/calendar status` | 令牌有效期、上次同步时间、记录数 |

序号来自上一条列表，缓存在会话 KV 里，避免让用户记 id。

### 4.2 LLM 工具（自然语言）

```python
@filter.llm_tool(name="create_calendar_event")
async def create_calendar_event(self, event, title: str, start: str, end: str = "",
                                remind_minutes: int = 15, alarm: bool = False, countdown: bool = False,
                                recurrence: str = ""):
    """创建发烧云日程。

    Args:
        title(string): 日程标题
        start(string): 开始时间，ISO 格式或"明天 15:00"
        end(string): 结束时间，留空则默认 1 小时
        remind_minutes(number): 提前提醒分钟数
        alarm(boolean): 是否开启闹钟提醒
        countdown(boolean): 是否开启倒计时提醒
        recurrence(string): 重复规则，如"每周五"或 RRULE
    """
```

同理提供 `list_calendar_events`、`update_calendar_event`、`delete_calendar_event`。
这样用户在 QQ 里说"明天三点提醒我交作业，提前十分钟"就能直接落库。

### 4.3 主动推送（可选）

后台任务每 N 分钟增量同步一次；每天固定时间推送"今日日程"。
用 `asyncio.create_task` 起循环，`terminate()` 里 `cancel()`。

---

## 五、配置 Schema（草案）

```json
{
  # 多用户版不再需要手填令牌：各用户扫码后只落在自己的 users/<账号>/session.json
  "allowed_users":   {"type": "list", "default": [], "hint": "允许操作日程的用户 ID（QQ 号），空表示不限制"},
  "default_reminder":{"type": "int", "default": 15, "hint": "新建日程默认提前提醒分钟数"},
  "default_alarm":   {"type": "bool", "default": false, "hint": "新建日程默认开启闹钟提醒"},
  "default_countdown": {"type": "bool", "default": false, "hint": "新建日程默认开启倒计时提醒"},
  "timezone":        {"type": "string", "default": "Asia/Shanghai"},
  "sync_interval":   {"type": "int", "default": 10, "hint": "后台增量同步间隔（分钟），0 关闭"},
  "daily_digest":    {"type": "bool", "default": false, "hint": "每天推送今日日程"},
  "digest_time":     {"type": "string", "default": "08:00"}
}
```

---

## 六、关键实现点

1. **令牌隔离**：每个账号一份 `users/<账号>/session.json`，权限 600；配置里不放任何令牌。
2. **破坏性操作二次确认**：`/calendar delete` 回一条确认按钮/文本，带 60 秒有效的确认 token，防止误删。
3. **错误分级**：
   - `401` → 令牌失效，提示 `/calendar login`
   - `10008/10016` → 参数问题，回显服务端 reason
   - 网络超时 → 重试 2 次后报错，不阻塞消息循环
4. **并发**：同一账号的写操作加 `asyncio.Lock`，避免 `eTag` 竞争。
5. **缓存**：列表查询走本地 `records.json`，不每次打云端；写操作后立即刷新对应记录。
6. **隐私**：日志里不打印 token、不打印日程详情（只打 id 与操作类型）。

---

## 七、里程碑

| 阶段 | 内容 | 预估 |
|---|---|---|
| P0 | 同步代码移植到 `httpx` + `cryptography`，跑通 login/pull/write | 0.5 天 |
| P1 | 插件骨架 + `metadata.yaml` + `_conf_schema.json` + `/calendar login/today` | 0.5 天 |
| P2 | 增删改指令 + 二次确认 + 本地缓存 | 1 天 |
| P3 | `llm_tool` 自然语言入口 | 0.5 天 |
| P4 | ics 导出 + 后台同步 + 每日简报 | 0.5 天 |
| P5 | 文档、i18n、单测、发布到插件市场 | 1 天 |

---

## 八、待确认

1. **部署环境**：AstrBot 版本（`llm_tool` 装饰器需 ≥ 4.5.1，KV 存储需 ≥ 4.9.2）？
2. **接入平台**：QQ 个人号 / 官方机器人 / 飞书？影响图片（二维码）发送方式。
3. **白名单**：是否需要限制只有你能用？还是群里任何人都能查日程（只读）？
4. **推送**：要不要"每天早八点播报今日日程"？如果要，推到哪个会话？
5. **多日历**：目前账号只有一个云端日历，要不要预留多日历选择？
