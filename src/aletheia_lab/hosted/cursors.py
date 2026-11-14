"""Bounded signed continuations; authorization remains the caller's transaction."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import cast

from itsdangerous import BadData, SignatureExpired, URLSafeTimedSerializer
from pydantic import SecretStr

_PURPOSE = re.compile(r"[a-z][a-z0-9._:-]{0,63}")
_TOKEN = re.compile(r"[A-Za-z0-9_.-]{1,4096}")


class CursorInvalid(ValueError):
    def __init__(self) -> None:
        super().__init__("The continuation is invalid for this request.")


class CursorExpired(ValueError):
    def __init__(self) -> None:
        super().__init__("The continuation has expired.")


def _bounded(value: object, depth: int = 0) -> None:
    if depth > 8:
        raise CursorInvalid()
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if not -(2 ** 63) <= value <= 2 ** 63 - 1:
            raise CursorInvalid()
        return
    if isinstance(value, str):
        if len(value) > 512 or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise CursorInvalid()
        return
    if isinstance(value, dict) and len(value) <= 32:
        _bounded_members(value, depth)
        return
    if isinstance(value, list) and len(value) <= 32:
        for item in value:
            _bounded(item, depth + 1)
        return
    raise CursorInvalid()


def _bounded_members(value: dict[object, object], depth: int) -> None:
    for key, item in value.items():
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise CursorInvalid()
        _bounded(key, depth + 1)
        _bounded(item, depth + 1)


def _canonical(value: object) -> str:
    _bounded(value)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if len(encoded) > 2048:
        raise CursorInvalid()
    return encoded


class ScopedCursors:
    def __init__(self, secret: SecretStr, max_age_seconds: int = 300) -> None:
        if (not isinstance(secret, SecretStr) or not 32 <= len(secret.get_secret_value().encode()) <= 4096
                or secret.get_secret_value().strip() != secret.get_secret_value()
                or any(ord(char) < 32 or ord(char) == 127 for char in secret.get_secret_value())
                or type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 3600):
            raise ValueError("A bounded shared cursor key and lifetime are required.")
        self._serializer = URLSafeTimedSerializer(secret.get_secret_value(), signer_kwargs={"digest_method": hashlib.sha256})
        self._max_age = max_age_seconds

    @staticmethod
    def _salt(purpose: str) -> str:
        if not isinstance(purpose, str) or _PURPOSE.fullmatch(purpose) is None:
            raise CursorInvalid()
        return "aletheia-cursor/v1:" + purpose

    def issue(self, *, purpose: str, binding: dict[str, object], value: dict[str, object]) -> str:
        if not isinstance(binding, dict) or not isinstance(value, dict):
            raise CursorInvalid()
        payload = {"v": 1, "purpose": purpose, "binding": binding, "value": value}
        _canonical(payload)
        token = self._serializer.dumps(payload, salt=self._salt(purpose))
        if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
            raise CursorInvalid()
        return token

    def read(self, *, purpose: str, binding: dict[str, object], cursor: str) -> dict[str, object]:
        if not isinstance(binding, dict) or not isinstance(cursor, str) or _TOKEN.fullmatch(cursor) is None:
            raise CursorInvalid()
        expected = _canonical(binding)
        try:
            payload = self._serializer.loads(cursor, salt=self._salt(purpose))
        except (BadData, ValueError, TypeError):
            raise CursorInvalid() from None
        if (not isinstance(payload, dict) or set(payload) != {"v", "purpose", "binding", "value"}
                or type(payload["v"]) is not int or payload["v"] != 1 or payload["purpose"] != purpose
                or not isinstance(payload["binding"], dict) or not isinstance(payload["value"], dict)):
            raise CursorInvalid()
        _canonical(payload)
        if not hmac.compare_digest(_canonical(payload["binding"]), expected):
            raise CursorInvalid()
        try:
            self._serializer.loads(cursor, salt=self._salt(purpose), max_age=self._max_age)
        except SignatureExpired:
            raise CursorExpired() from None
        except (BadData, ValueError, TypeError):
            raise CursorInvalid() from None
        return cast(dict[str, object], payload["value"])
