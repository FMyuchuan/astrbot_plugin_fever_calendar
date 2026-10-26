# -*- coding: utf-8 -*-
"""发烧云日历 AstrBot 插件（多用户版）。

每个聊天用户各自登录自己的发烧账号、各自读写自己的日历，互不可见。
能力：查看 / 新建 / 修改 / 删除发烧云日程，支持重复事件与单次实例、闹钟与倒计时提醒。
底层协议见项目文档 docs/01-协议说明.md。
"""
import re
from datetime import datetime, timedelta
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .core.accounts import AccountStore, IdentityBook, UserContext
from .core.ics import export as export_ics
from .core.pdc import ApiError
from .core.service import TZ
from .core.session import AuthError

PLUGIN_DIR = Path(__file__).resolve().parent


def _data_dir() -> Path:
    """AstrBot 规范：持久化数据放 data/plugin_data/<插件名>/，插件更新不会丢。"""
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path
        base = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_fever_calendar"
    except Exception:
        base = PLUGIN_DIR / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base


DATA_DIR = _data_dir()

LOGIN_HINT = "发 /calendar login 用发烧手机扫码"


class CalendarPlugin(Star):
    # 斜杠指令统一英文，不保留中文别名
    _SUBS = {"help", "login", "logout", "bind", "status", "today", "tomorrow", "week",
             "search", "show", "add", "update", "delete", "confirm", "export"}

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.book = IdentityBook(DATA_DIR / "identities.json")
        self.store = AccountStore(DATA_DIR)

    # ---------- 用户与权限 ----------
    def _uid(self, event: AstrMessageEvent) -> str:
        return str(event.get_sender_id())

    def _identity(self, event: AstrMessageEvent) -> str:
        """平台 + 发送者 ID：同一个人在不同平台是两个身份，靠 /calendar bind 合并。"""
        try:
            platform = event.get_platform_name() or event.get_platform_id()
        except Exception:
            platform = ""
        return IdentityBook.identity_key(platform, self._uid(event))

    def _ctx(self, event: AstrMessageEvent) -> UserContext:
        """取（或建）这个身份所属账号的上下文。"""
        return self.store.get(self.book.resolve(self._identity(event)))

    def _allowed(self, event: AstrMessageEvent) -> bool:
        """谁能使用本插件；留空表示所有人都能各用各的。只认发送者 ID，不认昵称。"""
        allow = [str(x).strip() for x in (self.config.get("allowed_users") or [])]
        if not allow:
            return True
        return self._uid(event) in allow

    async def _check(self, ctx: UserContext):
        """返回错误提示文本；None 表示可以继续。"""
        if not ctx.logged_in:
            return f"你还没登录。{LOGIN_HINT}。"
        try:
            await ctx.svc.refresh()
        except AuthError as e:
            return f"登录失效（{e}），重新{LOGIN_HINT}。"
        except Exception as e:
            logger.warning(f"fever calendar refresh failed (uid={ctx.uid}): {e}")
            return f"拉取日程失败：{type(e).__name__}: {e}"
        return None

    # ---------- 指令 ----------
    @filter.command("calendar")
    async def calendar_cmd(self, event: AstrMessageEvent):
        """发烧云日历：查看 / 添加 / 修改 / 删除日程"""
        raw = (event.message_str or "").strip()
        args = raw.split()[1:] if len(raw.split()) > 1 else []
        sub = args[0].lower() if args else "help"

        if sub == "help" or sub not in self._SUBS:
            yield event.plain_result(
                "📅 发烧云日历（每人一本，各登各的）\n"
                "/calendar login ｜ 扫码登录你自己的发烧账号\n"
                "/calendar logout ｜ 退出并清除本机登录态\n"
                "/calendar bind ｜ 生成短码，在另一个平台发 /calendar bind <短码> 就能共用同一个账号\n"
                "/calendar today | tomorrow | week\n"
                "/calendar search <关键词>\n"
                "/calendar show <序号>\n"
                "/calendar add <标题> <时间> [--remind 15] [--alarm on] [--countdown on] [--repeat weekly]\n"
                "/calendar update <序号> [--remind 15] [--alarm on] [--countdown on] [--title X] [--this-only]\n"
                "/calendar delete <序号> [--this-only]\n"
                "/calendar confirm <token>\n"
                "/calendar export\n"
                "/calendar status")
            return

        if not self._allowed(event):
            yield event.plain_result("你不在这个插件的使用名单里，找主人开通一下～")
            return

        ctx = self._ctx(event)

        if sub == "login":
            async for msg in self._cmd_login(ctx, event):
                yield msg
            return

        if sub == "logout":
            async for msg in self._cmd_logout(ctx, event):
                yield msg
            return

        if sub == "bind":
            async for msg in self._cmd_bind(ctx, event, args[1:]):
                yield msg
            return

        if sub == "status":
            if not ctx.logged_in:
                yield event.plain_result(f"你还没登录。{LOGIN_HINT}。")
                return
            err = await self._check(ctx)
            if err:
                yield event.plain_result(err)
                return
            identities = self.book.identities_of(ctx.uid)
            yield event.plain_result(
                f"账号：{ctx.session.user_id}\n"
                f"记录：{len(ctx.svc.records)} 条\n"
                f"日历：{len(ctx.svc.calendars)} 个\n"
                f"绑定身份：{'、'.join(identities) if identities else '仅当前平台'}")
            return

        err = await self._check(ctx)
        if err:
            yield event.plain_result(err)
            return

        try:
            if sub in ("today", "tomorrow", "week"):
                off = {"today": 0, "tomorrow": 1}.get(sub, 0)
                evs = ctx.svc.week() if sub == "week" else ctx.svc.day(off)
                ctx.cache(event.unified_msg_origin, evs)
                label = {"today": "今天", "tomorrow": "明天", "week": "本周"}[sub]
                yield event.plain_result(self._render(evs, label, with_date=(sub == "week")))
            elif sub == "search":
                kw = " ".join(args[1:]).strip()
                if not kw:
                    yield event.plain_result("用法：/calendar search <关键词>")
                    return
                evs = ctx.svc.search(kw)
                ctx.cache(event.unified_msg_origin, evs)
                yield event.plain_result(self._render(evs, f"搜索「{kw}」"))
            elif sub == "show":
                ev = ctx.pick(event.unified_msg_origin, args[1] if len(args) > 1 else "")
                if not ev:
                    yield event.plain_result("序号无效或列表已过期，请重新查询。")
                    return
                yield event.plain_result(self._detail(ev))
            elif sub == "add":
                async for msg in self._cmd_add(ctx, event, args[1:]):
                    yield msg
            elif sub == "update":
                async for msg in self._cmd_update(ctx, event, args[1:]):
                    yield msg
            elif sub == "delete":
                async for msg in self._cmd_delete(ctx, event, args[1:]):
                    yield msg
            elif sub == "confirm":
                async for msg in self._cmd_confirm(ctx, event, args[1:]):
                    yield msg
            elif sub == "export":
                text = export_ics(ctx.svc.records)
                out = ctx.path / "calendar_export.ics"
                out.write_text(text, encoding="utf-8")
                try:
                    out.chmod(0o600)
                except Exception:
                    pass
                yield event.plain_result(f"已导出 {len(ctx.svc.records)} 条记录：{out}")
            else:
                yield event.plain_result("不认识的子命令，发送 /calendar help 查看用法。")
        except ApiError as e:
            logger.warning(f"fever calendar api error (uid={ctx.uid}): {e}")
            yield event.plain_result(f"接口报错：{e.reason}（code={e.code}）")
        except Exception as e:
            logger.exception(f"fever calendar failed (uid={ctx.uid})")
            yield event.plain_result(f"出错了：{type(e).__name__}")

    # ---------- 子命令实现 ----------
    async def _cmd_login(self, ctx: UserContext, event):
        try:
            qr, lp, timeout, interval = await ctx.session.qr_start()
        except Exception as e:
            yield event.plain_result(f"申请二维码失败：{e}")
            return

        png = ctx.path / "login_qr.png"
        try:
            # 服务端返回的 qr 字段是图片地址，直接用它渲染好的二维码，扫一次即可
            png.write_bytes(await ctx.session.qr_image(qr))
        except Exception as e:
            try:
                import segno
                segno.make(qr, error="m").save(str(png), scale=10, border=3)
            except Exception:
                yield event.plain_result(f"生成二维码失败：{e}\n二维码地址：{qr}")
                return
        try:
            # 二维码等同于登录凭据，别让同机其它进程读到
            png.chmod(0o600)
        except Exception:
            pass

        yield event.plain_result("请用发烧手机扫码并在手机上点「确认登录」"
                                 "（相机 / 发烧账号扫一扫都行），2 分钟内有效。")
        try:
            yield event.image_result(str(png))
        except Exception:
            yield event.plain_result(f"二维码图片：{png}")

        ok = await ctx.session.qr_poll(lp, timeout=min(120, timeout), interval=interval)
        if not ok:
            yield event.plain_result("二维码已过期，重新发送 /calendar login 即可。")
            return
        ctx.session.save()
        try:
            png.unlink()
        except Exception:
            pass
        try:
            await ctx.svc.refresh()
        except Exception:
            pass
        yield event.plain_result(f"✅ 登录成功，账号 {ctx.session.user_id}，"
                                 f"已同步 {len(ctx.svc.records)} 条记录。"
                                 f"这本日历只属于你，别人看不到。")

    async def _cmd_logout(self, ctx: UserContext, event):
        if not ctx.logged_in:
            yield event.plain_result("你本来就没登录呀。")
            return
        uid = ctx.session.user_id
        async with ctx.lock:
            ctx.reset_session()
        yield event.plain_result(
            f"👋 已退出账号 {uid}，本机保存的登录态已清除，重新 /calendar login 可以换账号。")

    async def _cmd_bind(self, ctx: UserContext, event, args):
        """跨平台身份合并：A 平台出短码，B 平台输短码，两边指到同一个账号。"""
        identity = self._identity(event)
        if not args:
            code = self.book.issue_code(ctx.uid)
            yield event.plain_result(
                f"🔗 绑定短码：{code}（10 分钟内有效）\n"
                f"在另一个平台发 /calendar bind {code}，那边就会共用这个账号。\n"
                f"当前身份：{identity}")
            return
        account = self.book.redeem_code(identity, args[0])
        if not account:
            yield event.plain_result("短码不对或已过期，重新发 /calendar bind 生成一个。")
            return
        target = self.store.get(account)
        if not target.logged_in:
            yield event.plain_result("✅ 绑定好了，但那个账号还没登录。在这里发 /calendar login 扫码就行。")
            return
        try:
            await target.svc.refresh()
        except Exception:
            pass
        yield event.plain_result(
            f"✅ 已绑定到账号 {target.session.user_id or account}，"
            f"现在两个平台看到的是同一份日历（{len(target.svc.records)} 条记录）。")

    async def _cmd_add(self, ctx: UserContext, event, args):
        if len(args) < 2:
            yield event.plain_result(
                "用法：/calendar add <标题> <时间> [--remind 15] [--alarm on] [--countdown on]")
            return
        title = args[0]
        rest = " ".join(args[1:])
        opts = self._opts(rest)
        when = opts["_time"]
        try:
            start, end = self._parse_time(when)
        except Exception:
            yield event.plain_result("时间没看懂，试试 `2026-10-24 20:00` 或 `明天 15:00`。")
            return

        remind = self._num(opts.get("remind", self.config.get("default_reminder", 15)), 15)
        alarm = self._flag(opts.get("alarm"), default=(
            "alarm" not in opts and self.config.get("default_alarm", False)))
        countdown = self._flag(opts.get("countdown"), default=(
            "countdown" not in opts and self.config.get("default_countdown", False)))
        rrule = self._rrule(opts.get("repeat", ""))

        async with ctx.lock:
            ev = await ctx.svc.create(title, start, end, remind=remind, alarm=alarm,
                                      countdown=countdown, rrule=rrule)
        yield event.plain_result(
            f"✅ 已创建\n{ev.title}｜{ev.start.strftime('%m-%d %H:%M')}–{ev.end.strftime('%H:%M')}"
            f"｜{ev.mark()} {ev.remind_text()}")

    async def _cmd_update(self, ctx: UserContext, event, args):
        if not args:
            yield event.plain_result(
                "用法：/calendar update <序号> [--remind 15] [--alarm on] [--countdown on] [--title X]")
            return
        ev = ctx.pick(event.unified_msg_origin, args[0])
        if not ev:
            yield event.plain_result("序号无效或列表已过期。")
            return
        opts = self._opts(" ".join(args[1:]))
        only_this = "this-only" in opts
        changes = {}
        if "remind" in opts:
            changes["remind"] = self._num(opts["remind"], 15)
        if "alarm" in opts:
            changes["alarm"] = self._flag(opts["alarm"])
        if "countdown" in opts:
            changes["countdown"] = self._flag(opts["countdown"])
        if "title" in opts:
            changes["title"] = opts["title"]
        if not changes:
            yield event.plain_result("没给要改的字段。")
            return
        if only_this and not ev.occurrence_of:
            yield event.plain_result("这条不是重复事件的实例，没法「只改这次」。")
            return
        async with ctx.lock:
            new = (await ctx.svc.update_instance(ev, **changes)) if only_this \
                else (await ctx.svc.update(ev, **changes))
        yield event.plain_result(
            f"✅ 已修改{ '（仅这一次）' if only_this else ''}：{new.title}｜{new.mark()} {new.remind_text()}")

    async def _cmd_delete(self, ctx: UserContext, event, args):
        ev = ctx.pick(event.unified_msg_origin, args[0] if args else "")
        if not ev:
            yield event.plain_result("序号无效或列表已过期。")
            return
        only_this = any(a.startswith("--") and a[2:].lower() == "this-only"
                        for a in args[1:])
        if only_this and not ev.occurrence_of:
            yield event.plain_result("这条不是重复事件的实例，没法「只删这次」。")
            return
        token = ctx.add_confirm(ev, "instance" if only_this else "series")
        scope = "这一次" if only_this else "整条日程"
        yield event.plain_result(
            f"⚠️ 确认删除「{ev.title}」（{ev.start.strftime('%m-%d %H:%M')}，{scope}）？\n"
            f"回复 /calendar confirm {token} 执行，60 秒内有效。")

    async def _cmd_confirm(self, ctx: UserContext, event, args):
        token = args[0] if args else ""
        item = ctx.take_confirm(token)
        if not item:
            yield event.plain_result("确认码无效或已过期。")
            return
        ev, mode = item
        async with ctx.lock:
            if mode == "instance":
                await ctx.svc.delete_instance(ev)
            else:
                await ctx.svc.delete(ev)
        yield event.plain_result(f"🗑 已删除「{ev.title}」{'（仅这一次）' if mode == 'instance' else ''}")

    # ---------- 解析辅助 ----------
    _TRUE = ("on", "true", "1", "yes", "y", "开", "是")

    @classmethod
    def _flag(cls, value, default: bool = False) -> bool:
        if value is None:
            return bool(default)
        s = str(value).strip().lower()
        if not s:
            return bool(default)
        return s in cls._TRUE

    @staticmethod
    def _num(value, default: int = 15) -> int:
        s = re.sub(r"[^\d]", "", str(value or ""))
        return int(s) if s else default

    @classmethod
    def _opts(cls, text: str) -> dict:
        out, cur, buf = {}, "_time", []
        for tok in text.split():
            if tok.startswith("--"):
                if cur:
                    out[cur] = " ".join(buf).strip()
                cur, buf = tok[2:].lower(), []
            else:
                buf.append(tok)
        if cur:
            out[cur] = " ".join(buf).strip()
        return out

    # ---- 时间解析 ----
    # 支持：今天/明天/后天/大后天、N天后、周几/下周几、2026-10-29、10月29日、
    #      时段（15:00-17:30 / 15:00到17:30 / 15:00~17:30）、下午3点、9点半、中文数字
    _CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
               "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    _WD = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}

    @staticmethod
    def _cnnum(s: str) -> int:
        s = (s or "").strip()
        if not s:
            raise ValueError(s)
        if s.isdigit():
            return int(s)
        d = CalendarPlugin._CN_NUM
        if s == "十":
            return 10
        if s.startswith("十"):
            return 10 + d.get(s[1], 0)
        if s.endswith("十"):
            return d.get(s[0], 0) * 10
        if "十" in s:
            a, b = s.split("十", 1)
            return d.get(a, 0) * 10 + d.get(b, 0)
        if len(s) == 1 and s in d:
            return d[s]
        raise ValueError(s)

    @staticmethod
    def _split_date(text: str, now: datetime):
        """剥离日期部分，返回 (基准日期或 None, 剩余文本)。None 表示文本里没写日期。"""
        t = (text or "").strip()
        for kw, delta, hint in (("明早", 1, "早上"), ("明晚", 1, "晚上"),
                                ("今早", 0, "早上"), ("今晚", 0, "晚上")):
            if t.startswith(kw):
                return (now + timedelta(days=delta)), hint + t[len(kw):].strip()
        for kw, delta in (("大后天", 3), ("大前天", -3), ("今天", 0), ("今日", 0),
                          ("明天", 1), ("明日", 1), ("后天", 2),
                          ("昨天", -1), ("昨日", -1)):
            if t.startswith(kw):
                return (now + timedelta(days=delta)), t[len(kw):].strip()
        m = re.match(r"(\d+)\s*(?:个)?\s*(天|日|周|星期)\s*(?:之)?后", t)
        if m:
            n = int(m.group(1))
            unit = m.group(2)
            return (now + timedelta(days=n * 7 if unit in ("周", "星期") else n)), t[m.end():].strip()
        m = re.match(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*[日号]?", t)
        if m:
            try:
                return now.replace(year=int(m.group(1)), month=int(m.group(2)),
                                   day=int(m.group(3))), t[m.end():].strip()
            except ValueError:
                pass
        m = re.match(r"(\d{1,2})\s*[月/-]\s*(\d{1,2})\s*[日号]?(?!\d)", t)
        if m and ":" not in m.group(0):
            mo, d = int(m.group(1)), int(m.group(2))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                try:
                    base = now.replace(month=mo, day=d)
                except ValueError:
                    return None, t
                if (now - base).days > 180:
                    base = base.replace(year=now.year + 1)
                return base, t[m.end():].strip()
        m = re.match(r"(下{1,2}周|本?周|下{1,2}个?星期|本?星期|礼拜)([一二三四五六日天1-7])", t)
        if m:
            prefix, wd = m.group(1), m.group(2)
            idx = (int(wd) - 1) if wd.isdigit() else CalendarPlugin._WD[wd]
            monday = now - timedelta(days=now.weekday())
            if prefix.startswith("下"):
                weeks = 2 if prefix.startswith("下下") else 1
                base = monday + timedelta(days=7 * weeks + idx)
            else:
                base = monday + timedelta(days=idx)
                if not prefix.startswith("本") and base.date() < now.date():
                    base = base + timedelta(days=7)
            return base, t[m.end():].strip()
        return None, t

    @staticmethod
    def _parse_time(text: str):
        now = datetime.now(TZ)
        raw = (text or "").strip()
        base, rest = CalendarPlugin._split_date(raw, now)
        if base is None:
            base = now
        rest = (rest or "").strip()
        if not rest:
            raise ValueError(raw)
        left, right = rest, None
        for sep in ("到", "至", "~", "—", "-"):
            if sep in rest:
                left, right = rest.split(sep, 1)
                break
        s = CalendarPlugin._at(base, left)
        if right and right.strip():
            base2, rest2 = CalendarPlugin._split_date(right.strip(), now)
            if base2 is None:
                base2 = base
            rest2 = (rest2 or "").strip()
            e = CalendarPlugin._at(base2, rest2) if rest2 else s + timedelta(hours=1)
        else:
            e = s + timedelta(hours=1)
        if e <= s:
            e = s + timedelta(hours=1)
        return s, e

    @staticmethod
    def _at(base: datetime, hhmm: str):
        s = (hhmm or "").strip().replace("：", ":").replace("时", "点")
        s = s.replace("点", ":").replace("分", "").strip(": ")
        if not s:
            raise ValueError(hhmm)
        ampm = None
        for kw, v in (("凌晨", "am"), ("清晨", "am"), ("早上", "am"), ("上午", "am"),
                      ("中午", "pm"), ("下午", "pm"), ("傍晚", "pm"),
                      ("晚上", "pm"), ("晚", "pm")):
            if s.startswith(kw):
                ampm, s = v, s[len(kw):].strip(": ")
                break
        minute = 0
        if "半" in s:
            s = s.replace("半", "")
            minute = 30
        elif "三刻" in s:
            s = s.replace("三刻", "")
            minute = 45
        elif "一刻" in s:
            s = s.replace("一刻", "")
            minute = 15
        s = s.strip(": ")
        if ":" in s:
            h, _, m = s.partition(":")
            hh = CalendarPlugin._cnnum(h)
            mm = minute or (CalendarPlugin._cnnum(m) if m.strip() else 0)
        else:
            digits = re.sub(r"[^\d]", "", s)
            if len(digits) == 4 and s.isdigit():
                hh, mm = int(digits[:2]), int(digits[2:])
            elif s:
                hh, mm = CalendarPlugin._cnnum(s), minute
            else:
                raise ValueError(hhmm)
        if ampm == "pm" and hh < 12:
            hh += 12
        if ampm == "am" and hh == 12:
            hh = 0
        hh, mm = int(hh), int(mm)
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError(hhmm)
        return base.replace(hour=hh, minute=mm, second=0, microsecond=0)

    @staticmethod
    def _rrule(word: str) -> str:
        w = (word or "").strip()
        if not w:
            return ""
        if w.upper().startswith("FREQ="):
            return w.upper()
        mapping = {"每天": "FREQ=DAILY", "每周": "FREQ=WEEKLY;WKST=SU", "每月": "FREQ=MONTHLY",
                   "daily": "FREQ=DAILY", "weekly": "FREQ=WEEKLY;WKST=SU",
                   "monthly": "FREQ=MONTHLY", "yearly": "FREQ=YEARLY",
                   "day": "FREQ=DAILY", "week": "FREQ=WEEKLY;WKST=SU",
                   "month": "FREQ=MONTHLY", "year": "FREQ=YEARLY"}
        key = w.lower()
        if key in mapping:
            return mapping[key]
        return mapping.get(w, "")

    def _render(self, events, title: str, with_date: bool = False) -> str:
        if not events:
            return f"📅 {title}：没有日程。"
        lines = [f"📅 {title}"]
        for i, ev in enumerate(events[:10], 1):
            lines.append(ev.line(i, with_date=with_date))
        if len(events) > 10:
            lines.append(f"…还有 {len(events) - 10} 条")
        return "\n".join(lines)

    @staticmethod
    def _detail(ev) -> str:
        return (f"📌 {ev.title}\n"
                f"时间：{ev.start.strftime('%Y-%m-%d %H:%M')} – {ev.end.strftime('%H:%M')}\n"
                f"提醒：{ev.mark()} {ev.remind_text()}\n"
                f"重复：{ev.rrule or '无'}\n"
                f"地点：{ev.location or '—'}")

    # ---------- LLM 工具 ----------
    # 注意：工具和指令走同一套权限与账号隔离，不能只看 _check
    async def _tool_ctx(self, event: AstrMessageEvent):
        """返回 (ctx, 错误文本)。"""
        if not self._allowed(event):
            return None, "DENIED 这个用户没有使用权限"
        return self._ctx(event), None

    @filter.llm_tool(name="list_calendar_events")
    async def tool_list(self, event: AstrMessageEvent, range: str = "today"):
        """查询发烧云日历的日程。用户问“我今天有什么安排”“明天忙不忙”“这周有啥事”时调用。

        Args:
            range(string): 查询范围，可选 today（今天）/ tomorrow（明天）/ week（本周），默认 today
        """
        ctx, denied = await self._tool_ctx(event)
        if denied:
            return denied
        err = await self._check(ctx)
        if err:
            return f"ERROR {err}"
        rng = (range or "today").strip().lower()
        is_week = rng in ("week", "本周")
        evs = ctx.svc.week() if is_week else ctx.svc.day(1 if rng in ("tomorrow", "明天") else 0)
        ctx.cache(event.unified_msg_origin, evs)
        if not evs:
            return f"OK 范围={rng} 共 0 条"
        lines = [f"共 {len(evs)} 条："]
        for i, e in enumerate(evs[:10], 1):
            lines.append(e.line(i, with_date=is_week))
        return "\n".join(lines)

    @filter.llm_tool(name="create_calendar_event")
    async def tool_create(self, event: AstrMessageEvent, title: str, start: str,
                          end: str = "", remind_minutes: int = 15, alarm: bool = False,
                          countdown: bool = False, recurrence: str = "", location: str = ""):
        """在发烧云日历创建日程。用户说“提醒我…”“安排…”“加个日程”时调用。

        Args:
            title(string): 日程标题，例如“交作业”
            start(string): 开始时间，可写“明天 15:00”“2026-10-29 15:00”“10月29日 15:00”“下周四 15:00”“5天后 15:00”“后天 9点半”，也可直接写时段“明天 15:00-17:30”
            end(string): 结束时间，只写时间即可（如“17:30”），留空则默认 1 小时
            remind_minutes(number): 提前多少分钟提醒，默认 15，传 0 表示不提醒
            alarm(boolean): 是否开启闹钟提醒（响铃）
            countdown(boolean): 是否开启倒计时提醒，可与闹钟同时开
            recurrence(string): 重复规则，可写“每天”“每周”“每周五”或标准 RRULE，留空表示不重复
            location(string): 地点，可留空
        """
        ctx, denied = await self._tool_ctx(event)
        if denied:
            return denied
        err = await self._check(ctx)
        if err:
            return f"ERROR {err}"
        try:
            s, e = self._parse_time(start if not end else f"{start}-{end}")
        except Exception:
            s = None
        if s is None:
            return f"ERROR 时间没看懂：{start}。请让用户换个说法，例如“明天 15:00”。"
        async with ctx.lock:
            ev = await ctx.svc.create(title, s, e, remind=remind_minutes, alarm=alarm,
                                      countdown=countdown, location=location,
                                      rrule=self._rrule(recurrence))
        return (f"OK 已创建 id={ev.id} 标题={ev.title} "
                f"时间={ev.start.strftime('%Y-%m-%d %H:%M')}-{ev.end.strftime('%H:%M')} "
                f"提醒={ev.remind_text()}")

    @filter.llm_tool(name="update_calendar_event")
    async def tool_update(self, event: AstrMessageEvent, keyword: str, field: str, value: str):
        """修改已有日程。用户说“把 X 的提醒改成 30 分钟”“关掉 Y 的闹钟”时调用。

        Args:
            keyword(string): 用于定位日程的标题关键词
            field(string): 要改的字段，可选 title / remind / alarm / countdown
            value(string): 新值；remind 填分钟数，alarm / countdown 填 开 或 关，title 填新标题
        """
        ctx, denied = await self._tool_ctx(event)
        if denied:
            return denied
        err = await self._check(ctx)
        if err:
            return f"ERROR {err}"
        hits = self._resolve(ctx, keyword)
        if not hits:
            return f"NOT_FOUND 没找到包含「{keyword}」的日程"
        if len(hits) > 1:
            cand = "; ".join(f"[{i}] {h.title} {h.start.strftime('%m-%d %H:%M')}"
                             for i, h in enumerate(hits[:5], 1))
            return f"AMBIGUOUS 命中多条：{cand}。请让用户说明是哪一条"
        ev = hits[0]
        changes = {}
        f = (field or "").strip().lower()
        if f == "remind":
            changes["remind"] = int(re.sub(r"[^\d]", "", value) or 15)
        elif f in ("alarm", "countdown"):
            changes[f] = str(value).strip() in ("开", "on", "true", "1", "是")
        elif f == "title":
            changes["title"] = value
        else:
            return f"ERROR 不支持的字段：{field}"
        async with ctx.lock:
            new = await ctx.svc.update(ev, **changes)
        return f"OK 已修改 {new.title} 提醒={new.remind_text()}"

    @filter.llm_tool(name="delete_calendar_event")
    async def tool_delete(self, event: AstrMessageEvent, keyword: str):
        """删除日程。调用前必须先向用户复述标题并得到同意。

        Args:
            keyword(string): 日程标题关键词
        """
        ctx, denied = await self._tool_ctx(event)
        if denied:
            return denied
        err = await self._check(ctx)
        if err:
            return f"ERROR {err}"
        hits = self._resolve(ctx, keyword)
        if not hits:
            return f"NOT_FOUND 没找到包含「{keyword}」的日程"
        if len(hits) > 1:
            cand = "; ".join(f"[{i}] {h.title} {h.start.strftime('%m-%d %H:%M')}"
                             for i, h in enumerate(hits[:5], 1))
            return f"AMBIGUOUS 命中多条：{cand}"
        ev = hits[0]
        token = ctx.add_confirm(ev)
        return (f"NEEDS_CONFIRM token={token} 目标={ev.title} "
                f"{ev.start.strftime('%m-%d %H:%M')}；请让用户回复 /calendar confirm {token}")

    @staticmethod
    def _resolve(ctx: UserContext, keyword: str):
        kw = (keyword or "").strip()
        if not kw:
            return []
        return ctx.svc.search(kw)[:5]

    @filter.llm_tool(name="calendar_status")
    async def tool_status(self, event: AstrMessageEvent, query: str = ""):
        """检查发烧日历的登录状态与数据量。用户问“日历能用吗”“登录了吗”“有多少条日程”时调用。

        Args:
            query(string): 固定留空即可
        """
        ctx, denied = await self._tool_ctx(event)
        if denied:
            return denied
        if not ctx.logged_in:
            return f"NOT_LOGGED_IN 请让用户{LOGIN_HINT}"
        try:
            await ctx.svc.refresh()
        except AuthError as e:
            return f"NOT_LOGGED_IN 登录失效（{e}），请让用户重新 /calendar login"
        return (f"OK 已登录 账号={ctx.session.user_id} "
                f"记录={len(ctx.svc.records)} 日历={len(ctx.svc.calendars)}")

    async def terminate(self):
        await self.store.close_all()
