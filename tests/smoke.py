# -*- coding: utf-8 -*-
"""不联网的冒烟测试：重复规则展开 + iCalendar 导出。

本地：python tests/smoke.py
CI ：由 .github/workflows/ci.yml 调用
"""
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.accounts import AccountStore, IdentityBook, safe_uid  # noqa: E402
from core.ics import export          # noqa: E402
from core.service import TZ, expand, ms  # noqa: E402

def test_weekly_byday():
    start = datetime(2026, 10, 5, 9, 0, tzinfo=TZ)  # 周一
    win = (datetime(2026, 10, 1, tzinfo=TZ), datetime(2026, 10, 31, tzinfo=TZ))
    occ = expand("FREQ=WEEKLY;BYDAY=MO,WE", ms(start), *win)
    assert len(occ) == 8, len(occ)
    assert [d.weekday() for d in occ] == [0, 2] * 4


def test_count():
    start = datetime(2026, 10, 5, 9, 0, tzinfo=TZ)
    win = (datetime(2026, 10, 1, tzinfo=TZ), datetime(2026, 10, 31, tzinfo=TZ))
    assert len(expand("FREQ=DAILY;COUNT=3", ms(start), *win)) == 3


def test_yearly_leap_day():
    """2 月 29 日起的每年重复，平年不能抛异常。"""
    start = datetime(2028, 2, 29, 10, 0, tzinfo=TZ)
    occ = expand("FREQ=YEARLY", ms(start),
                 datetime(2028, 1, 1, tzinfo=TZ), datetime(2032, 1, 1, tzinfo=TZ))
    assert len(occ) == 4, len(occ)


def test_export():
    start = datetime(2026, 10, 5, 9, 0, tzinfo=TZ)
    recs = [{
        "id": "a1", "eTag": "1", "parentId": "cal", "type": "event",
        "contentJson": {
            "title": "带,逗号;分号", "dtstart": ms(start),
            "dtend": ms(start + timedelta(hours=1)),
            "extendedProperties": [{"name": "agenda_info", "value": '{"need_alarm": true}'}],
            "reminders": [{"minutes": 15}],
        },
    }]
    ics = export(recs)
    assert ics.startswith("BEGIN:VCALENDAR"), ics[:40]
    assert "VALARM" in ics and "RRULE" not in ics
    assert ics.rstrip().endswith("END:VCALENDAR")


def test_accounts_isolated():
    """两个用户各有各的目录、各有各的待确认操作。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = AccountStore(Path(tmp))
        a = store.get("1001")
        b = store.get("1002")
        assert a.path != b.path
        assert a.path.is_dir() and b.path.is_dir()

        token = a.add_confirm(object())
        assert token.isdigit() and len(token) == 6, token
        assert b.take_confirm(token) is None, "别的用户不该能用这枚确认码"
        assert a.take_confirm(token) is not None
        assert a.take_confirm(token) is None, "确认码只能用一次"


def test_safe_uid():
    assert "/" not in safe_uid("../../etc/passwd")
    assert safe_uid("") == "_unknown"
    assert safe_uid("123456") == "123456"


def test_identity_bind():
    """同一身份稳定；换个平台是另一个身份；短码能把两边并成一个账号。"""
    with tempfile.TemporaryDirectory() as tmp:
        book = IdentityBook(Path(tmp) / "identities.json")
        a1 = book.resolve("aiocqhttp:1001")
        a2 = book.resolve("aiocqhttp:1001")
        assert a1 == a2, "同一身份应稳定"

        other = book.resolve("telegram:8888")
        assert other != a1, "不同平台默认是两个账号"

        code = book.issue_code(a1)
        assert book.redeem_code("telegram:8888", "000000") is None
        assert book.redeem_code("telegram:8888", code) == a1
        assert book.resolve("telegram:8888") == a1, "绑定后应指向同一账号"
        assert len(book.identities_of(a1)) == 2
        assert book.redeem_code("telegram:8888", code) is None, "短码只能用一次"


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok   {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
