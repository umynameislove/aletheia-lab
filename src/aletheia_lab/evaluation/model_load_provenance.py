"""Actual signed in-toto artifact rules on the same scoped observer evidence as S.

Signing is a local transcode of trusted capture, not independent attestation.
No inspection commands, remote keys, or candidate-checker calls are used.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from aletheia_lab.evaluation.model_load_contract import Decision, Observation, Record
from aletheia_lab.project.identity import content_sha256

DEPENDENCIES = {
    "mlflow-skinny": "3.9.0",
    "in-toto": "3.0.0",
    "SQLAlchemy": "2.1.3",
    "alembic": "1.20.0",
    "securesystemslib": "1.5.1",
}


def dependency_versions() -> dict[str, str]:
    observed: dict[str, str] = {}
    for name, expected in DEPENDENCIES.items():
        try:
            observed[name] = version(name)
        except PackageNotFoundError as exc:
            raise RuntimeError("install the pinned provenance optional dependencies") from exc
        if observed[name] != expected:
            raise RuntimeError("provenance dependency differs from the supported version")
    return observed


def document_digest(value: object) -> str:
    return content_sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    )


@dataclass(frozen=True)
class ProvenanceResult:
    decision: Decision
    verifier_calls: int
    artifact_rules_passed: bool | None


def verify_chain(
    expected: dict[str, str],
    observed: dict[str, str],
    directory: Path,
    *,
    invalid_signature: bool = False,
    missing_link: bool = False,
) -> str:
    """Execute full package verification, not a Python equality substitute.

    Authority supplies required scoped artifacts; the consuming functionary signs
    the independently represented materials. REQUIRE precedes MATCH, with terminal
    DISALLOW. Keys are ephemeral and private keys are never serialized.
    """
    layout_module = importlib.import_module("in_toto.models.layout")
    link_module = importlib.import_module("in_toto.models.link")
    metadata_module = importlib.import_module("in_toto.models.metadata")
    signer_module = importlib.import_module("securesystemslib.signer")
    verify_module = importlib.import_module("in_toto.verifylib")
    exceptions = importlib.import_module("in_toto.exceptions")
    owner = signer_module.CryptoSigner.generate_ed25519()
    worker = signer_module.CryptoSigner.generate_ed25519()
    worker_key = {"keyid": worker.public_key.keyid, **worker.public_key.to_dict()}
    owner_key = {"keyid": owner.public_key.keyid, **owner.public_key.to_dict()}
    required = [["REQUIRE", name] for name in sorted(expected)]
    steps = [
        layout_module.Step(
            name="selection",
            pubkeys=[worker.public_key.keyid],
            expected_materials=[["DISALLOW", "*"]],
            expected_products=[*required, ["ALLOW", "*"], ["DISALLOW", "*"]],
        ),
        layout_module.Step(
            name="consume",
            pubkeys=[worker.public_key.keyid],
            expected_materials=[
                *required,
                ["MATCH", "*", "WITH", "PRODUCTS", "FROM", "selection"],
                ["DISALLOW", "*"],
            ],
            expected_products=[["DISALLOW", "*"]],
        ),
    ]
    layout = layout_module.Layout(steps=steps, keys={worker.public_key.keyid: worker_key})
    layout_metadata = metadata_module.Metablock(signed=layout)
    layout_metadata.create_signature(owner)
    directory.mkdir(parents=True, exist_ok=False)
    for name, materials, products in (
        ("selection", {}, expected),
        ("consume", observed, {}),
    ):
        if missing_link and name == "consume":
            continue
        link = link_module.Link(
            name=name,
            command=[],
            materials={key: {"sha256": digest} for key, digest in materials.items()},
            products={key: {"sha256": digest} for key, digest in products.items()},
        )
        metadata = metadata_module.Metablock(signed=link)
        metadata.create_signature(worker)
        if invalid_signature and name == "consume":
            metadata.signatures[0]["sig"] = "00" * 64
        metadata.dump(str(directory / f"{name}.{worker.public_key.keyid[:8]}.link"))
    try:
        verify_module.in_toto_verify(
            layout_metadata,
            {owner.public_key.keyid: owner_key},
            link_dir_path=str(directory),
            persist_inspection_links=False,
        )
    except exceptions.RuleVerificationError:
        return "artifact_rule_mismatch"
    except (exceptions.ThresholdVerificationError, exceptions.SignatureVerificationError):
        return "inadmissible_signature"
    except exceptions.LinkNotFoundError:
        return "missing_link"
    return "pass"


def _records(observation: Observation) -> tuple[list[Record], str | None]:
    unique: dict[str, Record] = {}
    for record in observation.records:
        if record.scope != observation.scope:
            continue
        if record.identifier in unique and unique[record.identifier] != record:
            return [], "conflicting_record_identity"
        unique[record.identifier] = record
    records = list(unique.values())
    selections = [record for record in records if record.kind == "selection"]
    closures = {record.load_count for record in records if record.kind == "closure"}
    values = {
        (r.digest, r.selection, r.revision, r.phase, r.parent_scope, r.parent_selection)
        for r in selections
    }
    if len(values) > 1 or len(closures) > 1:
        return [], "conflicting_receipts"
    return records, None


def _expected_digest(observation: Observation, selected: Record) -> tuple[str | None, str | None]:
    contract = observation.contract
    if contract is None:
        return None, None
    if observation.scope.attempt == 0 or contract.retry == "reselect":
        return selected.digest, None
    origin = selected.parent_scope
    if origin is None or origin.request != observation.scope.request or origin.attempt != 0:
        return None, None
    parents = [
        record
        for record in observation.records
        if record.scope == origin
        and record.kind == "selection"
        and record.selection == selected.parent_selection
    ]
    if len({(r.digest, r.revision, r.phase) for r in parents}) > 1:
        return None, "conflicting_parent_receipts"
    if not parents or parents[0].phase != contract.policy:
        return None, None
    return parents[0].digest, None


def provenance_checker(observation: Observation, directory: Path) -> ProvenanceResult:
    """Independent policy adapter; real in-toto decides the represented artifact rules."""
    records, problem = _records(observation)
    if problem:
        return ProvenanceResult(Decision("conflict", problem), 0, None)
    contract = observation.contract
    if contract is None:
        return ProvenanceResult(Decision("unknown", "missing_policy", "undetermined"), 0, None)
    loads = [record for record in records if record.kind == "load"]
    closure = next((record.load_count for record in records if record.kind == "closure"), None)
    selected = next((record for record in records if record.kind == "selection"), None)
    expected_digest, problem = _policy_admission(observation, selected, loads, closure)
    if problem:
        return ProvenanceResult(Decision("conflict", problem), 0, None)
    if closure == 0:
        return ProvenanceResult(Decision(None, "closed_without_new_load", "no_new_load"), 0, None)
    expected, observed = _artifacts(observation, selected, expected_digest, loads, closure)
    # A phase or excess-load violation remains conclusive without a complete
    # buffer/closure chain. Missing evidence is not replaced by a synthetic hash.
    result = verify_chain(expected, observed, directory) if expected else None
    return _verification_decision(result, expected_digest, loads, closure)


def _policy_admission(
    observation: Observation,
    selected: Record | None,
    loads: list[Record],
    closure: int | None,
) -> tuple[str | None, str | None]:
    contract = observation.contract
    if contract is None:
        raise ValueError("policy admission requires a contract")
    if (closure is not None and (closure > 2 or closure < len(loads))) or len(loads) > 2:
        return None, "invalid_load_cardinality"
    expected_digest, problem = _expected_digest(observation, selected) if selected else (None, None)
    if problem:
        return None, problem
    if selected and any(record.selection != selected.selection for record in loads):
        return None, "selection_token_misjoin"
    digests = [
        expected_digest,
        selected.digest if selected else None,
        *(record.digest for record in loads),
    ]
    if any(digest not in contract.artifact_domain for digest in digests if digest is not None):
        return None, "artifact_outside_domain"
    return expected_digest, None


def _verification_decision(
    result: str | None,
    expected_digest: str | None,
    loads: list[Record],
    closure: int | None,
) -> ProvenanceResult:
    if result == "artifact_rule_mismatch":
        return ProvenanceResult(Decision("violation", "signed_artifact_rule_mismatch"), 1, False)
    if result not in {"pass", None}:
        return ProvenanceResult(Decision("unknown", result), 1, None)
    calls = int(result is not None)
    if expected_digest is None or not loads:
        return ProvenanceResult(
            Decision(
                "unknown",
                "missing_selection_or_buffer",
                "load" if loads or closure else "undetermined",
            ),
            calls,
            True if calls else None,
        )
    if closure is None:
        return ProvenanceResult(Decision("unknown", "matching_prefix_without_closure"), 1, True)
    return ProvenanceResult(Decision("compliant", "signed_scoped_chain_verified"), 1, True)


def _artifacts(
    observation: Observation,
    selected: Record | None,
    expected_digest: str | None,
    loads: list[Record],
    closure: int | None,
) -> tuple[dict[str, str], dict[str, str]]:
    contract = observation.contract
    if contract is None:
        raise ValueError("artifact projection requires a policy")
    phase = contract.retry if observation.scope.attempt else contract.policy
    expected: dict[str, str] = {}
    observed: dict[str, str] = {}
    if selected and (loads or closure is not None):
        expected["phase.json"] = document_digest(phase)
        observed["phase.json"] = document_digest(selected.phase)
        if expected_digest is not None:
            expected["inherited-model.pkl"] = expected_digest
            observed["inherited-model.pkl"] = str(selected.digest)
    if selected and expected_digest is not None and loads:
        expected["model.pkl"] = expected_digest
        observed["model.pkl"] = str(loads[0].digest)
        expected["scope.json"] = document_digest(
            [observation.scope.request, observation.scope.attempt, selected.selection]
        )
        observed["scope.json"] = document_digest(
            [loads[0].scope.request, loads[0].scope.attempt, loads[0].selection]
        )
    if closure == 2 or len(loads) > 1 or (closure is not None and selected and loads):
        expected["closure.json"] = document_digest(1)
        observed["closure.json"] = document_digest(max(closure or 0, len(loads)))
    return expected, observed
