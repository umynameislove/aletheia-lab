"""Canonical serialization for the Phase 2 benchmark contract.

Phase 2 identity hashes must be reproducible across processes, operating
systems, locales and Python versions. That requires a single serialization
definition rather than an ad-hoc ``json.dumps`` call at each site.

Rules enforced here:

* text is normalized to Unicode NFC so visually identical strings hash equally;
* mappings are emitted with sorted keys and compact separators;
* ``ensure_ascii`` is disabled so the byte stream depends on content, not on
  escaping choices;
* numbers are normalized to the shortest exact decimal form without trailing
  zeros, so ``0.80`` and ``0.8`` cannot produce two different family IDs;
* ``NaN``, infinities and negative zero are rejected instead of silently
  serialized;
* sequences keep their declared order; callers that need order-insensitive
  semantics must sort before serializing.

This module deliberately does not reuse the Phase 1 canonical helper. Phase 1
serializes with ``ensure_ascii=True`` and no numeric normalization; changing it
would alter frozen Phase 1 hashes, and silently reusing it here would make two
different definitions share one name.
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from decimal import Decimal
from typing import Final, Literal

P2_CANONICAL_SERIALIZATION_VERSION: Final[Literal["p2-canonical/v1"]] = "p2-canonical/v1"

_MAX_KEY_LENGTH: Final[int] = 256


def normalize_text(value: str) -> str:
    """Return ``value`` in Unicode NFC form.

    Two strings that differ only by Unicode composition describe the same text
    and must therefore produce the same canonical bytes.
    """

    if not isinstance(value, str):  # pragma: no cover - guarded by callers
        raise TypeError(f"expected str, got {type(value).__name__}")
    return unicodedata.normalize("NFC", value)


def normalize_number(value: int | float) -> str:
    """Return the shortest exact decimal string for ``value``.

    Integers keep their literal form. Floats are converted through their
    shortest round-trip repr and then stripped of trailing zeros, so ``1.0``
    becomes ``"1"`` and ``0.80`` becomes ``"0.8"``. Non-finite values and
    negative zero are rejected because they have no single canonical form and
    would let two different payloads collide or diverge unpredictably.
    """

    if isinstance(value, bool):
        raise TypeError("bool is not a canonical number")
    if isinstance(value, int):
        return str(value)
    if not isinstance(value, float):  # pragma: no cover - guarded by callers
        raise TypeError(f"expected int or float, got {type(value).__name__}")
    if not math.isfinite(value):
        raise ValueError(f"non-finite number is not canonical: {value!r}")
    if value == 0.0 and math.copysign(1.0, value) < 0:
        raise ValueError("negative zero is not canonical")
    decimal = Decimal(repr(value)).normalize()
    if decimal == 0:
        return "0"
    text = format(decimal, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _canonicalize(payload: object) -> object:
    """Recursively rewrite ``payload`` into canonical primitives."""

    if payload is None or isinstance(payload, bool):
        return payload
    if isinstance(payload, str):
        return normalize_text(payload)
    if isinstance(payload, int | float):
        return normalize_number(payload)
    if isinstance(payload, dict):
        canonical: dict[str, object] = {}
        for raw_key, raw_value in payload.items():
            if not isinstance(raw_key, str):
                raise TypeError(f"canonical mapping keys must be str, got {type(raw_key).__name__}")
            key = normalize_text(raw_key)
            if not key:
                raise ValueError("canonical mapping keys must not be empty")
            if len(key) > _MAX_KEY_LENGTH:
                raise ValueError(f"canonical mapping key is too long: {len(key)}")
            if key in canonical:
                raise ValueError(f"canonical mapping has a duplicate key after NFC: {key!r}")
            canonical[key] = _canonicalize(raw_value)
        return canonical
    if isinstance(payload, list | tuple):
        return [_canonicalize(item) for item in payload]
    raise TypeError(f"value is not canonically serializable: {type(payload).__name__}")


def canonical_json(payload: object) -> str:
    """Serialize ``payload`` to the Phase 2 canonical JSON string."""

    return json.dumps(
        _canonicalize(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_bytes(payload: object) -> bytes:
    """Serialize ``payload`` to canonical UTF-8 bytes."""

    return canonical_json(payload).encode("utf-8")


def canonical_sha256(payload: object) -> str:
    """Return the lowercase SHA-256 digest of the canonical byte stream."""

    return hashlib.sha256(canonical_bytes(payload)).hexdigest()
