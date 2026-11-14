"""Bounded auth inputs and Secure cookie construction, not OIDC validation.

These helpers do not exchange/verify tokens, consume a login transaction or
create a session. Passing them is necessary, never sufficient, for login.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Literal
from urllib.parse import parse_qsl

from pydantic import SecretStr

from .settings import AuthPolicy, validate_return_path

SESSION_COOKIE = "__Host-aletheia_session"
LOGIN_COOKIE = "__Host-aletheia_login"
_OPAQUE_COOKIE = re.compile(r"[A-Za-z0-9_-]{43}")
_STATE = re.compile(r"[A-Za-z0-9_-]{43,128}")
_BAD_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_CALLBACK_FIELDS = frozenset({
    "code", "state", "iss", "session_state", "error", "error_description", "error_uri",
})


class AuthInputRejected(ValueError):
    """A single fixed safe message; no code/state/cookie/provider diagnostics."""

    def __init__(self) -> None:
        super().__init__("Authentication request could not be verified.")


def new_opaque_secret() -> SecretStr:
    """256-bit random opaque value, without embedding actor or permission data."""
    return SecretStr(secrets.token_urlsafe(32))


def allowed_return_to(value: str | None, policy: AuthPolicy) -> str:
    candidate = policy.default_return_to if value is None else value
    try:
        validate_return_path(candidate)
    except ValueError:
        raise AuthInputRejected() from None
    if candidate not in policy.return_to_paths:
        raise AuthInputRejected()
    return candidate


ProviderDisposition = Literal["cancelled", "unavailable", "rejected"]


@dataclass(frozen=True)
class CallbackInput:
    state: SecretStr
    code: SecretStr | None
    provider_denied: bool
    provider_disposition: ProviderDisposition | None = None


def _provider_disposition(value: str) -> ProviderDisposition:
    if value == "access_denied":
        return "cancelled"
    if value in ("server_error", "temporarily_unavailable"):
        return "unavailable"
    return "rejected"


def parse_callback_query(
    query: bytes, *, expected_issuer: str, policy: AuthPolicy,
) -> CallbackInput:
    """Strict query-mode code/error shape. Provider text is discarded.

    A matching issuer here is still an untrusted callback parameter, not a
    verified ID Token. State must subsequently be bound to the browser and
    atomically consumed in the server-side login transaction store.
    """
    values = _callback_values(query, maximum_bytes=policy.maximum_callback_query_bytes)
    state = values.get("state", "")
    if not _STATE.fullmatch(state):
        raise AuthInputRejected()
    issuer = values.get("iss")
    if (policy.require_response_issuer or issuer is not None) and issuer != expected_issuer:
        raise AuthInputRejected()
    has_code, has_error = "code" in values, "error" in values
    if has_code == has_error:
        raise AuthInputRejected()
    if has_error:
        if not values["error"] or len(values["error"]) > 256:
            raise AuthInputRejected()
        return CallbackInput(SecretStr(state), None, provider_denied=True,
                             provider_disposition=_provider_disposition(values["error"]))
    code = values["code"]
    if (
        not code or any(ord(character) <= 32 or ord(character) >= 127 for character in code)
        or "error_description" in values or "error_uri" in values
    ):
        raise AuthInputRejected()
    return CallbackInput(SecretStr(state), SecretStr(code), provider_denied=False)


def _callback_values(query: bytes, *, maximum_bytes: int) -> dict[str, str]:
    if not query or len(query) > maximum_bytes:
        raise AuthInputRejected()
    try:
        text = query.decode("ascii")
        if _BAD_ESCAPE.search(text) or _CONTROL.search(text):
            raise AuthInputRejected()
        pairs = parse_qsl(
            text, keep_blank_values=True, strict_parsing=True,
            encoding="utf-8", errors="strict", max_num_fields=8,
        )
    except (UnicodeError, ValueError):
        raise AuthInputRejected() from None
    values: dict[str, str] = {}
    for name, value in pairs:
        if (
            name not in _CALLBACK_FIELDS or name in values
            or _CONTROL.search(value) or len(value) > 2048
        ):
            raise AuthInputRejected()
        values[name] = value
    return values


def read_opaque_cookie(
    cookie: bytes | None, *, name: str, maximum_bytes: int,
) -> SecretStr | None:
    """Reject ambiguous protected cookies before any repository lookup."""
    if name not in (SESSION_COOKIE, LOGIN_COOKIE) or type(maximum_bytes) is not int or maximum_bytes <= 0:
        raise ValueError("invalid cookie boundary configuration")
    if cookie is None:
        return None
    if len(cookie) > maximum_bytes:
        raise AuthInputRejected()
    try:
        text = cookie.decode("ascii")
    except UnicodeError:
        raise AuthInputRejected() from None
    if _CONTROL.search(text):
        raise AuthInputRejected()
    found: SecretStr | None = None
    for part in text.split(";"):
        key, separator, value = part.strip().partition("=")
        if key.strip() == name and key != name:
            raise AuthInputRejected()
        if key != name:
            continue
        if found is not None or not separator or not _OPAQUE_COOKIE.fullmatch(value):
            raise AuthInputRejected()
        found = SecretStr(value)
    return found


def set_opaque_cookie(*, name: str, value: SecretStr, max_age: int) -> bytes:
    """Set-Cookie bytes are credentials: never log or include in diagnostics."""
    if name not in (SESSION_COOKIE, LOGIN_COOKIE):
        raise ValueError("unsupported protected cookie name")
    if type(max_age) is not int or not 1 <= max_age <= 604800:
        raise ValueError("invalid cookie lifetime")
    if not _OPAQUE_COOKIE.fullmatch(value.get_secret_value()):
        raise ValueError("invalid opaque cookie value")
    return _cookie(name, value.get_secret_value(), max_age)


def clear_opaque_cookie(*, name: str) -> bytes:
    if name not in (SESSION_COOKIE, LOGIN_COOKIE):
        raise ValueError("unsupported protected cookie name")
    return _cookie(name, "", 0)


def _cookie(name: str, value: str, max_age: int) -> bytes:
    cookies = SimpleCookie()
    cookies[name] = value
    item = cookies[name]
    item["path"] = "/"
    item["max-age"] = str(max_age)
    item["secure"] = True
    item["httponly"] = True
    item["samesite"] = "Lax"
    if max_age == 0:
        item["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
    return item.OutputString().encode("ascii")
