from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation import model_load_observability as study
from aletheia_lab.evaluation import model_load_runtime as runtime
from aletheia_lab.evaluation.model_load_contract import Record, Scope
from aletheia_lab.project.identity import content_sha256

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def report() -> dict[str, Any]:
    return study.run_development(ROOT)


def _resign(value: dict[str, Any]) -> None:
    unsigned = {key: item for key, item in value.items() if key != "report_sha256"}
    value["report_sha256"] = content_sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    )


def test_actual_two_workflow_census_preserves_unknowns_and_no_new_load_denominator(
    report: dict[str, Any],
) -> None:
    summary = report["summary"]
    assert summary["episode_count"] == 12
    assert summary["workflow_count"] == 2
    assert summary["eligible_load_attempts"] == 10
    assert summary["no_new_load_attempts"] == 2
    assert summary["auxiliary_retry_attempts"] == 4
    assert summary["cache_warmup_loads"] == 2
    assert summary["same_evidence_S_T_disagreements"] == 0
    for view, identified in (("native", 0), ("before", 5), ("after", 7)):
        for checker in ("S", "T"):
            counts = summary["comparisons"][view][checker]
            assert counts["correct_identified_load_attempts"] == identified
            assert counts["false_compliance"] == counts["false_violation"] == 0
    assert report["disposition"] == "narrow_controlled_feasibility_no_new_method_evidence"
    assert report["provider_calls"] == 0
    assert report["historical_artifacts_read"] is False


def test_actual_snapshots_retry_loader_bytes_and_transport_faults_are_retained(
    report: dict[str, Any],
) -> None:
    identities = report["artifact_sha256"]
    for row in report["rows"]:
        truth = row["truth"]
        reference = study.reference_from_control(row, identities)
        assert reference == row["reference"]
        if row["episode"] == "overlap_delayed":
            assert truth["selection_snapshot"] == ["A", 0]
            assert row["observations"]["native"]["records"][0]["revision"] == 1
            assert row["decisions"]["before"]["S"]["verdict"] == "unknown"
            assert row["decisions"]["after"]["S"]["verdict"] == "compliant"
        if "predecessor_attempt" in truth:
            parent = truth["predecessor_attempt"]
            assert parent["terminal"] == "injected_post_load_retry"
            assert parent["deserializer_invocations"] == len(parent["raw_loader_buffers"]) == 1
        if row["episode"] in {"retry_misjoin", "retry_lost_witness", "retry_expired_selection"}:
            assert reference["verdict"] == "violation"
            assert row["decisions"]["after"]["S"]["verdict"] == "unknown"
    assert report["summary"]["transport"] == {
        "submitted": 52,
        "delayed": 2,
        "dropped": 1,
        "expired": 3,
        "misjoined": 1,
    }


def test_independent_replay_never_deserializes_retained_report(
    report: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_load(*_: object, **__: object) -> None:
        raise AssertionError("replay must hash bytes, not deserialize pickle")

    monkeypatch.setattr(runtime.joblib, "load", no_load)
    result = study.verify_report(report, root=ROOT)
    assert result["verification"] == "pass"


@pytest.mark.parametrize(
    "mutation",
    [
        "schema",
        "identity",
        "code",
        "census",
        "truth",
        "decisions",
        "summary",
        "disposition",
        "policy",
        "scope",
        "views",
        "snapshot",
        "retry",
        "cache",
    ],
)
def test_replay_rejects_tampering_even_when_container_is_rehashed(
    report: dict[str, Any], mutation: str
) -> None:
    value = copy.deepcopy(report)
    row = value["rows"][0]
    if mutation == "schema":
        value["schema_version"] = "other"
    elif mutation == "identity":
        value["report_sha256"] = "bad"
    elif mutation == "code":
        value["code_sha256"][study.CODE_PATHS[0]] = "bad"
    elif mutation == "census":
        value["rows"].pop()
    elif mutation == "truth":
        row["reference"]["verdict"] = "compliant"
    elif mutation == "decisions":
        row["decisions"]["after"]["S"]["verdict"] = "compliant"
    elif mutation == "summary":
        value["summary"]["episode_count"] = 13
    elif mutation == "disposition":
        value["disposition"] = "novel_method"
    elif mutation == "policy":
        row["truth"]["policy"]["policy"] = "resolve_at_load"
    elif mutation == "scope":
        row["observations"]["before"]["scope"]["request"] = "foreign"
    elif mutation == "views":
        row["observations"].pop("native")
    elif mutation == "snapshot":
        row["truth"]["selection_snapshot"] = ["A", 0]
    elif mutation == "retry":
        row["truth"]["predecessor_attempt"] = {}
    elif mutation == "cache":
        row["truth"]["cache_origin_digest"] = value["artifact_sha256"]["A"]
    if mutation != "identity":
        _resign(value)
    with pytest.raises(ValueError):
        study.verify_report(value, root=ROOT)


def test_control_reference_uses_raw_bytes_not_candidate_receipts(report: dict[str, Any]) -> None:
    row = copy.deepcopy(report["rows"][0])
    original = study.reference_from_control(row, report["artifact_sha256"])
    row["observations"] = {}
    row["decisions"] = {}
    assert study.reference_from_control(row, report["artifact_sha256"]) == original
    row["truth"]["closed"] = False
    with pytest.raises(ValueError, match="incomplete"):
        study.reference_from_control(row, report["artifact_sha256"])


def test_same_actual_buffer_is_hashed_and_passed_to_joblib(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = runtime.create_artifacts(tmp_path)
    original_load = runtime.joblib.load
    observed: list[bytes] = []

    def capture(buffer: Any, *, mmap_mode: None) -> Any:
        assert mmap_mode is None and buffer.tell() == 0
        observed.append(buffer.getvalue())
        return original_load(buffer, mmap_mode=mmap_mode)

    monkeypatch.setattr(runtime.joblib, "load", capture)
    case = tmp_path / "case"
    case.mkdir()
    row = runtime.run_episode(case, "sqlite_queue", runtime.schedules("sqlite_queue")[0], artifacts)
    assert [value.hex() for value in observed] == row["truth"]["raw_loader_buffers"]
    load_record = next(
        record for record in row["observations"]["after"].records if record.kind == "load"
    )
    assert load_record.digest == content_sha256(observed[0])


def test_transport_routes_do_not_recover_misjoined_load_as_parent_evidence(tmp_path: Path) -> None:
    target = Scope("request", 1)
    spool = runtime.Transport(tmp_path / "spool.sqlite", "misjoin_load", target)
    try:
        record = Record("actual", target, "load", "a" * 64, "token")
        spool.submit(record)
        assert spool.read(target) == ()
        assert spool.read(Scope("request", 0)) == (record,)
        assert spool.counts["misjoined"] == 1
    finally:
        spool.close()


def test_disposition_rejects_early_false_commitment_even_if_final_safe(
    report: dict[str, Any],
) -> None:
    summary = copy.deepcopy(report["summary"])
    summary["comparisons"]["before"]["S"]["false_compliance"] = 1
    assert study._disposition(summary) == "implementation_or_reference_gap"
    summary["same_evidence_S_T_disagreements"] = 1
    assert study._disposition(summary) == "implementation_or_model_gap"


def test_artifact_creation_and_loading_do_not_reuse_or_accept_mutated_pickle(
    tmp_path: Path,
) -> None:
    artifacts = runtime.create_artifacts(tmp_path)
    with pytest.raises(FileExistsError):
        runtime.create_artifacts(tmp_path)
    artifacts["A"].path.write_bytes(b"not a trusted local model")
    with pytest.raises(ValueError, match="changed"):
        runtime._warm_cache(artifacts["A"])
