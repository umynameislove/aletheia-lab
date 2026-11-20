"""Strict, pinned capacity inputs; no credentials are part of a public report."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from aletheia_lab.hosted.content_hashing import content_hasher

_NAME = r"^[A-Za-z][A-Za-z0-9_]{0,63}$"
_SHA = r"^[0-9a-f]{64}$"
Role = Literal["owner", "admin", "analyst", "reviewer", "viewer"]
Disposition = Literal["recipe", "dedicated", "browser", "external_idp", "unsupported", "not_run"]


class Model(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, hide_input_in_errors=True, allow_inf_nan=False
    )


class Assertion(Model):
    pointer: str = Field(max_length=512)
    check: Literal["equals", "nonempty", "contains", "length_le", "at_least"]
    value: object = None


class ActionBudget(Model):
    deadline_seconds: float = Field(gt=0, le=300)
    p95_seconds: float = Field(gt=0, le=300)
    p99_seconds: float = Field(gt=0, le=300)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if not self.p95_seconds <= self.p99_seconds <= self.deadline_seconds:
            raise ValueError("Ordered p95/p99/action deadline required.")
        return self


class Step(Model):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"] = "GET"
    path: str = Field(min_length=1, max_length=2048)
    statuses: tuple[int, ...] = (200,)
    body: dict[str, object] | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    assertions: tuple[Assertion, ...] = ()
    captures: dict[str, str] = Field(default_factory=dict)
    follow_job: bool = False
    download_descriptor: bool = False
    operation_id: str = Field(pattern=r"^P(?:0[1-9]|[12][0-9]|3[0-8])$")

    @model_validator(mode="after")
    def safe(self) -> Self:
        if not self.path.startswith("/v1/") or ".." in self.path or "\\" in self.path:
            raise ValueError("Scoped relative API path required.")
        if not self.statuses or any(not 100 <= n <= 599 for n in self.statuses):
            raise ValueError("Explicit response status expectations required.")
        allowed = {"if-match", "content-type", "range", "last-event-id"}
        if any(k.lower() not in allowed for k in self.headers):
            raise ValueError("Recipe headers cannot override identity or target.")
        if any(re.fullmatch(_NAME, k) is None for k in self.captures):
            raise ValueError("Bounded capture names required.")
        if self.follow_job and 202 not in self.statuses:
            raise ValueError("Following a job requires a durable ACK expectation.")
        if self.follow_job and self.download_descriptor:
            raise ValueError("A step has one completion protocol.")
        return self


class Journey(Model):
    name: str = Field(pattern=_NAME)
    weight: int = Field(ge=1, le=100000)
    roles: tuple[Role, ...]
    sizes: tuple[Literal["S", "M", "L"], ...] = ("S", "M", "L")
    deadline_seconds: float = Field(gt=0, le=300)
    size_budgets: dict[str, ActionBudget] = Field(default_factory=dict)
    expected: Literal["positive", "deny"] = "positive"
    steps: tuple[Step, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def meaningful(self) -> Self:
        if not self.roles or not self.sizes:
            raise ValueError("Journey needs an actor role and physical size stratum.")
        if self.size_budgets and set(self.size_budgets) != set(self.sizes):
            raise ValueError("Size-specific budgets must cover every journey stratum.")
        if not any(s.assertions or s.follow_job or s.download_descriptor for s in self.steps):
            raise ValueError("A status code alone is not a business correctness oracle.")
        if self.expected == "deny" and any(
            not all(n in (401, 403, 404) for n in s.statuses) for s in self.steps
        ):
            raise ValueError("Negative security journeys require expected denials.")
        return self

    def deadline(self, size: str) -> float:
        budget = self.size_budgets.get(size)
        return budget.deadline_seconds if budget else self.deadline_seconds


class Actor(Model):
    actor_id: str = Field(pattern=_NAME)
    role: Role
    size: Literal["S", "M", "L"]
    cookie: SecretStr
    csrf: SecretStr
    values: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def safe(self) -> Self:
        if any(re.fullmatch(_NAME, k) is None for k in self.values):
            raise ValueError("Bounded actor template names required.")
        for value in (
            self.cookie.get_secret_value(),
            self.csrf.get_secret_value(),
            *self.values.values(),
        ):
            if len(value) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError("Actor values cannot contain control characters.")
        if not self.cookie.get_secret_value() or not self.csrf.get_secret_value():
            raise ValueError("Each actor requires its own server session and proof.")
        pairs = [p.strip().partition("=") for p in self.cookie.get_secret_value().split(";")]
        cookies = {name: value for name, separator, value in pairs if separator}
        if (
            len(pairs) != 2
            or set(cookies) != {"__Host-aletheia_session", "__Host-aletheia_csrf"}
            or cookies["__Host-aletheia_csrf"] != self.csrf.get_secret_value()
            or not cookies["__Host-aletheia_session"]
        ):
            raise ValueError("Exact distinct session and matching proof cookie required.")
        return self


class Profile(Model):
    schema_version: Literal["k17-capacity-profile/v1"] = "k17-capacity-profile/v1"
    target: str
    origin: str
    host: str
    tls_ca_file: str | None = None
    release_sha256: str = Field(pattern=_SHA)
    dataset_sha256: str = Field(pattern=_SHA)
    seed: int = Field(ge=0, le=2**63 - 1)
    shard_index: int = Field(default=0, ge=0, le=1023)
    shard_count: int = Field(default=1, ge=1, le=1024)
    mode: Literal["closed", "open"]
    duration_seconds: float = Field(gt=0, le=28800)
    warmup_seconds: float = Field(default=0.0, ge=0, le=3600)
    drain_seconds: float = Field(default=120.0, gt=0, le=600)
    arrivals_per_second: float = Field(default=1.0, gt=0, le=100000)
    max_inflight: int = Field(default=100, ge=1, le=50000)
    max_pending: int = Field(default=100, ge=1, le=100000)
    max_response_bytes: int = Field(default=33554432, ge=1024, le=268435456)
    poll_seconds: float = Field(default=2.0, ge=0.05, le=30)
    job_polling: Literal["status", "updates"] = "status"
    renew_every_seconds: float = Field(default=300.0, ge=0.1, le=600)
    think_seconds: float = Field(default=10.0, ge=0.01, le=30)
    jitter_fraction: float = Field(default=0.2, ge=0, le=0.9)
    active_window_seconds: Literal[30] = 30
    size_weights: dict[str, int] = Field(default_factory=lambda: {"S": 60, "M": 35, "L": 5})
    journeys: tuple[Journey, ...] = Field(min_length=1, max_length=64)
    operation_dispositions: dict[str, Disposition]
    scope: Literal["calibration", "primary"] = "calibration"
    cache_state: Literal["cold", "warm", "unknown"] = "unknown"
    run_label: str = Field(pattern=_NAME)

    @field_validator("target")
    @classmethod
    def target_url(cls, value: str) -> str:
        u = urlsplit(value)
        if (
            u.scheme not in ("http", "https")
            or not u.hostname
            or u.username
            or u.password
            or u.path not in ("", "/")
            or u.query
            or u.fragment
        ):
            raise ValueError("Target must be a bare authorized origin without credentials.")
        return value.rstrip("/")

    @model_validator(mode="after")
    def pinned(self) -> Self:
        if self.shard_index >= self.shard_count or len({j.name for j in self.journeys}) != len(
            self.journeys
        ):
            raise ValueError("Unique journeys and valid disjoint shard required.")
        if set(self.size_weights) != {"S", "M", "L"} or any(
            not 1 <= v <= 100000 for v in self.size_weights.values()
        ):
            raise ValueError("All physical size strata must retain positive locked weights.")
        if set(self.operation_dispositions) != {f"P{n:02}" for n in range(1, 39)}:
            raise ValueError("An explicit disposition for all 38 operations is required.")
        covered = {s.operation_id for j in self.journeys for s in j.steps}
        if any(v == "recipe" and k not in covered for k, v in self.operation_dispositions.items()):
            raise ValueError("Recipe coverage requires an actual step for that operation.")
        for value in (self.origin, self.host):
            if not value or len(value) > 2048 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError("Fixed trusted origin/host required.")
        return self

    def fingerprint(self) -> str:
        return content_hasher(
            json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


def load_actors(path: Path, profile: Profile) -> tuple[list[Actor], str]:
    """Private JSONL is bounded; shard by original row position, never by completed outcomes."""
    if path.is_symlink() or path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("Bounded regular private actor file required.")
    import os
    import stat

    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("Actor file must have mode 600 (or stricter).")
    actors, identities, credentials = [], set(), set()
    digest = content_hasher()
    with path.open("rb") as stream:
        for index in range(100001):
            line = stream.readline(65537)
            if not line:
                break
            if len(line) > 65536 or index == 100000:
                raise ValueError("Actor file bounds exceeded.")
            digest.update(line)
            actor = Actor.model_validate_json(line)
            session = next(
                p.strip().partition("=")[2]
                for p in actor.cookie.get_secret_value().split(";")
                if p.strip().startswith("__Host-aletheia_session=")
            )
            cookie = content_hasher(session.encode()).digest()
            if actor.actor_id in identities or cookie in credentials:
                raise ValueError("Every principal requires a distinct identity and credential.")
            identities.add(actor.actor_id)
            credentials.add(cookie)
            if index % profile.shard_count == profile.shard_index:
                actors.append(actor)
    if not actors:
        raise ValueError("The selected generator shard has no actors.")
    _verify_actor_journeys(actors, profile)
    return actors, digest.hexdigest()


def _verify_actor_journeys(actors: list[Actor], profile: Profile) -> None:
    for actor in actors:
        if not any(actor.role in j.roles and actor.size in j.sizes for j in profile.journeys):
            raise ValueError("Actor has no permitted pinned journey.")
