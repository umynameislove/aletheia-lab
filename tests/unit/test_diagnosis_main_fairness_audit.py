from __future__ import annotations

import hashlib
import json
from pathlib import Path

from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256

ROOT = Path(__file__).resolve().parents[2]
AUDIT_V1 = ROOT / "configs/evaluation/diagnosis_information_path_fairness_audit.json"
AUDIT_V2 = ROOT / "configs/evaluation/diagnosis_information_path_fairness_audit_v2.json"
CONTRACT = ROOT / "configs/evaluation/diagnosis_main_response_contract.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_information_path_audit_v1_is_self_hashed_and_preserved() -> None:
    payload = json.loads(AUDIT_V1.read_text(encoding="utf-8"))
    declared = payload.pop("audit_sha256")

    assert canonical_execution_sha256(payload) == declared
    assert payload["protected_outcomes_opened"] is False
    assert payload["execution_authorized"] is False
    assert payload["design_blockers"] == []
    assert payload["audit_status"] == (
        "design_and_runtime_conformance_closed_execution_not_authorized"
    )
    assert payload["runtime_blocker"] is None
    for key, value in payload["source_bindings"].items():
        if not key.endswith("_path"):
            continue
        hash_key = key.removesuffix("_path") + "_file_sha256"
        if key == "main_runtime_implementation_path":
            assert payload["source_bindings"][hash_key] == (
                "c4d63551f2b20d620b59c2f2a497f40fe4f7818fa7d58c02c3f4a9d69c1a4a4a"
            )
        else:
            assert _sha(ROOT / value) == payload["source_bindings"][hash_key]


def test_information_path_audit_v2_binds_current_runtime_without_rewriting_v1() -> None:
    payload = json.loads(AUDIT_V2.read_text(encoding="utf-8"))
    declared = payload.pop("audit_sha256")
    predecessor = payload["predecessor"]
    binding = payload["forward_runtime_binding"]

    assert canonical_execution_sha256(payload) == declared
    assert predecessor["history_mutated"] is False
    assert predecessor["file_sha256"] == _sha(AUDIT_V1)
    old = json.loads(AUDIT_V1.read_text(encoding="utf-8"))
    assert predecessor["audit_sha256"] == old["audit_sha256"]
    assert (
        _sha(ROOT / binding["main_runtime_implementation_path"])
        == binding["main_runtime_implementation_file_sha256"]
    )
    assert payload["scientific_design_changed"] is False
    assert payload["protected_outcomes_opened"] is False
    assert payload["execution_authorized"] is False
    assert payload["main_registered_attempts_consumed"] == 0


def test_forward_response_contract_resolves_citation_ablation_without_rewriting_history() -> None:
    payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
    declared = payload.pop("contract_sha256")

    assert canonical_execution_sha256(payload) == declared
    assert payload["predecessor_history_mutated"] is False
    citation = payload["citation_policy"]
    assert set(citation["forbidden"]) == {"A1", "B1", "B2"}
    assert set(citation["required_for_cause_and_evidence_claims"]) == {
        "A2",
        "A3",
        "CodeGraph",
        "FULL",
    }
    claim_schema = payload["json_schema"]["properties"]["atomic_claims"]["items"]
    assert claim_schema["properties"]["visible_evidence_ids"]["minItems"] == 0
    schema_text = json.dumps(payload["json_schema"], sort_keys=True)
    assert "minLength" not in schema_text
    assert "maxLength" not in schema_text
    assert "uniqueItems" not in schema_text


def test_only_clean_ablation_contrasts_receive_component_language() -> None:
    payload = json.loads(AUDIT_V1.read_text(encoding="utf-8"))

    assert payload["clean_ablation_claims"] == [
        "B1_vs_A1_plain_vs_structured_visible_evidence_rendering",
        "A1_vs_A2_citation_requirement",
        "A2_vs_A3_abstention_and_missing_evidence_contract",
    ]
    by_variant = {item["variant"]: item for item in payload["variant_paths"]}
    assert by_variant["B3"]["reporting_class"] == "external_transfer_only"
    assert by_variant["FULL"]["reporting_class"] == "separate_full_system_path"
    assert by_variant["B2"]["attribution_boundary"].startswith("retrieval selection")
    findings = {item["code"]: item["status"] for item in payload["findings"]}
    assert findings["retrieval_and_tool_ledger"] == "pass"
    assert findings["finite_census_route_coverage"] == "pass"
