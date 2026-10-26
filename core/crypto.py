# -*- coding: utf-8 -*-
"""签名与加解密。

规则来自 reverse 结果（见 mi-calendar/docs/01-协议说明.md）：
  signature = Base64(SHA1("METHOD&path&k1=v1&…&ssecurity"))
  游标/密文 = "1:" + b64url(iv) + ":" + b64url(AES-CBC-PKCS5(明文))
"""
import base64
import hashlib
import os

from Crypto.Cipher import AES


def b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def b64u_decode(text: str) -> bytes:
    text = text.replace("-", "+").replace("_", "/")
    return base64.b64decode(text + "=" * (-len(text) % 4))


def make_signature(method: str, path: str, params: dict, ssecurity: str) -> str:
    """按服务端要求拼接签名串。"""
    items = [method.upper(), path]
    items += [f"{k}={params[k]}" for k in sorted(params)]
    items.append(ssecurity)
    return base64.b64encode(hashlib.sha1("&".join(items).encode()).digest()).decode()


def encrypt_token(key: bytes, plaintext: bytes) -> str:
    """把明文（数字、JSON、syncToken）包成服务端认识的游标 token。"""
    iv = os.urandom(16)
    pad = 16 - len(plaintext) % 16
    cipher = AES.new(key, AES.MODE_CBC, iv)
    ct = cipher.encrypt(plaintext + bytes([pad]) * pad)
    return f"1:{b64u_encode(iv)}:{b64u_encode(ct)}"


def decrypt_payload(key: bytes, token: str) -> bytes:
    """解密响应里的 D 字段。"""
    parts = token.split(":")
    if len(parts) >= 3:
        iv = b64u_decode(parts[1])
    else:
        iv = b"0102030405060708"
    raw = b64u_decode(parts[-1])
    return AES.new(key, AES.MODE_CBC, iv).decrypt(raw)


def aes_key(ssecurity: str) -> bytes:
    return base64.b64decode(ssecurity)
