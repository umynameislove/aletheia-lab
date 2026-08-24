"""Release registration and atomic terminal evidence for registered v3.3."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Final, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_3_protocol import (
    V3_3_PROTOCOL_SHA256,
    V33ConfirmatoryProtocol,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_closeout import (
    V3AssumptionFamily,
    V3ExecutionEnvironmentReceipt,
    build_assumption_families,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_execution import (
    V3DatasetExecutionAttempt,
    V3DatasetOutcome,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_inference import (
    DatasetInference,
    V3StudyDecision,
    analyze_dataset,
    decide_study,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    ModelCalibrationAbstention,
    V3RuntimeError,
)
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN

REGISTRATION_SCHEMA_VERSION: Final[Literal["p2-v3-3-registration/1"]] = (
    "p2-v3-3-registration/1"
)
ATTEMPT_SCHEMA_VERSION: Final[Literal["p2-v3-3-dataset-attempt/1"]] = (
    "p2-v3-3-dataset-attempt/1"
)
CLOSEOUT_SCHEMA_VERSION: Final[Literal["p2-v3-3-closeout/1"]] = "p2-v3-3-closeout/1"
FAILURE_SCHEMA_VERSION: Final[Literal["p2-v3-3-technical-failure/1"]] = (
    "p2-v3-3-technical-failure/1"
)
STORE_SCHEMA_VERSION: Final[Literal["p2-v3-3-terminal-store/1"]] = (
    "p2-v3-3-terminal-store/1"
)
REQUIRED_TAG: Final[Literal["p2-label-noise-shift-factorial-v3.3"]] = (
    "p2-label-noise-shift-factorial-v3.3"
)

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
GitCommit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
DatasetRole = Literal["primary", "external_replication"]
FailureStage = Literal[
    "load_primary",
    "execute_primary",
    "load_replication",
    "execute_replication",
    "build_closeout",
]
TerminalStatus = Literal["scientific_closeout", "abstain", "technical_failure"]
_RELEASE_URL = re.compile(
    r"^https://github\.com/umynameislove/aletheia-lab/releases/tag/"
    r"p2-label-noise-shift-factorial-v3\.3$"
)


def _fail(message: str) -> NoReturn:
    raise V3RuntimeError(message)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class V33ProtocolRegistrationReceipt(_StrictFrozenModel):
    """Public immutable release evidence that predates the only v3.3 attempt."""

    schema_version: Literal["p2-v3-3-registration/1"] = REGISTRATION_SCHEMA_VERSION
    protocol_sha256: Sha256 = V3_3_PROTOCOL_SHA256
    tag_name: Literal["p2-label-noise-shift-factorial-v3.3"] = REQUIRED_TAG
    tagged_protocol_commit: GitCommit
    release_url: str
    release_id: int = Field(gt=0)
    release_created_at: datetime
    release_published_at: datetime
    immutable: Literal[True]
    draft: Literal[False]
    prerelease: Literal[False]

    @field_validator("release_url")
    @classmethod
    def _release_is_canonical(cls, value: str) -> str:
        if _RELEASE_URL.fullmatch(value) is None:
            raise ValueError("registration must use the canonical v3.3 release")
        return value

    @field_validator("release_created_at", "release_published_at")
    @classmethod
    def _timestamp_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("registration timestamps require timezone evidence")
        return value

    @model_validator(mode="after")
    def _timeline_is_valid(self) -> V33ProtocolRegistrationReceipt:
        if self.release_published_at < self.release_created_at:
            raise ValueError("release publication cannot predate creation")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def registration_from_github_release(
    *,
    protocol: V33ConfirmatoryProtocol,
    tagged_protocol_commit: str,
    payload: object,
) -> V33ProtocolRegistrationReceipt:
    """Validate the exact immutable GitHub release used for registration."""

    checked = V33ConfirmatoryProtocol.model_validate(protocol.model_dump())
    if checked.canonical_sha256() != V3_3_PROTOCOL_SHA256 or not isinstance(payload, dict):
        _fail("v3.3 registration requires its immutable protocol and release object")
    required = (
        "tag_name",
        "id",
        "html_url",
        "created_at",
        "published_at",
        "immutable",
        "draft",
        "prerelease",
    )
    if any(name not in payload for name in required):
        _fail("GitHub v3.3 release response is incomplete")
    release_id = payload["id"]
    created_at = payload["created_at"]
    published_at = payload["published_at"]
    if not isinstance(release_id, int) or isinstance(release_id, bool):
        _fail("GitHub release id must be an integer")
    if not isinstance(created_at, str) or not isinstance(published_at, str):
        _fail("GitHub release timestamps must be strings")
    try:
        return V33ProtocolRegistrationReceipt(
            tagged_protocol_commit=tagged_protocol_commit,
            tag_name=payload["tag_name"],
            release_url=payload["html_url"],
            release_id=release_id,
            release_created_at=datetime.fromisoformat(created_at.replace("Z", "+00:00")),
            release_published_at=datetime.fromisoformat(published_at.replace("Z", "+00:00")),
            immutable=payload["immutable"],
            draft=payload["draft"],
            prerelease=payload["prerelease"],
        )
    except (TypeError, ValueError) as exc:
        raise V3RuntimeError("GitHub release does not satisfy immutable v3.3 registration") from exc


class V33DatasetAttempt(_StrictFrozenModel):
    """One complete dataset outcome or a fail-closed calibration abstention."""

    schema_version: Literal["p2-v3-3-dataset-attempt/1"] = ATTEMPT_SCHEMA_VERSION
    protocol_sha256: Sha256 = V3_3_PROTOCOL_SHA256
    dataset_id: str
    dataset_role: DatasetRole
    status: Literal["complete", "abstain"]
    outcome: V3DatasetOutcome | None
    calibration_abstention: ModelCalibrationAbstention | None
    predictive_metrics_generated: bool
    partial_model_reusable: Literal[False] = False

    @model_validator(mode="after")
    def _terminal_state_is_exclusive(self) -> V33DatasetAttempt:
        if self.status == "complete":
            if self.outcome is None or self.calibration_abstention is not None:
                raise ValueError("complete attempt requires only a complete outcome")
            if not self.predictive_metrics_generated:
                raise ValueError("complete attempt must disclose generated metrics")
            evidence: V3DatasetOutcome | ModelCalibrationAbstention = self.outcome
        else:
            if self.calibration_abstention is None or self.outcome is not None:
                raise ValueError("abstention requires only calibration evidence")
            if self.predictive_metrics_generated:
                raise ValueError("abstention cannot contain predictive metrics")
            evidence = self.calibration_abstention
        if (
            evidence.protocol_sha256 != self.protocol_sha256
            or evidence.dataset_id != self.dataset_id
            or evidence.dataset_role != self.dataset_role
        ):
            raise ValueError("dataset attempt evidence provenance does not reconcile")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def dataset_attempt(evidence: V3DatasetExecutionAttempt) -> V33DatasetAttempt:
    if evidence.protocol_sha256 != V3_3_PROTOCOL_SHA256:
        _fail("v3.3 dataset attempt received evidence from another protocol")
    if isinstance(evidence, V3DatasetOutcome):
        return V33DatasetAttempt(
            dataset_id=evidence.dataset_id,
            dataset_role=evidence.dataset_role,
            status="complete",
            outcome=evidence,
            calibration_abstention=None,
            predictive_metrics_generated=True,
        )
    return V33DatasetAttempt(
        dataset_id=evidence.dataset_id,
        dataset_role=evidence.dataset_role,
        status="abstain",
        outcome=None,
        calibration_abstention=evidence,
        predictive_metrics_generated=False,
    )


def _dataset_inference(
    *, protocol: V33ConfirmatoryProtocol, outcome: V3DatasetOutcome, assumptions_pass: bool
) -> DatasetInference:
    admissions = sum(item.label_noise_admission for item in outcome.prior_only_controls)
    return analyze_dataset(
        protocol=protocol,
        dataset_id=outcome.dataset_id,
        dataset_role=outcome.dataset_role,
        split_membership_sha256=outcome.split_membership_sha256,
        seed_effects=outcome.seed_effects,
        prior_only_admissions={"yes_to_no": admissions, "no_to_yes": admissions},
        assumptions_pass={"yes_to_no": assumptions_pass, "no_to_yes": assumptions_pass},
    )


class V33ConfirmatoryCloseout(_StrictFrozenModel):
    schema_version: Literal["p2-v3-3-closeout/1"] = CLOSEOUT_SCHEMA_VERSION
    protocol_sha256: Sha256 = V3_3_PROTOCOL_SHA256
    registration_sha256: Sha256
    execution_commit: GitCommit
    executed_at: datetime
    environment_sha256: Sha256
    primary_attempt_sha256: Sha256
    replication_attempt_sha256: Sha256
    primary_inference: DatasetInference | None
    replication_inference: DatasetInference | None
    assumption_families: tuple[V3AssumptionFamily, ...]
    decision: V3StudyDecision | None
    disposition: Literal["cross_dataset_admission", "fail_closed", "abstain"]
    cross_dataset_claim_allowed: bool
    outcomes_released_together: Literal[True] = True

    @field_validator("executed_at")
    @classmethod
    def _timestamp_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("closeout timestamp requires timezone evidence")
        return value

    @model_validator(mode="after")
    def _disposition_is_derived(self) -> V33ConfirmatoryCloseout:
        analysis = (self.primary_inference, self.replication_inference, self.decision)
        analysis_is_empty = all(item is None for item in analysis)
        analysis_is_complete = all(item is not None for item in analysis)
        if self.disposition == "abstain" and analysis_is_empty:
            if self.assumption_families or self.cross_dataset_claim_allowed:
                raise ValueError("calibration abstention cannot expose scientific evidence")
            return self
        if not analysis_is_complete or len(self.assumption_families) != 3:
            raise ValueError("scientific closeout requires both complete analyses")
        assert self.primary_inference is not None
        assert self.replication_inference is not None
        assert self.decision is not None
        if (
            self.primary_inference.protocol_sha256 != self.protocol_sha256
            or self.replication_inference.protocol_sha256 != self.protocol_sha256
            or self.decision.protocol_sha256 != self.protocol_sha256
            or self.decision.primary_inference_sha256 != self.primary_inference.canonical_sha256()
            or self.decision.replication_inference_sha256
            != self.replication_inference.canonical_sha256()
            or self.disposition != self.decision.disposition
            or self.cross_dataset_claim_allowed != self.decision.cross_dataset_claim_allowed
        ):
            raise ValueError("v3.3 closeout inference bindings do not reconcile")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def build_closeout(
    *,
    protocol: V33ConfirmatoryProtocol,
    registration: V33ProtocolRegistrationReceipt,
    environment: V3ExecutionEnvironmentReceipt,
    execution_commit: str,
    primary: V33DatasetAttempt,
    replication: V33DatasetAttempt,
    executed_at: datetime | None = None,
) -> V33ConfirmatoryCloseout:
    """Build a decision only after both terminal dataset attempts exist."""

    checked = V33ConfirmatoryProtocol.model_validate(protocol.model_dump())
    if checked.canonical_sha256() != registration.protocol_sha256:
        _fail("v3.3 registration does not bind the immutable protocol")
    if environment.execution_commit != execution_commit:
        _fail("v3.3 environment does not bind the execution commit")
    if primary.dataset_role != "primary" or replication.dataset_role != "external_replication":
        _fail("v3.3 closeout requires primary then external replication")
    timestamp = executed_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp <= registration.release_published_at:
        _fail("v3.3 execution must occur after immutable registration")
    registration_sha256 = registration.canonical_sha256()
    environment_sha256 = environment.canonical_sha256()
    primary_attempt_sha256 = primary.canonical_sha256()
    replication_attempt_sha256 = replication.canonical_sha256()
    if primary.status == "abstain" or replication.status == "abstain":
        return V33ConfirmatoryCloseout(
            registration_sha256=registration_sha256,
            execution_commit=execution_commit,
            executed_at=timestamp,
            environment_sha256=environment_sha256,
            primary_attempt_sha256=primary_attempt_sha256,
            replication_attempt_sha256=replication_attempt_sha256,
            primary_inference=None,
            replication_inference=None,
            assumption_families=(),
            decision=None,
            disposition="abstain",
            cross_dataset_claim_allowed=False,
        )
    assert primary.outcome is not None
    assert replication.outcome is not None
    families = build_assumption_families(primary.outcome, replication.outcome)
    assumptions_pass = all(item.assumptions_pass for item in families)
    primary_inference = _dataset_inference(
        protocol=checked, outcome=primary.outcome, assumptions_pass=assumptions_pass
    )
    replication_inference = _dataset_inference(
        protocol=checked, outcome=replication.outcome, assumptions_pass=assumptions_pass
    )
    decision = decide_study(
        protocol=checked, primary=primary_inference, replication=replication_inference
    )
    return V33ConfirmatoryCloseout(
        registration_sha256=registration_sha256,
        execution_commit=execution_commit,
        executed_at=timestamp,
        environment_sha256=environment_sha256,
        primary_attempt_sha256=primary_attempt_sha256,
        replication_attempt_sha256=replication_attempt_sha256,
        primary_inference=primary_inference,
        replication_inference=replication_inference,
        assumption_families=families,
        decision=decision,
        disposition=decision.disposition,
        cross_dataset_claim_allowed=decision.cross_dataset_claim_allowed,
    )


class V33TechnicalFailureReceipt(_StrictFrozenModel):
    schema_version: Literal["p2-v3-3-technical-failure/1"] = FAILURE_SCHEMA_VERSION
    protocol_sha256: Sha256 = V3_3_PROTOCOL_SHA256
    registration_sha256: Sha256
    execution_commit: GitCommit
    failed_at: datetime
    failure_stage: FailureStage
    dataset_role: DatasetRole | None
    exception_class: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    exception_message_sha256: Sha256
    partial_outcome_published: Literal[False] = False
    scientific_disposition_generated: Literal[False] = False
    rerun_forbidden: Literal[True] = True

    @field_validator("failed_at")
    @classmethod
    def _failure_timestamp_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("failure timestamp requires timezone evidence")
        return value

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def build_technical_failure(
    *,
    registration: V33ProtocolRegistrationReceipt,
    execution_commit: str,
    failure_stage: FailureStage,
    dataset_role: DatasetRole | None,
    error: Exception,
    failed_at: datetime | None = None,
) -> V33TechnicalFailureReceipt:
    timestamp = failed_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        _fail("v3.3 failure timestamp requires timezone evidence")
    if timestamp <= registration.release_published_at:
        _fail("technical failure cannot predate immutable registration")
    return V33TechnicalFailureReceipt(
        registration_sha256=registration.canonical_sha256(),
        execution_commit=execution_commit,
        failed_at=timestamp,
        failure_stage=failure_stage,
        dataset_role=dataset_role,
        exception_class=type(error).__name__,
        exception_message_sha256=hashlib.sha256(str(error).encode()).hexdigest(),
    )


class StoreEntry(_StrictFrozenModel):
    relative_path: str
    sha256: Sha256
    size_bytes: int = Field(gt=0)

    @field_validator("relative_path")
    @classmethod
    def _path_is_portable(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("store paths must be normalized relative POSIX paths")
        if path.as_posix() != value:
            raise ValueError("store paths must use POSIX syntax")
        return value


class V33TerminalStoreManifest(_StrictFrozenModel):
    schema_version: Literal["p2-v3-3-terminal-store/1"] = STORE_SCHEMA_VERSION
    protocol_sha256: Sha256 = V3_3_PROTOCOL_SHA256
    terminal_status: TerminalStatus
    terminal_artifact_sha256: Sha256
    entries: tuple[StoreEntry, ...]
    store_sha256: Sha256

    @model_validator(mode="after")
    def _root_is_derived(self) -> V33TerminalStoreManifest:
        result_paths = {
            "registration.json",
            "environment.json",
            "primary-attempt.json",
            "replication-attempt.json",
            "closeout.json",
        }
        failure_paths = {"registration.json", "environment.json", "technical-failure.json"}
        expected_paths = failure_paths if self.terminal_status == "technical_failure" else result_paths
        paths = tuple(item.relative_path for item in self.entries)
        if set(paths) != expected_paths or paths != tuple(sorted(paths)):
            raise ValueError("v3.3 terminal-store artifact census is incomplete")
        expected = canonical_sha256(
            {
                "schema_version": STORE_SCHEMA_VERSION,
                "protocol_sha256": self.protocol_sha256,
                "terminal_status": self.terminal_status,
                "terminal_artifact_sha256": self.terminal_artifact_sha256,
                "entries": [item.model_dump(mode="json") for item in self.entries],
            }
        )
        if self.store_sha256 != expected:
            raise ValueError("v3.3 terminal-store root does not bind its entries")
        return self


def _json_bytes(model: BaseModel) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode()


def _write_exclusive(path: Path, content: bytes) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise V3RuntimeError(f"cannot write immutable v3.3 artifact: {path}") from exc


def _write_store(
    *,
    output_dir: str | Path,
    terminal_status: TerminalStatus,
    terminal_artifact: BaseModel,
    artifacts: tuple[tuple[str, BaseModel], ...],
) -> V33TerminalStoreManifest:
    destination = Path(output_dir)
    if destination.exists() or destination.is_symlink():
        _fail("v3.3 terminal store already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        entries = []
        for relative_path, model in artifacts:
            content = _json_bytes(model)
            _write_exclusive(stage / relative_path, content)
            entries.append(
                StoreEntry(
                    relative_path=relative_path,
                    sha256=hashlib.sha256(content).hexdigest(),
                    size_bytes=len(content),
                )
            )
        entries.sort(key=lambda item: item.relative_path)
        terminal_sha256 = canonical_sha256(terminal_artifact.model_dump(mode="json"))
        root = canonical_sha256(
            {
                "schema_version": STORE_SCHEMA_VERSION,
                "protocol_sha256": V3_3_PROTOCOL_SHA256,
                "terminal_status": terminal_status,
                "terminal_artifact_sha256": terminal_sha256,
                "entries": [item.model_dump(mode="json") for item in entries],
            }
        )
        manifest = V33TerminalStoreManifest(
            terminal_status=terminal_status,
            terminal_artifact_sha256=terminal_sha256,
            entries=tuple(entries),
            store_sha256=root,
        )
        _write_exclusive(stage / "store-manifest.json", _json_bytes(manifest))
        os.replace(stage, destination)
        return manifest
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def write_result_store(
    *,
    output_dir: str | Path,
    registration: V33ProtocolRegistrationReceipt,
    environment: V3ExecutionEnvironmentReceipt,
    primary: V33DatasetAttempt,
    replication: V33DatasetAttempt,
    closeout: V33ConfirmatoryCloseout,
) -> V33TerminalStoreManifest:
    if (
        registration.canonical_sha256() != closeout.registration_sha256
        or environment.canonical_sha256() != closeout.environment_sha256
        or primary.canonical_sha256() != closeout.primary_attempt_sha256
        or replication.canonical_sha256() != closeout.replication_attempt_sha256
    ):
        _fail("v3.3 result-store bindings do not reconcile")
    status: Literal["scientific_closeout", "abstain"] = (
        "abstain" if closeout.disposition == "abstain" else "scientific_closeout"
    )
    return _write_store(
        output_dir=output_dir,
        terminal_status=status,
        terminal_artifact=closeout,
        artifacts=(
            ("registration.json", registration),
            ("environment.json", environment),
            ("primary-attempt.json", primary),
            ("replication-attempt.json", replication),
            ("closeout.json", closeout),
        ),
    )


def write_failure_store(
    *,
    output_dir: str | Path,
    registration: V33ProtocolRegistrationReceipt,
    environment: V3ExecutionEnvironmentReceipt,
    failure: V33TechnicalFailureReceipt,
) -> V33TerminalStoreManifest:
    if (
        registration.canonical_sha256() != failure.registration_sha256
        or environment.execution_commit != failure.execution_commit
    ):
        _fail("v3.3 failure-store bindings do not reconcile")
    return _write_store(
        output_dir=output_dir,
        terminal_status="technical_failure",
        terminal_artifact=failure,
        artifacts=(
            ("registration.json", registration),
            ("environment.json", environment),
            ("technical-failure.json", failure),
        ),
    )


def load_and_verify_terminal_store(path: str | Path) -> V33TerminalStoreManifest:
    """Re-hash every artifact and reconstruct all terminal cross-bindings."""

    root = Path(path)
    try:
        manifest = V33TerminalStoreManifest.model_validate_json(
            (root / "store-manifest.json").read_text(encoding="utf-8")
        )
        registration = V33ProtocolRegistrationReceipt.model_validate_json(
            (root / "registration.json").read_text(encoding="utf-8")
        )
        environment = V3ExecutionEnvironmentReceipt.model_validate_json(
            (root / "environment.json").read_text(encoding="utf-8")
        )
        if manifest.terminal_status == "technical_failure":
            failure = V33TechnicalFailureReceipt.model_validate_json(
                (root / "technical-failure.json").read_text(encoding="utf-8")
            )
            if (
                registration.canonical_sha256() != failure.registration_sha256
                or environment.execution_commit != failure.execution_commit
            ):
                _fail("v3.3 technical-failure store bindings do not reconcile")
            terminal: V33TechnicalFailureReceipt | V33ConfirmatoryCloseout = failure
        else:
            primary = V33DatasetAttempt.model_validate_json(
                (root / "primary-attempt.json").read_text(encoding="utf-8")
            )
            replication = V33DatasetAttempt.model_validate_json(
                (root / "replication-attempt.json").read_text(encoding="utf-8")
            )
            closeout = V33ConfirmatoryCloseout.model_validate_json(
                (root / "closeout.json").read_text(encoding="utf-8")
            )
            if (
                registration.canonical_sha256() != closeout.registration_sha256
                or environment.canonical_sha256() != closeout.environment_sha256
                or primary.canonical_sha256() != closeout.primary_attempt_sha256
                or replication.canonical_sha256() != closeout.replication_attempt_sha256
            ):
                _fail("v3.3 closeout store bindings do not reconcile")
            terminal = closeout
    except (OSError, ValueError) as exc:
        raise V3RuntimeError("cannot load v3.3 terminal store") from exc
    for entry in manifest.entries:
        try:
            content = (root / entry.relative_path).read_bytes()
        except OSError as exc:
            raise V3RuntimeError("v3.3 terminal artifact is missing") from exc
        if len(content) != entry.size_bytes or hashlib.sha256(content).hexdigest() != entry.sha256:
            _fail("v3.3 terminal artifact checksum mismatch")
    terminal_sha256 = canonical_sha256(terminal.model_dump(mode="json"))
    if terminal_sha256 != manifest.terminal_artifact_sha256:
        _fail("v3.3 terminal artifact does not match the store root")
    if registration.protocol_sha256 != manifest.protocol_sha256:
        _fail("v3.3 registration and store protocol identities differ")
    return manifest
