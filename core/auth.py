from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, asdict
from urllib.parse import urlsplit

from .util import (
    HttpError,
    LauncherError,
    http_json,
    http_request,
    post_form,
    post_json,
)

LIVE_CLIENT_ID = "00000000402b5328"
LIVE_DEVICE_CODE_URL = "https://login.live.com/oauth20_connect.srf"
LIVE_TOKEN_URL = "https://login.live.com/oauth20_token.srf"
LIVE_SCOPE = "service::user.auth.xboxlive.com::MBI_SSL"
V2_DEVICE_CODE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
V2_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
V2_SCOPE = "XboxLive.signin offline_access"
XBL_USER_AUTH = "https://user.auth.xboxlive.com/user/authenticate"
XBL_XSTS = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_PROFILE = "https://api.minecraftservices.com/minecraft/profile"

XSTS_MESSAGES = {
    2148916233: "该微软账号还没有 Xbox 档案。请先访问 https://www.minecraft.net 登录一次，然后再回来。",
    2148916235: "该账号所在地区不支持 Xbox Live。",
    2148916236: "需要完成成人身份验证才能登录。",
    2148916237: "需要完成成人身份验证才能登录。",
    2148916238: "这是未成年账号，需要先加入 Microsoft 家庭组。",
}


@dataclass
class Account:
    type: str
    name: str
    uuid: str
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0
    xuid: str = ""
    server: str = ""
    client_token: str = ""

    @property
    def is_microsoft(self) -> bool:
        return self.type == "microsoft"

    @property
    def is_external(self) -> bool:
        return self.type == "external"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Account | None":
        if not isinstance(data, dict) or not data.get("name"):
            return None
        return cls(
            type=str(data.get("type") or "offline"),
            name=str(data.get("name")),
            uuid=str(data.get("uuid") or ""),
            access_token=str(data.get("access_token") or ""),
            refresh_token=str(data.get("refresh_token") or ""),
            expires_at=float(data.get("expires_at") or 0),
            xuid=str(data.get("xuid") or ""),
            server=str(data.get("server") or ""),
            client_token=str(data.get("client_token") or ""),
        )

    def display(self) -> str:
        label = ("正版" if self.is_microsoft
                 else "外置" if self.is_external else "离线")
        return f"{self.name} · {label}"


def validate_offline_name(name: str) -> str:
    name = (name or "").strip()
    if not re.match(r"^[A-Za-z0-9_]{3,16}$", name):
        raise LauncherError("离线用户名需要 3-16 位，只能使用字母、数字和下划线。")
    return name


def offline_account(name: str) -> Account:
    name = validate_offline_name(name)
    digest = hashlib.md5(f"OfflinePlayer:{name}".encode("utf-8")).digest()
    uid = uuid.UUID(bytes=digest, version=3).hex
    return Account(type="offline", name=name, uuid=uid, access_token="0")


def normalize_auth_server(server: str) -> str:
    server = (server or "").strip().rstrip("/")
    if not server:
        raise LauncherError("请填写外置登录服务器地址。")
    if "://" not in server:
        server = "https://" + server
    parts = urlsplit(server)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise LauncherError("外置登录服务器地址格式不正确。")
    host = (parts.hostname or "").lower()
    if parts.scheme == "http" and host not in ("localhost", "127.0.0.1", "::1"):
        raise LauncherError("外置登录服务器必须使用 HTTPS（本机调试除外）。")
    if host in ("littleskin.cn", "www.littleskin.cn"):
        path = parts.path.rstrip("/")
        if not path or path == "/":
            server = "https://littleskin.cn/api/yggdrasil"
    return server.rstrip("/")


def _external_error(error: HttpError) -> str:
    body = error.json() if isinstance(error, HttpError) else {}
    message = body.get("errorMessage") or body.get("error") or f"HTTP {error.status}"
    return str(message)


def external_account(server: str, username: str, password: str,
                     client_token: str = "") -> Account:
    server = normalize_auth_server(server)
    username = (username or "").strip()
    if not username or not password:
        raise LauncherError("请填写外置账号和密码。")
    client_token = client_token or uuid.uuid4().hex
    payload = {
        "agent": {"name": "Minecraft", "version": 1},
        "username": username,
        "password": password,
        "clientToken": client_token,
        "requestUser": False,
    }
    try:
        data = post_json(f"{server}/authserver/authenticate", payload, timeout=40)
    except HttpError as e:
        raise LauncherError(f"外置登录失败：{_external_error(e)}") from None
    token = data.get("accessToken") or ""
    profile = data.get("selectedProfile")
    if not profile:
        profiles = data.get("availableProfiles") or []
        profile = profiles[0] if profiles else None
    if not token or not profile:
        raise LauncherError(
            "外置登录成功但没有游戏角色，请先在皮肤站创建角色。")
    name = profile.get("name") or username
    uid = profile.get("id") or ""
    if not uid:
        raise LauncherError("外置登录返回的角色 UUID 无效。")
    return Account(
        type="external",
        name=str(name),
        uuid=str(uid),
        access_token=token,
        client_token=str(data.get("clientToken") or client_token),
        server=server,
    )


def validate_external(account: Account) -> bool:
    if not account.server or not account.access_token:
        return False
    payload = {
        "accessToken": account.access_token,
        "clientToken": account.client_token,
    }
    http_request(
        f"{account.server.rstrip('/')}/authserver/validate",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        timeout=30,
        retries=0,
    )
    return True


def refresh_external(account: Account) -> Account:
    if not account.server:
        raise LauncherError("外置账户缺少服务器地址，请重新登录。")
    payload = {
        "accessToken": account.access_token,
        "clientToken": account.client_token,
        "requestUser": False,
    }
    try:
        data = post_json(f"{account.server.rstrip('/')}/authserver/refresh",
                         payload, timeout=40, retries=0)
    except HttpError as e:
        raise LauncherError(f"外置登录刷新失败：{_external_error(e)}") from None
    token = data.get("accessToken") or account.access_token
    profile = data.get("selectedProfile") or {}
    return Account(
        type="external",
        name=str(profile.get("name") or account.name),
        uuid=str(profile.get("id") or account.uuid),
        access_token=token,
        client_token=str(data.get("clientToken") or account.client_token),
        server=account.server,
    )


def start_device_code(client_id: str) -> dict:
    if not client_id:
        raise LauncherError("未配置微软 Client ID，请到设置里填写。")
    if client_id == LIVE_CLIENT_ID:
        return post_form(LIVE_DEVICE_CODE_URL, {
            "client_id": client_id,
            "scope": LIVE_SCOPE,
            "response_type": "device_code",
        }, timeout=45)
    return post_form(V2_DEVICE_CODE_URL, {
        "client_id": client_id,
        "scope": V2_SCOPE,
    }, timeout=45)


def _token_url(client_id: str) -> str:
    return LIVE_TOKEN_URL if client_id == LIVE_CLIENT_ID else V2_TOKEN_URL


def _xbl_authenticate(ms_access_token: str, azure: bool = False) -> tuple[str, str, str]:
    ticket = f"d={ms_access_token}" if azure else ms_access_token
    payload = {
        "Properties": {
            "AuthMethod": "RPS",
            "SiteName": "user.auth.xboxlive.com",
            "RpsTicket": ticket,
        },
        "RelyingParty": "http://auth.xboxlive.com",
        "TokenType": "JWT",
    }
    try:
        data = post_json(XBL_USER_AUTH, payload, timeout=40)
    except HttpError as e:
        raise LauncherError(f"Xbox Live 验证失败（HTTP {e.status}）") from None
    token = data.get("Token") or ""
    xui = ((data.get("DisplayClaims") or {}).get("xui") or [{}])[0]
    if not token:
        raise LauncherError("Xbox Live 验证失败：没有返回令牌")
    return token, xui.get("uhs") or "", xui.get("xid") or ""


def _xsts_authorize(xbl_token: str) -> tuple[str, str]:
    payload = {
        "Properties": {
            "SandboxId": "RETAIL",
            "UserTokens": [xbl_token],
        },
        "RelyingParty": "rp://api.minecraftservices.com/",
        "TokenType": "JWT",
    }
    try:
        data = post_json(XBL_XSTS, payload, timeout=40)
    except HttpError as e:
        body = e.json()
        xerr = body.get("XErr")
        message = XSTS_MESSAGES.get(xerr)
        if message:
            raise LauncherError(message) from None
        raise LauncherError(f"XSTS 授权失败（HTTP {e.status}，XErr={xerr}）") from None
    token = data.get("Token") or ""
    xui = ((data.get("DisplayClaims") or {}).get("xui") or [{}])[0]
    if not token:
        raise LauncherError("XSTS 授权失败：没有返回令牌")
    return token, xui.get("uhs") or ""


def _profile(mc_token: str) -> dict:
    try:
        return http_json(MC_PROFILE, headers={"Authorization": f"Bearer {mc_token}"}, timeout=40)
    except HttpError as e:
        if e.status == 404:
            raise LauncherError(
                "该微软账号没有 Minecraft Java 版档案（未购买，或只有基岩版）。"
            ) from None
        if e.status == 401:
            raise LauncherError("Minecraft 登录已失效，请重新登录。") from None
        raise LauncherError(f"获取 Minecraft 档案失败（HTTP {e.status}）") from None


def _finish_login(token_data: dict, client_id: str = LIVE_CLIENT_ID,
                  old_refresh: str = "") -> Account:
    ms_access = token_data.get("access_token") or ""
    refresh = token_data.get("refresh_token") or old_refresh
    if not ms_access:
        raise LauncherError("微软登录失败：没有获得访问令牌")
    xbl_token, _uhs, xuid = _xbl_authenticate(ms_access, azure=client_id != LIVE_CLIENT_ID)
    xsts_token, uhs = _xsts_authorize(xbl_token)
    try:
        mc = post_json(MC_LOGIN, {"identityToken": f"XBL3.0 x={uhs};{xsts_token}"}, timeout=40)
    except HttpError as e:
        raise LauncherError(f"Minecraft 登录失败（HTTP {e.status}）") from None
    mc_token = mc.get("access_token") or ""
    if not mc_token:
        raise LauncherError("Minecraft 登录失败：没有获得访问令牌")
    profile = _profile(mc_token)
    name = profile.get("name")
    uid = profile.get("id")
    if not name or not uid:
        raise LauncherError("Minecraft 档案信息不完整")
    expires_in = int(mc.get("expires_in") or 86400)
    return Account(
        type="microsoft",
        name=name,
        uuid=uid,
        access_token=mc_token,
        refresh_token=refresh,
        expires_at=time.time() + expires_in,
        xuid=xuid,
    )


def device_code_login(client_id: str, on_code=None, cancel=None) -> Account:
    data = start_device_code(client_id)
    user_code = data.get("user_code") or ""
    verification = data.get("verification_uri") or data.get("verification_url") or "https://microsoft.com/link"
    device_code = data.get("device_code") or ""
    if not device_code:
        raise LauncherError("微软没有返回设备代码，请稍后重试。")
    expires_in = int(data.get("expires_in") or 900)
    interval = max(1, int(data.get("interval") or 5))
    if on_code:
        try:
            on_code(user_code, verification, expires_in, data.get("message") or "")
        except Exception:
            pass
    deadline = time.time() + max(30, expires_in - 10)
    while time.time() < deadline:
        if cancel is not None and cancel.is_set():
            from .util import Cancelled
            raise Cancelled("已取消登录")
        try:
            token_data = post_form(_token_url(client_id), {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": client_id,
                "device_code": device_code,
            }, timeout=45)
            return _finish_login(token_data, client_id)
        except HttpError as e:
            err = (e.json() or {}).get("error") or ""
            if err == "authorization_pending":
                time.sleep(interval)
                continue
            if err == "slow_down":
                interval += 5
                time.sleep(interval)
                continue
            if err in ("expired_token", "code_expired"):
                raise LauncherError("登录代码已过期，请重新获取。") from None
            if err in ("authorization_declined", "access_denied"):
                raise LauncherError("登录已被拒绝。") from None
            raise LauncherError(f"微软登录失败：{err or e.status}") from None
    raise LauncherError("登录超时，请重新获取代码。")


def refresh_account(account: Account, client_id: str) -> Account:
    if not account.refresh_token:
        raise LauncherError("登录信息已失效，请重新登录。")
    try:
        params = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": account.refresh_token,
        }
        if client_id != LIVE_CLIENT_ID:
            params["scope"] = V2_SCOPE
        data = post_form(_token_url(client_id), params, timeout=45, retries=0)
    except HttpError as e:
        err = (e.json() or {}).get("error") or ""
        if err in ("invalid_grant", "interaction_required"):
            raise LauncherError("微软登录已过期，请重新登录。") from None
        raise LauncherError(f"刷新登录失败：{err or e.status}") from None
    return _finish_login(data, client_id, old_refresh=account.refresh_token)


def ensure_account(account: Account, client_id: str) -> Account:
    if account.is_external:
        try:
            if validate_external(account):
                return account
        except Exception:
            pass
        try:
            return refresh_external(account)
        except Exception as e:
            raise LauncherError(f"外置登录已失效，请在账户里重新登录。\n{e}") from None
    if account.type != "microsoft":
        return account
    if time.time() < account.expires_at - 120:
        return account
    return refresh_account(account, client_id)
