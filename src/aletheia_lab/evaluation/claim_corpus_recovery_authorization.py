"""Recovery-owned authority and private destination checks, without network access."""

import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.evaluation.claim_corpus_normalization_recovery import (
    PREDECESSOR_TERMINAL_STORE_SHA256,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.filesystem import publish_immutable_file
from aletheia_lab.project.identity import canonical_project_json

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
RecoveryPhase = Literal["compatibility", "diagnosis"]
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class RecoveryAuthorization(BaseModel):
    """A separate explicit authority; the nested legacy binding never authorizes recovery."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["claim-corpus-recovery-authorization/v1"]
    phase: RecoveryPhase
    authorized_at: str
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    execution_plan_sha256: Sha256
    observed_evidence_census_sha256: Sha256
    observed_evidence_receipt_sha256: Sha256
    model: Literal["gpt-4.1"]
    model_snapshot: Literal["gpt-4.1-2025-04-14"]
    primary_request_count: Literal[360]
    model_request_count: Literal[315]
    deterministic_request_count: Literal[45]
    maximum_provider_attempts_per_request: Literal[2]
    maximum_output_tokens_per_model_request: Literal[600]
    protocol_sha256: Sha256
    rehearsal_sha256: Sha256
    destination_sha256: Sha256
    predecessor_store_sha256: Sha256
    compatibility_receipt_sha256: Sha256 | None
    estimated_upper_cost_usd: float = Field(ge=0, allow_inf_nan=False)
    operator_cost_ceiling_usd: float = Field(gt=0, allow_inf_nan=False)
    registered_attempts: Literal[1] = 1
    relation_assignment_authorized: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    authorization_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"authorization_sha256"})

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        if _UTC_TIMESTAMP.fullmatch(self.authorized_at) is None:
            raise ValueError("recovery authorization time is not canonical UTC seconds")
        try:
            datetime.fromisoformat(self.authorized_at[:-1] + "+00:00")
        except ValueError as exc:
            raise ValueError("recovery authorization time is invalid") from exc
        if self.authorization_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("recovery authority identity differs")
        if self.predecessor_store_sha256 != PREDECESSOR_TERMINAL_STORE_SHA256:
            raise ValueError("recovery predecessor identity differs")
        if (self.phase == "diagnosis") != (self.compatibility_receipt_sha256 is not None):
            raise ValueError("diagnosis requires a separately verified compatibility receipt")
        if self.operator_cost_ceiling_usd < self.estimated_upper_cost_usd:
            raise ValueError("operator ceiling is below the bounded schedule estimate")
        return self


def checked_private_path(path: Path, repository: Path) -> Path:
    """Reject symlink aliases before resolving and never publish within the repository."""
    absolute = path.absolute()
    if any(part.is_symlink() for part in (absolute, *absolute.parents)):
        raise ValueError("private path contains a symbolic link")
    resolved = absolute.resolve()
    repo = repository.resolve()
    if resolved == repo or resolved.is_relative_to(repo) or repo.is_relative_to(resolved):
        raise ValueError("private destination overlaps the repository")
    return resolved


def checked_run_directory(repository: Path, run_dir: Path, predecessor_store: Path) -> Path:
    run = checked_private_path(run_dir, repository)
    predecessor = checked_private_path(predecessor_store, repository).parent
    if run == predecessor or run.is_relative_to(predecessor) or predecessor.is_relative_to(run):
        raise ValueError("recovery destination overlaps the preserved predecessor directory")
    if run.exists() and not run.is_dir():
        raise ValueError("recovery destination is not a directory")
    if run.exists():
        allowed = {
            f"{phase}-{suffix}"
            for phase in ("compatibility", "diagnosis")
            for suffix in ("authorization.json", "lease.json", "store", "receipt.json")
        }
        if any(p.name not in allowed or p.is_symlink() for p in run.iterdir()):
            raise ValueError("recovery directory contains unknown or linked artifacts")
    return run


def destination_sha256(run_dir: Path) -> str:
    return canonical_execution_sha256({"private_recovery_directory": run_dir.resolve().as_posix()})


def publish_recovery_json(path: Path, payload: dict[str, object]) -> str:
    return publish_immutable_file(path, (canonical_project_json(payload) + "\n").encode())


def acquire_recovery_lease(run_dir: Path, authorization: RecoveryAuthorization) -> None:
    """Only the atomic creator may execute. An identical lease is NOT another permission."""
    phase = authorization.phase
    if (run_dir / f"{phase}-store").exists() or (run_dir / f"{phase}-receipt.json").exists():
        raise ValueError("existing execution artifacts forbid a new recovery invocation")
    payload: dict[str, object] = {
        "schema_version": "claim-corpus-recovery-lease/v1",
        "authorization_sha256": authorization.authorization_sha256,
        "destination_sha256": authorization.destination_sha256,
        "phase": phase,
        "registered_attempts": 1,
    }
    if publish_recovery_json(run_dir / f"{phase}-lease.json", payload) != "created":
        raise ValueError("recovery lease already consumed; do not restart or remove it")
