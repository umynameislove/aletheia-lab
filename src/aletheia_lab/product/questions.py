"""Canonical validation for product analysis and follow-up questions."""

from __future__ import annotations

import re
import unicodedata
from typing import Final

from aletheia_lab.product.boundary import ProductError
from aletheia_lab.project.import_content import path_contains_sensitive_content

_QUESTION_INVALID_MESSAGE: Final[str] = "The analysis question is not valid."
COUNTERFACTUAL_NOT_AVAILABLE: Final[str] = "Counterfactual comparison: not_available"
_PRIVATE_PATH: Final[re.Pattern[str]] = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\[^\\\s]+[\\/]|(?:^|[\s\"'(\[=`])/(?!/)\S+)"
)
_CREDENTIAL_VALUE: Final[re.Pattern[str]] = re.compile(
    r"(?i)(?:\bsk-[A-Za-z0-9_-]{16,}\b|\bBearer\s+[A-Za-z0-9._~+/-]{8,}|"
    r"\b(?:api[_ -]?key|password|private[_ -]?key|secret|token)[\"']?\s*[:=]\s*"
    r"[\"']?[^\s\"']{8,})"
)


def checked_product_question(question: str) -> str:
    """Return one normalized, bounded question or fail with a safe public error."""

    if (
        not isinstance(question, str)
        or not question
        or question != question.strip()
        or len(question) > 2000
        or unicodedata.normalize("NFC", question) != question
        or any(
            ord(character) < 32 or ord(character) == 127 or 0xD800 <= ord(character) <= 0xDFFF
            for character in question
        )
        or path_contains_sensitive_content(question)
        or _PRIVATE_PATH.search(question) is not None
        or _CREDENTIAL_VALUE.search(question) is not None
        or question == COUNTERFACTUAL_NOT_AVAILABLE
    ):
        raise ProductError("invalid_question", _QUESTION_INVALID_MESSAGE)
    return question


__all__ = ["COUNTERFACTUAL_NOT_AVAILABLE", "checked_product_question"]
