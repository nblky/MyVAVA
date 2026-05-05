from __future__ import annotations

import hashlib
import secrets
import sqlite3
import uuid
from typing import Any

from ..store_shared import (
    AuthResult,
    DEFAULT_EMAIL,
    DEFAULT_PRODUCT_LINE_ID,
    DEFAULT_STATION_SN,
    DEFAULT_USER_ID,
    iso_now,
    stable_uid,
)
from .base import BaseDomainService


class AuthDomainService(BaseDomainService):
    def _normalize_email(self, value: str | None) -> str:
        self = self.store
        return str(value or "").strip().lower()

    def _normalize_mobile(self, value: str | None) -> str:
        self = self.store
        return str(value or "").strip()

    def _canonical_username(
        self,
        username: str | None = "",
        email: str | None = "",
        mobile: str | None = "",
    ) -> str:
        self = self.store
        username = str(username or "").strip()
        email = self._normalize_email(email)
        mobile = self._normalize_mobile(mobile)
        return username or email or mobile

    def _hash_password(self, password: str, salt: str | None = None) -> str:
        self = self.store
        salt = salt or secrets.token_hex(8)
        raw = f"{salt}:{password or ''}".encode("utf-8")
        return f"sha256${salt}${hashlib.sha256(raw).hexdigest()}"

    def _password_md5(self, password: str | None) -> str:
        self = self.store
        return hashlib.md5(str(password or "").encode("utf-8")).hexdigest()

    def _looks_like_md5(self, value: str | None) -> bool:
        self = self.store
        value = str(value or "").strip().lower()
        return len(value) == 32 and all(ch in "0123456789abcdef" for ch in value)

    def _looks_like_test_verify_code(self, value: str | None) -> bool:
        self = self.store
        value = str(value or "").strip()
        return len(value) == 6 and value.isdigit()

    def _normalize_person_name(self, value: str | None) -> str:
        self = self.store
        return str(value or "").strip()

    def _full_name(self, first_name: str | None = "", last_name: str | None = "") -> str:
        self = self.store
        return " ".join(
            part
            for part in (
                self._normalize_person_name(first_name),
                self._normalize_person_name(last_name),
            )
            if part
        )

    def _effective_nickname(
        self,
        *,
        nickname: str | None = "",
        first_name: str | None = "",
        last_name: str | None = "",
        fallback: str | None = "",
    ) -> str:
        self = self.store
        explicit = str(nickname or "").strip()
        if explicit:
            return explicit
        full_name = self._full_name(first_name, last_name)
        return full_name or str(fallback or "").strip()

    def _should_refresh_nickname_from_names(
        self,
        *,
        current_nickname: str | None,
        username: str | None,
        old_first_name: str | None,
        old_last_name: str | None,
    ) -> bool:
        self = self.store
        current = str(current_nickname or "").strip()
        if not current:
            return True
        if current == str(username or "").strip():
            return True
        old_full_name = self._full_name(old_first_name, old_last_name)
        return bool(old_full_name and current == old_full_name)

    def _verify_password(self, password: str | None, user_row: sqlite3.Row) -> bool:
        self = self.store
        password = str(password or "")
        password_md5 = str(user_row["password_md5"] or "").strip().lower()
        if password_md5:
            if self._looks_like_md5(password):
                return secrets.compare_digest(password.lower(), password_md5)
            return secrets.compare_digest(self._password_md5(password), password_md5)
        password_hash = str(user_row["password_hash"] or "")
        if password_hash.startswith("sha256$"):
            _, salt, digest = password_hash.split("$", 2)
            return secrets.compare_digest(
                self._hash_password(password, salt),
                f"sha256${salt}${digest}",
            )
        return secrets.compare_digest(password, password_hash)

    def _row_to_profile(self, user_row: sqlite3.Row) -> dict[str, Any]:
        self = self.store
        username = user_row["username"] or user_row["email"] or DEFAULT_EMAIL
        user_id = user_row["user_id"] or DEFAULT_USER_ID
        first_name = self._normalize_person_name(user_row["first_name"])
        last_name = self._normalize_person_name(user_row["last_name"])
        full_name = self._full_name(first_name, last_name)
        nickname = user_row["nickname"] or full_name or username
        avatar = user_row["avatar"] or ""
        uid = stable_uid(user_id)
        return {
            "address": "",
            "avatar": avatar,
            "birthday": "",
            "city": "",
            "country": "",
            "email": user_row["email"] or username,
            "firstName": first_name,
            "first_name": first_name,
            "headPortrait": avatar,
            "industry": "",
            "lastName": last_name,
            "last_name": last_name,
            "mobile": user_row["mobile"] or "",
            "name": full_name or nickname,
            "nickname": nickname,
            "productLineId": DEFAULT_PRODUCT_LINE_ID,
            "province": "",
            "sex": 0,
            "tenantId": "",
            "uid": uid,
            "userId": user_id,
            "userid": user_id,
            "username": username,
        }

    def _row_to_auth_result(self, user_row: sqlite3.Row, auth_row: sqlite3.Row) -> AuthResult:
        self = self.store
        profile = self._row_to_profile(user_row)
        return AuthResult(
            access_token=auth_row["access_token"],
            refresh_token=auth_row["refresh_token"],
            expires_in=auth_row["expires_in"],
            token_type=auth_row["token_type"],
            scope=auth_row["scope"],
            userid=profile["userid"],
            username=profile["username"],
            nickname=profile["nickname"],
            avatar=profile["avatar"],
            first_name=profile["firstName"],
            last_name=profile["lastName"],
        )

    def _state_user_from_row(self, row: sqlite3.Row | None) -> dict[str, Any]:
        self = self.store
        if not row:
            return {
                "userid": DEFAULT_USER_ID,
                "userId": DEFAULT_USER_ID,
                "uid": stable_uid(DEFAULT_USER_ID),
                "username": DEFAULT_EMAIL,
                "nickname": "Local VAVA",
                "avatar": "",
                "headPortrait": "",
                "email": DEFAULT_EMAIL,
                "firstName": "",
                "first_name": "",
                "lastName": "",
                "last_name": "",
                "mobile": "",
                "name": "Local VAVA",
                "address": "",
                "birthday": "",
                "city": "",
                "country": "",
                "industry": "",
                "productLineId": DEFAULT_PRODUCT_LINE_ID,
                "province": "",
                "sex": 0,
                "tenantId": "",
            }
        email = row["email"] or ""
        username = row["username"] or email or DEFAULT_EMAIL
        user_id = row["user_id"] or DEFAULT_USER_ID
        first_name = self._normalize_person_name(row["first_name"])
        last_name = self._normalize_person_name(row["last_name"])
        full_name = self._full_name(first_name, last_name)
        nickname = row["nickname"] or full_name or username
        avatar = row["avatar"] or ""
        return {
            "userid": user_id,
            "userId": user_id,
            "uid": stable_uid(user_id),
            "username": username,
            "nickname": nickname,
            "avatar": avatar,
            "headPortrait": avatar,
            "email": email or username,
            "firstName": first_name,
            "first_name": first_name,
            "lastName": last_name,
            "last_name": last_name,
            "mobile": row["mobile"] or "",
            "name": full_name or nickname,
            "address": "",
            "birthday": "",
            "city": "",
            "country": "",
            "industry": "",
            "productLineId": DEFAULT_PRODUCT_LINE_ID,
            "province": "",
            "sex": 0,
            "tenantId": "",
        }

    def _state_auth_from_row(self, row: sqlite3.Row | None) -> dict[str, Any]:
        self = self.store
        if not row:
            return {}
        return {
            "access_token": row["access_token"],
            "refresh_token": row["refresh_token"],
            "expires_in": row["expires_in"],
            "token_type": row["token_type"],
            "scope": row["scope"],
        }

    def _find_user(
        self,
        conn: sqlite3.Connection,
        identifier: str | None = "",
        user_id: str | None = "",
    ):
        self = self.store
        if user_id:
            return conn.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (str(user_id),),
            ).fetchone()
        identifier = str(identifier or "").strip()
        if not identifier:
            return None
        return conn.execute(
            """
            SELECT * FROM users
            WHERE username = ?
               OR email = ?
               OR mobile = ?
            LIMIT 1
            """,
            (identifier, self._normalize_email(identifier), self._normalize_mobile(identifier)),
        ).fetchone()

    def _find_auth_by_access_token(self, conn: sqlite3.Connection, access_token: str | None):
        self = self.store
        token = str(access_token or "").strip()
        if not token:
            return None
        return conn.execute(
            "SELECT * FROM auth_tokens WHERE access_token = ?",
            (token,),
        ).fetchone()

    def _find_auth_by_refresh_token(self, conn: sqlite3.Connection, refresh_token: str | None):
        self = self.store
        token = str(refresh_token or "").strip()
        if not token:
            return None
        return conn.execute(
            "SELECT * FROM auth_tokens WHERE refresh_token = ?",
            (token,),
        ).fetchone()

    def _latest_auth_for_user(self, conn: sqlite3.Connection, user_id: str):
        self = self.store
        return conn.execute(
            """
            SELECT * FROM auth_tokens
            WHERE user_id = ?
            ORDER BY updated_at DESC, rowid DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()

    def _issue_tokens(self, conn: sqlite3.Connection, user_id: str) -> sqlite3.Row:
        self = self.store
        access_token = f"access-{secrets.token_hex(16)}"
        refresh_token = f"refresh-{secrets.token_hex(16)}"
        now = iso_now()
        conn.execute(
            """
            INSERT INTO auth_tokens (
                access_token, refresh_token, user_id, expires_in, token_type, scope, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                access_token,
                refresh_token,
                user_id,
                "31536000",
                "bearer",
                "all",
                now,
                now,
            ),
        )
        return self._find_auth_by_access_token(conn, access_token)

    def _set_active_session_unlocked(
        self,
        conn: sqlite3.Connection,
        user_row: sqlite3.Row,
        auth_row: sqlite3.Row,
    ) -> None:
        self = self.store
        state = self._load_state_unlocked(conn)
        state["user"] = self._state_user_from_row(user_row)
        state["auth"] = self._state_auth_from_row(auth_row)
        self._save_state_unlocked(conn, state)

    def _latest_auth_payload(
        self,
        conn: sqlite3.Connection,
    ) -> tuple[AuthResult, sqlite3.Row, sqlite3.Row]:
        self = self.store
        user_row = conn.execute(
            "SELECT * FROM users ORDER BY updated_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        if not user_row:
            raise ValueError("no users in database")
        auth_row = self._latest_auth_for_user(conn, user_row["user_id"])
        if not auth_row:
            auth_row = self._issue_tokens(conn, user_row["user_id"])
            conn.commit()
        return self._row_to_auth_result(user_row, auth_row), user_row, auth_row

    def ensure_default_user(self, conn: sqlite3.Connection | None = None) -> None:
        self = self.store
        close_conn = False
        if conn is None:
            conn = self._connect()
            close_conn = True
        try:
            row = self._find_user(conn, user_id=DEFAULT_USER_ID) or self._find_user(
                conn, identifier=DEFAULT_EMAIL
            )
            if row:
                if not str(row["password_md5"] or "").strip():
                    conn.execute(
                        """
                        UPDATE users
                        SET password_md5 = ?, updated_at = ?
                        WHERE user_id = ?
                        """,
                        (
                            self._password_md5(self.settings.default_password),
                            iso_now(),
                            row["user_id"],
                        ),
                    )
                    conn.commit()
                    row = self._find_user(conn, user_id=row["user_id"])
            else:
                now = iso_now()
                conn.execute(
                    """
                    INSERT INTO users (
                        user_id, username, email, mobile, password_hash, password_md5,
                        nickname, first_name, last_name, avatar, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        DEFAULT_USER_ID,
                        DEFAULT_EMAIL,
                        DEFAULT_EMAIL,
                        None,
                        self._hash_password(self.settings.default_password),
                        self._password_md5(self.settings.default_password),
                        "Local VAVA",
                        "",
                        "",
                        "",
                        now,
                        now,
                    ),
                )
                conn.commit()
                row = self._find_user(conn, user_id=DEFAULT_USER_ID)

            auth_row = self._latest_auth_for_user(conn, row["user_id"])
            if not auth_row:
                auth_row = self._issue_tokens(conn, row["user_id"])
                conn.commit()
            self._set_active_session_unlocked(conn, row, auth_row)
        finally:
            if close_conn:
                conn.close()

    def login(self, identifier: str, password: str | None):
        self = self.store
        with self.lock, self._connect() as conn:
            user_row = self._find_user(conn, identifier=identifier)
            if not user_row or not self._verify_password(password, user_row):
                raise ValueError("username or password error")
            auth_row = self._issue_tokens(conn, user_row["user_id"])
            conn.commit()
            self._set_active_session_unlocked(conn, user_row, auth_row)
            return self._row_to_auth_result(user_row, auth_row)

    def login_payload_for_station(self, station_sn: str | None = "") -> dict[str, Any]:
        self = self.store
        with self.lock, self._connect() as conn:
            station_value = str(station_sn or DEFAULT_STATION_SN)
            owner_binding = conn.execute(
                """
                SELECT user_id
                FROM user_station_bindings
                WHERE station_sn = ? AND bind_state = 'bound'
                ORDER BY CASE WHEN lower(role) = 'owner' THEN 0 ELSE 1 END, updated_at DESC
                LIMIT 1
                """,
                (station_value,),
            ).fetchone()
            owner_user_id = str((owner_binding["user_id"] if owner_binding else "") or "").strip()
            user_row = self._find_user(conn, user_id=owner_user_id) if owner_user_id else None
            auth_row = self._latest_auth_for_user(conn, owner_user_id) if owner_user_id else None
            if not user_row:
                result, user_row, auth_row = self._latest_auth_payload(conn)
            else:
                if not auth_row:
                    auth_row = self._issue_tokens(conn, owner_user_id)
                    conn.commit()
                result = self._row_to_auth_result(user_row, auth_row)
            self._set_active_session_unlocked(conn, user_row, auth_row)
            payload = result.as_login_payload()
            payload.update(self.station_did_payload(station_value, conn=conn))
            self.remember_station_access_token(station_value, payload.get("access_token", ""))
            self.update_station_session(station_value, True)
            return payload

    def client_credentials_payload(self) -> dict[str, Any]:
        self = self.store
        with self.lock, self._connect() as conn:
            result, user_row, auth_row = self._latest_auth_payload(conn)
            self._set_active_session_unlocked(conn, user_row, auth_row)
            return {
                "access_token": result.access_token,
                "expires_in": result.expires_in,
                "token_type": result.token_type,
                "scope": result.scope,
            }

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        self = self.store
        with self.lock, self._connect() as conn:
            auth_row = self._find_auth_by_refresh_token(conn, refresh_token)
            if not auth_row:
                raise ValueError("refresh token mismatch")
            user_row = self._find_user(conn, user_id=auth_row["user_id"])
            self._set_active_session_unlocked(conn, user_row, auth_row)
            return {
                "access_token": auth_row["access_token"],
                "refresh_token": auth_row["refresh_token"],
                "expires_in": auth_row["expires_in"],
            }

    def profile(self, access_token: str) -> dict[str, Any]:
        self = self.store
        with self.lock, self._connect() as conn:
            auth_row = self._find_auth_by_access_token(conn, access_token)
            if not auth_row:
                return self._row_to_profile(self._find_user(conn, user_id=DEFAULT_USER_ID))
            user_row = self._find_user(conn, user_id=auth_row["user_id"])
            self._set_active_session_unlocked(conn, user_row, auth_row)
            return self._row_to_profile(user_row)

    def update_user_profile(
        self,
        *,
        access_token: str = "",
        identifier: str = "",
        nickname: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        avatar: str | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        self = self.store
        with self.lock, self._connect() as conn:
            auth_row = self._find_auth_by_access_token(conn, access_token)
            user_row = None
            if auth_row:
                user_row = self._find_user(conn, user_id=auth_row["user_id"])
            if not user_row and identifier:
                user_row = self._find_user(conn, identifier=identifier)
            if not user_row:
                return None, "user not found"
            next_first_name = (
                self._normalize_person_name(user_row["first_name"])
                if first_name is None
                else self._normalize_person_name(first_name)
            )
            next_last_name = (
                self._normalize_person_name(user_row["last_name"])
                if last_name is None
                else self._normalize_person_name(last_name)
            )
            if nickname is None:
                next_nickname = str(user_row["nickname"] or "").strip()
                if first_name is not None or last_name is not None:
                    if self._should_refresh_nickname_from_names(
                        current_nickname=user_row["nickname"],
                        username=user_row["username"],
                        old_first_name=user_row["first_name"],
                        old_last_name=user_row["last_name"],
                    ):
                        next_nickname = self._effective_nickname(
                            first_name=next_first_name,
                            last_name=next_last_name,
                            fallback=user_row["username"],
                        )
            else:
                next_nickname = self._effective_nickname(
                    nickname=nickname,
                    first_name=next_first_name,
                    last_name=next_last_name,
                    fallback=user_row["username"],
                )
            next_avatar = user_row["avatar"] if avatar is None else str(avatar or "").strip()
            conn.execute(
                """
                UPDATE users
                SET nickname = ?, first_name = ?, last_name = ?, avatar = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (
                    next_nickname or user_row["username"],
                    next_first_name,
                    next_last_name,
                    next_avatar,
                    iso_now(),
                    user_row["user_id"],
                ),
            )
            conn.commit()
            user_row = self._find_user(conn, user_id=user_row["user_id"])
            if auth_row:
                self._set_active_session_unlocked(conn, user_row, auth_row)
            return self._state_user_from_row(user_row), None

    def issue_verification_code(self, target: str, purpose: str, channel: str) -> None:
        self = self.store
        with self._connect() as conn:
            now = iso_now()
            conn.execute(
                """
                INSERT INTO verification_codes (target, purpose, channel, code, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (target, purpose, channel, self.settings.default_verify_code, now, now),
            )
            conn.commit()
        self.add_event(
            "user.verification_code",
            {"target": str(target or "").strip(), "purpose": purpose, "channel": channel},
        )

    def verify_code(self, target: str, purpose: str, code: str | None) -> bool:
        self = self.store
        code = str(code or "").strip()
        target = str(target or "").strip()
        if not target or not code:
            return True
        if self.settings.allow_any_6digit_verify_code and self._looks_like_test_verify_code(code):
            return True
        if code == self.settings.default_verify_code:
            return True
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT code FROM verification_codes
                WHERE target = ? AND purpose = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (target, purpose),
            ).fetchone()
            if not row:
                return False
            return secrets.compare_digest(code, row["code"])

    def register_email(
        self,
        *,
        email: str,
        password: str,
        nickname: str = "",
        first_name: str = "",
        last_name: str = "",
        avatar: str = "",
        verify_code: str = "",
    ) -> dict[str, Any]:
        self = self.store
        email = self._normalize_email(email)
        if not email:
            raise ValueError("username required")
        if not self.verify_code(email, "register_email", verify_code):
            raise ValueError("verify code error")
        with self.lock, self._connect() as conn:
            existing = self._find_user(conn, identifier=email)
            if existing:
                if password and self._verify_password(password, existing):
                    auth_row = self._issue_tokens(conn, existing["user_id"])
                    conn.commit()
                    self._set_active_session_unlocked(conn, existing, auth_row)
                    return self._row_to_auth_result(existing, auth_row).as_login_payload()
                raise ValueError("user already exists")
            user_id = f"user-{uuid.uuid4().hex[:12]}"
            now = iso_now()
            stored_md5 = (
                password.lower() if self._looks_like_md5(password) else self._password_md5(password)
            )
            effective_nickname = self._effective_nickname(
                nickname=nickname,
                first_name=first_name,
                last_name=last_name,
                fallback=email,
            )
            conn.execute(
                """
                INSERT INTO users (
                    user_id, username, email, mobile, password_hash, password_md5,
                    nickname, first_name, last_name, avatar, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    email,
                    email,
                    None,
                    self._hash_password(password),
                    stored_md5,
                    effective_nickname,
                    self._normalize_person_name(first_name),
                    self._normalize_person_name(last_name),
                    avatar or "",
                    now,
                    now,
                ),
            )
            auth_row = self._issue_tokens(conn, user_id)
            user_row = self._find_user(conn, user_id=user_id)
            conn.commit()
            self._set_active_session_unlocked(conn, user_row, auth_row)
            return self._row_to_auth_result(user_row, auth_row).as_login_payload()

    def register_user(
        self,
        *,
        username: str = "",
        email: str = "",
        mobile: str = "",
        password: str = "",
        nickname: str = "",
        first_name: str = "",
        last_name: str = "",
        avatar: str = "",
        verify_code: str = "",
        verify_purpose: str = "",
    ) -> tuple[dict[str, Any] | None, str | None]:
        self = self.store
        with self.lock, self._connect() as conn:
            email = self._normalize_email(email)
            mobile = self._normalize_mobile(mobile)
            username = self._canonical_username(username, email, mobile)
            target = email or mobile or username
            if verify_purpose and not self.verify_code(target, verify_purpose, verify_code):
                return None, "verify code error"
            if not username:
                return None, "username required"
            existing = self._find_user(conn, identifier=username)
            if existing:
                if password and self._verify_password(password, existing):
                    auth_row = self._issue_tokens(conn, existing["user_id"])
                    conn.commit()
                    self._set_active_session_unlocked(conn, existing, auth_row)
                    return self._row_to_auth_result(existing, auth_row).as_login_payload(), None
                return None, "user already exists"
            user_id = f"user-{uuid.uuid4().hex[:12]}"
            now = iso_now()
            effective_password = password or self.settings.default_password
            stored_md5 = (
                effective_password.lower()
                if self._looks_like_md5(effective_password)
                else self._password_md5(effective_password)
            )
            effective_nickname = self._effective_nickname(
                nickname=nickname,
                first_name=first_name,
                last_name=last_name,
                fallback=username,
            )
            conn.execute(
                """
                INSERT INTO users (
                    user_id, username, email, mobile, password_hash, password_md5,
                    nickname, first_name, last_name, avatar, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    username,
                    email or None,
                    mobile or None,
                    self._hash_password(effective_password),
                    stored_md5,
                    effective_nickname,
                    self._normalize_person_name(first_name),
                    self._normalize_person_name(last_name),
                    avatar or "",
                    now,
                    now,
                ),
            )
            auth_row = self._issue_tokens(conn, user_id)
            user_row = self._find_user(conn, user_id=user_id)
            conn.commit()
            self._set_active_session_unlocked(conn, user_row, auth_row)
            return self._row_to_auth_result(user_row, auth_row).as_login_payload(), None

    def reset_password(
        self,
        *,
        identifier: str = "",
        new_password: str = "",
        verify_code: str = "",
        verify_purpose: str = "reset_password",
    ) -> tuple[bool, str | None]:
        self = self.store
        with self.lock, self._connect() as conn:
            user_row = self._find_user(conn, identifier=identifier)
            if not user_row:
                return False, "user not found"
            target = user_row["email"] or user_row["mobile"] or user_row["username"]
            if not self.verify_code(target, verify_purpose, verify_code):
                return False, "verify code error"
            effective_password = new_password or self.settings.default_password
            conn.execute(
                "UPDATE users SET password_hash = ?, password_md5 = ?, updated_at = ? WHERE user_id = ?",
                (
                    self._hash_password(effective_password),
                    (
                        effective_password.lower()
                        if self._looks_like_md5(effective_password)
                        else self._password_md5(effective_password)
                    ),
                    iso_now(),
                    user_row["user_id"],
                ),
            )
            conn.commit()
            return True, None

    def change_password(
        self,
        *,
        access_token: str = "",
        identifier: str = "",
        old_password: str = "",
        new_password: str = "",
    ) -> tuple[bool, str | None]:
        self = self.store
        with self.lock, self._connect() as conn:
            auth_row = self._find_auth_by_access_token(conn, access_token)
            user_row = None
            if auth_row:
                user_row = self._find_user(conn, user_id=auth_row["user_id"])
            if not user_row and identifier:
                user_row = self._find_user(conn, identifier=identifier)
            if not user_row:
                return False, "user not found"
            if old_password and not self._verify_password(old_password, user_row):
                return False, "old password error"
            effective_password = new_password or self.settings.default_password
            conn.execute(
                "UPDATE users SET password_hash = ?, password_md5 = ?, updated_at = ? WHERE user_id = ?",
                (
                    self._hash_password(effective_password),
                    (
                        effective_password.lower()
                        if self._looks_like_md5(effective_password)
                        else self._password_md5(effective_password)
                    ),
                    iso_now(),
                    user_row["user_id"],
                ),
            )
            conn.commit()
            if auth_row:
                self._set_active_session_unlocked(conn, user_row, auth_row)
            return True, None
