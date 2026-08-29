"""Registration, inference, and atomic terminal closeout for P2R studies."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Final, Literal, NoReturn, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_closeout import ExecutionEnvironmentReceipt
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN
from aletheia_lab.benchmark.p2.instrument_validity import (
    InstrumentCandidatePlan,
    InstrumentValidityAudit,
    ManipulationObservation,
    assess_instrument_validity,
)
from aletheia_lab.benchmark.p2.lightweight_protocol import (
    LightweightConfirmatoryProtocol,
    MechanismName,
    verify_lightweight_confirmatory_protocol,
)
from aletheia_lab.benchmark.p2.p2r_runtime import (
    DatasetSeedMeasurement,
    measurement_census,
)
from aletheia_lab.benchmark.p2.p2r_runtime import paired_observations as derive_paired_observations
from aletheia_lab.filesystem import publish_staged_directory

REGISTRATION_SCHEMA_VERSION: Final[Literal["p2r-protocol-registration/1"]] = (
    "p2r-protocol-registration/1"
)
DATASET_DECISION_SCHEMA_VERSION: Final[Literal["p2r-dataset-decision/1"]] = "p2r-dataset-decision/1"
MECHANISM_CLOSEOUT_SCHEMA_VERSION: Final[Literal["p2r-mechanism-closeout/1"]] = (
    "p2r-mechanism-closeout/1"
)
JOINT_CLOSEOUT_SCHEMA_VERSION: Final[Literal["p2r-joint-closeout/1"]] = "p2r-joint-closeout/1"
FAILURE_SCHEMA_VERSION: Final[Literal["p2r-technical-failure/1"]] = "p2r-technical-failure/1"
STORE_SCHEMA_VERSION: Final[Literal["p2r-terminal-store/1"]] = "p2r-terminal-store/1"

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
GitCommit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Disposition = Literal["admitted", "assumption_limited", "rejected"]
TerminalStatus = Literal["complete", "technical_failure"]


class P2RCloseoutError(ValueError):
    """Raised when immutable registration or terminal evidence is invalid."""


def _fail(message: str) -> NoReturn:
    raise P2RCloseoutError(message)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class P2RRegistrationEvidence(Protocol):
    """Structural registration evidence shared by registered protocol versions."""

    mechanism: MechanismName
    protocol_sha256: str

    def canonical_sha256(self) -> str: ...

    def model_dump(self, *, mode: str) -> dict[str, object]: ...


class P2RProtocolRegistration(_StrictFrozenModel):
    schema_version: Literal["p2r-protocol-registration/1"] = REGISTRATION_SCHEMA_VERSION
    mechanism: MechanismName
    protocol_sha256: Sha256
    tagged_protocol_commit: GitCommit
    tag_name: str
    release_url: str
    release_id: int = Field(gt=0)
    release_created_at: datetime
    release_published_at: datetime
    immutable: Literal[True]
    draft: Literal[False]
    prerelease: Literal[False]

    @field_validator("release_created_at", "release_published_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("release timestamps require timezone evidence")
        return value

    @model_validator(mode="after")
    def _identity_reconciles(self) -> P2RProtocolRegistration:
        expected = {
            "data_drift": "p2r-data-drift-confirmatory-v1",
            "preprocessing_bug": "p2r-preprocessing-mismatch-confirmatory-v1",
        }[self.mechanism]
        expected_url = "https://github.com/umynameislove/aletheia-lab/releases/tag/" + expected
        if self.tag_name != expected or self.release_url != expected_url:
            raise ValueError("release identity differs from the mechanism registration")
        if self.release_published_at < self.release_created_at:
            raise ValueError("release publication precedes creation")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def registration_from_release(
    *,
    protocol: LightweightConfirmatoryProtocol,
    tagged_protocol_commit: str,
    payload: object,
) -> P2RProtocolRegistration:
    """Validate an immutable GitHub release response without trusting omissions."""

    checked = verify_lightweight_confirmatory_protocol(protocol)
    if not isinstance(payload, dict):
        _fail("GitHub release response must be an object")
    required = {
        "tag_name",
        "id",
        "html_url",
        "created_at",
        "published_at",
        "immutable",
        "draft",
        "prerelease",
    }
    if not required <= set(payload):
        _fail("GitHub release response is incomplete")
    if isinstance(payload["id"], bool) or not isinstance(payload["id"], int):
        _fail("GitHub release id must be an integer")
    try:
        return P2RProtocolRegistration(
            mechanism=checked.mechanism,
            protocol_sha256=checked.canonical_sha256(),
            tagged_protocol_commit=tagged_protocol_commit,
            tag_name=payload["tag_name"],
            release_url=payload["html_url"],
            release_id=payload["id"],
            release_created_at=datetime.fromisoformat(
                str(payload["created_at"]).replace("Z", "+00:00")
            ),
            release_published_at=datetime.fromisoformat(
                str(payload["published_at"]).replace("Z", "+00:00")
            ),
            immutable=payload["immutable"],
            draft=payload["draft"],
            prerelease=payload["prerelease"],
        )
    except (TypeError, ValueError) as exc:
        raise P2RCloseoutError("GitHub release is not an immutable P2R registration") from exc


class P2RDatasetDecision(_StrictFrozenModel):
    schema_version: Literal["p2r-dataset-decision/1"] = DATASET_DECISION_SCHEMA_VERSION
    mechanism: MechanismName
    dataset_id: str
    dataset_role: Literal["primary", "external_replication"]
    protocol_sha256: Sha256
    measurement_sha256s: tuple[Sha256, ...]
    seeds: tuple[int, ...]
    median_target_effect: float
    median_nuisance_effect: float
    expected_direction_fraction: float
    manipulation_fidelity_pass: bool
    target_effect_pass: bool
    direction_pass: bool
    dominance_pass: bool
    passed: bool
    decision_sha256: Sha256

    @model_validator(mode="after")
    def _decision_is_derived(self) -> P2RDatasetDecision:
        if self.seeds != (8201, 8202, 8203, 8204, 8205):
            raise ValueError("dataset decision requires the five registered seeds")
        if len(self.measurement_sha256s) != 5 or len(set(self.measurement_sha256s)) != 5:
            raise ValueError("dataset decision requires five unique measurements")
        expected_pass = all(
            (
                self.manipulation_fidelity_pass,
                self.target_effect_pass,
                self.direction_pass,
                self.dominance_pass,
            )
        )
        if self.passed != expected_pass:
            raise ValueError("dataset pass must be conjunctive")
        payload = self.model_dump(mode="json", exclude={"decision_sha256"})
        if self.decision_sha256 != canonical_sha256(payload):
            raise ValueError("decision_sha256 does not bind the dataset decision")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def _median(values: Sequence[float]) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered or any(not math.isfinite(value) for value in ordered):
        _fail("median requires finite non-empty evidence")
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def decide_dataset(
    *,
    protocol: LightweightConfirmatoryProtocol,
    measurements: Sequence[DatasetSeedMeasurement],
) -> P2RDatasetDecision:
    """Apply every frozen endpoint gate to exactly five seed measurements."""

    checked = verify_lightweight_confirmatory_protocol(protocol)
    try:
        ordered = tuple(
            sorted(
                (DatasetSeedMeasurement.model_validate(item.model_dump()) for item in measurements),
                key=lambda item: item.seed,
            )
        )
    except ValidationError as exc:
        raise P2RCloseoutError("dataset decision measurement is invalid") from exc
    if len(ordered) != 5 or tuple(item.seed for item in ordered) != checked.execution.seeds:
        _fail("dataset decision requires the complete registered seed census")
    identities = {
        (item.mechanism, item.dataset_id, item.dataset_role, item.protocol_sha256)
        for item in ordered
    }
    if len(identities) != 1:
        _fail("dataset decision measurements have inconsistent provenance")
    mechanism, dataset_id, dataset_role, protocol_sha256 = next(iter(identities))
    if mechanism != checked.mechanism or protocol_sha256 != checked.canonical_sha256():
        _fail("dataset decision is bound to another mechanism protocol")
    effects = tuple(max(0.0, -item.target_metric_delta) for item in ordered)
    nuisances = tuple(item.nuisance_effect_magnitude for item in ordered)
    median_effect = _median(effects)
    median_nuisance = _median(nuisances)
    direction_fraction = sum(
        item.target_metric_delta <= -checked.endpoint.minimum_practical_effect for item in ordered
    ) / len(ordered)
    manipulation_pass = all(
        abs(item.achieved_manipulation_magnitude - item.declared_manipulation_magnitude)
        <= max(0.01, 0.10 * item.declared_manipulation_magnitude) + 1e-12
        for item in ordered
    )
    target_pass = median_effect + 1e-12 >= checked.endpoint.minimum_practical_effect
    direction_pass = (
        direction_fraction + 1e-12 >= checked.endpoint.minimum_expected_direction_fraction
    )
    dominance_pass = (
        median_effect + 1e-12 >= 1.5 * median_nuisance
        or median_effect - median_nuisance + 1e-12 >= 0.005
    )
    payload: dict[str, object] = {
        "schema_version": DATASET_DECISION_SCHEMA_VERSION,
        "mechanism": mechanism,
        "dataset_id": dataset_id,
        "dataset_role": dataset_role,
        "protocol_sha256": protocol_sha256,
        "measurement_sha256s": tuple(item.measurement_sha256 for item in ordered),
        "seeds": tuple(item.seed for item in ordered),
        "median_target_effect": median_effect,
        "median_nuisance_effect": median_nuisance,
        "expected_direction_fraction": direction_fraction,
        "manipulation_fidelity_pass": manipulation_pass,
        "target_effect_pass": target_pass,
        "direction_pass": direction_pass,
        "dominance_pass": dominance_pass,
        "passed": manipulation_pass and target_pass and direction_pass and dominance_pass,
    }
    return P2RDatasetDecision.model_validate(
        {**payload, "decision_sha256": canonical_sha256(payload)}
    )


class P2RMechanismCloseout(_StrictFrozenModel):
    schema_version: Literal["p2r-mechanism-closeout/1"] = MECHANISM_CLOSEOUT_SCHEMA_VERSION
    mechanism: MechanismName
    protocol_sha256: Sha256
    registration_sha256: Sha256
    primary_decision: P2RDatasetDecision
    replication_decision: P2RDatasetDecision
    paired_instrument_decision_sha256: Sha256
    paired_instrument_pass: bool
    disposition: Disposition
    admitted: bool
    cross_dataset_claim_allowed: bool
    closeout_sha256: Sha256

    @model_validator(mode="after")
    def _disposition_is_derived(self) -> P2RMechanismCloseout:
        decisions = (self.primary_decision, self.replication_decision)
        if tuple(item.dataset_role for item in decisions) != (
            "primary",
            "external_replication",
        ):
            raise ValueError("mechanism closeout requires canonical dataset roles")
        if any(
            item.mechanism != self.mechanism or item.protocol_sha256 != self.protocol_sha256
            for item in decisions
        ):
            raise ValueError("mechanism closeout evidence provenance does not reconcile")
        passes = sum(item.passed for item in decisions)
        expected: Disposition = (
            "admitted"
            if passes == 2 and self.paired_instrument_pass
            else "assumption_limited"
            if passes == 1
            else "rejected"
        )
        if (
            self.disposition != expected
            or self.admitted != (expected == "admitted")
            or self.cross_dataset_claim_allowed != (expected == "admitted")
        ):
            raise ValueError("mechanism disposition is not derived from both datasets")
        payload = self.model_dump(mode="json", exclude={"closeout_sha256"})
        if self.closeout_sha256 != canonical_sha256(payload):
            raise ValueError("closeout_sha256 does not bind the mechanism closeout")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def close_mechanism(
    *,
    protocol: LightweightConfirmatoryProtocol,
    registration: P2RRegistrationEvidence,
    measurements: Sequence[DatasetSeedMeasurement],
    instrument_audit: InstrumentValidityAudit,
) -> P2RMechanismCloseout:
    checked = verify_lightweight_confirmatory_protocol(protocol)
    if (
        registration.mechanism != checked.mechanism
        or registration.protocol_sha256 != checked.canonical_sha256()
    ):
        _fail("mechanism registration differs from the protocol")
    mechanism_audit = next(
        (
            item
            for item in instrument_audit.mechanism_decisions
            if item.fault_type == checked.mechanism
        ),
        None,
    )
    if mechanism_audit is None:
        _fail("instrument audit omits the mechanism")
    by_role = {
        role: tuple(item for item in measurements if item.dataset_role == role)
        for role in ("primary", "external_replication")
    }
    primary = decide_dataset(protocol=checked, measurements=by_role["primary"])
    replication = decide_dataset(protocol=checked, measurements=by_role["external_replication"])
    passes = sum(item.passed for item in (primary, replication))
    disposition: Disposition = (
        "admitted"
        if passes == 2 and mechanism_audit.passed
        else "assumption_limited"
        if passes == 1
        else "rejected"
    )
    payload: dict[str, object] = {
        "schema_version": MECHANISM_CLOSEOUT_SCHEMA_VERSION,
        "mechanism": checked.mechanism,
        "protocol_sha256": checked.canonical_sha256(),
        "registration_sha256": registration.canonical_sha256(),
        "primary_decision": primary,
        "replication_decision": replication,
        "paired_instrument_decision_sha256": canonical_sha256(
            mechanism_audit.model_dump(mode="json")
        ),
        "paired_instrument_pass": mechanism_audit.passed,
        "disposition": disposition,
        "admitted": disposition == "admitted",
        "cross_dataset_claim_allowed": disposition == "admitted",
    }
    hash_payload = {
        **payload,
        "primary_decision": primary.model_dump(mode="json"),
        "replication_decision": replication.model_dump(mode="json"),
    }
    return P2RMechanismCloseout.model_validate(
        {**payload, "closeout_sha256": canonical_sha256(hash_payload)}
    )


class P2RJointCloseout(_StrictFrozenModel):
    schema_version: Literal["p2r-joint-closeout/1"] = JOINT_CLOSEOUT_SCHEMA_VERSION
    execution_commit: GitCommit
    executed_at: datetime
    environment_sha256: Sha256
    candidate_plan_sha256: Sha256
    instrument_audit_sha256: Sha256
    measurement_census_sha256: Sha256
    paired_observation_census_sha256: Sha256
    mechanism_closeouts: tuple[P2RMechanismCloseout, ...]
    n_mechanisms: Literal[2]
    n_admitted: int = Field(ge=0, le=2)
    outcomes_released_together: Literal[True]
    rerun_forbidden: Literal[True]
    closeout_sha256: Sha256

    @field_validator("executed_at")
    @classmethod
    def _timestamp_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("joint closeout timestamp requires timezone evidence")
        return value

    @model_validator(mode="after")
    def _joint_census_is_derived(self) -> P2RJointCloseout:
        if tuple(item.mechanism for item in self.mechanism_closeouts) != (
            "data_drift",
            "preprocessing_bug",
        ):
            raise ValueError("joint closeout requires both mechanisms in canonical order")
        if self.n_admitted != sum(item.admitted for item in self.mechanism_closeouts):
            raise ValueError("n_admitted must be derived from mechanism closeouts")
        payload = self.model_dump(mode="json", exclude={"closeout_sha256"})
        if self.closeout_sha256 != canonical_sha256(payload):
            raise ValueError("closeout_sha256 does not bind the joint closeout")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def build_joint_closeout(
    *,
    execution_commit: str,
    plan: InstrumentCandidatePlan,
    observations: Sequence[ManipulationObservation],
    measurements: Sequence[DatasetSeedMeasurement],
    environment: ExecutionEnvironmentReceipt,
    protocols: Mapping[MechanismName, LightweightConfirmatoryProtocol],
    registrations: Mapping[MechanismName, P2RRegistrationEvidence],
    instrument_protocol: object,
    executed_at: datetime | None = None,
) -> tuple[InstrumentValidityAudit, P2RJointCloseout]:
    """Build both decisions only after the full 20-measurement census exists."""

    # Runtime import avoids widening the public type to a protocol-like object.
    from aletheia_lab.benchmark.p2.instrument_validity import InstrumentValidityProtocol

    checked_instrument = InstrumentValidityProtocol.model_validate(instrument_protocol)
    if environment.execution_commit != execution_commit:
        _fail("execution environment is bound to another commit")
    checked_measurements = measurement_census(measurements, protocols)
    derived_observations = derive_paired_observations(plan=plan, measurements=checked_measurements)
    if tuple(observations) != derived_observations:
        _fail("paired observations do not derive from the dataset measurement census")
    audit = assess_instrument_validity(
        protocol=checked_instrument,
        candidate_plan=plan,
        observations=derived_observations,
    )
    closeouts = tuple(
        close_mechanism(
            protocol=protocols[mechanism],
            registration=registrations[mechanism],
            measurements=tuple(
                item for item in checked_measurements if item.mechanism == mechanism
            ),
            instrument_audit=audit,
        )
        for mechanism in ("data_drift", "preprocessing_bug")
    )
    timestamp = executed_at or datetime.now(UTC)
    census_hash = canonical_sha256(
        {"measurements": tuple(item.measurement_sha256 for item in checked_measurements)}
    )
    payload: dict[str, object] = {
        "schema_version": JOINT_CLOSEOUT_SCHEMA_VERSION,
        "execution_commit": execution_commit,
        "executed_at": timestamp,
        "environment_sha256": environment.canonical_sha256(),
        "candidate_plan_sha256": plan.canonical_sha256(),
        "instrument_audit_sha256": audit.canonical_sha256(),
        "measurement_census_sha256": census_hash,
        "paired_observation_census_sha256": canonical_sha256(
            {"observations": tuple(item.observation_sha256 for item in derived_observations)}
        ),
        "mechanism_closeouts": closeouts,
        "n_mechanisms": 2,
        "n_admitted": sum(item.admitted for item in closeouts),
        "outcomes_released_together": True,
        "rerun_forbidden": True,
    }
    hash_payload = {
        **payload,
        "executed_at": timestamp.isoformat().replace("+00:00", "Z"),
        "mechanism_closeouts": tuple(item.model_dump(mode="json") for item in closeouts),
    }
    return audit, P2RJointCloseout.model_validate(
        {**payload, "closeout_sha256": canonical_sha256(hash_payload)}
    )


class P2RTechnicalFailure(_StrictFrozenModel):
    schema_version: Literal["p2r-technical-failure/1"] = FAILURE_SCHEMA_VERSION
    protocol_sha256s: tuple[Sha256, ...]
    registration_sha256s: tuple[Sha256, ...]
    execution_commit: GitCommit
    failure_stage: Literal[
        "load_primary",
        "execute_primary",
        "load_replication",
        "execute_replication",
        "build_closeout",
    ]
    exception_class: str
    exception_message_sha256: Sha256
    partial_outcome_published: Literal[False]
    scientific_disposition_generated: Literal[False]
    rerun_forbidden: Literal[True]

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def build_technical_failure(
    *,
    protocols: Sequence[LightweightConfirmatoryProtocol],
    registrations: Sequence[P2RRegistrationEvidence],
    execution_commit: str,
    failure_stage: Literal[
        "load_primary",
        "execute_primary",
        "load_replication",
        "execute_replication",
        "build_closeout",
    ],
    error: BaseException,
) -> P2RTechnicalFailure:
    return P2RTechnicalFailure(
        protocol_sha256s=tuple(item.canonical_sha256() for item in protocols),
        registration_sha256s=tuple(item.canonical_sha256() for item in registrations),
        execution_commit=execution_commit,
        failure_stage=failure_stage,
        exception_class=type(error).__name__,
        exception_message_sha256=hashlib.sha256(str(error).encode()).hexdigest(),
        partial_outcome_published=False,
        scientific_disposition_generated=False,
        rerun_forbidden=True,
    )


class StoreEntry(_StrictFrozenModel):
    relative_path: str
    sha256: Sha256
    byte_count: int = Field(gt=0)


class P2RTerminalStore(_StrictFrozenModel):
    schema_version: Literal["p2r-terminal-store/1"] = STORE_SCHEMA_VERSION
    terminal_status: TerminalStatus
    protocol_sha256s: tuple[Sha256, ...]
    terminal_artifact_sha256: Sha256
    entries: tuple[StoreEntry, ...]
    store_sha256: Sha256

    @model_validator(mode="after")
    def _store_hash_is_derived(self) -> P2RTerminalStore:
        paths = tuple(item.relative_path for item in self.entries)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("terminal store paths must be unique and canonical")
        payload = self.model_dump(mode="json", exclude={"store_sha256"})
        if self.store_sha256 != canonical_sha256(payload):
            raise ValueError("store_sha256 does not bind the terminal manifest")
        return self


def _json_bytes(model: BaseModel) -> bytes:
    return (model.model_dump_json(indent=2) + "\n").encode()


def write_terminal_store(
    *,
    output_dir: str | Path,
    protocols: Sequence[LightweightConfirmatoryProtocol],
    registrations: Sequence[P2RRegistrationEvidence],
    terminal: P2RJointCloseout | P2RTechnicalFailure,
    environment: ExecutionEnvironmentReceipt,
    measurements: Sequence[DatasetSeedMeasurement] = (),
    observations: Sequence[ManipulationObservation] = (),
    audit: InstrumentValidityAudit | None = None,
    sealed_marker: BaseModel | None = None,
) -> P2RTerminalStore:
    """Publish one complete directory by atomic rename; never overwrite."""

    target = Path(output_dir)
    if target.exists() or target.is_symlink():
        _fail("P2R terminal store already exists; rerun is forbidden")
    if environment.execution_commit != terminal.execution_commit:
        _fail("P2R environment receipt is bound to another execution commit")
    is_failure = isinstance(terminal, P2RTechnicalFailure)
    if is_failure and (measurements or observations or audit is not None):
        _fail("technical failure store must not publish partial scientific evidence")
    if not is_failure and (len(measurements) != 20 or len(observations) != 10 or audit is None):
        _fail("complete store requires the full measurement census and audit")
    if not is_failure:
        assert isinstance(terminal, P2RJointCloseout)
        assert audit is not None
        if environment.canonical_sha256() != terminal.environment_sha256:
            _fail("P2R environment receipt differs from the joint closeout")
        if audit.canonical_sha256() != terminal.instrument_audit_sha256:
            _fail("P2R instrument audit differs from the joint closeout")
        if (
            canonical_sha256(
                {"measurements": tuple(item.measurement_sha256 for item in measurements)}
            )
            != terminal.measurement_census_sha256
        ):
            _fail("P2R measurement census differs from the joint closeout")
        if (
            canonical_sha256(
                {"observations": tuple(item.observation_sha256 for item in observations)}
            )
            != terminal.paired_observation_census_sha256
        ):
            _fail("P2R paired observations differ from the joint closeout")
    registration_schemas = tuple(
        item.model_dump(mode="json").get("schema_version") for item in registrations
    )
    is_v1_2 = registration_schemas == (
        "p2r-v1-2-registration/1",
        "p2r-v1-2-registration/1",
    )
    if is_v1_2 != (sealed_marker is not None):
        _fail("P2R v1.2 terminal stores require exactly one sealed-open marker")
    checked_marker: BaseModel | None = None
    if sealed_marker is not None:
        from aletheia_lab.benchmark.p2.p2r_v1_2_execution import P2RV12SealedMarker

        checked_marker = P2RV12SealedMarker.model_validate(sealed_marker.model_dump())
        if checked_marker.execution_commit != terminal.execution_commit:
            _fail("P2R v1.2 marker differs from the terminal execution")
        if checked_marker.execution_protocol_sha256s != tuple(
            item.canonical_sha256() for item in protocols
        ):
            _fail("P2R v1.2 marker differs from the execution protocols")
        if checked_marker.registration_sha256s != tuple(
            item.canonical_sha256() for item in registrations
        ):
            _fail("P2R v1.2 marker differs from the registrations")
    files: dict[str, bytes] = {
        "environment.json": _json_bytes(environment),
        "registrations.json": (
            json.dumps(
                [item.model_dump(mode="json") for item in registrations],
                indent=2,
                sort_keys=True,
                default=str,
            )
            + "\n"
        ).encode(),
        ("technical-failure.json" if is_failure else "closeout.json"): _json_bytes(terminal),
    }
    if checked_marker is not None:
        files["sealed-open.json"] = _json_bytes(checked_marker)
    if not is_failure:
        assert audit is not None
        files["instrument-audit.json"] = _json_bytes(audit)
        files["measurements.json"] = (
            json.dumps(
                [item.model_dump(mode="json") for item in measurements],
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode()
        files["paired-observations.json"] = (
            json.dumps(
                [item.model_dump(mode="json") for item in observations],
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode()
    entries = tuple(
        StoreEntry(
            relative_path=name,
            sha256=hashlib.sha256(content).hexdigest(),
            byte_count=len(content),
        )
        for name, content in sorted(files.items())
    )
    protocol_hashes = tuple(item.canonical_sha256() for item in protocols)
    payload: dict[str, object] = {
        "schema_version": STORE_SCHEMA_VERSION,
        "terminal_status": "technical_failure" if is_failure else "complete",
        "protocol_sha256s": protocol_hashes,
        "terminal_artifact_sha256": terminal.canonical_sha256(),
        "entries": entries,
    }
    hash_payload = {
        **payload,
        "entries": tuple(item.model_dump(mode="json") for item in entries),
    }
    manifest = P2RTerminalStore.model_validate(
        {**payload, "store_sha256": canonical_sha256(hash_payload)}
    )
    files["manifest.json"] = _json_bytes(manifest)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    try:
        for name, content in files.items():
            path = temporary / name
            with path.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        publish_staged_directory(temporary, target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return manifest


def load_and_verify_terminal_store(path: str | Path) -> P2RTerminalStore:
    root = Path(path)
    try:
        manifest = P2RTerminalStore.model_validate_json(
            (root / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise P2RCloseoutError("P2R terminal manifest is unavailable or invalid") from exc
    observed = set()
    for entry in manifest.entries:
        artifact = root / entry.relative_path
        try:
            content = artifact.read_bytes()
        except OSError as exc:
            raise P2RCloseoutError("P2R terminal artifact is unavailable") from exc
        if len(content) != entry.byte_count or hashlib.sha256(content).hexdigest() != entry.sha256:
            _fail("P2R terminal artifact hash or size mismatch")
        observed.add(entry.relative_path)
    actual = {
        item.name for item in root.iterdir() if item.is_file() and item.name != "manifest.json"
    }
    if actual != observed:
        _fail("P2R terminal store contains missing or unmanifested artifacts")
    expected_paths = (
        {"environment.json", "registrations.json", "technical-failure.json"}
        if manifest.terminal_status == "technical_failure"
        else {
            "closeout.json",
            "environment.json",
            "instrument-audit.json",
            "measurements.json",
            "paired-observations.json",
            "registrations.json",
        }
    )
    if "sealed-open.json" in actual:
        expected_paths.add("sealed-open.json")
    if actual != expected_paths:
        _fail("P2R terminal store does not match its terminal status")
    terminal_path = (
        root / "technical-failure.json"
        if manifest.terminal_status == "technical_failure"
        else root / "closeout.json"
    )
    try:
        environment = ExecutionEnvironmentReceipt.model_validate_json(
            (root / "environment.json").read_text(encoding="utf-8")
        )
        raw_registrations = json.loads((root / "registrations.json").read_text(encoding="utf-8"))
        if not isinstance(raw_registrations, list):
            raise ValueError("registration artifact must contain a list")
        registrations: tuple[P2RRegistrationEvidence, ...] = tuple(
            _registration_from_json(item) for item in raw_registrations
        )
        terminal: P2RJointCloseout | P2RTechnicalFailure
        if manifest.terminal_status == "technical_failure":
            terminal = P2RTechnicalFailure.model_validate_json(
                terminal_path.read_text(encoding="utf-8")
            )
        else:
            terminal = P2RJointCloseout.model_validate_json(
                terminal_path.read_text(encoding="utf-8")
            )
    except (OSError, ValueError) as exc:
        raise P2RCloseoutError("P2R terminal artifact is invalid") from exc
    if terminal.canonical_sha256() != manifest.terminal_artifact_sha256:
        _fail("P2R terminal artifact identity differs from its manifest")
    if environment.execution_commit != terminal.execution_commit:
        _fail("P2R environment receipt differs from the terminal execution")
    registration_protocols = tuple(item.protocol_sha256 for item in registrations)
    if registration_protocols != manifest.protocol_sha256s:
        _fail("P2R registration census differs from the terminal manifest")
    registration_hashes = tuple(item.canonical_sha256() for item in registrations)
    registration_schemas = tuple(
        item.model_dump(mode="json").get("schema_version") for item in registrations
    )
    is_v1_2 = registration_schemas == (
        "p2r-v1-2-registration/1",
        "p2r-v1-2-registration/1",
    )
    if is_v1_2 != ("sealed-open.json" in actual):
        _fail("P2R v1.2 terminal store is missing its sealed-open marker")
    if is_v1_2:
        from aletheia_lab.benchmark.p2.p2r_v1_2_execution import P2RV12SealedMarker

        try:
            marker = P2RV12SealedMarker.model_validate_json(
                (root / "sealed-open.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise P2RCloseoutError("P2R v1.2 sealed-open marker is invalid") from exc
        if marker.execution_commit != terminal.execution_commit:
            _fail("P2R v1.2 marker differs from the terminal execution")
        if marker.execution_protocol_sha256s != manifest.protocol_sha256s:
            _fail("P2R v1.2 marker differs from the terminal protocols")
        if marker.registration_sha256s != registration_hashes:
            _fail("P2R v1.2 marker differs from the terminal registrations")
    if isinstance(terminal, P2RTechnicalFailure):
        if registration_hashes != terminal.registration_sha256s:
            _fail("P2R registrations differ from the technical failure")
        return manifest
    if environment.canonical_sha256() != terminal.environment_sha256:
        _fail("P2R environment receipt differs from the terminal closeout")
    if registration_hashes != tuple(
        item.registration_sha256 for item in terminal.mechanism_closeouts
    ):
        _fail("P2R registrations differ from the mechanism closeouts")
    try:
        audit = InstrumentValidityAudit.model_validate_json(
            (root / "instrument-audit.json").read_text(encoding="utf-8")
        )
        raw_measurements = json.loads((root / "measurements.json").read_text(encoding="utf-8"))
        raw_observations = json.loads(
            (root / "paired-observations.json").read_text(encoding="utf-8")
        )
        if not isinstance(raw_measurements, list) or not isinstance(raw_observations, list):
            raise ValueError("P2R evidence artifacts must contain lists")
        measurements = tuple(
            DatasetSeedMeasurement.model_validate_json(json.dumps(item))
            for item in raw_measurements
        )
        observations = tuple(
            ManipulationObservation.model_validate_json(json.dumps(item))
            for item in raw_observations
        )
    except (OSError, ValueError) as exc:
        raise P2RCloseoutError("P2R scientific evidence artifact is invalid") from exc
    if audit.canonical_sha256() != terminal.instrument_audit_sha256:
        _fail("P2R instrument audit differs from the terminal closeout")
    if (
        canonical_sha256({"measurements": tuple(item.measurement_sha256 for item in measurements)})
        != terminal.measurement_census_sha256
    ):
        _fail("P2R measurement census differs from the terminal closeout")
    if (
        canonical_sha256({"observations": tuple(item.observation_sha256 for item in observations)})
        != terminal.paired_observation_census_sha256
    ):
        _fail("P2R paired observations differ from the terminal closeout")
    return manifest


def _registration_from_json(item: object) -> P2RRegistrationEvidence:
    """Parse only explicitly supported registration schemas."""

    if not isinstance(item, dict):
        _fail("P2R registration entry must be an object")
    schema = item.get("schema_version")
    if schema == REGISTRATION_SCHEMA_VERSION:
        return P2RProtocolRegistration.model_validate_json(json.dumps(item))
    if schema == "p2r-v1-2-registration/1":
        from aletheia_lab.benchmark.p2.p2r_v1_2_execution import P2RV12Registration

        return P2RV12Registration.model_validate_json(json.dumps(item))
    _fail("P2R registration schema is unsupported")
