"""Deterministic, atomic writer/loader for P1 benchmark cases.

Each case is written as four validated JSON payloads plus a checksums file.
Validation happens before any file is created, so a validation error never
leaves a partial case on disk. JSON is emitted with sorted keys and a trailing
newline, so regenerating an identical case yields byte-identical files (there is
no timestamp inside a case payload).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aletheia_lab.benchmark.case_schema import (
    FORBIDDEN_TERMS,
    CaseGroundTruth,
    CaseManifest,
    DiagnosisInput,
    InjectionProvenance,
    project_diagnosis_input,
)
from aletheia_lab.evidence.leakage import find_forbidden_terms

_CASE_FILES = ("manifest.json", "diagnosis_input.json", "ground_truth.json", "injection.json")


def dumps_deterministic(payload: Any) -> str:
    """Serialize to a stable JSON string (sorted keys, trailing newline)."""

    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, payload: Any) -> Path:
    """Write JSON via a temp file + atomic replace (no half-written artifact)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".part")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(dumps_deterministic(payload))
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
    return path


@dataclass(frozen=True)
class LoadedCase:
    manifest: CaseManifest
    diagnosis_input: DiagnosisInput
    ground_truth: CaseGroundTruth
    injection: InjectionProvenance


def diagnosis_input_leakage(diagnosis_input: DiagnosisInput) -> list[str]:
    """Return forbidden answer-key terms found in the diagnosis-visible payload."""

    text = dumps_deterministic(diagnosis_input.model_dump())
    return find_forbidden_terms(text, FORBIDDEN_TERMS)


def write_case(
    case_dir: str | Path,
    manifest_data: dict[str, Any] | CaseManifest,
    ground_truth_data: dict[str, Any] | CaseGroundTruth,
    injection_data: dict[str, Any] | InjectionProvenance,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Validate then write one case directory. Fails closed before any write."""

    # Validate everything first: a failure here leaves nothing on disk.
    manifest = CaseManifest.model_validate(manifest_data)
    ground_truth = CaseGroundTruth.model_validate(ground_truth_data)
    injection = InjectionProvenance.model_validate(injection_data)
    diagnosis_input = project_diagnosis_input(manifest)

    leaked = diagnosis_input_leakage(diagnosis_input)
    if leaked:
        msg = f"refusing to write {manifest.case_id}: diagnosis input leaks {leaked}"
        raise ValueError(msg)

    case_path = Path(case_dir)
    if case_path.exists() and any(case_path.iterdir()) and not overwrite:
        msg = f"case directory already populated: {case_path} (pass overwrite=True)"
        raise FileExistsError(msg)
    case_path.mkdir(parents=True, exist_ok=True)

    payloads = {
        "manifest.json": manifest.model_dump(),
        "diagnosis_input.json": diagnosis_input.model_dump(),
        "ground_truth.json": ground_truth.model_dump(),
        "injection.json": injection.model_dump(),
    }
    checksums: dict[str, str] = {}
    for name, payload in payloads.items():
        written = _atomic_write_json(case_path / name, payload)
        checksums[name] = sha256_file(written)
    _atomic_write_json(case_path / "checksums.json", checksums)

    return {"case_dir": str(case_path), "checksums": checksums, "case_id": manifest.case_id}


def load_case_dir_schema_only(case_dir: str | Path) -> LoadedCase:
    """Parse one case directory and validate each payload schema only.

    This function deliberately does not verify checksums or relationships across
    payloads. Use ``validate_p1_cases`` as the integrity gate before consuming a
    generated P1 case set. The explicit name prevents callers from mistaking a
    schema parse for full case-integrity validation.
    """

    base = Path(case_dir)
    for name in _CASE_FILES:
        if not (base / name).exists():
            msg = f"missing case file {name} in {base}"
            raise FileNotFoundError(msg)
    return LoadedCase(
        manifest=CaseManifest.model_validate_json((base / "manifest.json").read_text("utf-8")),
        diagnosis_input=DiagnosisInput.model_validate_json(
            (base / "diagnosis_input.json").read_text("utf-8")
        ),
        ground_truth=CaseGroundTruth.model_validate_json(
            (base / "ground_truth.json").read_text("utf-8")
        ),
        injection=InjectionProvenance.model_validate_json(
            (base / "injection.json").read_text("utf-8")
        ),
    )
