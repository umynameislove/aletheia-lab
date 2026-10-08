"""Canonical validation for product analysis and follow-up questions."""

from __future__ import annotations

import unicodedata
from typing import Final

from aletheia_lab.product.boundary import ProductError

_QUESTION_INVALID_MESSAGE: Final[str] = "The analysis question is not valid."


def checked_product_question(question: str) -> str:
    """Return one normalized, bounded question or fail with a safe public error."""

    if (
        not isinstance(question, str)
        or not question
        or question != question.strip()
        or len(question) > 2000
        or unicodedata.normalize("NFC", question) != question
        or any(ord(character) < 32 or ord(character) == 127 for character in question)
    ):
        raise ProductError("invalid_question", _QUESTION_INVALID_MESSAGE)
    return question


__all__ = ["checked_product_question"]
