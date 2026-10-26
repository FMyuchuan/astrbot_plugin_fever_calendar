# -*- coding: utf-8 -*-
"""会话：登录、续期、令牌落盘（异步版）。

登录链路（已验证）：
    passToken → GET /pass/serviceLogin?sid=micloud
              → location + 整数 nonce + ssecurity
              → location?clientSign=Base64(SHA1("nonce=<整数>&<ssecurity>"))
              → 跟随后拿到 serviceToken
"""
import asyncio
import json
import time
from pathlib import Path
from urllib import parse

import httpx

from . import crypto

ACCOUNT = "https://account.xiaomi.com"
UA = ("Mozilla/5.0 (Linux; Android 13; 2211133C Build/TKQ1.220905.001; wv) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/111.0.5563.116 "
      "Mobile Safari/537.36 XiaoMi/MiuiBrowser/17.3.8")

SID_CALLBACK = {
    "micloud": "https://api.micloud.xiaomi.net/sts",
    "calendar": "https://api.comm.miui.com/calendar/sts",
}


class AuthError(Exception):
    """需要重新登录。"""


def _jdec(text: str) -> dict:
    text = text.strip()
    if text.startswith("&&&START&&&"):
        text = text[len("&&&START&&&"):]
    return json.loads(text)


class Session:
    def __init__(self, store_path: Path, sid: str = "micloud"):
        self.path = Path(store_path)
        self.sid = sid
        self.client = httpx.AsyncClient(headers={"User-Agent": UA},
                                        timeout=httpx.Timeout(20.0), follow_redirects=True)
        self.cookies: dict = {}
        self.user_id = ""
        self.cuser_id = ""
        self.pass_token = ""
        self.service_token = ""
        self.ssecurity = ""

    # ---------- 持久化 ----------
    def load(self) -> bool:
        """读落盘状态。已在内存里的值优先——配置文件填的 passToken 不该被旧文件覆盖。"""
        if not self.path.exists():
            return False
        try:
            d = json.loads(self.path.read_text("utf-8"))
        except Exception:
            return False
        if not self.cookies:
            self.cookies = d.get("cookies") or {}
        self.user_id = self.user_id or str(d.get("user_id") or "")
        self.cuser_id = self.cuser_id or (d.get("cuser_id") or "")
        self.pass_token = self.pass_token or (d.get("pass_token") or "")
        self.service_token = self.service_token or (d.get("service_token") or "")
        self.ssecurity = self.ssecurity or (d.get("ssecurity") or "")
        return bool(self.pass_token and self.ssecurity)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "sid": self.sid,
            "user_id": self.user_id,
            "cuser_id": self.cuser_id,
            "pass_token": self.pass_token,
            "service_token": self.service_token,
            "ssecurity": self.ssecurity,
            "cookies": self.cookies,
            "updated_at": int(time.time()),
        }, ensure_ascii=False, indent=1), "utf-8")
        try:
            self.path.chmod(0o600)
        except Exception:
            pass

    @property
    def ready(self) -> bool:
        """有 passToken 就算“有登录凭据”，ssecurity 是登录后才拿到的。"""
        return bool(self.pass_token)


    def _cookie(self, name: str) -> str:
        """同名 cookie 可能存在于多个域，httpx 的 .get 会抛 CookieConflict，这里手动取最后一个。"""
        vals = [c.value for c in self.client.cookies.jar if c.name == name]
        return vals[-1] if vals else ""

    def _clear_jar(self):
        """清空 cookie jar：API 请求都显式带 Cookie 头，留着 jar 反而会拼出重复/过期的值。"""
        try:
            self.client.cookies.jar.clear()
        except Exception:
            pass

    def _drop_cookie(self, name: str):
        for c in list(self.client.cookies.jar):
            if c.name == name:
                try:
                    self.client.cookies.jar.clear(c.domain, c.path, c.name)
                except Exception:
                    pass

    # ---------- 登录 ----------
    def _cookies_for_account(self) -> dict:
        return {"userId": self.user_id, "cUserId": self.cuser_id,
                "passToken": self.pass_token, "uLocale": "zh_CN"}

    async def login(self):
        """用 passToken 静默换 serviceToken。"""
        if not self.pass_token:
            raise AuthError("没有 passToken，需要先扫码登录")
        if not self.user_id:
            raise AuthError("缺少 user_id：serviceLogin 必须带 userId/cUserId 的 cookie，请在配置里补上")

        self._clear_jar()
        r = await self.client.get(f"{ACCOUNT}/pass/serviceLogin",
                                  params={"sid": self.sid, "_json": "true"},
                                  cookies=self._cookies_for_account())
        d = _jdec(r.text)
        if d.get("code") != 0 or not d.get("location"):
            raise AuthError(f"passToken 失效: code={d.get('code')}")

        self.ssecurity = d.get("ssecurity") or self.ssecurity
        self.user_id = str(d.get("userId") or self.user_id)
        self.cuser_id = d.get("cUserId") or self.cuser_id

        loc = d["location"]
        nonce = d.get("nonce")
        if nonce is not None and self.ssecurity:
            raw = f"nonce={nonce}&{self.ssecurity}"
            sign = __import__("base64").b64encode(
                __import__("hashlib").sha1(raw.encode()).digest()).decode()
            loc += ("&" if "?" in loc else "?") + "clientSign=" + parse.quote(sign)

        r2 = await self.client.get(loc)
        tok = self._cookie("serviceToken") or r2.cookies.get("serviceToken")
        if not tok:
            raise AuthError(f"sts 未返回 serviceToken（HTTP {r2.status_code}）")
        self.service_token = tok
        self.save()

    async def ensure(self):
        if not self.service_token:
            try:
                self.load()
            except Exception:
                pass
            await self.login()

    async def relogin(self):
        """令牌过期时强制重新登录（passToken 还在就能无感恢复）。"""
        self.service_token = ""
        self.ssecurity = ""
        self._clear_jar()
        await self.login()

    # ---------- 扫码登录 ----------
    async def qr_start(self):
        """申请登录二维码，返回 (二维码图片地址, 轮询地址, 超时秒数, 轮询间隔)。

        注意：`qr` 字段本身是一个返回 PNG 的地址，服务端渲染好的那张才是要扫的二维码；
        拿这串 URL 自己再编码一遍，扫出来会再套一层（要先扫 URL，再扫里面的图）。
        """
        r = await self.client.get(f"{ACCOUNT}/longPolling/loginUrl",
                                  params={"sid": self.sid, "_json": "true", "_locale": "zh_CN"})
        d = _jdec(r.text)
        if d.get("code") != 0 or not d.get("qr"):
            raise AuthError(f"申请二维码失败: code={d.get('code')} {d.get('desc') or ''}")
        return d["qr"], d["lp"], int(d.get("timeout") or 300), int(d.get("timeInterval") or 2)

    async def qr_image(self, url: str) -> bytes:
        """下载服务端渲染好的二维码 PNG。"""
        r = await self.client.get(url)
        if r.status_code != 200 or not r.content.startswith(b"\x89PNG"):
            raise AuthError(f"拉取二维码图片失败: HTTP {r.status_code}")
        return r.content

    async def qr_poll(self, lp: str, timeout: int = 120, interval: int = 2) -> bool:
        """轮询扫码结果；成功后落盘 passToken 并换 serviceToken。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = await self.client.get(lp, timeout=40)
                d = _jdec(r.text)
            except Exception:
                await asyncio.sleep(interval)
                continue
            if d.get("location"):
                await self.client.get(d["location"])
                self.pass_token = self._cookie("passToken") or self.pass_token
                self.user_id = str(self._cookie("userId") or self.user_id)
                self.cuser_id = self._cookie("cUserId") or self.cuser_id
                if not self.pass_token:
                    raise AuthError("扫码成功但没拿到 passToken")
                await self.login()
                self.save()
                return True
            await asyncio.sleep(interval)
        return False

    # ---------- 请求 ----------
    def cookie_header(self) -> str:
        return (f"serviceToken={self.service_token}; userId={self.user_id}; "
                f"cUserId={self.cuser_id}")

    async def api_get(self, url: str, params: dict) -> httpx.Response:
        # 每次调用前清空 jar：服务端可能 Set-Cookie 一份旧的 serviceToken，
        # 与显式 Cookie 头拼在一起就会变成重复 cookie，导致 401。
        self._clear_jar()
        return await self.client.get(url, params=params,
                                     headers={"Cookie": self.cookie_header()})

    async def api_post(self, url: str, params: dict) -> httpx.Response:
        self._clear_jar()
        return await self.client.post(url, params=params,
                                      headers={"Cookie": self.cookie_header()})

    async def close(self):
        await self.client.aclose()
