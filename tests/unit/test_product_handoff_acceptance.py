"""Current projection checks are distinct from historical research acceptance."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation.product_handoff_acceptance import check_product_handoff
from aletheia_lab.product import ProductService
from aletheia_lab.product.view import ProductView

_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE = _ROOT / "tests/fixtures/synthetic_p6_view.json"
_PIN = "0f5413427d1adf184639bff08181d5a81d41e25dad7c45bfb71788742d957fd2"
_SCRIPT = _ROOT / "scripts/accept_product_handoff.py"


def _reference() -> dict[str, Any]:
    raw = _FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _PIN
    return json.loads(raw)


def _schema_valid(value: dict[str, Any]) -> None:
    ProductView.model_validate_json(json.dumps(value, allow_nan=False))


def test_current_demo_matches_pinned_reference_without_claiming_consumer_execution(
    tmp_path: Path,
) -> None:
    candidate = ProductService(tmp_path / "store").demo_view()
    report = check_product_handoff(_reference(), candidate)

    assert report["status"] == "current_product_projection_acceptance_pass"
    assert report["projection_scope"] == "demo"
    assert report["reference_sha256"] == report["candidate_sha256"]
    assert report["independent_families"] is None
    assert report["backend_execution"] == "not_attested_by_projection_check"
    assert report["api_dashboard_integration"] == "not_executed"


@pytest.mark.parametrize(
    "mutation", ["disposition", "causal_claim", "support", "missing", "metric"]
)
def test_equal_schema_valid_bad_reference_and_candidate_do_not_self_authorize(
    mutation: str,
) -> None:
    bad = _reference()
    if mutation == "disposition":
        bad["result"]["disposition"] = "supported"
    elif mutation == "causal_claim":
        bad["claims"][0].update(claim_type="cause_assertion", max_strength="bounded_hypothesis")
    elif mutation == "support":
        bad["claims"][0]["support"] = "unsupported"
    elif mutation == "missing":
        bad["claims"][0]["missing_evidence"] = []
    else:
        bad["snapshot"]["metric_changes"][0]["adverse_status"] = "not_adverse"
    _schema_valid(bad)
    with pytest.raises(ValueError):
        check_product_handoff(bad, deepcopy(bad))


@pytest.mark.parametrize(
    "mutation", ["wording", "reproduction", "model", "denominator", "question", "missing_text"]
)
def test_schema_valid_content_changes_cannot_hide_behind_unchanged_ids_or_hashes(
    mutation: str,
) -> None:
    expected, changed = _reference(), _reference()
    if mutation == "wording":
        changed["claims"][0]["text"] = "The configuration caused the decrease."
    elif mutation == "reproduction":
        changed["evidence"][0]["reproduction_ref"]["record_id"] = "foreign-comparison-001"
    elif mutation == "model":
        changed["runtime"]["model"] = "wrong-declared-model"
        changed["conversation"]["turns"][0]["runtime"]["model"] = "wrong-declared-model"
    elif mutation == "denominator":
        changed["result"]["denominators"]["outputs"] = 0
    elif mutation == "question":
        changed["conversation"]["turns"][0]["question"] = "A different request?"
    else:
        changed["conversation"]["turns"][0]["missing_evidence"][0] = "Nothing else is missing."
    _schema_valid(changed)
    assert changed["snapshot"]["sha256"] == expected["snapshot"]["sha256"]
    assert changed["evidence"][0]["source_sha256"] == expected["evidence"][0]["source_sha256"]
    with pytest.raises(ValueError, match="trusted expected view"):
        check_product_handoff(expected, changed)


@pytest.mark.parametrize(
    "delta,direction,threshold,adverse",
    [
        (-0.08, "higher_is_better", 0.08, True),
        (-0.08, "higher_is_better", 0.081, False),
        (-0.08, "lower_is_better", 0.0, False),
        (0.08, "lower_is_better", 0.08, True),
        (0.08, "higher_is_better", 0.0, False),
    ],
)
def test_metric_policy_checks_direction_and_inclusive_decimal_threshold(
    delta: float, direction: str, threshold: float, adverse: bool
) -> None:
    reference = _reference()
    definition = reference["snapshot"]["metric_definitions"][0]
    definition.update(direction=direction, regression_threshold=threshold)
    change = reference["snapshot"]["metric_changes"][0]
    change["before"]["value"] = 0.82
    change["after"]["value"] = 0.74 if delta < 0 else 0.9
    change.update(
        delta=delta,
        kind="decreased" if delta < 0 else "increased",
        adverse_status="adverse" if adverse else "not_adverse",
    )
    check_product_handoff(reference, deepcopy(reference))
    change["adverse_status"] = "not_adverse" if adverse else "adverse"
    _schema_valid(reference)
    with pytest.raises(ValueError, match="declared policy"):
        check_product_handoff(reference, deepcopy(reference))


def test_technical_failure_is_not_promoted_to_a_successful_diagnosis() -> None:
    reference = _reference()
    reference["result"].update(status="technical_failure", disposition=None)
    reference["result"]["denominators"]["claims"] = 0
    reference["claims"] = []
    reference["conversation"]["turns"][0]["missing_evidence"] = []
    reference["graph"]["nodes"] = [
        node for node in reference["graph"]["nodes"] if node["kind"] in {"Snapshot", "EvidenceItem"}
    ]
    reference["graph"]["edges"] = [reference["graph"]["edges"][0]]

    check_product_handoff(reference, deepcopy(reference))
    promoted = deepcopy(reference)
    promoted["result"]["disposition"] = "abstain"
    with pytest.raises(ValueError):
        check_product_handoff(reference, promoted)


@pytest.mark.parametrize("field", ["schema_version", "step"])
def test_schema_defaults_do_not_authorize_an_incomplete_projection(field: str) -> None:
    incomplete = _reference()
    if field == "schema_version":
        del incomplete[field]
    else:
        del incomplete["snapshot"]["metric_changes"][0]["before"][field]
    _schema_valid(incomplete)
    with pytest.raises(ValueError, match="explicitly include"):
        check_product_handoff(incomplete, deepcopy(incomplete))


def test_comparison_preserves_json_types_and_raw_array_order() -> None:
    reference, changed = _reference(), _reference()
    changed["graph"]["nodes"].reverse()
    _schema_valid(changed)
    with pytest.raises(ValueError, match="trusted expected view"):
        check_product_handoff(reference, changed)
    changed = _reference()
    changed["result"]["denominators"]["claims"] = True
    with pytest.raises(ValueError):
        check_product_handoff(reference, changed)
    changed = _reference()
    changed["claims"] = tuple(changed["claims"])
    with pytest.raises(ValueError, match="JSON values"):
        check_product_handoff(reference, changed)


def _command(candidate: Path, *, pin: str = _PIN) -> list[str]:
    return [
        sys.executable,
        str(_SCRIPT),
        "--reference",
        str(_FIXTURE),
        "--expected-reference-sha256",
        pin,
        "--candidate",
        str(candidate),
    ]


def test_read_only_cli_pins_reference_and_checks_complete_projection(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps(_reference()), encoding="utf-8")
    before = candidate.read_bytes()
    completed = subprocess.run(_command(candidate), capture_output=True, check=False, timeout=30)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    report = json.loads(completed.stdout)
    assert report["reference_file_sha256"] == _PIN
    assert report["api_dashboard_integration"] == "not_executed"
    assert candidate.read_bytes() == before
    failed = subprocess.run(_command(candidate, pin="0" * 64), capture_output=True, timeout=30)
    assert failed.returncode == 1
    assert json.loads(failed.stdout) == {"status": "product_handoff_acceptance_failed_closed"}
    assert str(tmp_path).encode() not in failed.stdout


@pytest.mark.parametrize("raw", [b'{"duplicate":1,"duplicate":2}', b'{"value":NaN}', b"[]"])
def test_cli_rejects_ambiguous_or_nonfinite_json_without_echoing_payload(
    tmp_path: Path, raw: bytes
) -> None:
    candidate = tmp_path / "candidate.json"
    candidate.write_bytes(raw)
    completed = subprocess.run(_command(candidate), capture_output=True, timeout=30)
    assert completed.returncode == 1
    assert json.loads(completed.stdout) == {"status": "product_handoff_acceptance_failed_closed"}
    assert completed.stderr == b""
    assert candidate.read_bytes() == raw
