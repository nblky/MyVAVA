from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ..store import get_store

from .deps import _ok, _bearer_token


router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    auth_type: str | None = None
    grant_type: str | None = None
    sn: str | None = None
    username: str | None = None
    account: str | None = None
    email: str | None = None
    mobile: str | None = None
    phone: str | None = None
    password: str | None = None
    passwd: str | None = None
    pwd: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


class EmailVerifyRequest(BaseModel):
    email: str | None = None
    username: str | None = None
    mobile: str | None = None
    phone: str | None = None
    verifyCode: str | None = None
    code: str | None = None
    emailCode: str | None = None
    smsCode: str | None = None


class EmailRegisterRequest(BaseModel):
    username: str | None = None
    email: str | None = None
    mobile: str | None = None
    phone: str | None = None
    password: str | None = None
    passwd: str | None = None
    pwd: str | None = None
    nickname: str | None = None
    name: str | None = None
    firstName: str | None = None
    lastName: str | None = None
    firstname: str | None = None
    lastname: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    avatar: str | None = None
    verifyCode: str | None = None
    code: str | None = None
    emailCode: str | None = None
    smsCode: str | None = None


class ResetPasswordRequest(BaseModel):
    username: str | None = None
    email: str | None = None
    mobile: str | None = None
    phone: str | None = None
    newPassword: str | None = None
    password: str | None = None
    passwd: str | None = None
    pwd: str | None = None
    verifyCode: str | None = None
    code: str | None = None
    emailCode: str | None = None
    smsCode: str | None = None


class ChangePasswordRequest(BaseModel):
    username: str | None = None
    email: str | None = None
    mobile: str | None = None
    phone: str | None = None
    oldPassword: str | None = None
    password: str | None = None
    newPassword: str | None = None
    confirmPassword: str | None = None


def _error(code: int, message: str):
    raise HTTPException(status_code=200, detail={"stateCode": code, "stateMsg": message, "data": {}})


def _station_bind_error_code(error: str) -> int:
    normalized = str(error or "").strip().lower()
    if normalized == "station has been added by someone":
        return 216004
    if normalized == "station has been added to your account already":
        return 216003
    return 400


def _identity(payload) -> str:
    return (
        getattr(payload, "username", None)
        or getattr(payload, "email", None)
        or getattr(payload, "mobile", None)
        or getattr(payload, "phone", None)
        or ""
    )


def _password(payload) -> str:
    return (
        getattr(payload, "password", None)
        or getattr(payload, "passwd", None)
        or getattr(payload, "pwd", None)
        or ""
    )


def _verify_code(payload) -> str:
    return (
        getattr(payload, "verifyCode", None)
        or getattr(payload, "code", None)
        or getattr(payload, "emailCode", None)
        or getattr(payload, "smsCode", None)
        or ""
    )


def _split_display_name(value: str | None) -> tuple[str, str]:
    parts = [part for part in str(value or "").strip().split() if part]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _name_parts(payload) -> tuple[str, str]:
    first_name = (
        getattr(payload, "firstName", None)
        or getattr(payload, "firstname", None)
        or getattr(payload, "first_name", None)
        or ""
    )
    last_name = (
        getattr(payload, "lastName", None)
        or getattr(payload, "lastname", None)
        or getattr(payload, "last_name", None)
        or ""
    )
    if first_name or last_name:
        return str(first_name or "").strip(), str(last_name or "").strip()
    return _split_display_name(getattr(payload, "name", None))


@router.post("/oauth/login")
def oauth_login(payload: LoginRequest):
    store = get_store()
    if payload.grant_type == "client_credentials":
        return _ok(store.client_credentials_payload())
    if payload.auth_type == "sn_password" or payload.sn:
        return _ok(store.login_payload_for_station(payload.sn))
    identifier = payload.username or payload.account or payload.email or payload.mobile or payload.phone
    password = payload.password or payload.passwd or payload.pwd
    if identifier:
        try:
            return _ok(store.login(identifier, password).as_login_payload())
        except ValueError as exc:
            _error(401, str(exc))
    return _ok(store.client_credentials_payload())


@router.post("/oauth/refresh-token")
def oauth_refresh(payload: RefreshRequest):
    store = get_store()
    try:
        return _ok(store.refresh(payload.refresh_token or ""))
    except ValueError as exc:
        _error(401, str(exc))


@router.post("/oauth/logout")
def oauth_logout():
    return _ok({})


@router.post("/users/detail")
def users_detail(authorization: str | None = Header(default=None), access_token: str | None = None):
    token = access_token or _bearer_token(authorization)
    return _ok(get_store().profile(token or ""))


@router.post("/users/send-register-email-verify-code")
def send_register_email_verify_code(payload: EmailVerifyRequest):
    store = get_store()
    email = payload.email or payload.username or ""
    store.issue_verification_code(email, "register_email", "email")
    code = store.settings.default_verify_code
    return _ok({"verifyCode": code, "code": code, "emailCode": code})


@router.post("/users/send-register-sms-verify-code")
def send_register_sms_verify_code(payload: EmailVerifyRequest):
    mobile = payload.mobile or payload.phone or ""
    get_store().issue_verification_code(mobile, "register_mobile", "sms")
    code = get_store().settings.default_verify_code
    return _ok({"verifyCode": code, "code": code, "smsCode": code})


@router.post("/users/email-verify")
def email_verify(payload: EmailVerifyRequest):
    store = get_store()
    email = payload.email or payload.username or ""
    code = _verify_code(payload)
    if not store.verify_code(email, "register_email", code):
        _error(400, "verify code error")
    return _ok({})


@router.post("/users/mobile-verify")
def mobile_verify(payload: EmailVerifyRequest):
    mobile = payload.mobile or payload.phone or ""
    if not get_store().verify_code(mobile, "register_mobile", _verify_code(payload)):
        _error(400, "verify code error")
    return _ok({})


@router.post("/users/email-password-register")
def email_password_register(payload: EmailRegisterRequest):
    store = get_store()
    first_name, last_name = _name_parts(payload)
    try:
        return _ok(
            store.register_email(
                email=payload.email or payload.username or "",
                password=payload.password or payload.passwd or payload.pwd or "",
                nickname=payload.nickname or payload.name or "",
                first_name=first_name,
                last_name=last_name,
                avatar=payload.avatar or "",
                verify_code=payload.verifyCode or payload.code or payload.emailCode or "",
            )
        )
    except ValueError as exc:
        _error(400, str(exc))


@router.post("/users/mobile-password-register")
def mobile_password_register(payload: EmailRegisterRequest):
    first_name, last_name = _name_parts(payload)
    result, error = get_store().register_user(
        username=payload.username or payload.mobile or payload.phone or "",
        mobile=payload.mobile or payload.phone or "",
        password=_password(payload),
        nickname=payload.nickname or payload.name or "",
        first_name=first_name,
        last_name=last_name,
        avatar=payload.avatar or "",
        verify_code=_verify_code(payload),
        verify_purpose="register_mobile",
    )
    if error:
        _error(400, error)
    return _ok(result)


@router.post("/users/send-reset-password-email")
@router.post("/users/forget-password-send-email-code")
@router.post("/users/send-reset-password-email-verify-code")
def send_reset_password_email_verify_code(payload: EmailVerifyRequest):
    email = payload.email or payload.username or ""
    get_store().issue_verification_code(email, "reset_password", "email")
    return _ok({})


@router.post("/users/send-reset-password-sms-verify-code")
def send_reset_password_sms_verify_code(payload: EmailVerifyRequest):
    mobile = payload.mobile or payload.phone or ""
    get_store().issue_verification_code(mobile, "reset_password", "sms")
    return _ok({})


@router.post("/users/reset-password")
@router.post("/users/update-password-by-email-code")
def reset_password(payload: ResetPasswordRequest):
    ok, error = get_store().reset_password(
        identifier=_identity(payload),
        new_password=payload.newPassword or _password(payload),
        verify_code=_verify_code(payload),
        verify_purpose="reset_password",
    )
    if error:
        _error(400, error)
    return _ok({"success": ok})


@router.post("/users/change-password")
def change_password(
    payload: ChangePasswordRequest,
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    token = access_token or _bearer_token(authorization)
    ok, error = get_store().change_password(
        access_token=token or "",
        identifier=_identity(payload),
        old_password=payload.oldPassword or payload.password or "",
        new_password=payload.newPassword or payload.confirmPassword or "",
    )
    if error:
        _error(400, error)
    return _ok({"success": ok})


class UserUpdateRequest(BaseModel):
    username: str | None = None
    email: str | None = None
    mobile: str | None = None
    nickname: str | None = None
    name: str | None = None
    firstName: str | None = None
    lastName: str | None = None
    firstname: str | None = None
    lastname: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    avatar: str | None = None


class StationBindingRequest(BaseModel):
    stationSn: str | None = None
    deviceSn: str | None = None
    sn: str | None = None
    role: str | None = None
    username: str | None = None
    email: str | None = None
    mobile: str | None = None


@router.post("/users/update")
def users_update(
    payload: UserUpdateRequest,
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    token = access_token or _bearer_token(authorization)
    first_name, last_name = _name_parts(payload)
    profile, error = get_store().update_user_profile(
        access_token=token or "",
        identifier=payload.username or payload.email or payload.mobile or "",
        nickname=payload.nickname or payload.name,
        first_name=first_name if (first_name or last_name or payload.firstName is not None or payload.firstname is not None or payload.first_name is not None or payload.name is not None) else None,
        last_name=last_name if (first_name or last_name or payload.lastName is not None or payload.lastname is not None or payload.last_name is not None or payload.name is not None) else None,
        avatar=payload.avatar,
    )
    if error:
        _error(400, error)
    return _ok(profile)


@router.post("/users/station-bind")
def users_station_bind(
    payload: StationBindingRequest,
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    token = access_token or _bearer_token(authorization)
    result, error = get_store().bind_station_to_user(
        station_sn=payload.stationSn or payload.deviceSn or payload.sn or "",
        access_token=token or "",
        identifier=payload.username or payload.email or payload.mobile or "",
        role=payload.role or "owner",
    )
    if error:
        _error(_station_bind_error_code(error), error)
    return _ok(result)


@router.post("/users/station-unbind")
def users_station_unbind(
    payload: StationBindingRequest,
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    token = access_token or _bearer_token(authorization)
    result, error = get_store().unbind_station_from_user(
        station_sn=payload.stationSn or payload.deviceSn or payload.sn or "",
        access_token=token or "",
        identifier=payload.username or payload.email or payload.mobile or "",
    )
    if error:
        _error(400, error)
    return _ok(result)


@router.post("/users/station-list")
def users_station_list(
    payload: StationBindingRequest,
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    token = access_token or _bearer_token(authorization)
    result, error = get_store().station_bindings_for_user(
        access_token=token or "",
        identifier=payload.username or payload.email or payload.mobile or "",
    )
    if error:
        _error(400, error)
    return _ok({"stationList": result})


@router.post("/users/collectAppVersion")
def users_collect_app_version():
    return _ok({})
