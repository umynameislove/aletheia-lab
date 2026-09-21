"""Wire serialization for the loopback Qwen calibration provider."""

from __future__ import annotations

import json


def provider_wire_json(payload: dict[str, object]) -> bytes:
    """Serialize the provider payload directly and deterministically as UTF-8 JSON."""

    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
