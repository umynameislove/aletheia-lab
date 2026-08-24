"""Fail-closed entrypoint contracts for the single v3.2 attempt."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from aletheia_lab.benchmark.p2.confirmatory_v3_2_closeout import (
    V32ProtocolRegistrationReceipt,
    load_and_verify_terminal_store,
    registration_from_github_release,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_2_protocol import (
    DEFAULT_V3_2_PROTOCOL_PATH,
    load_v3_2_confirmatory_protocol,
    verify_v3_2_protocol_artifacts,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_closeout import (
    V3ExecutionEnvironmentReceipt,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    DEFAULT_V3_PROTOCOL_PATH,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import V3RuntimeError
from scripts import p2_v3_2_confirmatory as entrypoint

_COMMIT = "d63e4262961930d7d8126875d38c2c9625893f14"


def _registration() -> V32ProtocolRegistrationReceipt:
    return registration_from_github_release(
        protocol=load_v3_2_confirmatory_protocol(),
        tagged_protocol_commit=_COMMIT,
        payload={
            "id": 1234567,
            "tag_name": "p2-label-noise-shift-factorial-v3.2",
            "html_url": (
                "https://github.com/umynameislove/aletheia-lab/releases/tag/"
                "p2-label-noise-shift-factorial-v3.2"
            ),
            "created_at": "2026-08-24T04:00:00Z",
            "published_at": "2026-08-24T04:05:00Z",
            "immutable": True,
            "draft": False,
            "prerelease": False,
        },
    )


def _environment() -> V3ExecutionEnvironmentReceipt:
    return V3ExecutionEnvironmentReceipt(
        execution_commit=_COMMIT,
        python_version="3.12.0",
        python_implementation="CPython",
        operating_system="test-os",
        machine="test-machine",
        package_versions={
            "numpy": "2.0",
            "pandas": "2.0",
            "pydantic": "2.0",
            "scikit-learn": "1.5",
            "scipy": "1.14",
        },
    )


def test_tag_parser_accepts_only_the_v3_2_protocol() -> None:
    checked = entrypoint._protocol_from_text(DEFAULT_V3_2_PROTOCOL_PATH.read_text(encoding="utf-8"))
    assert checked == load_v3_2_confirmatory_protocol()
    with pytest.raises(V3RuntimeError, match="valid v3.2"):
        entrypoint._protocol_from_text(DEFAULT_V3_PROTOCOL_PATH.read_text(encoding="utf-8"))


def test_registration_write_is_idempotent_but_not_replaceable(tmp_path: Path) -> None:
    path = tmp_path / "registration.json"
    registration = _registration()
    entrypoint._write_registration_exclusive(path, registration)
    entrypoint._write_registration_exclusive(path, registration)
    changed = registration.model_copy(update={"release_id": registration.release_id + 1})
    with pytest.raises(V3RuntimeError, match="different evidence"):
        entrypoint._write_registration_exclusive(path, changed)


def test_sealed_marker_is_exclusive(tmp_path: Path) -> None:
    marker = tmp_path / "sealed.json"
    registration = _registration()
    entrypoint._open_sealed_marker(
        path=marker,
        protocol_sha256=registration.protocol_sha256,
        registration_sha256=registration.canonical_sha256(),
        execution_commit=_COMMIT,
    )
    with pytest.raises(V3RuntimeError, match="rerun is forbidden"):
        entrypoint._open_sealed_marker(
            path=marker,
            protocol_sha256=registration.protocol_sha256,
            registration_sha256=registration.canonical_sha256(),
            execution_commit=_COMMIT,
        )


@pytest.mark.parametrize(
    "responses",
    [
        {"status": "dirty"},
        {"status": "", "branch": "feature", "head": _COMMIT, "origin": _COMMIT},
        {"status": "", "branch": "main", "head": "a" * 40, "origin": _COMMIT},
    ],
)
def test_execution_requires_clean_synchronized_main(
    monkeypatch: pytest.MonkeyPatch, responses: dict[str, str]
) -> None:
    def fake_git(_root: Path, *arguments: str) -> str:
        if arguments[0] == "status":
            return responses.get("status", "")
        if arguments[:2] == ("branch", "--show-current"):
            return responses.get("branch", "main")
        if arguments[-1] == "HEAD":
            return responses.get("head", _COMMIT)
        return responses.get("origin", _COMMIT)

    monkeypatch.setattr(entrypoint, "_git", fake_git)
    with pytest.raises(V3RuntimeError, match="clean|synchronized"):
        entrypoint._verify_clean_main(Path("."))


def test_post_marker_hard_failure_creates_atomic_failure_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = load_v3_2_confirmatory_protocol()
    _, manifest, receipt, _, _ = verify_v3_2_protocol_artifacts(protocol)
    registration = _registration()
    registration_path = tmp_path / "registration.json"
    registration_path.write_text(registration.model_dump_json(), encoding="utf-8")
    marker = tmp_path / "sealed.json"
    output = tmp_path / "terminal-store"
    args = Namespace(
        root=tmp_path,
        protocol=DEFAULT_V3_2_PROTOCOL_PATH,
        manifest=Path("manifest.json"),
        receipt=Path("receipt.json"),
        data_dir=tmp_path,
        registration=registration_path,
        marker=marker,
        output=output,
        confirm_protocol_sha256=protocol.canonical_sha256(),
        confirm_registration_sha256=registration.canonical_sha256(),
    )
    monkeypatch.setattr(
        entrypoint,
        "_validated_inputs",
        lambda _args: (
            tmp_path,
            tmp_path / DEFAULT_V3_2_PROTOCOL_PATH,
            tmp_path,
            registration_path,
            marker,
            protocol,
            manifest,
            receipt,
        ),
    )
    monkeypatch.setattr(entrypoint, "_verify_clean_main", lambda _root: _COMMIT)
    monkeypatch.setattr(
        entrypoint,
        "_verify_tag",
        lambda _root, _path, _sha256: registration.tagged_protocol_commit,
    )
    monkeypatch.setattr(entrypoint, "capture_execution_environment", lambda _head: _environment())

    def fail_load(**_kwargs: object) -> None:
        raise V3RuntimeError("synthetic hard failure")

    monkeypatch.setattr(entrypoint, "load_v3_dataset_snapshot_for_registration", fail_load)
    report = entrypoint._execute(args)
    manifest_store = load_and_verify_terminal_store(output)
    assert report["status"] == "registered_v3_2_technical_failure"
    assert report["partial_outcome_published"] is False
    assert report["scientific_disposition_generated"] is False
    assert manifest_store.terminal_status == "technical_failure"
    assert marker.is_file()
    assert not (output / "primary-attempt.json").exists()
