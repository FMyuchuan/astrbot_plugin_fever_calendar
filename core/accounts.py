# -*- coding: utf-8 -*-
"""每个聊天用户一份账号上下文：凭据、业务层、列表缓存、待确认操作都互相隔离。

目录结构：
    <data>/users/<sender_id>/session.json    各自的登录态
    <data>/session.json                       旧版单账号遗留，启动时自动迁走
"""
import asyncio
import json
import re
import secrets
import time
from pathlib import Path

from .service import CalendarService
from .session import Session

CONFIRM_TTL = 60
LIST_TTL = 600
BIND_TTL = 600


def safe_uid(uid) -> str:
    """用户 ID 直接拿来当目录名不安全，先过滤掉路径分隔符之类的字符。"""
    s = re.sub(r"[^0-9A-Za-z_.\-]", "_", str(uid or "").strip())
    return s[:64] or "_unknown"


class UserContext:
    """一个聊天用户的全部状态。"""

    def __init__(self, uid: str, path: Path):
        self.uid = str(uid)
        self.path = Path(path)
        self.session = Session(self.path / "session.json")
        self.session.load()
        self.svc = CalendarService(self.session)
        self.lock = asyncio.Lock()
        self._lists: dict[str, tuple] = {}
        self._confirms: dict[str, tuple] = {}

    @property
    def logged_in(self) -> bool:
        return bool(self.session.pass_token)

    def reset_session(self):
        """登出：清空内存里的凭据并删掉落盘文件。"""
        self.session.pass_token = ""
        self.session.service_token = ""
        self.session.ssecurity = ""
        self.session.cookies = {}
        self.svc.records = []
        self.svc.calendars = {}
        self.svc.cal_id = None
        self._lists.clear()
        self._confirms.clear()
        try:
            (self.path / "session.json").unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass

    # ---------- 列表缓存（同一个人的私聊/群聊各自一份，避免串号）----------
    def cache(self, umo: str, events, ttl: int = LIST_TTL):
        self._lists[umo] = (time.time() + ttl, events)

    def pick(self, umo: str, idx: str):
        item = self._lists.get(umo)
        if not item or item[0] < time.time():
            return None
        try:
            n = int(idx) - 1
        except Exception:
            return None
        return item[1][n] if 0 <= n < len(item[1]) else None

    # ---------- 待确认操作：只属于这个人，60 秒后自己消失 ----------
    def add_confirm(self, event, mode: str = "series") -> str:
        self._purge_confirms()
        token = str(secrets.randbelow(900000) + 100000)
        self._confirms[token] = (time.time() + CONFIRM_TTL, event, mode)
        return token

    def take_confirm(self, token: str):
        self._purge_confirms()
        item = self._confirms.pop(str(token or "").strip(), None)
        if not item or item[0] < time.time():
            return None
        return item[1], (item[2] if len(item) > 2 else "series")

    def _purge_confirms(self):
        now = time.time()
        for k in [k for k, v in self._confirms.items() if v[0] < now]:
            self._confirms.pop(k, None)

    async def close(self):
        await self.session.close()


class IdentityBook:
    """把「平台 + 发送者 ID」映射到一个日历账号。

    默认一个身份一个账号；用 /calendar bind 可以把多个平台上的身份
    指到同一个账号（同一份登录态），不需要搞共享日历。
    """

    def __init__(self, path):
        self.path = Path(path)
        self.data = {"version": 1, "identities": {}, "pending": {}}
        self.load()

    # ---------- 持久化 ----------
    def load(self):
        try:
            d = json.loads(self.path.read_text("utf-8"))
        except Exception:
            return
        if isinstance(d, dict):
            self.data["identities"] = dict(d.get("identities") or {})
            self.data["pending"] = dict(d.get("pending") or {})

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), "utf-8")
            self.path.chmod(0o600)
        except Exception:
            pass

    # ---------- 身份 → 账号 ----------
    @staticmethod
    def identity_key(platform, sender_id) -> str:
        return f"{str(platform or 'unknown').strip() or 'unknown'}:{sender_id}"

    def account_of(self, identity: str) -> str:
        return self.data["identities"].get(identity, "")

    def resolve(self, identity: str) -> str:
        """查这个身份对应的账号；没绑过就当场建一个（一个身份一个账号）。"""
        acc = self.account_of(identity)
        if acc:
            return acc
        acc = safe_uid(identity)
        self.data["identities"][identity] = acc
        self.save()
        return acc

    def identities_of(self, account: str) -> list[str]:
        return sorted([k for k, v in self.data["identities"].items() if v == account])

    # ---------- 跨平台绑定 ----------
    def issue_code(self, account: str, ttl: int = 600) -> str:
        self._purge()
        code = str(secrets.randbelow(900000) + 100000)
        self.data["pending"][code] = {"account": account, "exp": int(time.time()) + ttl}
        self.save()
        return code

    def redeem_code(self, identity: str, code: str):
        """把当前身份绑到短码对应的账号上，返回账号；失败返回 None。"""
        self._purge()
        item = self.data["pending"].pop(str(code or "").strip(), None)
        if not item:
            return None
        account = str(item.get("account") or "")
        if not account:
            return None
        self.data["identities"][identity] = account
        self.save()
        return account

    def _purge(self):
        now = int(time.time())
        for k in [k for k, v in self.data["pending"].items() if int(v.get("exp") or 0) < now]:
            self.data["pending"].pop(k, None)


class AccountStore:
    """按发送者 ID 管理用户上下文，按需创建。"""

    def __init__(self, root):
        self.root = Path(root)
        self.users_dir = self.root / "users"
        self.users_dir.mkdir(parents=True, exist_ok=True)
        self._ctx: dict[str, UserContext] = {}

    def _dir(self, uid) -> Path:
        return self.users_dir / safe_uid(uid)

    def path_of(self, uid) -> Path:
        return self._dir(uid) / "session.json"

    def get(self, uid) -> UserContext:
        uid = str(uid or "")
        ctx = self._ctx.get(uid)
        if ctx is None:
            d = self._dir(uid)
            d.mkdir(parents=True, exist_ok=True)
            ctx = UserContext(uid, d)
            self._ctx[uid] = ctx
        return ctx

    def peek(self, uid) -> UserContext | None:
        return self._ctx.get(str(uid or ""))

    async def close_all(self):
        for ctx in list(self._ctx.values()):
            try:
                await ctx.close()
            except Exception:
                pass
        self._ctx.clear()
