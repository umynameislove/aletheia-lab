"""Independent, non-mutating recovery terminal and normalization checks."""

from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

from aletheia_lab.evaluation.claim_corpus_contracts import ClaimCorpusRequestCensus
from aletheia_lab.evaluation.claim_corpus_normalization_recovery import normalize_provider_output_v2
from aletheia_lab.evaluation.claim_corpus_reconciliation import _read_issue
from aletheia_lab.evaluation.claim_corpus_terminal_reader import ClaimCorpusTerminalReader
from aletheia_lab.evaluation.claim_evidence_semantics import ModelVisibleEvidenceContext
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.project.identity import canonical_project_json, content_sha256

if TYPE_CHECKING:
    from aletheia_lab.evaluation.claim_corpus_live import PreparedClaimCorpusRequest


def audit_recovery_store(
    root: Path, store: Path, prepared: tuple["PreparedClaimCorpusRequest", ...]
) -> dict[str, object]:
    """Verify exact membership, terminal bytes and the downstream normalization boundary."""
    by_id = {p.request.initial_attempt.request_identity_sha256: p for p in prepared}
    if len(by_id) != len(prepared) or not by_id:
        raise ValueError("recovery request set is empty or duplicated")
    _check_members(store, set(by_id))
    census = ClaimCorpusRequestCensus.model_validate_json(
        (root / "configs/evaluation/claim_support_request_census.json").read_bytes()
    )
    frozen = {r.request_sha256: r for r in census.primary_requests}
    statuses: Counter[str] = Counter()
    issues: Counter[str] = Counter()
    normalized = completed = candidates = attempts = 0
    shard_hashes: list[tuple[str, str]] = []
    authority_hashes: list[tuple[str, str]] = []
    for identity, item in sorted(by_id.items()):
        authority_bytes = (store / "authorities" / f"{identity}.json").read_bytes()
        if authority_bytes != (
            canonical_project_json(item.authority.model_dump(mode="json")) + "\n"
        ).encode():
            raise ValueError("recovery authority binding differs")
        authority_hashes.append((identity, content_sha256(authority_bytes)))
        reader = _reader(store / "requests" / identity)
        shard_hashes.append((identity, reader.store_sha256()))
        inventory = reader.terminal_inventory(identity)
        statuses[inventory.gateway_status] += 1
        attempts += len(inventory.attempt_outcomes)
        issue = _read_issue(reader, identity)
        if issue is not None:
            issues[issue.code] += 1
        parsed = reader.terminal_parsed_payload(identity)
        if parsed is None:
            continue
        context = item.request.context
        if not isinstance(context, ModelVisibleEvidenceContext):
            raise ValueError("recovery context type differs")
        output = normalize_provider_output_v2(
            frozen[item.request_sha256], parsed,
            source_record_sha256=canonical_execution_sha256(parsed),
            visible_evidence_ids=tuple(e.evidence_id for e in context.items),
        )
        normalized += 1
        completed += output.output_status == "completed"
        candidates += len(output.atomic_claims)
    return {
        "terminal_store_sha256": canonical_execution_sha256({
            "schema_version": "claim-corpus-sharded-attempt-store/v1",
            "authorities": tuple(authority_hashes), "shards": tuple(shard_hashes),
        }),
        "terminal_request_count": len(prepared),
        "gateway_status_counts": dict(sorted(statuses.items())),
        "provider_issue_code_counts": dict(sorted(issues.items())),
        "technical_attempt_count": attempts,
        "normalized_output_count": normalized,
        "completed_output_count": completed,
        "claim_candidate_count": candidates,
        "failures_preserved_in_denominator": True,
    }


def _check_members(store: Path, identities: set[str]) -> None:
    if store.is_symlink() or not store.is_dir():
        raise ValueError("recovery store is not a real directory")
    if {p.name for p in store.iterdir()} != {"requests", "authorities"}:
        raise ValueError("unexpected recovery store membership")
    for directory in (store / "requests", store / "authorities"):
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("linked or missing recovery store bucket")
        if any(p.is_symlink() for p in directory.iterdir()):
            raise ValueError("linked recovery store member")
    if {p.name for p in (store / "requests").iterdir()} != identities:
        raise ValueError("recovery request membership differs")
    if {p.name for p in (store / "authorities").iterdir()} != {
        f"{identity}.json" for identity in identities
    }:
        raise ValueError("recovery authority membership differs")


def _reader(shard: Path) -> ClaimCorpusTerminalReader:
    return ClaimCorpusTerminalReader(
        root=shard, object_root=shard / "objects" / "sha256",
        request_root=shard / "requests", terminal_root=shard / "terminal",
        failure_root=shard / "failures",
    )
