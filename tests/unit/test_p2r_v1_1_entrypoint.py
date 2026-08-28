"""Entrypoint boundaries for the single prospective P2R v1.1 attempt."""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from types import ModuleType

import pytest

from aletheia_lab.benchmark.p2.p2r_recovery_execution import (
    P2RRecoveryExecutionError,
)

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT_PATH = ROOT / "scripts/p2r_v1_1_confirmatory.py"


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
