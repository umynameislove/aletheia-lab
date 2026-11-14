"""Hosted process configuration, independent of the public DTO contract.

Only public settings and secret *variable names* belong in TOML. This module
validates configuration offline; it does not discover an IdP, connect to a DB,
create directories, configure a worker or claim readiness.
"""

from __future__ import annotations

import ipaddress
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import SplitResult, urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

DatabaseIdentifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,62}$", max_length=63)]
Positive = Annotated[int, Field(gt=0)]
Timeout = Annotated[float, Field(gt=0, allow_inf_nan=False)]
SecretVariable = Annotated[str, Field(pattern=r"^ALETHEIA_HOSTED_[A-Z0-9_]+$", max_length=128)]
_CONTROL = re.compile(r"[\x00-\x20\x7f\\]")
_MAX_CONFIG_BYTES = 65536


class ConfigurationRejected(ValueError):
    """Safe top-level locations only: no raw settings, URLs, paths or secrets."""

    def __init__(self, fields: tuple[str, ...]) -> None:
        self.fields = tuple(sorted(set(fields)))
        super().__init__("Invalid hosted configuration: " + ", ".join(self.fields))


class _Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


def _url(value: str) -> SplitResult:
    if _CONTROL.search(value):
        raise ValueError("URL contains unsupported characters")
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("absolute HTTP URL required")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError("URL credentials, query and fragment are not allowed")
    if parsed.port == 0:
        raise ValueError("URL port is invalid")
    return parsed


def _origin(value: SplitResult) -> tuple[str, str | None, int]:
    return value.scheme, value.hostname, value.port or (443 if value.scheme == "https" else 80)


def _loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


class ApiSettings(_Settings):
    bind_host: str = "127.0.0.1"
    port: int = Field(default=18766, ge=1, le=65535)
    processes: Positive = 1
    database_pool_size: Positive = 8
    concurrency_limit: Positive = 64
    backlog: Positive = 128
    keep_alive_seconds: Positive = 5
    graceful_shutdown_seconds: Positive = 15
    h11_max_incomplete_event_bytes: Positive = 16384
    header_timeout_seconds: Timeout = 5.0
    backend_timeout_seconds: Timeout = 5.0
    request_timeout_seconds: Timeout = 30.0
    startup_timeout_seconds: Timeout = 15.0
    resource_close_timeout_seconds: Timeout = 15.0
    maximum_response_bytes: int = Field(default=1048576, ge=4096)
    maximum_cookie_bytes: Positive = 4096
    maximum_header_bytes: Positive = 32768
    maximum_header_count: Positive = 100
    maximum_json_bytes: Positive = 1048576
    maximum_json_depth: Positive = 64
    maximum_body_events: Positive = 1024
    body_idle_timeout_seconds: Timeout = 5.0
    body_total_timeout_seconds: Timeout = 10.0

    @field_validator("bind_host")
    @classmethod
    def _literal_bind(cls, value: str) -> str:
        ipaddress.ip_address(value)
        return value

    @model_validator(mode="after")
    def _timeout_order(self) -> Self:
        if self.body_total_timeout_seconds < self.body_idle_timeout_seconds:
            raise ValueError("total body deadline must include the idle deadline")
        return self


class WorkerSettings(_Settings):
    """Planned separate processes; no worker is started by these settings."""

    processes: Positive = 1
    database_pool_size: Positive = 2
    job_slots_per_process: Positive = 1
    shutdown_grace_seconds: Positive = 30


class IdentitySettings(_Settings):
    issuer: Annotated[str, Field(min_length=1, max_length=2048)]
    client_id: Annotated[str, Field(min_length=1, max_length=256)]
    redirect_uri: Annotated[str, Field(min_length=1, max_length=2048)]
    client_kind: Literal["public", "confidential"]
    client_secret_env: SecretVariable | None = None

    @field_validator("issuer", "redirect_uri")
    @classmethod
    def _url_shape(cls, value: str) -> str:
        _url(value)
        return value

    @model_validator(mode="after")
    def _secret_reference(self) -> Self:
        if (self.client_secret_env is not None) != (self.client_kind == "confidential"):
            raise ValueError("client secret reference must match the registered client kind")
        return self


class StorageSettings(_Settings):
    database_url_env: SecretVariable
    expected_schema: DatabaseIdentifier | None = None
    expected_database_role: DatabaseIdentifier | None = None
    object_root: Annotated[str, Field(min_length=1, max_length=4096)]
    postgres_connection_budget: Positive = 16
    admin_connection_reserve: Positive = 2
    pool_wait_timeout_seconds: Timeout = 5.0

    @field_validator("expected_schema")
    @classmethod
    def _application_schema(cls, value: str | None) -> str | None:
        if value in ("public", "information_schema") or (value is not None and value.startswith("pg_")):
            raise ValueError("application schema required")
        return value

    @model_validator(mode="after")
    def _binding_pair(self) -> Self:
        if (self.expected_schema is None) != (self.expected_database_role is None):
            raise ValueError("expected schema and database role must be supplied together")
        return self

    @field_validator("object_root")
    @classmethod
    def _absolute_root(cls, value: str) -> str:
        if "\x00" in value or not Path(value).is_absolute():
            raise ValueError("absolute object root required")
        return value


class RuntimeSettings(_Settings):
    environment: Literal["local", "staging"]
    public_origin: Annotated[str, Field(min_length=1, max_length=2048)]
    identity: IdentitySettings
    storage: StorageSettings
    api: ApiSettings = Field(default_factory=ApiSettings)
    worker: WorkerSettings = Field(default_factory=WorkerSettings)

    @field_validator("public_origin")
    @classmethod
    def _origin_shape(cls, value: str) -> str:
        parsed = _url(value)
        if parsed.path not in ("", "/"):
            raise ValueError("public origin must not contain an application path")
        return value

    @model_validator(mode="after")
    def _cross_boundary_rules(self) -> Self:
        public = _url(self.public_origin)
        callback = _url(self.identity.redirect_uri)
        if _origin(public) != _origin(callback) or callback.path in ("", "/"):
            raise ValueError("callback must be a registered path on the configured public origin")
        for value in (public, callback, _url(self.identity.issuer)):
            if value.scheme != "https" and (self.environment != "local" or not _loopback(value.hostname)):
                raise ValueError("HTTPS required except explicit local loopback testing")
        if self.planned_database_connections > self.storage.postgres_connection_budget:
            raise ValueError("API plus worker plus admin connections exceed the configured budget")
        return self

    @property
    def planned_database_connections(self) -> int:
        return (
            self.api.processes * self.api.database_pool_size
            + self.worker.processes * self.worker.database_pool_size
            + self.storage.admin_connection_reserve
        )


def load_runtime_settings(config_path: Path) -> RuntimeSettings:
    """Read a bounded TOML file; never implicitly load .env or other configs."""
    try:
        with config_path.open("rb") as stream:
            raw = stream.read(_MAX_CONFIG_BYTES + 1)
        if len(raw) > _MAX_CONFIG_BYTES:
            raise ConfigurationRejected(("configuration_file",))
        data = tomllib.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        raise ConfigurationRejected(("configuration_file",)) from None
    try:
        settings = RuntimeSettings.model_validate(data)
    except ValidationError as error:
        locations: set[str] = set()
        for item in error.errors(include_input=False, include_context=False, include_url=False):
            first = item["loc"][0] if item["loc"] else "configuration"
            locations.add(first if isinstance(first, str) and first in RuntimeSettings.model_fields else "configuration")
        raise ConfigurationRejected(tuple(sorted(locations))) from None
    if settings.environment == "staging" and settings.storage.expected_schema is None:
        raise ConfigurationRejected(("storage",))
    return settings


@dataclass(frozen=True)
class RuntimeSecrets:
    database_url: SecretStr
    oidc_client_secret: SecretStr | None


def resolve_database_url(settings: RuntimeSettings, environment: Mapping[str, str]) -> SecretStr:
    """Read only the declared DB variable and validate its shape offline."""
    name = settings.storage.database_url_env
    database_url = environment.get(name, "")
    try:
        if not database_url or _CONTROL.search(database_url):
            raise ValueError("missing or invalid database URL")
        parsed = urlsplit(database_url)
        if parsed.scheme != "postgresql" or not parsed.hostname or not parsed.username:
            raise ValueError("PostgreSQL connection URL required")
        if parsed.fragment or not parsed.path.strip("/") or parsed.port == 0:
            raise ValueError("database name and valid port required")
    except ValueError:
        raise ConfigurationRejected(("storage.database_url_env",)) from None
    return SecretStr(database_url)


def resolve_runtime_secrets(settings: RuntimeSettings, environment: Mapping[str, str]) -> RuntimeSecrets:
    """Read declared variables only; validate DSN shape without contacting it."""
    database_url = resolve_database_url(settings, environment)
    secret_name = settings.identity.client_secret_env
    secret = environment.get(secret_name, "") if secret_name else None
    if secret_name and (not secret or not secret.strip()):
        raise ConfigurationRejected(("identity.client_secret_env",))
    return RuntimeSecrets(database_url, SecretStr(secret) if secret is not None else None)
