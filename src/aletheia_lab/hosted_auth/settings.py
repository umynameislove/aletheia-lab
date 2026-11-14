"""Offline auth policy draft; reuse K-02 identity/runtime settings unchanged."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from aletheia_lab.hosted.settings import RuntimeSettings, SecretVariable
from aletheia_lab.hosted_contract.session_capabilities import Identifier

_UNSAFE_PATH = re.compile(r"[\x00-\x20\x7f\\%?#]")
_MAX_POLICY_BYTES = 16384
Algorithm = Literal["RS256", "PS256", "ES256"]


def validate_return_path(value: str) -> str:
    """Only literal app paths; no URL decoding or prefix-based permission."""
    if not value or len(value) > 1024 or _UNSAFE_PATH.search(value):
        raise ValueError("literal application path required")
    if not value.startswith("/") or "//" in value:
        raise ValueError("literal application path required")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("literal application path required")
    if any(segment in (".", "..") for segment in value.split("/")):
        raise ValueError("normalized application path required")
    return value


class AuthConfigurationRejected(ValueError):
    """Fixed message: do not expose parser diagnostics or submitted settings."""

    def __init__(self) -> None:
        super().__init__("Authentication policy is missing or invalid.")


class AuthPolicy(BaseModel):
    """Internal policy, not a new Session/Capabilities/Problem wire schema.

    Defaults are proposals for testing, not agreed deployment or capacity values.
    Return paths intentionally have no working default until the UI is agreed.
    """

    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, hide_input_in_errors=True,
    )

    default_return_to: str
    return_to_paths: Annotated[tuple[str, ...], Field(min_length=1, max_length=32)]
    idle_seconds: int = Field(default=900, ge=60, le=86400)
    absolute_seconds: int = Field(default=28800, ge=60, le=604800)
    rotation_seconds: int = Field(default=300, ge=1, le=86400)
    login_transaction_seconds: int = Field(default=300, ge=30, le=600)
    clock_skew_seconds: int = Field(default=30, ge=0, le=60)
    idp_timeout_seconds: float = Field(default=3.0, gt=0, le=30, allow_inf_nan=False)
    idp_maximum_inflight: int = Field(default=2, ge=1, le=64)
    idp_initial_backoff_seconds: float = Field(default=1.0, gt=0, le=60, allow_inf_nan=False)
    idp_maximum_backoff_seconds: float = Field(default=30.0, gt=0, le=300, allow_inf_nan=False)
    maximum_callback_query_bytes: int = Field(default=8192, ge=512, le=16384)
    require_response_issuer: bool = True
    require_verified_email: bool = True
    required_acr: Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[\x21-\x7e]+$")] | None = None
    id_token_algorithms: tuple[Algorithm, ...] = ("RS256",)
    csrf_secret_env: SecretVariable = "ALETHEIA_HOSTED_CSRF_SECRET"
    admission_namespace: Identifier | None = None
    admission_secret_env: SecretVariable = "ALETHEIA_HOSTED_ADMISSION_SECRET"
    admission_global_attempts: int = Field(default=600, ge=1, le=10000)
    admission_subject_attempts: int = Field(default=30, ge=1, le=10000)
    admission_window_seconds: int = Field(default=60, ge=1, le=3600)

    @field_validator("return_to_paths", "id_token_algorithms", mode="before")
    @classmethod
    def _toml_array(cls, value: object) -> object:
        # TOML arrays are lists; normalize only that container, not element types.
        return tuple(value) if isinstance(value, list) else value

    @field_validator("default_return_to")
    @classmethod
    def _default_path(cls, value: str) -> str:
        return validate_return_path(value)

    @field_validator("return_to_paths")
    @classmethod
    def _paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            validate_return_path(item)
        if len(value) != len(set(value)):
            raise ValueError("duplicate return paths")
        return value

    @field_validator("id_token_algorithms")
    @classmethod
    def _algorithms(cls, value: tuple[Algorithm, ...]) -> tuple[Algorithm, ...]:
        if not value or len(value) != len(set(value)):
            raise ValueError("explicit unique signing algorithms required")
        return value

    @model_validator(mode="after")
    def _relationships(self) -> Self:
        if self.default_return_to not in self.return_to_paths:
            raise ValueError("default path must be explicitly allowed")
        if not self.rotation_seconds < self.idle_seconds <= self.absolute_seconds:
            raise ValueError("rotation, idle and absolute budgets are inconsistent")
        if self.login_transaction_seconds > self.idle_seconds:
            raise ValueError("login transaction exceeds the idle budget")
        if self.idp_initial_backoff_seconds > self.idp_maximum_backoff_seconds:
            raise ValueError("IdP backoff budgets are inconsistent")
        if self.admission_subject_attempts > self.admission_global_attempts:
            raise ValueError("subject budget exceeds the global budget")
        return self

    def require_runtime(self, runtime: RuntimeSettings) -> None:
        """Offline consistency only; does not establish IdP/TLS/DB readiness.

        This first slice uses Secure host-only cookies and query-mode GET
        callbacks. It deliberately has no insecure development-cookie fallback.
        """
        try:
            runtime = RuntimeSettings.model_validate(runtime.model_dump(warnings=False))
        except ValidationError:
            raise AuthConfigurationRejected() from None
        public = urlsplit(runtime.public_origin)
        callback = urlsplit(runtime.identity.redirect_uri)
        issuer = urlsplit(runtime.identity.issuer)
        if any(url.scheme != "https" for url in (public, callback, issuer)):
            raise AuthConfigurationRejected()
        try:
            validate_return_path(callback.path)
        except ValueError:
            raise AuthConfigurationRejected() from None
        if self.idp_timeout_seconds >= runtime.api.backend_timeout_seconds:
            raise AuthConfigurationRejected()


def load_auth_policy(policy_path: Path) -> AuthPolicy:
    """Read explicit bounded TOML; do not implicitly load .env or contact IdP."""
    try:
        with policy_path.open("rb") as stream:
            raw = stream.read(_MAX_POLICY_BYTES + 1)
        if len(raw) > _MAX_POLICY_BYTES:
            raise AuthConfigurationRejected() from None
        data = tomllib.loads(raw.decode("utf-8"))
        return AuthPolicy.model_validate(data)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, ValidationError):
        raise AuthConfigurationRejected() from None
