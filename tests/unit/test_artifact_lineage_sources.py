"""Fixed retained controls and alias symmetries without source fitting."""

from __future__ import annotations

from copy import deepcopy

import pytest
from test_artifact_lineage_policy import observations

from aletheia_lab.evaluation import artifact_lineage_sources as sources
from aletheia_lab.evaluation.artifact_lineage_sources import cases_from_observations, validate_cases


def test_read_only_adapter_uses_only_pinned_development_receipt(tmp_path, monkeypatch):
    memory = tmp_path / "private"
    retained = memory / "m4-artifact-binding-development-v2"
    retained.mkdir(parents=True)
    (retained / "receipt.json").write_text("retained fixture")
    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        return observations()

    monkeypatch.setattr(sources, "retained_artifact_observations", read)
    cases, hashes = sources.retained_development_cases(root=tmp_path, memory_root=memory)
    assert len(cases) == 64
    assert list(hashes) == ["receipt.json"]
    assert calls == [
        {"root": tmp_path, "cell_dir": retained, "expected_receipt_sha256": sources.RECEIPT_SHA256}
    ]
    assert sources.SOURCE_CLUSTER == "retained-online-shoppers-development"
    assert "new-sources" not in str(calls)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_case",
        "duplicate_case",
        "truth",
        "pair",
        "reference",
        "source",
        "sham",
        "missing_alias_leak",
        "missing_pair_leak",
        "full_pair",
        "manifest_full",
    ],
)
def test_incomplete_mislabeled_or_leaky_census_is_rejected(mutation):
    cases = deepcopy(cases_from_observations(observations()))
    index = {(case["case_kind"], case["alias_order"], case["condition"]): case for case in cases}
    if mutation == "missing_case":
        cases.pop()
    elif mutation == "duplicate_case":
        cases[-1] = deepcopy(cases[0])
    elif mutation == "truth":
        cases[0]["truth"] = "binding_fault"
    elif mutation == "pair":
        cases[0]["pair_id"] = "pretend-new-family"
    elif mutation == "reference":
        cases[0]["reference"] = {"compatible": [], "witness_available": False}
    elif mutation == "source":
        cases[0]["source_cluster"] = "invented-independent-source"
    elif mutation == "sham":
        index[("sham", 0, "misleading")]["context"] = index[
            ("manifest_text_only", 0, "misleading")
        ]["context"]
    elif mutation == "missing_alias_leak":
        index[("healthy", 1, "missing_key")]["context"] = index[("faulty", 0, "missing_key")][
            "context"
        ]
    elif mutation == "missing_pair_leak":
        index[("legitimate_B", 0, "missing_key")]["context"] = index[("healthy", 0, "missing_key")][
            "context"
        ]
    elif mutation == "full_pair":
        index[("legitimate_B", 0, "full")]["context"] = index[("faulty", 0, "full")]["context"]
    else:
        index[("manifest_text_only", 0, "full")]["context"] = index[("legitimate_B", 0, "full")][
            "context"
        ]
    with pytest.raises(ValueError):
        validate_cases(cases)


def test_observation_census_and_loader_control_truth_cannot_be_invented():
    bad = observations()
    del bad["sham"]
    with pytest.raises(ValueError, match="all six"):
        cases_from_observations(bad)
    bad = observations()
    bad["healthy"] = bad["faulty"]
    with pytest.raises(ValueError, match="truth"):
        cases_from_observations(bad)
