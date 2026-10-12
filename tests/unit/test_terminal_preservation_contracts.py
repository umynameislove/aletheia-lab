"""Owned-byte preservation with explicit manifest and permission boundaries.

These unit fixtures never read a private terminal store or reproduce a study.
"""

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from aletheia_lab.benchmark.p2 import p2r_v1_2_results as results
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import V3RuntimeError
from aletheia_lab.filesystem import windows_io_path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def owned_store(tmp_path):
    store = tmp_path / "source"
    store.mkdir()
    for name in results._STORE_FILES:
        (store / name).write_bytes(json.dumps({"synthetic": name}).encode() + b"\n")
    return store


@pytest.fixture
def preservation_boundary(monkeypatch):
    # Real hashes, copies, exclusive writes and publication remain exercised.
    # This manifest is deliberately not a claim about the terminal artifact.
    manifest = SimpleNamespace(
        store_sha256=results.TERMINAL_STORE_SHA256,
        terminal_artifact_sha256=results.TERMINAL_ARTIFACT_SHA256,
    )
    monkeypatch.setattr(results, "_load_terminal_manifest", lambda _: manifest)
    permissions = []
    monkeypatch.setattr(results, "_make_read_only", lambda p: permissions.append(p))
    monkeypatch.setattr(results, "_require_read_only_tree", lambda p: None)
    return manifest, permissions


def preserve(tmp_path, store):
    archive = tmp_path / "archive"
    receipt = results.preserve_p2r_v1_2_evidence(
        root=tmp_path,
        terminal_store_path=store,
        preservation_root=archive,
        preserved_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return results.preservation_destination(archive), receipt


@pytest.mark.parametrize("deep", [False, True])
def test_preservation_copies_owned_bytes_once_and_reconciles_receipt(
    tmp_path, preservation_boundary, deep
):
    if deep:
        tmp_path = windows_io_path(tmp_path / ("café-" * 10) / ("owned-" * 10))
        tmp_path.mkdir(parents=True)
    store = owned_store(tmp_path)
    before = {p.name: digest(p) for p in store.iterdir()}
    destination, receipt = preserve(tmp_path, store)
    assert {p.name: digest(p) for p in store.iterdir()} == before
    assert {p.name: digest(p) for p in (destination / "result-store").iterdir()} == before
    assert receipt.manifest_file_sha256 == before["manifest.json"]
    assert receipt.result_store_bytes_with_manifest == sum(
        p.stat().st_size for p in store.iterdir()
    )
    assert receipt == results.verify_preserved_p2r_v1_2(destination)
    assert preserve(tmp_path, store) == (destination, receipt)
    assert len(preservation_boundary[1]) == 1
    assert len(receipt.canonical_sha256()) == 64
    if deep:
        assert len(str(destination / "result-store/manifest.json")) > 260


@pytest.mark.parametrize("change", ["manifest", "bytes", "extra", "receipt", "writable"])
def test_preservation_rejects_owned_archive_tampering(
    tmp_path, monkeypatch, preservation_boundary, change
):
    destination, _ = preserve(tmp_path, owned_store(tmp_path))
    if change == "manifest":
        (destination / "result-store/manifest.json").write_bytes(b"changed\n")
    elif change == "bytes":
        (destination / "result-store/closeout.json").write_bytes(b"changed size\n")
    elif change == "extra":
        (destination / "extra").write_bytes(b"extra")
    elif change == "receipt":
        (destination / "preservation-receipt.json").write_bytes(b"{}")
    else:

        def reject(_):
            raise V3RuntimeError("preservation tree must remain read-only")

        monkeypatch.setattr(results, "_require_read_only_tree", reject)
    with pytest.raises(V3RuntimeError):
        results.verify_preserved_p2r_v1_2(destination)


def test_preservation_rejects_foreign_manifest_before_writing(tmp_path, preservation_boundary):
    store = owned_store(tmp_path)
    preservation_boundary[0].store_sha256 = "0" * 64
    with pytest.raises(V3RuntimeError, match="differs from the registered"):
        preserve(tmp_path, store)
    assert not (tmp_path / "archive").exists()


@pytest.mark.parametrize("failure", ["copy", "source_change"])
def test_preservation_retains_source_and_cleans_failed_stage(
    tmp_path, monkeypatch, preservation_boundary, failure
):
    store = owned_store(tmp_path)
    real_copy = results.shutil.copyfile

    def copy(source, destination):
        if failure == "copy":
            raise OSError("owned copy failed")
        answer = real_copy(source, destination)
        if source.name == "sealed-open.json":
            (store / "closeout.json").write_bytes(b"changed during copy\n")
        return answer

    monkeypatch.setattr(results.shutil, "copyfile", copy)
    with pytest.raises((OSError, V3RuntimeError)):
        preserve(tmp_path, store)
    assert store.is_dir()
    assert sorted(p.name for p in store.iterdir()) == list(results._STORE_FILES)
    assert not results.preservation_destination(tmp_path / "archive").exists()
    assert not list((tmp_path / "archive").rglob("preservation-receipt.json"))


def test_store_and_exclusive_write_fail_closed_on_unavailable_content(tmp_path):
    with pytest.raises(V3RuntimeError, match="regular file"):
        results._file_sha256(tmp_path / "missing")
    with pytest.raises(V3RuntimeError, match="regular directory"):
        results._exact_store_files(tmp_path / "missing")
    store = owned_store(tmp_path)
    (store / "manifest.json").unlink()
    with pytest.raises(V3RuntimeError, match="missing or unexpected"):
        results._exact_store_files(store)
    target = tmp_path / "exclusive"
    results._write_exclusive(target, b"original")
    with pytest.raises(V3RuntimeError, match="cannot create"):
        results._write_exclusive(target, b"replacement")
    assert target.read_bytes() == b"original"
    with pytest.raises(V3RuntimeError, match="directory is unavailable"):
        results.verify_preserved_p2r_v1_2(tmp_path / "absent")


@pytest.mark.parametrize(
    "loader,filename,payload",
    [
        ("_load_closeout", "closeout.json", {}),
        ("_load_audit", "instrument-audit.json", {}),
        ("_load_registrations", "registrations.json", {}),
        ("_load_registrations", "registrations.json", [{}, {}]),
        ("_load_observations", "paired-observations.json", []),
        ("_load_observations", "paired-observations.json", [{}] * 10),
        ("_load_terminal_manifest", "manifest.json", {}),
    ],
)
def test_terminal_parsers_reject_authored_incomplete_census(tmp_path, loader, filename, payload):
    (tmp_path / filename).write_bytes(json.dumps(payload).encode())
    with pytest.raises(V3RuntimeError):
        getattr(results, loader)(tmp_path)
    (tmp_path / filename).write_bytes(b"{")
    with pytest.raises(V3RuntimeError):
        getattr(results, loader)(tmp_path)


def summary_sources(monkeypatch):
    # Public facts supply model shapes, not a private measurement source.
    summary = results.load_p2r_v1_2_publication_summary()
    closeouts, registrations, decisions, observations = [], [], [], []
    for mechanism in summary.mechanisms:
        closeouts.append(
            SimpleNamespace(
                mechanism=mechanism.mechanism,
                primary_decision=mechanism.dataset_results[0],
                replication_decision=mechanism.dataset_results[1],
                protocol_sha256=mechanism.execution_protocol_sha256,
                registration_sha256=mechanism.registration_sha256,
                closeout_sha256=mechanism.mechanism_closeout_sha256,
                disposition="rejected",
                admitted=False,
                paired_instrument_pass=False,
                cross_dataset_claim_allowed=False,
            )
        )
        registrations.append(
            SimpleNamespace(
                mechanism=mechanism.mechanism,
                amendment_protocol_sha256=mechanism.amendment_protocol_sha256,
            )
        )
        decisions.append(
            SimpleNamespace(
                fault_type=mechanism.mechanism,
                reason_codes=("mechanism_direction_unstable", "no_dominant_cause_candidate"),
                eligible_candidate_ids=(),
            )
        )
        observations.extend(
            SimpleNamespace(
                fault_type=mechanism.mechanism,
                achieved_manipulation_magnitude=0.2,
            )
            for _ in range(5)
        )
    closeout = SimpleNamespace(
        execution_commit=summary.execution_commit,
        measurement_census_sha256=summary.measurement_census_sha256,
        paired_observation_census_sha256=summary.paired_observation_census_sha256,
        instrument_audit_sha256=summary.instrument_audit_sha256,
        n_mechanisms=2,
        n_admitted=0,
        outcomes_released_together=True,
        rerun_forbidden=True,
        mechanism_closeouts=closeouts,
    )
    for name, value in {
        "_load_terminal_manifest": SimpleNamespace(
            store_sha256=summary.terminal_store_sha256,
            terminal_artifact_sha256=summary.terminal_artifact_sha256,
        ),
        "_load_closeout": closeout,
        "_load_audit": SimpleNamespace(mechanism_decisions=decisions),
        "_load_registrations": registrations,
        "_load_observations": observations,
    }.items():
        monkeypatch.setattr(results, name, lambda _, value=value: value)
    return summary, closeout, observations


def test_public_summary_crosschecks_assembled_sources_without_study_execution(monkeypatch):
    summary, _, _ = summary_sources(monkeypatch)
    checked = results.verify_p2r_v1_2_publication_summary(summary, terminal_store_path="unused")
    assert checked == summary
    assert checked.n_admitted == 0
    assert checked.rerun_forbidden


@pytest.mark.parametrize("mutation", ["census", "commit", "observation", "metric", "decision"])
def test_public_summary_rejects_changed_boundary_facts(monkeypatch, mutation):
    summary, closeout, observations = summary_sources(monkeypatch)
    if mutation == "census":
        closeout.n_admitted = 1
    elif mutation == "commit":
        closeout.execution_commit = "0" * 40
    elif mutation == "observation":
        observations[0].achieved_manipulation_magnitude = 0.3
    elif mutation == "decision":
        closeout.mechanism_closeouts[0].admitted = True
    else:
        payload = deepcopy(summary.model_dump(mode="json"))
        payload["mechanisms"][0]["dataset_results"][0]["median_target_effect"] = 0.02
        summary = results.P2RV12PublicationSummary.model_validate_json(json.dumps(payload))
    with pytest.raises(V3RuntimeError, match="differs from terminal|does not reproduce"):
        results.verify_p2r_v1_2_publication_summary(summary, terminal_store_path="unused")


def test_permission_helpers_keep_policy_separate_from_host_acl(tmp_path, monkeypatch):
    root = tmp_path / "tree"
    root.mkdir()
    child = root / "artifact"
    child.write_bytes(b"synthetic")
    calls = []
    with monkeypatch.context() as scope:
        scope.setattr(Path, "chmod", lambda path, mode: calls.append((path, mode)))
        results._make_read_only(root)
        results._make_writable_for_cleanup(root)
    assert calls == [(child, 0o444), (root, 0o555), (root, 0o755), (child, 0o644)]
    with monkeypatch.context() as scope:
        scope.setattr(Path, "stat", lambda path, **_: SimpleNamespace(st_mode=0o444))
        results._require_read_only_tree(root)
        scope.setattr(Path, "stat", lambda path, **_: SimpleNamespace(st_mode=0o644))
        with pytest.raises(V3RuntimeError, match="remain read-only"):
            results._require_read_only_tree(root)


@pytest.mark.parametrize(
    "mutation",
    [
        "mechanism_order",
        "reason_order",
        "dataset_order",
        "passing_dataset",
        "threshold",
        "identity",
    ],
)
def test_public_summary_cannot_relax_census_or_registered_bounds(mutation):
    payload = results.load_p2r_v1_2_publication_summary().model_dump(mode="json")
    mechanism = payload["mechanisms"][0]
    if mutation == "mechanism_order":
        payload["mechanisms"].reverse()
    elif mutation == "reason_order":
        mechanism["instrument_reason_codes"].reverse()
    elif mutation == "dataset_order":
        mechanism["dataset_results"].reverse()
    elif mutation == "passing_dataset":
        mechanism["dataset_results"][0]["passed"] = True
    elif mutation == "threshold":
        mechanism["minimum_practical_effect"] = 0.005
    else:
        payload["execution_commit"] = "0" * 40
    with pytest.raises(ValidationError):
        results.P2RV12PublicationSummary.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("mutation", ["timestamp", "identity"])
def test_preservation_receipt_requires_aware_time_and_registered_identity(
    tmp_path, preservation_boundary, mutation
):
    _, receipt = preserve(tmp_path, owned_store(tmp_path))
    payload = receipt.model_dump()
    if mutation == "timestamp":
        payload["preserved_at"] = datetime(2026, 1, 1)
    else:
        payload["terminal_store_sha256"] = "0" * 64
    with pytest.raises(ValidationError):
        results.P2RV12PreservationReceipt.model_validate(payload)


def test_preservation_hash_and_enumeration_report_owned_io_failure(tmp_path, monkeypatch):
    target = tmp_path / "artifact"
    target.write_bytes(b"synthetic")

    def unavailable(*args, **kwargs):
        raise PermissionError("owned fixture denied")

    with monkeypatch.context() as scope:
        scope.setattr(Path, "open", unavailable)
        with pytest.raises(V3RuntimeError, match="cannot hash"):
            results._file_sha256(target)
    store = owned_store(tmp_path)
    with monkeypatch.context() as scope:
        scope.setattr(Path, "iterdir", unavailable)
        with pytest.raises(V3RuntimeError, match="cannot inspect"):
            results._exact_store_files(store)
    (store / "manifest.json").unlink()
    (store / "manifest.json").mkdir()
    with pytest.raises(V3RuntimeError, match="non-regular artifact"):
        results._exact_store_files(store)
    with pytest.raises(V3RuntimeError, match="publication summary is unavailable"):
        results.load_p2r_v1_2_publication_summary(tmp_path / "absent")
