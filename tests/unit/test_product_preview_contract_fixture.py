from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

from aletheia_lab.product.candidates import MappingCandidates

_FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "p6_preview_import_v2.json"
_FIXTURE_SHA256 = "8e04f2e3b6fb854c8a22e79b0f47fef1663191df146730337b1a6b8434aad4c6"
_PREVIEW_KEYS = {
    "preview_id",
    "included_count",
    "redacted_count",
    "excluded_count",
    "withheld_count",
    "blockers",
    "warnings",
    "outbound_categories",
    "mapping_candidates",
}
_ISSUE_KEYS = {
    "code",
    "severity",
    "stage",
    "message",
    "relative_path",
    "occurrences",
}


def test_shared_preview_fixture_has_exact_contract_and_stable_sha256() -> None:
    fixture_bytes = _FIXTURE_PATH.read_bytes()
    assert hashlib.sha256(fixture_bytes).hexdigest() == _FIXTURE_SHA256
    decoded = json.loads(fixture_bytes)
    assert isinstance(decoded, dict)
    preview = cast(dict[str, object], decoded)
    assert set(preview) == _PREVIEW_KEYS
    assert isinstance(preview["preview_id"], str)
    assert str(preview["preview_id"]).startswith("p6-preview-")
    for key in (
        "included_count",
        "redacted_count",
        "excluded_count",
        "withheld_count",
    ):
        assert isinstance(preview[key], int)
        assert cast(int, preview[key]) >= 0

    blockers = cast(list[dict[str, object]], preview["blockers"])
    warnings = cast(list[dict[str, object]], preview["warnings"])
    assert all(set(issue) == _ISSUE_KEYS for issue in (*blockers, *warnings))
    assert all(issue["severity"] == "blocker" for issue in blockers)
    assert all(issue["severity"] in {"info", "warning", "error"} for issue in warnings)

    candidates_json = json.dumps(
        preview["mapping_candidates"],
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    candidates = MappingCandidates.model_validate_json(candidates_json)
    assert candidates.model_dump(mode="json") == preview["mapping_candidates"]
    assert set(candidates.model_dump(mode="json")) == {
        "targets",
        "metric_sources",
        "configs",
        "runs",
    }

    unavailable = next(value for value in candidates.targets if not value.available)
    assert unavailable.unavailable_reason == "sensitive_fields_withheld"
    assert unavailable.relative_path is None
    assert unavailable.field_names == ()
    paths = {
        value.relative_path: value.records_path_candidates
        for value in candidates.metric_sources
    }
    assert paths["metrics/validation.json"] == (("runs",), ("records",))
    assert paths["metrics/train.csv"] == ()
    assert paths["metrics/legacy.json"] == ()
    assert paths["metrics/flat.json"] == ((),)
    metric_names = {
        value.relative_path: value.metric_name_candidates
        for value in candidates.metric_sources
        if value.available
    }
    assert metric_names == {
        "metrics/validation.json": ("accuracy", "loss"),
        "metrics/train.csv": ("loss",),
        "metrics/legacy.json": (),
        "metrics/flat.json": ("accuracy",),
    }
    withheld_metric = next(
        value for value in candidates.metric_sources if not value.available
    )
    assert withheld_metric.unavailable_reason == "sensitive_fields_withheld"
    assert withheld_metric.relative_path is None
    assert withheld_metric.field_names == ()
    assert withheld_metric.metric_name_candidates == ()
