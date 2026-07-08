from pathlib import Path

from aletheia_lab.benchmark.manifest import load_case


def test_load_minimal_case() -> None:
    case = load_case(Path("tests/fixtures/minimal_case.json"))

    assert case.case_id == "p1-data-drift-0001"
    assert case.ground_truth.cause_label == "data_drift"
    assert case.split == "dev"
