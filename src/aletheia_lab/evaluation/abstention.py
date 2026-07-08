"""Abstention evaluation helpers."""

from __future__ import annotations


ABSTENTION_MARKERS = (
    "insufficient evidence",
    "not enough evidence",
    "cannot determine",
    "không đủ bằng chứng",
    "chưa đủ bằng chứng",
)


def detects_abstention(text: str) -> bool:
    """Return true if the text appears to abstain."""

    normalized = text.casefold()
    return any(marker in normalized for marker in ABSTENTION_MARKERS)
