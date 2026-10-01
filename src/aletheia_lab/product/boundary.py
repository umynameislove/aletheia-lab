"""Public error boundary for the P6 product service."""

from __future__ import annotations

import re
import unicodedata
from typing import Final

_ERROR_CODE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_CONTROL_CHARACTER_PATTERN: Final[re.Pattern[str]] = re.compile(r"[\x00-\x1f\x7f]")
_ABSOLUTE_PATH_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\[^\\\s]+[\\/]|(?:^|\s)/\S+)",
)
_SENSITIVE_TERM_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?i)(?:api[_ -]?key|authorization|bearer|credential|password|secret|token|sk-[A-Za-z0-9])"
)
_MAX_SAFE_MESSAGE_LENGTH: Final[int] = 256


class ProductError(ValueError):
    """A stable public failure containing no private diagnostic detail."""

    __slots__ = ("_code", "_safe_message")

    def __init__(self, code: str, safe_message: str) -> None:
        checked_code = _validate_error_code(code)
        checked_message = _validate_safe_message(safe_message)
        self._code = checked_code
        self._safe_message = checked_message
        super().__init__(checked_message)

    @property
    def code(self) -> str:
        """Return the stable remediation code."""

        return self._code

    @property
    def safe_message(self) -> str:
        """Return the validated user-facing message."""

        return self._safe_message


def _validate_error_code(code: str) -> str:
    if not isinstance(code, str) or _ERROR_CODE_PATTERN.fullmatch(code) is None:
        raise ValueError("product error code must be a stable lowercase identifier")
    return code


def _validate_safe_message(message: str) -> str:
    if not isinstance(message, str):
        raise TypeError("product safe message must be text")
    if (
        not message
        or message != message.strip()
        or len(message) > _MAX_SAFE_MESSAGE_LENGTH
        or _CONTROL_CHARACTER_PATTERN.search(message) is not None
        or unicodedata.normalize("NFC", message) != message
    ):
        raise ValueError("product safe message must be short, normalized, and single-line")
    if _ABSOLUTE_PATH_PATTERN.search(message) is not None:
        raise ValueError("product safe message must not contain an absolute path")
    if _SENSITIVE_TERM_PATTERN.search(message) is not None:
        raise ValueError("product safe message must not contain sensitive material")
    return message
