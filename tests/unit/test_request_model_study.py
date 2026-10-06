"""Synthetic source/reference/replay falsifiers; no Ray or signing execution.

The byte releases below are inert test bytes, never deserialized model artifacts.
These code checks do not constitute independent realized-study verification.
"""

from __future__ import annotations

import base64
import copy
import json
import sqlite3
import zlib
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation import request_model_study as study
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.request_model_reference import (
    check_source,
    digest,
    native_context_baseline,
    output_baseline,
    reference_row,
)
from aletheia_lab.evaluation.request_model_retention import RequestModelArchive
from aletheia_lab.evaluation.request_model_source import planned_rows
from aletheia_lab.project.identity import content_sha256

ADAPTER_HASH = "a" * 64
VERSION = "2.54.0"


def source_fixture(mode: str = "owned_ml", stale: bool = False) -> dict[str, Any]:
    """A complete synthetic caller census, observed objects, operands and contexts."""
    planned = planned_rows(mode)
    artifacts = {}
    if mode == "owned_ml":
        for model, slope in (("aaa", 1.0), ("bbb", 2.0)):
            raw = f"inert synthetic release {model}".encode()
            parameters = {"coef": [slope], "intercept": 0.0}
            artifacts[model] = {
                "raw_hex": raw.hex(),
                "artifact_sha256": content_sha256(raw),
                "parameters": parameters,
                "object_fingerprint": digest(parameters),
            }
    source: dict[str, Any] = {
        "mode": mode,
        "planned": planned,
        "environment": {"packages": {"ray": VERSION}, "adapter_sha256": ADAPTER_HASH},
        "rows": [],
        "loads": {},
        "batches": [],
        "artifacts": artifacts,
        "failures": [],
    }
    first: dict[str, str] = {}
    for plan in planned:
        scenario, requested = plan["scenario"], plan["requested_model"]
        first.setdefault(scenario, requested)
        actual = first[scenario] if stale and plan["kind"] == "batched" else requested
        generation = f"{scenario}-load-{actual}"
        if generation not in source["loads"]:
            parameters = (
                {"value": f"model_obj_for_{actual}"}
                if mode == "original"
                else artifacts[actual]["parameters"]
            )
            source["loads"][generation] = {
                "model": actual,
                "raw_hex": None if mode == "original" else artifacts[actual]["raw_hex"],
                "parameters": copy.deepcopy(parameters),
                "object_fingerprint": digest(parameters),
                "object_id": 100 + len(source["loads"]),
                "pid": 10,
            }
        load = source["loads"][generation]
        invalid = plan["arg"] == "invalid"
        output: Any = None
        if not invalid:
            output = (
                f"Response from model_obj_for_{actual} {plan['arg']}"
                if mode == "original"
                else load["parameters"]["coef"][0] * float(plan["arg"])
                + load["parameters"]["intercept"]
            )
        request_id = f"response-{plan['token']}"
        row = {
            **plan,
            "http_status": 500 if invalid else 200,
            "output": output,
            "response_text": "native invalid input" if invalid else encode(output),
            "response_request_id": request_id,
            "error": "native_http_500" if invalid else None,
        }
        source["rows"].append(row)
        source["batches"].append(
            {
                "batch": f"batch-{plan['token']}",
                "scenario": scenario,
                "kind": plan["kind"],
                "completed": not invalid,
                "error": "ValueError: synthetic invalid input" if invalid else None,
                "actual_generation": generation,
                "actual_object": {
                    key: copy.deepcopy(load[key])
                    for key in ("parameters", "object_fingerprint", "object_id", "pid")
                },
                "members": [
                    {
                        "token": None if mode == "original" else plan["token"],
                        "arg": plan["arg"],
                        "output": output,
                    }
                ],
                "native_context": {
                    "model": actual,
                    "generic_model": actual if plan["kind"] == "non_batched" else "",
                    "request": request_id if plan["kind"] == "non_batched" else "",
                },
                "native_batch_contexts": []
                if plan["kind"] == "non_batched" or stale
                else [{"model": requested, "request": request_id}],
            }
        )
    return source


@pytest.mark.parametrize("mode", ["original", "owned_ml"])
@pytest.mark.parametrize("stale", [False, True])
def test_summary_agrees_with_raw_objects_and_ordinary_correspondence(
    mode: str, stale: bool
) -> None:
    source = source_fixture(mode, stale)
    expected = check_source(source, VERSION, ADAPTER_HASH)
    summary = study.audit_summary(source, VERSION, ADAPTER_HASH)
    assert summary["reference"] == expected
    assert summary["audit"]["denominator"] == len(planned_rows(mode))
    assert [row["receipt"] for row in summary["audit"]["outcomes"]] == expected
    assert [row["explicit_item_links"] for row in summary["audit"]["outcomes"]] == expected
    if mode == "original":
        assert summary["output_only"] == expected
        assert summary["reference_counts"] == (
            {"compliant": 8, "violation": 4} if stale else {"compliant": 12}
        )
    else:
        assert summary["reference_counts"] == (
            {"compliant": 12, "violation": 6, "unknown": 1}
            if stale
            else {"compliant": 18, "unknown": 1}
        )
        assert summary["http_failure_count"] == 1


@pytest.mark.parametrize("field", ["object_id", "pid", "parameters", "object_fingerprint"])
def test_raw_reference_rejects_a_different_actual_inference_object(field: str) -> None:
    source = source_fixture()
    observed = source["batches"][0]["actual_object"]
    observed[field] = {"coef": [9.0], "intercept": 0.0} if field == "parameters" else "foreign"
    with pytest.raises(ValueError, match="different object"):
        check_source(source, VERSION, ADAPTER_HASH)


@pytest.mark.parametrize("field", ["raw_hex", "parameters", "object_fingerprint"])
def test_raw_reference_rejects_loaded_bytes_or_parameters_outside_owned_release(
    field: str,
) -> None:
    source = source_fixture()
    generation = source["batches"][0]["actual_generation"]
    source["loads"][generation][field] = {
        "raw_hex": b"other bytes".hex(),
        "parameters": {"coef": [9.0], "intercept": 0.0},
        "object_fingerprint": "b" * 64,
    }[field]
    with pytest.raises(ValueError):
        check_source(source, VERSION, ADAPTER_HASH)


def test_release_hash_tampering_cannot_authorize_unchanged_native_bytes() -> None:
    source = source_fixture()
    source["artifacts"]["aaa"]["artifact_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="owned model release"):
        check_source(source, VERSION, ADAPTER_HASH)


@pytest.mark.parametrize("field", ["arg", "output"])
def test_foreign_member_operand_or_result_is_conflict(field: str) -> None:
    source = source_fixture()
    source["batches"][0]["members"][0][field] = "foreign operand" if field == "arg" else 9.0
    assert reference_row(source, source["rows"][0]) == "conflict"


def test_self_consistent_output_tampering_is_recomputed_from_actual_coefficients() -> None:
    source = source_fixture()
    source["rows"][0]["output"] = 9.0
    source["batches"][0]["members"][0]["output"] = 9.0
    with pytest.raises(ValueError, match="independently recomputed"):
        check_source(source, VERSION, ADAPTER_HASH)


def test_original_object_content_is_checked_not_merely_its_self_reported_hash() -> None:
    source = source_fixture("original")
    load = next(iter(source["loads"].values()))
    load["parameters"] = {"value": "another placeholder"}
    load["object_fingerprint"] = digest(load["parameters"])
    with pytest.raises(ValueError, match="original model object"):
        check_source(source, VERSION, ADAPTER_HASH)


@pytest.mark.parametrize("change", ["omit", "reorder", "authority", "environment", "adapter"])
def test_raw_reference_enforces_full_ordered_census_and_executed_binding(change: str) -> None:
    source = source_fixture()
    if change == "omit":
        source["rows"].pop()
    elif change == "reorder":
        source["rows"].reverse()
    elif change == "authority":
        source["rows"][0]["requested_model"] = "bbb"
    elif change == "environment":
        source["environment"]["packages"]["ray"] = "2.48.0"
    else:
        source["environment"]["adapter_sha256"] = "b" * 64
    with pytest.raises(ValueError):
        check_source(source, VERSION, ADAPTER_HASH)


@pytest.mark.parametrize("missing", ["load", "completion", "member"])
def test_incomplete_raw_capture_remains_unknown(missing: str) -> None:
    source = source_fixture()
    batch = source["batches"][0]
    if missing == "load":
        source["loads"].pop(batch["actual_generation"])
    elif missing == "completion":
        batch["completed"] = False
    else:
        batch["members"][0]["token"] = "other request"
    assert reference_row(source, source["rows"][0]) == "unknown"


def test_duplicate_actual_membership_is_conflict() -> None:
    source = source_fixture()
    source["batches"].append(copy.deepcopy(source["batches"][0]))
    assert reference_row(source, source["rows"][0]) == "conflict"
    assert study.audit_summary(source, VERSION, ADAPTER_HASH)["reference"][0] == "conflict"


def test_equal_numeric_outputs_do_not_identify_the_used_model() -> None:
    source = source_fixture(stale=True)
    rows = [row for row in source["rows"] if row["scenario"] == "equal_output"]
    assert [row["output"] for row in rows] == [0.0, 0.0]
    assert [reference_row(source, row) for row in rows] == ["compliant", "violation"]
    assert [output_baseline(source, row) for row in rows] == ["unknown", "unknown"]


def test_fixed_native_batch_context_joins_response_id_without_candidate_payload_links() -> None:
    source = source_fixture()
    row = next(row for row in source["rows"] if row["kind"] == "batched")
    batch = next(
        batch for batch in source["batches"] if batch["members"][0]["token"] == row["token"]
    )
    assert batch["native_context"]["request"] == ""
    batch["members"][0]["token"] = "foreign payload token"
    assert reference_row(source, row) == "unknown"
    assert native_context_baseline(source, row) == "compliant"
    row["requested_model"] = "bbb"
    assert native_context_baseline(source, row) == "violation"
    row["response_request_id"] = "unmatched response"
    assert native_context_baseline(source, row) == "unknown"


def test_duplicate_native_response_id_is_not_an_unambiguous_join() -> None:
    source = source_fixture()
    row = next(row for row in source["rows"] if row["kind"] == "batched")
    batch = next(
        batch for batch in source["batches"] if batch["members"][0]["token"] == row["token"]
    )
    batch["native_batch_contexts"] *= 2
    assert native_context_baseline(source, row) == "unknown"


@pytest.mark.parametrize("comparison", ["receipt", "explicit_item_links"])
def test_summary_rejects_candidate_disagreement_instead_of_reporting_equality(
    monkeypatch: pytest.MonkeyPatch, comparison: str
) -> None:
    source = source_fixture()
    candidate = study.analyze(source)
    candidate["outcomes"][0][comparison] = "violation"
    monkeypatch.setattr(study, "analyze", lambda _: candidate)
    with pytest.raises(ValueError, match="differs from raw native reference"):
        study.audit_summary(source, VERSION, ADAPTER_HASH)


def test_unstarted_native_failure_retains_every_offered_request_as_unknown() -> None:
    source = source_fixture()
    source.update(environment=None, loads={}, batches=[], failures=[{"stage": "source"}])
    for row in source["rows"]:
        row.update(http_status=None, output=None, error="unexecuted_after_native_failure")
    summary = study.audit_summary(source, VERSION, ADAPTER_HASH)
    assert summary["reference_counts"] == {"unknown": len(planned_rows("owned_ml"))}
    assert summary["http_failure_count"] == len(source["planned"])
    assert summary["native_failure_count"] == 1
    assert summary["audit"]["denominator"] == len(source["planned"])


@pytest.mark.parametrize("mode", ["full", "compact"])
def test_replay_retains_offered_denominator_and_inclusive_hard_deadlines(
    tmp_path: Path, mode: str
) -> None:
    source = source_fixture()
    arm = study.replay(source, tmp_path / mode, "static", mode, 65536, "early")
    assert arm["failure"] is None
    analysis = arm["analysis"]
    assert analysis["offered_items"] == analysis["processed_items"] == 19
    assert analysis["optional_offered"] == analysis["optional_processed"] == 57
    assert analysis["optional_correct"] == 54
    assert analysis["optional_native_unknown"] == 3
    assert analysis["hard_offered"] == analysis["hard_accepted"] == analysis["hard_correct"] == 6
    assert analysis["hard_unserved"] == analysis["hard_false"] == analysis["optional_false"] == 0
    arrivals = {offer["token"]: offer for offer in arm["offers"]}
    for query in (query for query in arm["queries"] if query["kind"] == "hard"):
        assert query["now"] == arrivals[query["token"]]["until"]
        assert query["now"] == arrivals[query["token"]]["now"] + 4
        assert query["verdict"] == "compliant"
    study.verify_arm(arm, source, tmp_path)


@pytest.mark.parametrize("mode", ["full", "compact"])
def test_refusal_does_not_shrink_offered_optional_or_hard_denominators(
    tmp_path: Path, mode: str
) -> None:
    source = source_fixture()
    arm = study.replay(source, tmp_path / mode, "static", mode, 2048, "late")
    analysis = arm["analysis"]
    assert analysis["optional_offered"] == 57
    assert analysis["hard_offered"] == 6
    assert analysis["hard_accepted"] < analysis["hard_offered"]
    assert analysis["hard_refused_or_unprocessed"] > 0
    assert analysis["hard_false"] == analysis["optional_false"] == analysis["hard_unserved"] == 0
    study.verify_arm(arm, source, tmp_path)


def test_replay_failure_keeps_original_offers_and_accepted_unserved_hard_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = RequestModelArchive.put

    def fail_after_first(
        archive: RequestModelArchive, frame: dict[str, Any], now: int, until: int | None
    ) -> bool:
        if now == 1:
            raise sqlite3.OperationalError("synthetic durable failure")
        return original(archive, frame, now, until)

    monkeypatch.setattr(RequestModelArchive, "put", fail_after_first)
    source = source_fixture()
    arm = study.replay(source, tmp_path / "partial", "static", "compact", 65536, "early")
    assert arm["failure"] == arm["analysis"]["failure"] == "OperationalError"
    assert arm["analysis"]["offered_items"] == 19
    assert arm["analysis"]["processed_items"] == 1
    assert arm["analysis"]["optional_offered"] == 57
    assert arm["analysis"]["optional_processed"] == 1
    assert arm["analysis"]["hard_offered"] == 6
    assert arm["analysis"]["hard_accepted"] == arm["analysis"]["hard_unserved"] == 1
    study.verify_arm(arm, source, tmp_path)


def repack_snapshot(snapshot: dict[str, Any], document: dict[str, Any]) -> None:
    raw = encode(document).encode()
    snapshot.update(
        state_zlib=base64.b64encode(zlib.compress(raw)).decode("ascii"),
        state_sha256=content_sha256(raw),
        logical_bytes=len(raw),
        peak_logical_bytes=max(snapshot["peak_logical_bytes"], len(raw)),
    )


@pytest.mark.parametrize("alteration", ["aggregate", "atom", "durable_blob", "schedule", "cap"])
def test_replay_verifier_rejects_changed_aggregate_basis_durable_state_or_design(
    tmp_path: Path, alteration: str
) -> None:
    source = source_fixture()
    arm = study.replay(source, tmp_path / "tamper", "static", "compact", 65536, "early")
    if alteration == "aggregate":
        arm["analysis"]["optional_offered"] -= 1
    elif alteration == "atom":
        snapshot = arm["offers"][0]["snapshot"]
        document = json.loads(zlib.decompress(base64.b64decode(snapshot["state_zlib"])))
        entry = next(iter(document["entries"].values()))
        key = entry["target"]
        atom = json.loads(zlib.decompress(base64.b64decode(document["atoms"][key])))
        atom["root"][0] = "forged ingress token"
        document["atoms"][key] = base64.b64encode(zlib.compress(encode(atom).encode())).decode()
        repack_snapshot(snapshot, document)
    elif alteration == "durable_blob":
        with sqlite3.connect(tmp_path / arm["directory"] / "archive.sqlite") as database:
            database.execute("UPDATE state SET payload=? WHERE id=1", (b"altered durable BLOB",))
    elif alteration == "schedule":
        arm["schedule"] = "late"
    else:
        arm["budget"] += 1  # Still fits; must reject mismatched capability, not just overflow.
    with pytest.raises(ValueError):
        study.verify_arm(arm, source, tmp_path)


def test_replay_verifier_rejects_changed_query_timestamp_even_with_same_answer(
    tmp_path: Path,
) -> None:
    source = source_fixture()
    arm = study.replay(source, tmp_path / "timestamp", "static", "compact", 65536, "early")
    hard = next(query for query in arm["queries"] if query["kind"] == "hard")
    hard["now"] += 1
    with pytest.raises(ValueError):
        study.verify_arm(arm, source, tmp_path)
