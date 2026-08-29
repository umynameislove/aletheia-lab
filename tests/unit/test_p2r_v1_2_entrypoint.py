"""Outcome-blind boundaries for the single P2R v1.2 execution attempt."""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from types import ModuleType

import pytest

from aletheia_lab.benchmark.p2.p2r_v1_2_execution import P2RV12ExecutionError

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT_PATH = ROOT / "scripts/p2r_v1_2_confirmatory.py"


def _entrypoint() -> ModuleType:
    spec = importlib.util.spec_from_file_location("p2r_v1_2_entrypoint", ENTRYPOINT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("P2R v1.2 entrypoint cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runtime_paths_are_versioned_and_do_not_overwrite_predecessors() -> None:
    args = _entrypoint()._parser().parse_args(["preflight"])

    assert args.readiness.as_posix().endswith("p2r-v1-2-archive-readiness.json")
    assert args.registration.as_posix().endswith("p2r-v1-2-registration.json")
    assert args.marker.as_posix().endswith("p2r-v1-2-sealed-open.json")
    assert args.output.as_posix().endswith("p2r-confirmatory-v1-2")
    assert "v1-1" not in args.registration.as_posix()
    assert "v1-1" not in args.marker.as_posix()


def test_preflight_reproduces_archives_before_registration_without_outcomes() -> None:
    source = inspect.getsource(_entrypoint()._preflight)

    assert source.index("_readiness") < source.index("registration_from_release")
    assert source.index("write_archive_readiness_exclusive") < source.index(
        "_write_json_exclusive"
    )
    assert "execute_p2r_dataset" not in source
    assert '"registered_attempts_consumed": 0' in source
    assert '"model_fitted": False' in source
    assert '"outcomes_generated": False' in source


def test_execute_revalidates_archive_and_registration_before_marker() -> None:
    source = inspect.getsource(_entrypoint()._execute)

    assert source.index("verify_p2r_archive_readiness") < source.index(
        "verify_registration_pair"
    )
    assert source.index("verify_registration_pair") < source.index(
        "build_sealed_marker"
    )
    assert source.index("write_marker_exclusive") < source.index(
        "load_v3_dataset_snapshot_for_registration"
    )
    assert source.index("write_marker_exclusive") < source.index(
        "execute_p2r_dataset"
    )


def test_execute_requires_both_explicit_hash_confirmations() -> None:
    module = _entrypoint()
    with pytest.raises(P2RV12ExecutionError, match="both explicit hash"):
        module.main(["execute"])
    with pytest.raises(P2RV12ExecutionError, match="both explicit hash"):
        module.main(["execute", "--confirm-protocol-sha256s", "0" * 64])


def test_registration_file_is_idempotent_but_not_replaceable(tmp_path: Path) -> None:
    module = _entrypoint()

    class _Model:
        def __init__(self, value: str) -> None:
            self.value = value

        def model_dump(self, *, mode: str) -> dict[str, str]:
            assert mode == "json"
            return {"value": self.value}

    path = tmp_path / "registration.json"
    module._write_json_exclusive(path, (_Model("same"),))
    first = path.read_bytes()
    module._write_json_exclusive(path, (_Model("same"),))
    assert path.read_bytes() == first
    with pytest.raises(P2RV12ExecutionError, match="different evidence"):
        module._write_json_exclusive(path, (_Model("different"),))
