"""Entrypoint boundaries for the single prospective P2R v1.1 attempt."""

from __future__ import annotations

import importlib.util
import inspect
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from aletheia_lab.benchmark.p2.p2r_closeout import P2RProtocolRegistration
from aletheia_lab.benchmark.p2.p2r_recovery_execution import (
    P2RRecoveryExecutionError,
    P2RRecoveryRegistration,
)

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT_PATH = ROOT / "scripts/p2r_v1_1_confirmatory.py"
CI_PATH = ROOT / ".github/workflows/ci.yml"


def _entrypoint() -> ModuleType:
    spec = importlib.util.spec_from_file_location("p2r_v1_1_entrypoint", ENTRYPOINT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("P2R v1.1 entrypoint cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runtime_uses_new_paths_and_never_overwrites_v1() -> None:
    module = _entrypoint()
    parser = module._parser()
    args = parser.parse_args(["preflight"])

    assert args.registration.as_posix().endswith("p2r-v1-1-registration.json")
    assert args.readiness.as_posix().endswith("p2r-v1-1-archive-readiness.json")
    assert args.marker.as_posix().endswith("p2r-v1-1-sealed-open.json")
    assert args.output.as_posix().endswith("p2r-confirmatory-v1-1")
    assert args.v1_registration.as_posix().endswith("p2r-registration.json")
    assert args.v1_marker.as_posix().endswith("p2r-sealed-open.json")
    assert args.v1_store.as_posix().endswith("p2r-confirmatory-v1")
    assert args.registration != args.v1_registration
    assert args.marker != args.v1_marker
    assert args.output != args.v1_store


def test_ci_test_matrix_preserves_history_for_registered_commit_ancestry() -> None:
    workflow = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["test"]["steps"]
    checkout = next(step for step in steps if step.get("uses") == "actions/checkout@v5")

    assert checkout.get("with", {}).get("fetch-depth") == 0


def test_preflight_orders_readiness_before_registration() -> None:
    module = _entrypoint()
    source = inspect.getsource(module._preflight)

    assert source.index("_failure_chain") < source.index("build_p2r_archive_readiness")
    assert source.index("build_p2r_archive_readiness") < source.index(
        "write_archive_readiness_exclusive"
    )
    assert source.index("write_archive_readiness_exclusive") < source.index(
        "_write_json_exclusive"
    )
    assert "execute_p2r_dataset" not in source
    assert '"registered_attempts_consumed": 0' in source


def test_execute_revalidates_archives_before_shared_marker() -> None:
    module = _entrypoint()
    source = inspect.getsource(module._execute)

    assert source.index("_failure_chain") < source.index("verify_p2r_archive_readiness")
    assert source.index("verify_p2r_archive_readiness") < source.index(
        "build_recovery_sealed_marker"
    )
    assert source.index("build_recovery_sealed_marker") < source.index(
        "write_recovery_marker_exclusive"
    )
    assert source.index("write_recovery_marker_exclusive") < source.index(
        "execute_p2r_dataset"
    )


def test_main_refuses_execute_without_both_explicit_hash_confirmations() -> None:
    module = _entrypoint()
    with pytest.raises(P2RRecoveryExecutionError, match="both explicit hash"):
        module.main(["execute"])
    with pytest.raises(P2RRecoveryExecutionError, match="both explicit hash"):
        module.main(["execute", "--confirm-protocol-sha256s", "0" * 64])


def test_registration_write_is_idempotent_but_not_replaceable(tmp_path: Path) -> None:
    module = _entrypoint()

    class _Model:
        def __init__(self, value: str) -> None:
            self.value = value

        def model_dump(self, *, mode: str) -> dict[str, str]:
            assert mode == "json"
            return {"value": self.value}

    path = tmp_path / "registration.json"
    module._write_json_exclusive(path, (_Model("a"),))
    first = path.read_bytes()
    module._write_json_exclusive(path, (_Model("a"),))
    assert path.read_bytes() == first
    with pytest.raises(P2RRecoveryExecutionError, match="different evidence"):
        module._write_json_exclusive(path, (_Model("b"),))


def test_registration_loaders_round_trip_serialized_datetime_evidence(
    tmp_path: Path,
) -> None:
    module = _entrypoint()
    timestamp = datetime(2026, 8, 28, tzinfo=UTC)
    recovery = P2RRecoveryRegistration(
        mechanism="data_drift",
        recovery_protocol_sha256="1" * 64,
        predecessor_protocol_sha256="2" * 64,
        predecessor_terminal_store_sha256="3" * 64,
        tagged_protocol_commit="4" * 40,
        tag_name="p2r-data-drift-confirmatory-v1.1",
        release_url=(
            "https://github.com/umynameislove/aletheia-lab/releases/tag/"
            "p2r-data-drift-confirmatory-v1.1"
        ),
        release_id=1,
        release_created_at=timestamp,
        release_published_at=timestamp,
        immutable=True,
        draft=False,
        prerelease=False,
    )
    scientific = P2RProtocolRegistration(
        mechanism="data_drift",
        protocol_sha256="5" * 64,
        tagged_protocol_commit="6" * 40,
        tag_name="p2r-data-drift-confirmatory-v1",
        release_url=(
            "https://github.com/umynameislove/aletheia-lab/releases/tag/"
            "p2r-data-drift-confirmatory-v1"
        ),
        release_id=2,
        release_created_at=timestamp,
        release_published_at=timestamp,
        immutable=True,
        draft=False,
        prerelease=False,
    )
    scientific_preprocessing = P2RProtocolRegistration(
        mechanism="preprocessing_bug",
        protocol_sha256="7" * 64,
        tagged_protocol_commit="8" * 40,
        tag_name="p2r-preprocessing-mismatch-confirmatory-v1",
        release_url=(
            "https://github.com/umynameislove/aletheia-lab/releases/tag/"
            "p2r-preprocessing-mismatch-confirmatory-v1"
        ),
        release_id=3,
        release_created_at=timestamp,
        release_published_at=timestamp,
        immutable=True,
        draft=False,
        prerelease=False,
    )
    recovery_path = tmp_path / "recovery.json"
    scientific_path = tmp_path / "scientific.json"
    module._write_json_exclusive(recovery_path, (recovery,))
    module._write_json_exclusive(scientific_path, (scientific, scientific_preprocessing))

    assert module._load_recovery_registrations(recovery_path) == (recovery,)
    assert module._load_scientific_registrations(scientific_path) == (
        scientific,
        scientific_preprocessing,
    )
