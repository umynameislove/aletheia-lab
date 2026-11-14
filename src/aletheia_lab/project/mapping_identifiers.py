"""One frozen Unicode repertoire for mapping identities and browser hints."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

from .identity import normalize_text

POLICY = cast(dict[str, str], json.loads(Path(__file__).with_suffix(".json").read_text(encoding="utf-8")))


def codepoint_ranges(value: str) -> list[tuple[int, int]]:
    return [(int(token.split("-")[0], 16), int(token.split("-")[-1], 16)) for token in value.split()]


def _character_class(value: str) -> str:
    return "".join(chr(first) + ("-" + chr(last) if first != last else "")
                   for first, last in codepoint_ranges(value))


_IGNORABLE_IDENTIFIER_CHARACTERS = frozenset(
    chr(codepoint) for first, last in codepoint_ranges(POLICY["ignored"]) for codepoint in range(first, last + 1)
)
_LETTERS_NUMBERS = _character_class(POLICY["letters_numbers"])
_MARKS = _character_class(POLICY["marks"])
_IDENTIFIER = re.compile(f"^[{_LETTERS_NUMBERS}][{_LETTERS_NUMBERS}{_MARKS}._:@+\\-]{{0,127}}$")
_LABEL = re.compile(f"^[{_character_class(POLICY['labels'])}]+$")


def mapping_identifier(value: str, *, label: str) -> str:
    normalize_text(value, label=label, max_length=128)
    if _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{label} must be a safe Unicode identifier")
    return value


def metric_label(value: str, *, label: str, max_length: int = 128) -> str:
    normalize_text(value, label=label, max_length=max_length)
    if _LABEL.fullmatch(value) is None:
        raise ValueError(f"{label} must contain supported visible Unicode text")
    return value
