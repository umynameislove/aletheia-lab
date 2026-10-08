"""Resealed census tampering and read-only replay of actual durable codecs."""

from __future__ import annotations

import copy

import pytest

from aletheia_lab.evaluation.calibration_audit_archive import FullCalibrationArchive
from aletheia_lab.evaluation.calibration_audit_source import QUERIES
from aletheia_lab.evaluation.calibration_audit_storage import (
    CompactEvidenceArchive,
    RawEvidenceArchive,
)
from aletheia_lab.evaluation.calibration_audit_study import sealed
from aletheia_lab.evaluation.calibration_audit_verification import (
    _check_controls,
    check_census,
    check_cost,
    configurations,
)
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.request_model_audit import digest
from aletheia_lab.project.identity import content_sha256

CLASSES = {
    "raw": RawEvidenceArchive,
    "compact": CompactEvidenceArchive,
    "whole": FullCalibrationArchive,
}


def _fixture_packet(token):
    state = {
        "class": "LogisticRegression",
        "params": {},
        "coef": [[1.0, 0.0]],
        "intercept": [0.0],
        "classes": [0, 1],
        "features": 2,
    }
    sigmoid = {"a": 1.0, "b": 0.0}
    ledger = [
        {
            "api": "base.fit",
            "members": [0],
            "input": [[1.0, 0.0]],
            "labels": [0],
            "returned_state": state,
        },
        {
            "api": "calibrator.fit",
            "members": [1],
            "input": [[2.0, 0.0]],
            "labels": [1],
            "returned_sigmoid": sigmoid,
        },
    ]
    packet = {
        "schema": "calibration-native-evidence/v1",
        "token": token,
        "approved_state": copy.deepcopy(state),
        "membership": {
            "base": [{"id": 0, "sha256": digest([[1.0, 0.0], 0])}],
            "calibration": [{"id": 1, "sha256": digest([[2.0, 0.0], 1])}],
        },
        "call": {
            "token": token,
            "input": [[0.0, 0.0]],
            "actual_state": state,
            "sigmoid": sigmoid,
            "output": [[0.5, 0.5]],
            "closed": True,
            "failed": False,
            "state_stable": True,
            "held_base_is_enrolled": True,
        },
    }
    return packet, ledger


def _cost_fixture(directory, config):
    """Known arrays exercise persistence; this fixture does not fit a native model."""
    directory.mkdir()
    owner = CLASSES[config["mode"]](directory / "archive.sqlite", "lru", config["quota"])
    packets, offers, audits = [], [], []
    try:
        for index in range(config["count"]):
            token = f"call-{index:03d}"
            packet, ledger = _fixture_packet(token)
            accepted = owner.reserve(
                token, now=index, until=index + config["deadline_ms"], bound=config["bound"]
            )
            retained = owner.put_evidence(packet, now=index)
            packets.append(packet)
            offers.append(
                {
                    "token": token,
                    "offered_ms": index,
                    "until_ms": index + config["deadline_ms"],
                    "accepted": accepted,
                    "retained_ack": retained,
                    "ack_ms": index,
                }
            )
        for index, offer in enumerate(offers):
            finished = config["count"] + index
            token = offer["token"]
            owner.query([token], now=finished)
            correct = owner.evidence(token) is not None
            audits.append(
                {
                    "token": token,
                    "finished_ms": finished,
                    "late": False,
                    "correct": correct,
                    "complete": offer["accepted"] and correct,
                    "answer": dict.fromkeys(QUERIES, "compliant" if correct else "unknown"),
                    "witness": owner.witness([token]),
                }
            )
            if f"pre:{token}" in owner.state["leases"]:
                owner.drain(f"pre:{token}", now=finished)
        metrics = owner.snapshot()
        witness = owner.witness([packet["token"] for packet in packets])
        retained_count = len(owner.state["entries"])
    finally:
        owner.close()
    accepted = sum(offer["accepted"] for offer in offers)
    complete = sum(audit["complete"] for audit in audits)
    return {
        **config,
        "status": "complete",
        "native_predictions": config["count"],
        "provider_calls": 0,
        "workload_ns": 1,
        "timers": {"fixture_ns": 0},
        "fit_call_ledger": ledger,
        "native_packets": packets,
        "offers": offers,
        "audits": audits,
        "service": {
            "accepted": accepted,
            "refused": config["count"] - accepted,
            "complete": complete,
            "unknown": config["count"] - complete,
            "late": 0,
            "wrong": 0,
            "accepted_but_unserved": accepted - complete,
        },
        "archive_metrics": metrics,
        "final_witness": witness,
        "reopened_scopes": retained_count,
        "closed_storage_bytes": (directory / "archive.sqlite").stat().st_size,
    }


def _config(mode="whole", count=2, bound=32768):
    return {
        "mode": mode,
        "count": count,
        "repeat": 0,
        "deadline_ms": 30000,
        "seed": 101,
        "quota": 131072,
        "bound": bound,
    }


def _reseal_witness(witness):
    witness["state_sha256"] = content_sha256(encode(witness["state"]).encode())


@pytest.mark.parametrize("field", ("accepted", "refused"))
def test_final_counter_must_match_reserve_offers_even_when_resealed(tmp_path, field):
    row = _cost_fixture(tmp_path / "store", _config())
    check_cost(row)
    state = row["final_witness"]["state"]
    state[field] = f"{int(state[field], 16) + 1:08x}"
    row["archive_metrics"][field] += 1
    _reseal_witness(row["final_witness"])
    with pytest.raises(ValueError, match="final reserve offer census"):
        check_cost(row)


def test_retained_packet_requires_completion_acknowledgement(tmp_path):
    row = _cost_fixture(tmp_path / "store", _config())
    row["offers"][0]["retained_ack"] = False
    with pytest.raises(ValueError, match="completion acknowledgement"):
        check_cost(row)


def test_accepted_overrun_remains_unserved_and_counter_is_reconstructed(tmp_path):
    row = _cost_fixture(tmp_path / "store", _config(bound=512))
    check_cost(row)
    assert row["service"]["accepted"] == row["service"]["accepted_but_unserved"] == 2
    assert row["service"]["refused"] == row["reopened_scopes"] == 0
    row["final_witness"]["state"]["overruns"] = "00000000"
    row["archive_metrics"]["overruns"] = 0
    _reseal_witness(row["final_witness"])
    with pytest.raises(ValueError, match="overrun acknowledgement census"):
        check_cost(row)


def _write(path, payload):
    path.write_text(encode(sealed(payload)), encoding="utf-8")


def _development_fixture(directory):
    plan = {
        "development_seeds": [],
        "evaluation_seeds": [],
        "cost_seed": 101,
        "logical_quota": 131072,
        "growth_bound": 32768,
    }
    costs = []
    for index, config in enumerate(configurations(plan, "development")):
        row = _cost_fixture(directory / f"cost-store-{index}", config)
        _write(directory / f"config-{index}.json", config)
        _write(directory / f"cost-{index}.json", row)
        costs.append(row)
    return plan, {"phase": "development", "transfers": [], "costs": costs, "controls": []}


def test_census_reads_all_closed_codecs_without_modifying_any_files(tmp_path):
    plan, report = _development_fixture(tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    check_census(plan, report, tmp_path)
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


def test_census_rejects_resealed_closed_storage_size(tmp_path):
    plan, report = _development_fixture(tmp_path)
    report["costs"][0]["closed_storage_bytes"] += 1
    _write(tmp_path / "cost-0.json", report["costs"][0])
    with pytest.raises(ValueError, match="closed storage allocation"):
        check_census(plan, report, tmp_path)


@pytest.mark.parametrize("index", range(3))
def test_census_rejects_valid_durable_state_different_from_final_witness(tmp_path, index):
    plan, report = _development_fixture(tmp_path)
    row = report["costs"][index]
    path = tmp_path / f"cost-store-{index}" / "archive.sqlite"
    owner = CLASSES[row["mode"]](path, "lru", row["quota"], reopen=True)
    owner.query(["call-000"], now=20)
    owner.close()
    row["closed_storage_bytes"] = path.stat().st_size
    _write(tmp_path / f"cost-{index}.json", row)
    with pytest.raises(ValueError, match="closed durable state differs"):
        check_census(plan, report, tmp_path)


def test_census_preserves_planned_terminal_failure_and_rejects_duplicate_config(tmp_path):
    plan, report = _development_fixture(tmp_path)
    config = {key: report["costs"][0][key] for key in _config()}
    report["costs"][0] = {**config, "status": "worker_failure", "error_type": "child_failure"}
    check_census(plan, report, tmp_path)
    report["costs"][1] = copy.deepcopy(report["costs"][0])
    with pytest.raises(ValueError, match="planned cost configuration census"):
        check_census(plan, report, tmp_path)


def _control_report():
    return {
        "phase": "evaluation",
        "controls": [
            {
                "mode": mode,
                "control": name,
                "status": "worker_failure",
                "error_type": "child_failure",
            }
            for mode in CLASSES
            for name in ("original_token_eviction_recovery", "before_commit", "after_ack")
        ],
    }


def _witness_fixture(tmp_path, token, *, retain):
    owner = FullCalibrationArchive(tmp_path / f"{token}-{retain}.sqlite", "lru", 131072)
    packet, _ = _fixture_packet(token)
    if retain:
        owner.put_evidence(packet, now=0)
    witness = owner.witness([token])
    owner.close()
    return witness, packet


def test_control_census_preserves_failed_workers_and_requires_terminal_reason(tmp_path):
    report = _control_report()
    _check_controls(report, tmp_path)
    report["controls"][0].pop("error_type")
    with pytest.raises(ValueError, match="control failure reason"):
        _check_controls(report, tmp_path)
    report = _control_report()
    report["controls"].pop()
    with pytest.raises(ValueError, match="control census"):
        _check_controls(report, tmp_path)


def test_control_rejects_false_original_recovery_packet(tmp_path):
    report = _control_report()
    before, packet = _witness_fixture(tmp_path, "recover-0", retain=False)
    after, _ = _witness_fixture(tmp_path, "recover-0", retain=True)
    control = report["controls"][0]
    control.update(
        status="pass",
        before_recovery=before,
        after_recovery=after,
        original_packet=packet,
        fetched_packet=copy.deepcopy(packet),
        evicted_original=True,
        restored=True,
        exact_original=True,
        unavailable_fetch_returns_none=True,
    )
    _check_controls(report, tmp_path)
    control["fetched_packet"]["call"]["output"][0] = [0.4, 0.6]
    with pytest.raises(ValueError, match="authentic original recovery"):
        _check_controls(report, tmp_path)


@pytest.mark.parametrize("boundary", ("before_commit", "after_ack"))
def test_exit_controls_check_ack_and_durable_frontier(tmp_path, boundary):
    report = _control_report()
    retained = boundary == "after_ack"
    witness, packet = _witness_fixture(tmp_path, "crash-call", retain=retained)
    owned = tmp_path / f"exit-raw-{boundary}"
    owned.mkdir()
    if retained:
        (owned / "ack.json").write_text("{}", encoding="utf-8")
    control = next(
        row for row in report["controls"] if row["mode"] == "raw" and row["control"] == boundary
    )
    control.update(
        status="pass",
        ack_observed=retained,
        reopened_witness=witness,
        expected_packet=packet,
        returncode=19,
    )
    _check_controls(report, tmp_path)
    control["ack_observed"] = not retained
    with pytest.raises(ValueError, match="exit acknowledgement"):
        _check_controls(report, tmp_path)
    control["ack_observed"] = retained
    control["returncode"] = 0
    with pytest.raises(ValueError, match="control result"):
        _check_controls(report, tmp_path)
