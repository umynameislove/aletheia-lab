from pathlib import Path

from aletheia_lab.evidence.leakage import bundle_has_leakage, find_forbidden_terms
from aletheia_lab.evidence.schema import EvidenceBundle


def test_find_forbidden_terms() -> None:
    matches = find_forbidden_terms("The answer key says data drift.", ["answer key", "label noise"])

    assert matches == ["answer key"]


def test_sample_evidence_has_no_ground_truth_leakage() -> None:
    bundle = EvidenceBundle.model_validate_json(
        Path("tests/fixtures/sample_evidence.json").read_text(encoding="utf-8")
    )

    assert bundle_has_leakage(bundle, ["injected_change", "answer key"]) is False
