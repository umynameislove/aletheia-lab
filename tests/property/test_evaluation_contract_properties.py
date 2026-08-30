"""Property tests for neutral execution canonicalization."""

from __future__ import annotations

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from aletheia_lab.evaluation.execution_contracts import (
    EXECUTION_CANONICAL_SCHEMA_VERSION,
    canonical_execution_json,
)


def _oracle_execution_json(payload: object) -> str:
    return json.dumps(
        {
            "schema_version": EXECUTION_CANONICAL_SCHEMA_VERSION,
            "payload": payload,
        },
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


@settings(max_examples=25)
@given(st.permutations(("alpha", "bravo", "charlie", "delta")))
def test_canonical_json_is_independent_of_mapping_insertion_order(
    keys: tuple[str, ...],
) -> None:
    values = {
        "alpha": 1,
        "bravo": 2,
        "charlie": 3,
        "delta": 4,
    }
    payload = {key: values[key] for key in keys}

    assert canonical_execution_json(payload) == _oracle_execution_json(payload)
