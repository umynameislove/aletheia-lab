"""Independent binary arithmetic, evidence closure and matched-service checks."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from aletheia_lab.evaluation.calibration_audit_analysis import (
    historical_membership,
    history_reference,
    summarize,
)
from aletheia_lab.evaluation.calibration_audit_archive import FullCalibrationArchive, frame
from aletheia_lab.evaluation.calibration_audit_recovery import RecoveryTier
from aletheia_lab.evaluation.calibration_audit_source import ARMS, execute
from aletheia_lab.evaluation.calibration_audit_storage import (
    CompactEvidenceArchive,
    RawEvidenceArchive,
)
from aletheia_lab.evaluation.calibration_audit_study import (
    cost_worker,
    drain_completed,
    prepare,
    read,
    recovery_control,
    sealed,
)
from aletheia_lab.evaluation.calibration_audit_verification import check_cost, configurations
from aletheia_lab.evaluation.model_load_retention import encode

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def cases():
    pytest.importorskip("sklearn.frozen")
    return {arm: execute(arm, 1931) for arm in ARMS}


@pytest.mark.parametrize("arm", ARMS)
def test_forecast_replay_and_complete_history_do_not_hide_evidence(cases, arm):
    case = cases[arm]
    assert case["forecast_supported"]
    assert history_reference(case) == case["ordinary_complete_history"]
    assert historical_membership(case) == ("violation" if arm == "overlap" else "compliant")
    assert case["native_top_level_predictions"] == 2


def test_behavioral_equality_does_not_establish_authorized_state(cases):
    for arm in ("changed_state_equal_output", "ineffective_frozen_fit"):
        row = cases[arm]
        assert row["same_entire_probability_output"]
        assert row["captured_answers"] == {
            "authorized_state": "violation",
            "disjoint_membership": "compliant",
            "sigmoid_arithmetic": "compliant",
        }
    assert cases["restore_state"]["captured_answers"]["authorized_state"] == "compliant"
    assert (
        cases["wrong_probability_report"]["captured_answers"]["sigmoid_arithmetic"] == "violation"
    )


def test_literal_fit_membership_remains_answerable_on_call_failure(cases):
    row = cases["native_failure"]
    assert set(row["captured_answers"].values()) == {"unknown"}
    assert historical_membership(row) == "compliant"


@pytest.mark.parametrize("field", ("input", "labels", "members"))
def test_reference_rejects_rebound_fit_arrays(cases, field):
    row = copy.deepcopy(cases["original"])
    row["fit_call_ledger"][0][field][0] = row["fit_call_ledger"][0][field][1]
    with pytest.raises(ValueError):
        history_reference(row)


def test_reference_detects_forged_equal_output_summary(cases):
    row = copy.deepcopy(cases["original"])
    row["same_entire_probability_output"] = not row["same_entire_probability_output"]
    with pytest.raises(ValueError, match="counterpair"):
        summarize([{"seed": 1931, "cases": [row]}], [], [])


@pytest.mark.parametrize(
    "cls", (RawEvidenceArchive, CompactEvidenceArchive, FullCalibrationArchive)
)
def test_complete_capsule_survives_leases_reopen_and_original_recovery(tmp_path, cases, cls):
    path = tmp_path / "archive.sqlite"
    owner = cls(path, "lru", 131072)
    packet = cases["original"]["captured_packet"]
    assert owner.reserve("request", now=0, until=30, bound=32768)
    assert owner.put_evidence(packet, now=1)
    assert owner.demand("later", ["request"], now=2, until=20)
    assert owner.evidence("request") == packet
    assert owner.query(["request"], now=3)["request"] == "compliant"
    owner.drain("later", now=4)
    owner.close()
    reopened = cls(path, "lru", 131072, reopen=True)
    assert reopened.evidence("request") == packet
    assert reopened.restore_evidence([packet], now=5)
    revision = copy.deepcopy(packet)
    revision["call"]["output"][0].reverse()
    before = copy.deepcopy(reopened.state)
    with pytest.raises(ValueError, match="revision"):
        reopened.restore_evidence([revision], now=6)
    assert reopened.state == before == reopened._read()
    reopened.close()


@pytest.mark.parametrize(
    "cls", (RawEvidenceArchive, CompactEvidenceArchive, FullCalibrationArchive)
)
def test_old_frame_only_put_cannot_silently_omit_query_capsule(tmp_path, cases, cls):
    owner = cls(tmp_path / "archive.sqlite", "lru", 131072)
    before = copy.deepcopy(owner.state)
    with pytest.raises(ValueError, match="dependency"):
        owner.put(frame(cases["original"]["captured_packet"]), now=0)
    assert owner.state == before == owner._read()
    owner.close()


@pytest.mark.parametrize(
    "cls", (RawEvidenceArchive, CompactEvidenceArchive, FullCalibrationArchive)
)
def test_envelope_overrun_is_unserved_not_retroactive_refusal(tmp_path, cases, cls):
    owner = cls(tmp_path / "archive.sqlite", "lru", 131072)
    assert owner.reserve("request", now=0, until=30, bound=512)
    assert not owner.put_evidence(cases["original"]["captured_packet"], now=1)
    assert owner.state["accepted"] == 1 and owner.state["refused"] == 0
    assert owner.state["overruns"] == 1 and owner.evidence("request") is None
    owner.close()


@pytest.mark.parametrize("mode", ("raw", "compact", "whole"))
def test_actual_cost_worker_and_resealed_service_tamper(tmp_path, mode):
    pytest.importorskip("sklearn.frozen")
    config = {
        "mode": mode,
        "count": 3,
        "repeat": 0,
        "deadline_ms": 30000,
        "seed": 1531,
        "quota": 131072,
        "bound": 32768,
    }
    row = cost_worker(config, tmp_path / "worker")
    check_cost(row)
    assert row["service"]["complete"] == 3 and row["reopened_scopes"] == 3
    forged = copy.deepcopy(row)
    forged["service"]["complete"] = 2
    with pytest.raises(ValueError, match="census"):
        check_cost(forged)
    forged = copy.deepcopy(row)
    forged["audits"][0]["finished_ms"] = 40000
    with pytest.raises(ValueError, match="service result"):
        check_cost(forged)


@pytest.mark.parametrize("mode", ("raw", "compact", "whole"))
def test_real_original_token_recovery_and_tier_cost(tmp_path, mode):
    pytest.importorskip("sklearn.frozen")
    result = recovery_control(tmp_path / "recovery", mode, 1531)
    assert result["status"] == "pass"
    assert result["tier"]["payload_bytes"] > 0 and result["tier"]["closed_bytes"] > 0


def test_recovery_tier_rejects_rebound_packet_and_symlink(tmp_path, cases):
    path = tmp_path / "tier.sqlite"
    tier = RecoveryTier(path)
    packet = cases["original"]["captured_packet"]
    tier.put(packet)
    tier.db.execute("UPDATE packets SET identity=?", ("0" * 64,))
    tier.db.commit()
    with pytest.raises(ValueError, match="identity"):
        tier.fetch("request")
    tier.close()


def test_prospective_plan_binds_core_dependencies_and_exact_census(tmp_path):
    pytest.importorskip("sklearn.frozen")
    directory = tmp_path / "study"
    prepare(ROOT, directory)
    plan = read(directory / "plan.json")
    assert "src/aletheia_lab/evaluation/incident_audit_archive.py" in plan["code_sha256"]
    assert len(configurations(plan, "evaluation")) == 36
    assert plan["forecasts"] == {arm: list(execute(arm, 1931)["forecast"].values()) for arm in ARMS}
    with pytest.raises(ValueError, match="fresh"):
        prepare(ROOT, directory)
    payload = json.loads((directory / "plan.json").read_bytes())
    payload["growth_bound"] = 1
    (directory / "plan.json").write_text(encode(payload))
    with pytest.raises(ValueError, match="identity"):
        read(directory / "plan.json")
    assert sealed({"x": 1})["sha256"] != sealed({"x": 2})["sha256"]


@pytest.mark.parametrize(
    "cls", (RawEvidenceArchive, CompactEvidenceArchive, FullCalibrationArchive)
)
@pytest.mark.parametrize("finished", (9, 10, 11))
def test_drain_uses_transition_deadline_not_stale_lease_presence(tmp_path, cases, cls, finished):
    owner = cls(tmp_path / "archive.sqlite", "lru", 131072)
    assert owner.reserve("request", now=0, until=10, bound=32768)
    assert owner.put_evidence(cases["original"]["captured_packet"], now=1)
    assert "pre:request" in owner.state["leases"]
    assert drain_completed(owner, "request", now=finished) is (finished <= 10)
    owner.query(["request"], now=finished)
    assert "pre:request" not in owner.state["leases"]
    assert owner.evidence("request") == cases["original"]["captured_packet"]
    assert not drain_completed(owner, "request", now=finished)
    owner.close()


@pytest.mark.parametrize("cls", (RawEvidenceArchive, CompactEvidenceArchive))
def test_storage_sql_accepts_only_literal_internal_tables(tmp_path, monkeypatch, cls):
    owner = cls(tmp_path / "archive.sqlite", "lru", 131072)
    before = owner._read()
    monkeypatch.setattr(owner, "table", "fragments; DROP TABLE fragments")
    with pytest.raises(ValueError, match="unsupported archive table"):
        owner._durable_rows()
    monkeypatch.undo()
    assert owner._read() == before
    owner.close()
