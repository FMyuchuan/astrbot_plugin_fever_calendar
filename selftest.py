#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""脱离 AstrBot 的自测脚本：直接调用 core/ 里的协议实现。

用法：
    python selftest.py                 # 只读：登录 + 今天/明天/本周 + 导出
    python selftest.py --write         # 额外验证 建 → 改 → 删（会真的写一条再删掉）
    python selftest.py --user 12345    # 指定要看哪个聊天用户的登录态

登录态来源，按优先级：
    1. --user 指定的 users/<id>/session.json
    2. 环境变量 MICLOUD_PASS_TOKEN / MICLOUD_USER_ID / MICLOUD_CUSER_ID
    3. users/ 下唯一的那份登录态；若有多份则必须用 --user 指定
数据目录可用 MICLOUD_CAL_DATA 覆盖。
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.accounts import safe_uid                # noqa: E402
from core.ics import export                      # noqa: E402
from core.service import TZ, CalendarService     # noqa: E402
from core.session import AuthError, Session      # noqa: E402


def data_dir() -> Path:
    env = os.environ.get("MICLOUD_CAL_DATA")
    if env:
        return Path(env)
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path
        return Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_fever_calendar"
    except Exception:
        return ROOT / "data"


def known_users() -> list:
    d = data_dir() / "users"
    if not d.exists():
        return []
    return sorted(p.parent.name for p in d.glob("*/session.json"))


def pick_session(user: str = "") -> Path | None:
    if user:
        return data_dir() / "users" / safe_uid(user) / "session.json"
    if os.environ.get("MICLOUD_PASS_TOKEN"):
        return data_dir() / "users" / safe_uid("_env") / "session.json"
    found = known_users()
    if len(found) == 1:
        return data_dir() / "users" / found[0] / "session.json"
    return None


def make_session(user: str = "") -> Session:
    session = Session(pick_session(user) or (data_dir() / "users" / "_env" / "session.json"))
    session.load()
    if os.environ.get("MICLOUD_PASS_TOKEN"):
        session.pass_token = os.environ["MICLOUD_PASS_TOKEN"]
        session.user_id = os.environ.get("MICLOUD_USER_ID", session.user_id)
        session.cuser_id = os.environ.get("MICLOUD_CUSER_ID", session.cuser_id)
    return session


def show(title: str, events):
    print(f"\n== {title} ==")
    if not events:
        print("  （没有日程）")
        return
    for i, ev in enumerate(events[:10], 1):
        print("  " + ev.line(i, with_date=(title == "本周")))
    if len(events) > 10:
        print(f"  …还有 {len(events) - 10} 条")


async def _write_test(svc: CalendarService) -> int:
    print("\n== 写入测试 ==")
    base = datetime.now(TZ).replace(second=0, microsecond=0) + timedelta(days=3)
    title = f"selftest-{int(base.timestamp())}"
    try:
        ev = await svc.create(title, base, base + timedelta(minutes=30), remind=10)
        print(f"  建：{ev.title} {ev.start.strftime('%m-%d %H:%M')}")
        ev = await svc.update(ev, title=title + "-改", remind=30)
        print(f"  改：{ev.title} 提醒={ev.remind_text()}")
        await svc.delete(ev)
        print("  删：ok")
    except Exception as e:
        print(f"  写入测试失败：{type(e).__name__}: {e}")
        return 1
    return 0


async def run(write: bool, user: str) -> int:
    session = make_session(user)
    try:
        return await _run_with(session, write)
    finally:
        await session.close()


async def _run_with(session: Session, write: bool) -> int:
    if not session.pass_token:
        users = known_users()
        print(f"没有登录态（{data_dir()}）")
        if users:
            print("当前有登录态的用户：" + "、".join(users))
            print("用 --user <ID> 指定其中一个。")
        else:
            print("先在 AstrBot 里发 /calendar login 扫码，")
            print("或设置环境变量 MICLOUD_PASS_TOKEN / MICLOUD_USER_ID / MICLOUD_CUSER_ID。")
        return 2

    svc = CalendarService(session)
    try:
        await svc.refresh()
    except AuthError as e:
        print(f"登录失败：{e}")
        return 1
    except Exception as e:
        print(f"拉取失败：{type(e).__name__}: {e}")
        return 1

    print(f"登录成功：账号 {session.user_id}，记录 {len(svc.records)} 条，"
          f"日历 {len(svc.calendars)} 个")
    show("今天", svc.day(0))
    show("明天", svc.day(1))
    show("本周", svc.week())

    ics = export(svc.records)
    out = data_dir() / "selftest_export.ics"
    out.write_text(ics, encoding="utf-8")
    try:
        out.chmod(0o600)
    except Exception:
        pass
    print(f"\n导出 {len(ics.encode('utf-8'))} 字节到 {out}")

    return await _write_test(svc) if write else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="发烧云日历插件自测")
    ap.add_argument("--write", action="store_true", help="额外验证 建 → 改 → 删")
    ap.add_argument("--user", default="", help="指定聊天用户 ID（对应 users/<ID>/）")
    args = ap.parse_args()
    return asyncio.run(run(args.write, args.user))


if __name__ == "__main__":
    sys.exit(main())
