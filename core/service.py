# -*- coding: utf-8 -*-
"""业务层：把 PDC 原始记录整理成「日程」，并做重复展开、范围筛选。"""
import json
from datetime import datetime, timedelta, timezone

from .pdc import PdcClient
from .session import Session

TZ = timezone(timedelta(hours=8))
MAX_MS = 1 << 62
WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def from_ms(v) -> datetime:
    return datetime.fromtimestamp(int(v) / 1000, TZ)


def parse_rrule(text: str) -> dict:
    out = {}
    for part in (text or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.upper()] = v
    return out


def _until_dt(v: str):
    try:
        if v.endswith("Z"):
            return datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return datetime.strptime(v, "%Y%m%dT%H%M%S").replace(tzinfo=TZ)
    except Exception:
        return None


def expand(rrule: str, dtstart_ms: int, win_start: datetime, win_end: datetime, hard_cap=4000):
    """把 RRULE 展开成落在窗口内的开始时间列表（够用即可，不追求全量 RFC）。"""
    rule = parse_rrule(rrule)
    freq = rule.get("FREQ", "DAILY")
    interval = max(1, int(rule.get("INTERVAL", 1)))
    count = int(rule["COUNT"]) if rule.get("COUNT") else None
    until = _until_dt(rule.get("UNTIL", "")) if rule.get("UNTIL") else None
    byday = [WEEKDAYS[x[-2:]] for x in rule.get("BYDAY", "").split(",") if x[-2:] in WEEKDAYS]
    bymonthday = [int(x) for x in rule.get("BYMONTHDAY", "").split(",") if x.strip().lstrip("-").isdigit()]

    cur = from_ms(dtstart_ms)
    out, produced, steps = [], 0, 0
    while steps < hard_cap:
        steps += 1
        if until and cur > until:
            break
        if count is not None and produced >= count:
            break
        if cur > win_end:
            break

        hit = True
        if freq == "WEEKLY" and byday:
            hit = cur.weekday() in byday
        elif freq == "MONTHLY" and bymonthday:
            hit = cur.day in bymonthday

        if hit:
            produced += 1
            if cur >= win_start:
                out.append(cur)

        if freq == "DAILY":
            cur += timedelta(days=interval)
        elif freq == "WEEKLY":
            cur += timedelta(days=1) if byday else timedelta(weeks=interval)
        elif freq == "MONTHLY":
            cur += timedelta(days=1) if bymonthday else timedelta(days=30 * interval)
        elif freq == "YEARLY":
            try:
                cur = cur.replace(year=cur.year + interval)
            except ValueError:
                # 2 月 29 日这类日子，平年直接跳过一年
                cur = cur.replace(year=cur.year + interval, day=28)
        else:
            break

    # 起始时刻本身也要算一次（上面循环首轮已包含）
    return out


class Event:
    def __init__(self, rec: dict, start_ms: int, end_ms: int, occurrence_of: str = None):
        cj = rec.get("contentJson") or {}
        self.rec = rec
        self.id = rec.get("id")
        self.e_tag = rec.get("eTag")
        self.parent_id = rec.get("parentId")
        self.title = cj.get("title") or "(无标题)"
        self.start = from_ms(start_ms)
        self.end = from_ms(end_ms)
        self.all_day = bool(cj.get("allDay"))
        self.rrule = cj.get("rrule")
        self.status = cj.get("eventStatus") or 1
        self.location = cj.get("eventLocation") or ""
        self.description = cj.get("description") or ""
        # 重复事件的实例信息
        self.occurrence_of = occurrence_of
        self.is_exception = bool(cj.get("originalServerId"))
        self.instance_ts = int(cj.get("originalInstanceTime") or start_ms) if self.is_exception else start_ms
        ag = {}
        for ep in cj.get("extendedProperties") or []:
            if ep.get("name") == "agenda_info":
                try:
                    ag = json.loads(ep.get("value") or "{}")
                except Exception:
                    ag = {}
        self.alarm = bool(ag.get("need_alarm"))
        self.countdown = bool(ag.get("is_countdown_reminder"))
        self.reminders = [int(r.get("minutes", 0)) for r in (cj.get("reminders") or [])]

    @property
    def cancelled(self) -> bool:
        return self.status == 2

    def mark(self) -> str:
        if self.all_day:
            return "📌"
        if self.alarm and self.countdown:
            return "🔔⏳"
        if self.alarm:
            return "🔔"
        if self.countdown:
            return "⏳"
        return "⏰"

    def remind_text(self) -> str:
        if not self.reminders:
            return "无提醒"
        mins = "、".join(str(m) for m in sorted(set(self.reminders)))
        style = "闹钟+倒计时" if self.alarm and self.countdown else (
            "闹钟" if self.alarm else ("倒计时" if self.countdown else ""))
        base = f"提前 {mins} 分钟"
        return f"{style}，{base}" if style else base

    def line(self, idx: int, with_date: bool = False) -> str:
        if self.all_day:
            when = self.start.strftime("%m-%d 全天") if with_date else "全天"
        else:
            when = self.start.strftime("%m-%d %H:%M") if with_date else self.start.strftime("%H:%M")
        return f"{idx}. {when}  {self.title}  {self.mark()} {self.remind_text()}"


class CalendarService:
    def __init__(self, session: Session):
        self.session = session
        self.pdc = PdcClient(session)
        self.records = []
        self.calendars = {}
        self.cal_id = None

    async def refresh(self):
        self.session.load()
        await self.session.ensure()
        self.records, _, _ = await self.pdc.list_records(limit=500)
        self.calendars = {r["id"]: (r.get("contentJson") or {}) for r in self.records if r.get("type") == "calendar"}
        if not self.calendars:
            raise RuntimeError("账号里没有可用的云日历")
        self.cal_id = next(iter(self.calendars))
        return self.records

    # ---------- 查询 ----------
    def events_between(self, start: datetime, end: datetime):
        masters, exceptions = [], {}
        for r in self.records:
            if r.get("type") != "event":
                continue
            cj = r.get("contentJson") or {}
            if cj.get("originalServerId"):
                exceptions.setdefault(cj["originalServerId"], {})[int(cj.get("originalInstanceTime") or 0)] = r
            else:
                masters.append(r)

        found = []
        for r in masters:
            cj = r.get("contentJson") or {}
            st = int(cj.get("dtstart") or 0)
            en = int(cj.get("dtend") or 0)
            if not en or en >= MAX_MS:
                en = st + 3600_000
            dur = en - st

            if cj.get("rrule"):
                for occ in expand(cj["rrule"], st, start, end):
                    exc = exceptions.get(r["id"], {}).get(ms(occ))
                    if exc:
                        ecj = exc.get("contentJson") or {}
                        if ecj.get("eventStatus") == 2:
                            continue
                        found.append(Event(exc, int(ecj.get("dtstart") or ms(occ)),
                                           int(ecj.get("dtend") or ms(occ) + dur), r["id"]))
                    else:
                        found.append(Event(r, ms(occ), ms(occ) + dur, r["id"]))
            else:
                if st < ms(end) and en > ms(start) and not (cj.get("eventStatus") == 2):
                    found.append(Event(r, st, en))

        found.sort(key=lambda e: (e.start, e.title))
        return found

    def day(self, offset=0):
        base = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        s = base + timedelta(days=offset)
        return self.events_between(s, s + timedelta(days=1))

    def week(self):
        today = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        s = today - timedelta(days=today.weekday())
        return self.events_between(s, s + timedelta(days=7))

    def search(self, keyword: str, days=90):
        now = datetime.now(TZ)
        evs = self.events_between(now - timedelta(days=30), now + timedelta(days=days))
        kw = (keyword or "").strip().lower()
        if not kw:
            return []
        return [e for e in evs if kw in (e.title or "").lower()]

    # ---------- 写入 ----------
    async def create(self, title, start: datetime, end: datetime, remind=15,
                     alarm=False, countdown=False, location="", description="", rrule=""):
        ag = {"need_alarm": bool(alarm), "is_ai": False,
              "is_countdown_reminder": bool(countdown), "extend_agenda_type": 0}
        cj = {
            "title": title,
            "dtstart": ms(start), "dtend": ms(end),
            "allDay": 0, "eventTimeZone": "Asia/Shanghai", "eventStatus": 1,
            "availability": 0, "accessLevel": 0,
            "hasAlarm": 1 if remind else 0,
            "reminders": [{"method": 1, "minutes": int(remind)}] if remind else [],
            "extendedProperties": [{"name": "agenda_info",
                                    "value": json.dumps(ag, ensure_ascii=False, separators=(",", ":"))}],
        }
        if location:
            cj["eventLocation"] = location
        if description:
            cj["description"] = description
        if rrule:
            cj["rrule"] = rrule
        rec = await self.pdc.create(cj, self.cal_id)
        self.records.append(rec)
        return Event(rec, cj["dtstart"], cj["dtend"])

    async def update(self, event: Event, **changes):
        rec = dict(event.rec)
        cj = dict(rec.get("contentJson") or {})
        self._apply(cj, changes)
        rec["contentJson"] = cj
        new = await self.pdc.update(rec)
        for i, r in enumerate(self.records):
            if r.get("id") == new.get("id"):
                self.records[i] = new
                break
        return Event(new, cj["dtstart"], cj.get("dtend") or cj["dtstart"] + 3600_000)

    # ---------- 单次实例 ----------
    def _record_by_id(self, rid):
        for r in self.records:
            if r.get("id") == rid:
                return r
        raise RuntimeError(f"找不到母事件 {rid}")

    async def update_instance(self, event: Event, **changes):
        """只改重复事件的某一次：写一条带 originalServerId 的例外记录。"""
        parent = event.occurrence_of or event.id
        cj = dict(self._record_by_id(parent).get("contentJson") or {})
        cj.pop("rrule", None)
        # 先把时间锚定到「这一次」，否则例外记录会落在母事件的首次时间上
        dur = int(cj.get("dtend") or 0) - int(cj.get("dtstart") or 0)
        if dur <= 0:
            dur = 1800_000
        cj["dtstart"] = int(event.instance_ts)
        cj["dtend"] = int(event.instance_ts) + dur
        self._apply(cj, changes)
        cj.update({
            "originalServerId": parent,
            "originalInstanceTime": int(event.instance_ts),
            "originalAllDay": int(cj.get("allDay") or 0),
        })
        rec = await self.pdc.create(cj, self.cal_id)
        self.records.append(rec)
        return Event(rec, int(cj["dtstart"]), cj.get("dtend") or cj["dtstart"] + 3600_000, parent)

    async def delete_instance(self, event: Event):
        """只删重复事件的某一次：写一条 eventStatus=2 的例外记录。"""
        parent = event.occurrence_of or event.id
        cj = dict(self._record_by_id(parent).get("contentJson") or {})
        cj.pop("rrule", None)
        cj.update({
            "eventStatus": 2,
            "dtstart": int(event.instance_ts),
            "originalServerId": parent,
            "originalInstanceTime": int(event.instance_ts),
            "originalAllDay": int(cj.get("allDay") or 0),
        })
        rec = await self.pdc.create(cj, self.cal_id)
        self.records.append(rec)
        return rec

    @staticmethod
    def _apply(cj: dict, changes: dict):
        """把「提醒/闹钟/倒计时/标题/时间」翻译成 contentJson 的改动。"""
        for k, v in changes.items():
            if k == "remind":
                cj["reminders"] = [{"method": 1, "minutes": int(v)}] if v else []
                cj["hasAlarm"] = 1 if v else 0
            elif k in ("alarm", "countdown"):
                ag = {}
                for ep in cj.get("extendedProperties") or []:
                    if ep.get("name") == "agenda_info":
                        ag = json.loads(ep.get("value") or "{}")
                ag["need_alarm" if k == "alarm" else "is_countdown_reminder"] = bool(v)
                ag.setdefault("is_ai", False)
                ag.setdefault("extend_agenda_type", 0)
                cj["extendedProperties"] = [{"name": "agenda_info",
                                             "value": json.dumps(ag, ensure_ascii=False, separators=(",", ":"))}]
            elif k == "title":
                cj["title"] = v
            elif k == "time":
                s, e = v
                cj["dtstart"], cj["dtend"] = ms(s), ms(e)
            else:
                cj[k] = v

    async def delete(self, event: Event):
        return await self.pdc.delete(event.id, event.e_tag)
