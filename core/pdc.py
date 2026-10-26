# -*- coding: utf-8 -*-
"""PDC 日历接口封装（异步）。

接口（均已实测）：
  GET  /mic/pdc/v4.1/apps/calendar/spaces/default/records        拉取
  POST /mic/pdc/v4.1/apps/calendar/spaces/default/records        新增
  POST /…/records/{id}/update                                    修改
  POST /…/records/{id}/delete                                    删除
"""
import json
import os
from base64 import b64encode

from . import crypto
from .session import Session

HOST = "https://pdc.micloud.xiaomi.net"
BASE_PATH = "/mic/pdc/v4.1/apps/calendar/spaces/default/records"
APP_VER = ("112000200", "1.12.0.2.00")


class ApiError(Exception):
    def __init__(self, code, reason, payload=None):
        super().__init__(f"[{code}] {reason}")
        self.code = code
        self.reason = reason
        self.payload = payload or {}


class PdcClient:
    def __init__(self, session: Session):
        self.s = session
        self.apk_code, self.apk_version = APP_VER

    @property
    def key(self) -> bytes:
        """ssecurity 是登录后才有的，所以每次现算。"""
        return crypto.aes_key(self.s.ssecurity)

    # ---------- 底层 ----------
    def _enc(self, text) -> str:
        if isinstance(text, str):
            text = text.encode()
        return crypto.encrypt_token(self.key, text)

    def _build_query(self, path: str, extra: dict, method: str) -> dict:
        params = {
            "_nonce": b64encode(os.urandom(8)).decode(),
            "_count": "20",
            "_delta": "0",
            "_backend": "false",
            "_apkversioncode": self.apk_code,
            "_apkversion": self.apk_version,
        }
        params.update(extra or {})
        # 游标必须用「当前」ssecurity 加密；重登后密钥会变，旧的 token 会让服务端解不开
        raw_limit = params.pop("_limit_raw", None)
        raw_sync = params.pop("_sync_raw", None)
        if raw_limit is not None:
            params["limit"] = self._enc(raw_limit)
        if raw_sync:
            params["syncToken"] = self._enc(raw_sync)
            params["_delta"] = "1"
        q = dict(params)
        q["signature"] = crypto.make_signature(method, path, params, self.s.ssecurity)
        return q

    async def _call(self, path: str, extra: dict, method: str = "GET"):
        """401 时自动重登并重试一次（serviceToken 有有效期，缓存的会过期）。"""
        last_401 = False
        for attempt in range(2):
            q = self._build_query(path, extra, method)
            if method == "GET":
                r = await self.s.api_get(HOST + path, q)
            else:
                r = await self.s.api_post(HOST + path, q)
            if r.status_code == 401:
                last_401 = True
                if attempt == 0:
                    await self.s.relogin()
                    continue
                break
            if r.status_code >= 500:
                raise ApiError(r.status_code, "服务端错误，请稍后重试")
            return self._parse(r)
        if last_401:
            raise ApiError(401, "登录态失效（已尝试重新登录）")
        raise ApiError(0, "请求失败")

    def _parse(self, r):
        try:
            body = json.loads(r.text)
        except Exception:
            raise ApiError(r.status_code, f"返回不是 JSON: {r.text[:80]}")
        if "D" not in body:
            return body
        raw = crypto.decrypt_payload(self.key, body["D"]).lstrip(b"\x00\r\n\t ")
        data = json.JSONDecoder().raw_decode(raw.decode("utf-8", "replace"))[0]
        if data.get("result") not in (None, "ok"):
            raise ApiError(data.get("code"), data.get("reason") or "未知错误", data)
        return data

    # ---------- 读 ----------
    async def list_records(self, limit=200, max_pages=10, sync_token=None):
        out, token, watermark = [], sync_token, None
        seen = set()
        for _ in range(max_pages):
            extra = {"_limit_raw": str(limit)}
            if token:
                extra["_sync_raw"] = token
            data = await self._call(BASE_PATH, extra)
            res = data.get("data") or {}
            recs = res.get("records") or []
            out.extend(recs)
            watermark = res.get("watermark") or watermark
            new_token = res.get("syncToken")
            if not recs or not new_token or new_token in seen:
                token = new_token or token
                break
            seen.add(new_token)
            token = new_token
        return out, token, watermark

    # ---------- 写 ----------
    async def create(self, content: dict, parent_id: str):
        rec = {"type": "event", "parentId": parent_id, "contentJson": content}
        data = await self._call(BASE_PATH, {"record": self._enc(json.dumps(rec, ensure_ascii=False))}, "POST")
        return (data.get("data") or {}).get("record") or {}

    async def update(self, record: dict):
        rid = record["id"]
        body = {"id": rid, "type": "event", "parentId": record["parentId"],
                "contentJson": record["contentJson"]}
        data = await self._call(f"{BASE_PATH}/{rid}/update",
                                {"record": self._enc(json.dumps(body, ensure_ascii=False)),
                                 "eTag": self._enc(str(record["eTag"]))}, "POST")
        return (data.get("data") or {}).get("record") or {}

    async def delete(self, rid: str, e_tag, record_type="event"):
        return await self._call(f"{BASE_PATH}/{rid}/delete",
                                {"type": self._enc(record_type), "eTag": self._enc(str(e_tag))}, "POST")
