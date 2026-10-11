from __future__ import annotations

import copy
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation import model_load_validation as validation
from aletheia_lab.evaluation.model_load_provenance import document_digest

ROOT = Path(__file__).resolve().parents[2]
HEAD = "a" * 40


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "checkout"
    for relative in validation.CODE_PATHS:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)

    def git(path: Path, *args: str) -> str:
        assert path == root.resolve()
        if args == ("rev-parse", "--show-toplevel"):
            return str(root)
        if args == ("rev-parse", "HEAD"):
            return HEAD
        assert args == ("merge-base", "--is-ancestor", HEAD, "HEAD")
        return ""

    monkeypatch.setattr(validation, "_git", git)
    return root


def _plan(root: Path, path: Path) -> dict[str, Any]:
    validation.prepare(root, path)
    return json.loads(path.read_text())


def _rewrite(path: Path, plan: dict[str, Any]) -> None:
    unsigned = {key: value for key, value in plan.items() if key != "plan_sha256"}
    plan["plan_sha256"] = document_digest(unsigned)
    path.write_text(json.dumps(plan))


def test_complete_census_and_native_entry_budget_are_independently_counted() -> None:
    protocol = validation.load_protocol(ROOT)
    slots = validation.census(protocol)
    assert len(slots) == len({slot["slot_id"] for slot in slots}) == 48
    assert Counter(slot["backend"] for slot in slots) == {"onnxruntime": 24, "skops": 24}
    assert Counter(slot["family"] for slot in slots) == {
        "handoff": 12,
        "retry_delivery": 12,
        "native_reentry": 12,
        "warm_cache": 12,
    }
    expected = {
        "planned_slots": 48,
        "loader_format_strata": 2,
        "lifecycle_families_per_stratum": 4,
        "planned_load_slots": 44,
        "planned_cache_slots": 4,
        "observation_load_slots": 22,
        "prevention_opportunities": 24,
        "max_target_native_entries": 48,
        "max_auxiliary_native_entries": 24,
    }
    assert validation.counts(slots) == expected
    assert expected["max_target_native_entries"] + expected["max_auxiliary_native_entries"] == 72
    assert all("verdict" not in slot and "expected_status" not in slot for slot in slots)
    reentries = [slot for slot in slots if slot["schedule"] == "native-same-buffer-reentry"]
    duplicates = [slot for slot in slots if slot["schedule"] == "delivery-duplicate-only"]
    assert len(reentries) == len(duplicates) == 4
    assert all(slot["planned_target_entries"] == 2 for slot in reentries)
    assert all(slot["planned_target_entries"] == 1 for slot in duplicates)


def test_prepare_preflight_only_design_no_loader_or_fit(checkout: Path, tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    summary = validation.prepare(checkout, path)
    before = path.read_bytes()
    replay = validation.preflight(checkout, path)
    assert path.read_bytes() == before
    assert summary["plan_sha256"] == replay["plan_sha256"]
    assert replay["protocol_locked"] is True
    for field in (
        "runner_implemented",
        "execution_ready",
        "execution_authorized",
        "validation_outcomes_observed",
        "runtime_compatibility_verified",
    ):
        assert replay[field] is False
    assert replay["native_loader_entries"] == replay["provider_calls"] == 0
    with pytest.raises(FileExistsError):
        validation.prepare(checkout, path)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution_authorized", True),
        ("execution_ready", True),
        ("runner_implemented", True),
        ("validation_outcomes_observed", True),
        ("model_fitted", True),
        ("native_loader_entries", 1),
        ("provider_calls", 1),
        ("execution_seal", {}),
        ("execution_authorized", 0),
        ("protocol_locked", 1),
        ("new_field", "not allowed"),
        ("census", []),
        ("counts", {}),
        ("creation_git_head", "invalid"),
    ],
    ids=[
        "authority",
        "ready",
        "runner",
        "outcomes",
        "fit",
        "native-load",
        "provider",
        "seal",
        "bool-as-int",
        "true-as-int",
        "extra",
        "census",
        "counts",
        "head",
    ],
)
def test_even_rehashed_plan_tampering_fails(
    checkout: Path, tmp_path: Path, field: str, value: Any
) -> None:
    path = tmp_path / "plan.json"
    plan = _plan(checkout, path)
    plan[field] = value
    _rewrite(path, plan)
    with pytest.raises(ValueError):
        validation.preflight(checkout, path)


def test_every_code_binding_and_protocol_bytes_are_replayed(checkout: Path, tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    _plan(checkout, path)
    for relative in validation.CODE_PATHS:
        source = checkout / relative
        before = source.read_bytes()
        source.write_bytes(before + b"\n")
        with pytest.raises(ValueError):
            validation.preflight(checkout, path)
        source.write_bytes(before)
    assert validation.preflight(checkout, path)["status"].endswith("pass")


@pytest.mark.parametrize(
    "section",
    [
        "backends",
        "contract",
        "schedules",
        "capture",
        "comparators",
        "analysis",
        "resources",
        "lock",
        "prior_exposure",
    ],
)
def test_any_prospective_design_change_requires_a_new_protocol(
    checkout: Path, section: str
) -> None:
    path = checkout / validation.PROTOCOL_PATH
    protocol = json.loads(path.read_text())
    protocol[section] = {}
    path.write_text(json.dumps(protocol))
    with pytest.raises(ValueError, match="protocol identity"):
        validation.load_protocol(checkout)


@pytest.mark.parametrize(
    "data",
    [
        b'{"x":1,"x":2}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b"[]",
        b"not JSON",
        b"x" * (validation.MAX_PLAN_BYTES + 1),
    ],
    ids=["duplicate", "nan", "infinite", "array", "invalid", "oversized"],
)
def test_bounded_strict_json_reader(tmp_path: Path, data: bytes) -> None:
    path = tmp_path / "plan.json"
    path.write_bytes(data)
    with pytest.raises(ValueError):
        validation._read_document(path)


def test_private_plan_rejects_checkout_missing_parent_and_git_parent(
    checkout: Path, tmp_path: Path
) -> None:
    with pytest.raises(ValueError):
        validation.prepare(checkout, checkout / "private.json")
    with pytest.raises(ValueError):
        validation.prepare(checkout, tmp_path / "absent" / "plan.json")
    other = tmp_path / "other-repo"
    other.mkdir()
    (other / ".git").mkdir()
    with pytest.raises(ValueError):
        validation.prepare(checkout, other / "private.json")


def test_symlink_destination_ancestor_and_code_fail_closed(checkout: Path, tmp_path: Path) -> None:
    directory = tmp_path / "private"
    directory.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(directory, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation requires OS privilege")
    with pytest.raises(ValueError):
        validation.prepare(checkout, link / "plan.json")
    file_link = tmp_path / "plan.json"
    file_link.symlink_to(directory / "target.json")
    with pytest.raises(ValueError):
        validation.prepare(checkout, file_link)
    code = checkout / validation.CODE_PATHS[-1]
    code.unlink()
    code.symlink_to(ROOT / validation.CODE_PATHS[-1])
    with pytest.raises(ValueError):
        validation.prepare(checkout, directory / "plan.json")


def test_raw_digest_and_creation_commit_are_verified(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "plan.json"
    plan = _plan(checkout, path)
    tampered = copy.deepcopy(plan)
    tampered["plan_sha256"] = "0" * 64
    path.write_text(json.dumps(tampered))
    with pytest.raises(ValueError, match="digest"):
        validation.preflight(checkout, path)
    path.write_text(json.dumps(plan))
    original = validation._git

    def git(root: Path, *args: str) -> str:
        if args[0] == "merge-base":
            raise ValueError("not a descendant")
        return original(root, *args)

    monkeypatch.setattr(validation, "_git", git)
    with pytest.raises(ValueError, match="descendant"):
        validation.preflight(checkout, path)


def test_guard_baseline_and_failure_contracts_are_not_checker_win_labels() -> None:
    protocol = validation.load_protocol(ROOT)
    assert "atomic_root_attempt_bound_one_invocation_token" in protocol["comparators"]["prevention"]
    assert "same_scoped_facts" in protocol["comparators"]["P"]
    assert "full_constructor_bytes_argument_available" in protocol["capture"]["native_available"]
    assert "no_population_CI" in protocol["analysis"]["inference"]
    assert "no_drop_no_replacement" in protocol["analysis"]["failure"]
    assert "no_posthoc_field_drops" in protocol["capture"]["frame_equality"]
    assert protocol["resources"]["provider_calls"] == 0
    assert protocol["resources"]["network_permitted_during_execution"] is False
