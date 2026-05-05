from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


DEFAULT_DID = "F02F-9A80-VAHS0031111A"
DEFAULT_DID_TOKEN = "VAVAHS003AUTH2019"
DEFAULT_INIT = "EBGAEIBIKHJJGFJKEOGCFAEPHPMAHONDGJFPBKCPAJJMLFKBDBAGCJPBGOLKIKLKAJMJKFDOOFMOBECEJIMM"
DEFAULT_CRC = "LOCAL-VAVA-CRC"
DEFAULT_STATION_SN = "64XI7DE3Q2115F3BBF02F9A80"
DEFAULT_STATION_NAME = "VAVA Base"
DEFAULT_CAMERA_SN = "64XIJEE3QF0EAC5DE2647496E"
DEFAULT_CAMERA_NAME = "Front Door"
DEFAULT_CHANNEL = 0
DEFAULT_SESSION_KEY = "VAVA_TEST_AUTH_KEY_2017"
DEFAULT_VISIBLE_NOTICE_TYPE = 2
VISIBLE_NOTICE_TYPES = {2, 4, 5, 6, 7, 8}
DEFAULT_USER_ID = "local-user-1"
DEFAULT_EMAIL = "local@vava.invalid"
DEFAULT_PRODUCT_LINE_ID = "4f975dc1a43d4117a6f3eb83b2cbc778"


def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def deep_get(obj: Any, key: str, default=None):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for variant in (key.lower(), key.upper()):
            if variant in obj:
                return obj[variant]
    return default


def stable_uid(value: str) -> int:
    digest = hashlib.md5((value or DEFAULT_USER_ID).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


@dataclass
class AuthResult:
    access_token: str
    refresh_token: str
    expires_in: str
    token_type: str
    scope: str
    userid: str
    username: str
    nickname: str
    avatar: str
    first_name: str = ""
    last_name: str = ""

    def as_login_payload(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "avatar": self.avatar,
            "expires_in": self.expires_in,
            "firstName": self.first_name,
            "first_name": self.first_name,
            "lastName": self.last_name,
            "last_name": self.last_name,
            "nickname": self.nickname,
            "refresh_token": self.refresh_token,
            "scope": self.scope,
            "token_type": self.token_type,
            "userid": self.userid,
            "username": self.username,
        }
