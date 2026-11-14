"""Draft CSRF/source guard using asgi-csrf plus current server context binding.

No automatic routing/bootstrap or random per-process signing key. K-02 must
place this inside its total request deadline and wire a real binding loader.
Authorization/membership must still be rechecked at the business transaction.
"""

from __future__ import annotations

import asyncio
import hmac
import math
import re
import secrets
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from http.cookies import SimpleCookie
from importlib import import_module
from typing import Protocol, cast
from urllib.parse import urlsplit
from uuid import uuid4

from itsdangerous import URLSafeSerializer
from pydantic import (
    BaseModel,
    ConfigDict,
    SecretStr,
    TypeAdapter,
    ValidationError,
    field_validator,
)

from aletheia_lab.hosted.application import VERSION, AccessDenied, Send
from aletheia_lab.hosted.read_backend import SessionRepository
from aletheia_lab.hosted.request_boundary import Receive
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted_contract.session_capabilities import PROBLEM_STATUS, Identifier, Problem

from .boundary import LOGIN_COOKIE, SESSION_COOKIE, AuthInputRejected, read_opaque_cookie
from .repositories import RepositoryDataRejected
from .settings import AuthConfigurationRejected, AuthPolicy

CSRF_COOKIE = "__Host-aletheia_csrf"
CSRF_HEADER = b"x-csrf-token"
_NAMESPACE = "aletheia-csrf/v1"
_TOKEN = re.compile(r"[A-Za-z0-9_.-]{1,512}")
_NONCE = re.compile(r"[A-Za-z0-9_-]{43}")
_CONTROL = re.compile(rb"[\x00-\x1f\x7f]")
_IDENTIFIER = TypeAdapter(Identifier)
_SINGLETONS = frozenset({
    CSRF_HEADER, b"origin", b"content-type", b"content-encoding", b"host",
    b"sec-fetch-site", b"sec-fetch-mode", b"sec-fetch-dest", b"authorization", b"if-match",
})
_OVERRIDES = frozenset({b"x-http-method-override", b"x-method-override", b"x-http-method"})
Application = Callable[[Mapping[str, object], Receive, Send], Awaitable[None]]


class _CsrfDenied(ValueError):
    def __init__(self) -> None:
        super().__init__("Request could not be verified.")


class _MiddlewareFactory(Protocol):
    def __call__(self, app: Application, **kwargs: object) -> Application: ...


class CsrfBinding(BaseModel):
    """Current trusted context proof, not caller-supplied role/identity/token data."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    context_id: Identifier
    csrf_token: SecretStr
    expires_at: datetime

    @field_validator("csrf_token")
    @classmethod
    def _bounded(cls, value: SecretStr) -> SecretStr:
        if not _TOKEN.fullmatch(value.get_secret_value()):
            raise ValueError("bounded signed proof required")
        return value

    @field_validator("expires_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("timezone required")
        return value


class BindingLoader(Protocol):
    """Load current server proof from verified cookies, never identity headers.

    The preauth bootstrap authority needs its own explicit loader. Returning no
    binding is a denial, not an anonymous/no-cookie CSRF exemption.
    """

    async def __call__(self, cookie: bytes | None) -> CsrfBinding | None: ...


class SessionBindingLoader:
    def __init__(self, sessions: SessionRepository) -> None:
        self._sessions = sessions

    async def __call__(self, cookie: bytes | None) -> CsrfBinding | None:
        if not cookie:
            return None
        record = await self._sessions.resolve_cookie(cookie)
        if record is None:
            return None
        if record.state == "expired":
            raise AccessDenied("session_expired")
        if record.state != "active" or record.actor_enabled is not True:
            raise AccessDenied("session_revoked")
        return CsrfBinding(context_id=record.context_id, csrf_token=record.csrf_token, expires_at=record.expires_at)


class CsrfTokens:
    """Explicit shared signing key; issue proof for a server-selected context."""

    def __init__(self, secret: SecretStr) -> None:
        if not isinstance(secret, SecretStr):
            raise AuthConfigurationRejected()
        value = secret.get_secret_value()
        if not 32 <= len(value.encode("utf-8")) <= 4096 or value.strip() != value or _CONTROL.search(value.encode("utf-8")):
            raise AuthConfigurationRejected()
        self._secret = secret
        self._serializer = URLSafeSerializer(value)
        self._middleware = cast(_MiddlewareFactory, import_module("asgi_csrf").asgi_csrf)

    @classmethod
    def from_environment(cls, policy: AuthPolicy, environment: Mapping[str, str]) -> CsrfTokens:
        """Only the declared variable; never read .env or generate a fallback."""
        policy = AuthPolicy.model_validate(policy.model_dump(warnings=False))
        value = environment.get(policy.csrf_secret_env, "")
        if not isinstance(value, str):
            raise AuthConfigurationRejected()
        return cls(SecretStr(value))

    def issue(self, context_id: str) -> SecretStr:
        try:
            context_id = _IDENTIFIER.validate_python(context_id, strict=True)
        except ValidationError:
            raise AuthConfigurationRejected() from None
        token = self._serializer.dumps(context_id + "." + secrets.token_urlsafe(32), salt=_NAMESPACE)
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise AuthConfigurationRejected()
        return SecretStr(token)

    def matches_context(self, token: SecretStr, context_id: str) -> bool:
        try:
            payload = self._serializer.loads(token.get_secret_value(), salt=_NAMESPACE)
        except Exception:
            return False
        if not isinstance(payload, str) or len(payload) > 172:
            return False
        context, separator, nonce = payload.rpartition(".")
        return (
            bool(separator) and _NONCE.fullmatch(nonce) is not None
            and hmac.compare_digest(context.encode("utf-8"), context_id.encode("utf-8"))
        )

    def wrap(self, app: Application, *, path: str, failed: Callable[..., Awaitable[None]]) -> Application:
        # Every call supplies a real secret, exact current path and no skip hook.
        return self._middleware(
            app, signing_secret=self._secret.get_secret_value(), signing_namespace=_NAMESPACE,
            cookie_name=CSRF_COOKIE, http_header=CSRF_HEADER.decode("ascii"),
            always_protect={path}, always_set_cookie=False, skip_if_scope=None,
            cookie_path="/", cookie_domain=None, cookie_secure=True,
            cookie_samesite="Lax", send_csrf_failed=failed,
        )


def csrf_cookie(token: SecretStr, *, max_age: int) -> bytes:
    if not _TOKEN.fullmatch(token.get_secret_value()) or type(max_age) is not int or not 1 <= max_age <= 604800:
        raise AuthConfigurationRejected()
    return _cookie(token.get_secret_value(), max_age)


def clear_csrf_cookie() -> bytes:
    return _cookie("", 0)


def _cookie(value: str, max_age: int) -> bytes:
    cookies = SimpleCookie()
    cookies[CSRF_COOKIE] = value
    item = cookies[CSRF_COOKIE]
    item["path"], item["secure"], item["httponly"], item["samesite"] = "/", True, True, "Lax"
    item["max-age"] = str(max_age)
    if max_age == 0:
        item["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
    return item.OutputString().encode("ascii")


def _origin(value: str) -> tuple[str, str, int]:
    if not value or re.search(r"[\x00-\x20\x7f\\%]", value):
        raise _CsrfDenied()
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
        or parsed.password is not None or parsed.path or parsed.query or parsed.fragment
        or parsed.port == 0
    ):
        raise _CsrfDenied()
    return parsed.scheme, parsed.hostname.encode("idna").decode("ascii").lower(), parsed.port or 443


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CsrfMiddleware:
    """Strict cookie-auth API guard; K-02 still owns actual HTTP integration.

    GET/HEAD/OPTIONS pass to the router. Other methods are denied except explicit
    POST/PUT/PATCH/DELETE with source, media and current bound proof. OIDC callback
    GET relies on the separately verified one-use state flow, not a path-prefix
    CSRF exemption. Missing DELETE media is delegated to the bounded route parser
    to prove an empty body. This does not authorize the business mutation itself.
    """

    def __init__(
        self, app: Application, tokens: CsrfTokens, bindings: BindingLoader,
        runtime: RuntimeSettings, *, lookup_timeout_seconds: float,
        allowed_media_types: frozenset[str] = frozenset({"application/json"}),
        maximum_header_bytes: int = 16384, clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._runtime = RuntimeSettings.model_validate(runtime.model_dump(warnings=False))
        if (
            isinstance(lookup_timeout_seconds, bool) or not math.isfinite(lookup_timeout_seconds)
            or not 0 < lookup_timeout_seconds <= self._runtime.api.backend_timeout_seconds
            or type(maximum_header_bytes) is not int or maximum_header_bytes <= 0
            or not allowed_media_types or any("/" not in item or ";" in item or "*" in item for item in allowed_media_types)
        ):
            raise AuthConfigurationRejected()
        try:
            self._expected_origin = _origin(self._runtime.public_origin.rstrip("/"))
        except (ValueError, UnicodeError):
            raise AuthConfigurationRejected() from None
        self._app, self._tokens, self._bindings = app, tokens, bindings
        self._timeout, self._media, self._maximum_headers = lookup_timeout_seconds, allowed_media_types, maximum_header_bytes
        self._clock = clock

    def _headers(self, scope: Mapping[str, object]) -> dict[bytes, bytes]:
        raw = cast(Sequence[tuple[bytes, bytes]], scope.get("headers", ()))
        if sum(len(key) + len(value) + 4 for key, value in raw) > self._maximum_headers:
            raise _CsrfDenied()
        result: dict[bytes, bytes] = {}
        cookies: list[bytes] = []
        for key, value in raw:
            key = key.lower()
            if _CONTROL.search(key) or _CONTROL.search(value):
                raise _CsrfDenied()
            if key == b"cookie":
                cookies.append(value)
            elif key in _SINGLETONS and key in result:
                raise _CsrfDenied()
            else:
                result[key] = value
        if cookies:
            result[b"cookie"] = b"; ".join(cookies)
        if len(result.get(b"cookie", b"")) > self._runtime.api.maximum_cookie_bytes:
            raise _CsrfDenied()
        return result

    def _source(self, scope: Mapping[str, object], headers: Mapping[bytes, bytes]) -> None:
        if scope.get("scheme") != "https":
            raise _CsrfDenied()
        try:
            actual = _origin(headers.get(b"origin", b"").decode("ascii"))
        except (UnicodeError, ValueError):
            raise _CsrfDenied() from None
        if actual != self._expected_origin:
            raise _CsrfDenied()
        allowed = {
            b"sec-fetch-site": {b"same-origin"},
            b"sec-fetch-mode": {b"cors", b"same-origin"},
            b"sec-fetch-dest": {b"empty"},
        }
        for name, values in allowed.items():
            if name in headers and headers[name] not in values:
                raise _CsrfDenied()
        if any(name in headers for name in _OVERRIDES):
            raise _CsrfDenied()
        self._content_type(headers, allow_missing=scope.get("method") == "DELETE")

    def _content_type(self, headers: Mapping[bytes, bytes], *, allow_missing: bool = False) -> None:
        if headers.get(b"content-encoding", b"identity").lower() != b"identity":
            raise _CsrfDenied()
        # The route must inspect the bounded stream; Content-Length is not proof.
        if allow_missing and b"content-type" not in headers:
            return
        try:
            media, *parameters = [part.strip().lower() for part in headers.get(b"content-type", b"").decode("ascii").split(";")]
        except UnicodeError:
            raise _CsrfDenied() from None
        if media not in self._media:
            raise _CsrfDenied()
        if parameters and (media != "application/json" or len(parameters) != 1 or parameters[0] not in ('charset=utf-8', 'charset="utf-8"')):
            raise _CsrfDenied()

    def _submitted(self, headers: Mapping[bytes, bytes]) -> SecretStr:
        cookie = headers.get(b"cookie")
        try:
            for name in (SESSION_COOKIE, LOGIN_COOKIE):
                read_opaque_cookie(cookie, name=name, maximum_bytes=self._runtime.api.maximum_cookie_bytes)
            text = (cookie or b"").decode("ascii")
            header = headers.get(CSRF_HEADER, b"").decode("ascii")
        except (AuthInputRejected, UnicodeError):
            raise _CsrfDenied() from None
        found: str | None = None
        for part in text.split(";"):
            name, separator, value = part.strip().partition("=")
            if name.strip() == CSRF_COOKIE and name != CSRF_COOKIE:
                raise _CsrfDenied()
            if name == CSRF_COOKIE:
                if found is not None or not separator or not _TOKEN.fullmatch(value):
                    raise _CsrfDenied()
                found = value
        if found is None or not _TOKEN.fullmatch(header) or not hmac.compare_digest(found, header):
            raise _CsrfDenied()
        return SecretStr(header)

    async def _binding(self, cookie: bytes | None) -> CsrfBinding:
        async with asyncio.timeout(self._timeout):
            binding = await self._bindings(cookie)
        if binding is None:
            raise AccessDenied("session_expired")
        try:
            binding = CsrfBinding.model_validate(binding.model_dump(warnings=False))
        except (ValidationError, TypeError, AttributeError):
            raise RepositoryDataRejected() from None
        now = self._clock()
        if now.utcoffset() is None:
            raise RepositoryDataRejected()
        if binding.expires_at <= now:
            raise AccessDenied("session_expired")
        return binding

    async def _verify(self, scope: Mapping[str, object]) -> dict[bytes, bytes]:
        if scope.get("method") not in ("POST", "PUT", "PATCH", "DELETE"):
            raise _CsrfDenied()
        headers = self._headers(scope)
        self._source(scope, headers)
        submitted = self._submitted(headers)
        binding = await self._binding(headers.get(b"cookie"))
        if not hmac.compare_digest(submitted.get_secret_value(), binding.csrf_token.get_secret_value()):
            raise _CsrfDenied()
        if not self._tokens.matches_context(submitted, binding.context_id):
            raise _CsrfDenied()
        return headers

    @staticmethod
    async def _problem(send: Send, status: int, code: str) -> None:
        detail = "Request could not be verified." if code == "csrf_invalid" else "The request could not be completed."
        value = Problem(
            schema_version=VERSION, type="urn:aletheia:problem:" + code.replace("_", "-"),
            title="Request could not be completed", status=status, code=code, detail=detail,
            request_id="req_" + uuid4().hex,
        )
        body = value.model_dump_json().encode("utf-8")
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/problem+json"), (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"), (b"content-length", str(len(body)).encode("ascii")),
            (b"x-request-id", value.request_id.encode("ascii")),
        ]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        if scope.get("type") == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        raw_headers = cast(Sequence[tuple[bytes, bytes]], scope.get("headers", ()))
        if scope.get("type") == "http" and any(name.lower() in _OVERRIDES for name, _ in raw_headers):
            await self._problem(send, 403, "csrf_invalid")
            return
        if scope.get("type") != "http" or scope.get("method") in ("GET", "HEAD", "OPTIONS"):
            await self._app(scope, receive, send)
            return
        await self._unsafe_request(scope, receive, send)

    async def _unsafe_request(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        try:
            headers = await self._verify(scope)
        except _CsrfDenied:
            await self._problem(send, 403, "csrf_invalid")
            return
        except AccessDenied as error:
            await self._problem(send, PROBLEM_STATUS[error.code], error.code)
            return
        except RepositoryDataRejected:
            await self._problem(send, 500, "service_failure")
            return
        except Exception:
            await self._problem(send, 503, "service_unavailable")
            return

        # Join Cookie and remove the Bearer exemption; preserve other validated
        # fields so downstream framing/idempotency parsers still see duplicates.
        canonical = dict(scope)
        raw = cast(Sequence[tuple[bytes, bytes]], scope.get("headers", ()))
        forwarded = [(key.lower(), value) for key, value in raw if key.lower() not in (b"cookie", b"authorization")]
        if b"cookie" in headers:
            forwarded.append((b"cookie", headers[b"cookie"]))
        canonical["headers"] = forwarded

        async def protected(inner_scope: Mapping[str, object], ignored: Receive, inner_send: Send) -> None:
            # The library must never consume the real request body during a
            # CSRF fallback. The business app receives the original bounded I/O.
            await self._app(canonical, receive, inner_send)

        async def refuse_body() -> dict[str, object]:
            raise _CsrfDenied()

        async def failed(inner_scope: Mapping[str, object], inner_send: Send, message_id: object) -> None:
            await self._problem(inner_send, 403, "csrf_invalid")

        guarded = self._tokens.wrap(protected, path=str(scope.get("path", "")), failed=failed)
        try:
            await guarded(canonical, refuse_body, send)
        except _CsrfDenied:
            await self._problem(send, 403, "csrf_invalid")
