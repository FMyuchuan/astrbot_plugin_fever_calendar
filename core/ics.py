# -*- coding: utf-8 -*-
"""导出标准 iCalendar（RFC 5545）。逻辑与 mi-calendar/mi_ics.py 一致。"""
import json
import re
from datetime import datetime, timezone

MAX_MS = 1 << 62
UTC = timezone.utc
PRODID = "-//fever-calendar//Cloud Calendar Exporter//CN"


def esc(s) -> str:
    s = str(s)
    s = s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return s.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def fold(line: str) -> str:
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > 74:
            out.append(cur.decode("utf-8"))
            cur = b" " + b
        else:
            cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n".join(out)


def _dt(ms) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, UTC).strftime("%Y%m%dT%H%M%SZ")


def _date(ms) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, UTC).strftime("%Y%m%d")


def _dur(d: str) -> int:
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m or not any(m.groups()):
        return 3600
    g = [int(x or 0) for x in m.groups()]
    return g[0] * 86400 + g[1] * 3600 + g[2] * 60 + g[3]


def _agenda(cj):
    for ep in cj.get("extendedProperties") or []:
        if ep.get("name") == "agenda_info":
            try:
                return json.loads(ep.get("value") or "{}") or {}
            except Exception:
                return {}
    return {}


def _vevent(rec, uid, recurrence_id=None):
    cj = rec.get("contentJson") or {}
    start = int(cj.get("dtstart") or 0)
    end = int(cj.get("dtend") or 0)
    if not end or end >= MAX_MS:
        end = start + _dur(cj.get("duration")) * 1000
    all_day = bool(cj.get("allDay"))
    cancelled = cj.get("eventStatus") == 2
    ag = _agenda(cj)

    L = ["BEGIN:VEVENT", f"UID:{uid}"]
    if recurrence_id is not None:
        L.append(f"RECURRENCE-ID;VALUE=DATE:{_date(recurrence_id)}" if all_day
                 else f"RECURRENCE-ID:{_dt(recurrence_id)}")
    L.append(f"DTSTAMP:{_dt(cj.get('modificationTime') or rec.get('modificationTime') or start)}")

    if cancelled and recurrence_id is None:
        L += [f"SUMMARY:{esc(cj.get('title') or '(无标题)')}", "STATUS:CANCELLED", "END:VEVENT"]
        return L

    if all_day:
        d0 = _date(start)
        d1 = _date(end if end > start else start + 86400_000)
        if d1 == d0:
            d1 = _date(start + 86400_000)
        L += [f"DTSTART;VALUE=DATE:{d0}", f"DTEND;VALUE=DATE:{d1}"]
    else:
        L += [f"DTSTART:{_dt(start)}", f"DTEND:{_dt(end)}"]

    L += [f"SUMMARY:{esc(cj.get('title') or '(无标题)')}",
          f"CREATED:{_dt(rec.get('creationTime') or start)}",
          f"LAST-MODIFIED:{_dt(cj.get('modificationTime') or rec.get('modificationTime') or start)}",
          "SEQUENCE:0"]
    if cj.get("rrule"):
        L.append(f"RRULE:{cj['rrule']}")
    if cj.get("eventLocation"):
        L.append(f"LOCATION:{esc(cj['eventLocation'])}")
    if cj.get("description"):
        L.append(f"DESCRIPTION:{esc(cj['description'])}")
    L.append("STATUS:CANCELLED" if cancelled else "STATUS:CONFIRMED")

    if ag.get("need_alarm"):
        L.append("X-MI-NEED-ALARM:TRUE")
    if ag.get("is_countdown_reminder"):
        L.append("X-MI-COUNTDOWN:TRUE")
    if ag.get("is_ai"):
        L.append(f"X-MI-AGENDA-TYPE:{ag.get('extend_agenda_type', 0)}")
    if cj.get("originalServerId"):
        L.append(f"X-MI-ORIGINAL-SERVER-ID:{esc(cj['originalServerId'])}")

    for rm in cj.get("reminders") or []:
        mins = int(rm.get("minutes", 0))
        trig = f"-PT{abs(mins)}M" if mins >= 0 else f"PT{abs(mins)}M"
        L += ["BEGIN:VALARM", "ACTION:DISPLAY",
              f"DESCRIPTION:{esc(cj.get('title') or '提醒')}", f"TRIGGER:{trig}"]
        if ag.get("need_alarm"):
            L.append("X-MI-ALARM-STYLE:alarm")
        L.append("END:VALARM")

    L.append("END:VEVENT")
    return L


def export(records, cal_name="Cloud Calendar") -> str:
    events = [r for r in records if r.get("type") == "event"]
    masters = [r for r in events if not (r.get("contentJson") or {}).get("originalServerId")]
    exc = {}
    for r in events:
        pid = (r.get("contentJson") or {}).get("originalServerId")
        if pid:
            exc.setdefault(pid, []).append(r)

    out = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}",
           "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
           f"X-WR-CALNAME:{esc(cal_name)}", "X-WR-TIMEZONE:Asia/Shanghai"]

    for r in masters:
        out += _vevent(r, f"{r['id']}@micloud.xiaomi.net")
        for ex in exc.get(r["id"], []):
            cj = ex.get("contentJson") or {}
            out += _vevent(ex, f"{r['id']}@micloud.xiaomi.net", cj.get("originalInstanceTime"))

    known = {r["id"] for r in masters}
    for pid, exs in exc.items():
        if pid in known:
            continue
        for ex in exs:
            cj = ex.get("contentJson") or {}
            out += _vevent(ex, f"{pid}@micloud.xiaomi.net", cj.get("originalInstanceTime"))

    out.append("END:VCALENDAR")
    return "\r\n".join(fold(x) for x in out) + "\r\n"
