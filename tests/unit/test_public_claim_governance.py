from __future__ import annotations

import json
from pathlib import Path

import pytest

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.governance.claims import (
    ClaimRegistryError,
    MechanismDenominators,
    PublicClaimRegistry,
    audit_public_claim_registry,
    load_public_claim_registry,
)

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "configs/governance/public_claim_registry_v1.json"
FILTER_PATH = ROOT / "configs/benchmark/provenance/p4_p5_mechanism_filter_manifest.json"
SUMMARY_PATH = ROOT / "configs/benchmark/provenance/p2r_v1_2_publication_summary.json"


def _payload() -> dict[str, object]:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _resign(payload: dict[str, object]) -> dict[str, object]:
    unsigned = {key: value for key, value in payload.items() if key != "registry_sha256"}
    payload["registry_sha256"] = canonical_sha256(unsigned)
    return payload


def _validate(payload: dict[str, object]) -> PublicClaimRegistry:
    return PublicClaimRegistry.model_validate_json(json.dumps(payload))


def test_tracked_public_claim_registry_reconciles_terminal_evidence() -> None:
    registry = load_public_claim_registry(REGISTRY_PATH)
    audit = audit_public_claim_registry(
        root=ROOT,
        registry=registry,
        mechanism_filter_path=FILTER_PATH,
        p2r_summary_path=SUMMARY_PATH,
    )

    assert audit.status == "pass"
    assert audit.denominator_reconciled
    assert audit.artifacts_resolved
    assert audit.forbidden_wording_findings == ()
    assert registry.mechanism_denominators == MechanismDenominators(
        inventory=3,
        admitted=0,
        assumption_limited=1,
        rejected=2,
        pending=0,
        diagnostic_ground_truth=0,
    )


def test_registry_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    payload = _payload()
    payload["terminal_store_sha256"] = "f" * 64
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ClaimRegistryError, match="unavailable or invalid"):
        load_public_claim_registry(path)


def test_zero_admitted_claim_cannot_substitute_another_denominator() -> None:
    payload = _payload()
    claims = payload["claims"]
    assert isinstance(claims, list)
    admission = next(claim for claim in claims if claim["claim_id"] == "C2-P2-ADMISSION")
    admission["denominator"] = "3 implemented mechanisms"

    with pytest.raises(ValueError, match="zero admitted denominator"):
        _validate(_resign(payload))


def test_missing_primary_artifact_blocks_audit(tmp_path: Path) -> None:
    payload = _payload()
    claims = payload["claims"]
    assert isinstance(claims, list)
    claims[0]["primary_artifacts"] = ["missing/current-evidence.json"]
    registry = _validate(_resign(payload))

    audit = audit_public_claim_registry(
        root=ROOT,
        registry=registry,
        mechanism_filter_path=FILTER_PATH,
        p2r_summary_path=SUMMARY_PATH,
    )

    assert audit.status == "blocked"
    assert not audit.artifacts_resolved


def test_assertive_forbidden_wording_blocks_publication(tmp_path: Path) -> None:
    public = tmp_path / "README.md"
    public.write_text("P2R v1.2 was a technical failure.\n", encoding="utf-8")
    payload = _payload()
    payload["public_surfaces"] = ["README.md"]
    claims = payload["claims"]
    assert isinstance(claims, list)
    for claim in claims:
        claim["primary_artifacts"] = ["README.md"]
    registry = _validate(_resign(payload))

    audit = audit_public_claim_registry(
        root=tmp_path,
        registry=registry,
        mechanism_filter_path=FILTER_PATH,
        p2r_summary_path=SUMMARY_PATH,
    )

    assert audit.status == "blocked"
    assert [finding.rule_id for finding in audit.forbidden_wording_findings] == ["FW-02"]
