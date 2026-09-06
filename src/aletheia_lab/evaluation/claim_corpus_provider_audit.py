"""Read-only failure characterization; never infer missing provider diagnostics."""

from collections import Counter
from pathlib import Path

from aletheia_lab.evaluation._attempt_store.integrity import AttemptStoreIntegrityVerifier
from aletheia_lab.evaluation.claim_corpus_normalization_recovery import (
    PREDECESSOR_TERMINAL_STORE_SHA256,
)
from aletheia_lab.evaluation.claim_corpus_reconciliation import _read_issue
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.project.identity import content_sha256


def _store_members(store: Path) -> tuple[list[Path], list[Path]]:
    if store.is_symlink() or not store.is_dir():
        raise ValueError("predecessor store must be a real directory")
    if {p.name for p in store.iterdir()} != {"authorities", "requests"}:
        raise ValueError("unexpected predecessor store membership")
    authorities = store / "authorities"
    requests = store / "requests"
    if authorities.is_symlink() or requests.is_symlink():
        raise ValueError("predecessor directories must not be symlinks")
    authority_paths = sorted(authorities.iterdir())
    shards = sorted(requests.iterdir())
    if len(shards) != 360 or {p.name for p in authority_paths} != {
        f"{p.name}.json" for p in shards
    }:
        raise ValueError("predecessor census is incomplete")
    if any(p.is_symlink() or not p.is_file() for p in authority_paths):
        raise ValueError("invalid authority file")
    return authority_paths, shards


def audit_predecessor_provider_failures(store: Path) -> dict[str, object]:
    """Verify the exact predecessor store before reporting aggregate diagnostics."""
    authority_paths, shards = _store_members(store)
    shard_hashes = []
    statuses: Counter[str] = Counter()
    issue_codes: Counter[str] = Counter()
    failure_raw_count = 0
    for shard in shards:
        if shard.is_symlink() or not shard.is_dir():
            raise ValueError("invalid predecessor shard")
        verifier = AttemptStoreIntegrityVerifier(
            root=shard,
            object_root=shard / "objects" / "sha256",
            request_root=shard / "requests",
            terminal_root=shard / "terminal",
            failure_root=shard / "failures",
        )
        shard_hashes.append((shard.name, verifier.store_sha256()))
        inventory = verifier._terminal_inventory(shard.name)
        statuses[inventory.gateway_status] += 1
        if inventory.gateway_status == "provider_failed":
            issue = _read_issue(verifier, shard.name)
            if issue is None:
                raise ValueError("provider failure has no issue record")
            issue_codes[issue.code] += 1
            failure_raw_count += inventory.raw_response_sha256 is not None
    identity = canonical_execution_sha256(
        {
            "schema_version": "claim-corpus-sharded-attempt-store/v1",
            "authorities": tuple((p.stem, content_sha256(p.read_bytes())) for p in authority_paths),
            "shards": tuple(shard_hashes),
        }
    )
    if identity != PREDECESSOR_TERMINAL_STORE_SHA256:
        raise ValueError("store differs from the registered predecessor")
    payload: dict[str, object] = {
        "schema_version": "claim-corpus-provider-failure-audit/v1",
        "status": "predecessor_verified_root_cause_not_identifiable",
        "terminal_store_sha256": identity,
        "gateway_status_counts": dict(sorted(statuses.items())),
        "provider_issue_code_counts": dict(sorted(issue_codes.items())),
        "failed_request_raw_response_count": failure_raw_count,
        "exact_provider_failure_cause_known": False,
        "provider_calls_executed": False,
        "predecessor_modified": False,
    }
    return {**payload, "audit_sha256": canonical_execution_sha256(payload)}
