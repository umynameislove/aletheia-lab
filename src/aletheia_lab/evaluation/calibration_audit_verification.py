"""Rebuild planned census, service predicates and retained evidence read-only."""

from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, cast

from aletheia_lab.evaluation.calibration_audit_analysis import history_reference
from aletheia_lab.evaluation.calibration_audit_archive import (
    CalibrationEvidenceMixin,
    FullCalibrationArchive,
)
from aletheia_lab.evaluation.calibration_audit_source import QUERIES, assess
from aletheia_lab.evaluation.calibration_audit_storage import (
    CompactEvidenceArchive,
    RawEvidenceArchive,
)
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.project.identity import content_sha256

SERVICE_FIELDS = (
    "accepted",
    "refused",
    "complete",
    "unknown",
    "late",
    "wrong",
    "accepted_but_unserved",
)


def _witness_state(witness: dict[str, Any], quota: int) -> dict[str, Any]:
    if witness["state_sha256"] != content_sha256(encode(witness["state"]).encode()):
        raise ValueError("audit frontier identity differs")
    state = FullCalibrationArchive._parse(witness["state"])
    inspector = object.__new__(CalibrationEvidenceMixin)
    inspector.budget = quota
    inspector._validate(state)
    return state


def witnessed_packet(witness: dict[str, Any], token: str, quota: int) -> dict[str, Any] | None:
    state = _witness_state(witness, quota)
    inspector = object.__new__(CalibrationEvidenceMixin)
    return inspector._evidence(state, token)


def configurations(plan: dict[str, Any], phase: str) -> list[dict[str, Any]]:
    if phase == "evaluation":
        rows = [
            {
                "mode": mode,
                "count": count,
                "repeat": repeat,
                "deadline_ms": plan["core_deadline_ms"],
            }
            for mode in plan["modes"]
            for count in plan["counts"]
            for repeat in range(plan["repeats"])
        ]
        rows += [
            {"mode": mode, "count": 24, "repeat": 0, "deadline_ms": deadline}
            for mode in ("raw", "compact", "whole")
            for deadline in plan["deadline_sensitivity_ms"]
        ]
    else:
        rows = [
            {"mode": mode, "count": 8, "repeat": 0, "deadline_ms": 30000}
            for mode in ("raw", "compact", "whole")
        ]
    return [
        {
            **row,
            "seed": plan["cost_seed"],
            "quota": plan["logical_quota"],
            "bound": plan["growth_bound"],
        }
        for row in rows
    ]


def _config_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(
        row[key] for key in ("mode", "count", "repeat", "deadline_ms", "seed", "quota", "bound")
    )


def _worker_failed(row: dict[str, Any], message: str) -> bool:
    if row["status"] == "worker_failure":
        if not row.get("error_type"):
            raise ValueError(message)
        return True
    return False


def _check_native_census(row: dict[str, Any]) -> None:
    if (
        row["status"] != "complete"
        or row["native_predictions"] != row["count"]
        or row["provider_calls"] != 0
    ):
        raise ValueError("native prediction census differs")


def _check_application_floor(row: dict[str, Any]) -> None:
    expected = dict.fromkeys(SERVICE_FIELDS, 0)
    expected.update(refused=row["count"], unknown=row["count"])
    if row["service"] != expected or row["native_packets"] or row["offers"] or row["audits"]:
        raise ValueError("insufficient-evidence application floor differs")


def _check_scope_census(row: dict[str, Any]) -> None:
    tokens = [f"call-{index:03d}" for index in range(row["count"])]
    for key in ("native_packets", "offers", "audits"):
        if [item["token"] for item in row[key]] != tokens:
            raise ValueError("cost scope census differs")


def _check_offer_timing(
    row: dict[str, Any], offer: dict[str, Any], audit: dict[str, Any], previous: int
) -> int:
    if (
        offer["until_ms"] != offer["offered_ms"] + row["deadline_ms"]
        or not previous <= offer["offered_ms"] <= offer["ack_ms"] <= audit["finished_ms"]
    ):
        raise ValueError("monotonic service envelope differs")
    if type(offer["accepted"]) is not bool or type(offer["retained_ack"]) is not bool:
        raise ValueError("offer acknowledgement flags differ")
    return cast(int, offer["offered_ms"])


def _check_audit_evidence(
    row: dict[str, Any], packet: dict[str, Any], offer: dict[str, Any], audit: dict[str, Any]
) -> dict[str, str]:
    reference = history_reference(
        {"fit_call_ledger": row["fit_call_ledger"], "native_packets": [packet]}
    )
    if reference != dict.fromkeys(QUERIES, "compliant"):
        raise ValueError("cost workload scientific reference differs")
    stored = witnessed_packet(audit["witness"], packet["token"], row["quota"])
    if stored is not None and stored != packet:
        raise ValueError("audit frontier packet differs")
    if stored is not None and not offer["retained_ack"]:
        raise ValueError("retained evidence lacks completion acknowledgement")
    expected = assess(stored) if stored else dict.fromkeys(QUERIES, "unknown")
    answers = audit["answer"]
    if answers != expected:
        raise ValueError("query answer differs from actual retained frontier")
    if set(answers) != set(QUERIES) or any(
        value not in {"compliant", "unknown"} for value in answers.values()
    ):
        raise ValueError("cost answer outside native reference")
    return reference


def _audit_counts(
    offer: dict[str, Any], audit: dict[str, Any], reference: dict[str, str]
) -> dict[str, int]:
    correct = audit["answer"] == reference
    late = audit["finished_ms"] > offer["until_ms"]
    complete = bool(offer["accepted"] and correct and not late)
    if (audit["correct"], audit["late"], audit["complete"]) != (correct, late, complete):
        raise ValueError("service result differs from offer/answer/deadline")
    return {
        "accepted": int(offer["accepted"]),
        "refused": int(not offer["accepted"]),
        "complete": int(complete),
        "unknown": int("unknown" in audit["answer"].values()),
        "late": int(late),
        "wrong": 0,
        "accepted_but_unserved": int(offer["accepted"] and not complete),
    }


def _check_final_frontier(row: dict[str, Any], census: dict[str, int]) -> None:
    witness = row["final_witness"]
    state = _witness_state(witness, row["quota"])
    if row["reopened_scopes"] != len(state["entries"]):
        raise ValueError("reopened scope census differs")
    for field in ("accepted", "refused"):
        if state[field] != census[field]:
            raise ValueError("final reserve offer census differs")
    overruns = sum(offer["accepted"] and not offer["retained_ack"] for offer in row["offers"])
    if state["overruns"] != overruns:
        raise ValueError("final overrun acknowledgement census differs")
    for field in ("accepted", "refused", "overruns"):
        if row["archive_metrics"][field] != state[field]:
            raise ValueError("archive metric counters differ from final frontier")
    for packet, offer in zip(row["native_packets"], row["offers"], strict=True):
        stored = witnessed_packet(witness, packet["token"], row["quota"])
        if stored is not None and stored != packet:
            raise ValueError("retained packet differs from native capture")
        if stored is not None and not offer["retained_ack"]:
            raise ValueError("retained evidence lacks completion acknowledgement")


def _check_retained_service(row: dict[str, Any]) -> None:
    _check_scope_census(row)
    census = dict.fromkeys(SERVICE_FIELDS, 0)
    previous = 0
    for packet, offer, audit in zip(
        row["native_packets"], row["offers"], row["audits"], strict=True
    ):
        previous = _check_offer_timing(row, offer, audit, previous)
        reference = _check_audit_evidence(row, packet, offer, audit)
        for field, value in _audit_counts(offer, audit, reference).items():
            census[field] += value
    if row["service"] != census:
        raise ValueError("service census differs")
    _check_final_frontier(row, census)


def check_cost(row: dict[str, Any]) -> None:
    if _worker_failed(row, "terminal failure reason missing"):
        return
    _check_native_census(row)
    if row["mode"] in {"native", "hash"}:
        _check_application_floor(row)
    else:
        _check_retained_service(row)
    if row["workload_ns"] < sum(row["timers"].values()) or min(row["timers"].values()) < 0:
        raise ValueError("overlapping/negative stage timers")


def _check_transfer_case(case: dict[str, Any], seed: int, plan: dict[str, Any]) -> None:
    if case["seed"] != seed or list(case["forecast"].values()) != plan["forecasts"][case["arm"]]:
        raise ValueError("prospective prediction changed")
    if case["captured_answers"] != assess(case["captured_packet"]) or case[
        "forecast_supported"
    ] != (case["captured_answers"] == case["forecast"]):
        raise ValueError("capture assessment differs")


def _check_transfers(plan: dict[str, Any], report: dict[str, Any], directory: Path) -> None:
    from aletheia_lab.evaluation.calibration_audit_study import read

    phase = report["phase"]
    seeds = plan["development_seeds"] if phase == "development" else plan["evaluation_seeds"]
    if [row["seed"] for row in report["transfers"]] != seeds:
        raise ValueError("transfer seed census differs")
    for row in report["transfers"]:
        if row["status"] == "worker_failure":
            continue
        if [case["arm"] for case in row["cases"]] != plan["arms"]:
            raise ValueError("transfer arm census differs")
        for case in row["cases"]:
            _check_transfer_case(case, row["seed"], plan)
        if row != read(directory / f"transfer-{row['seed']}.json"):
            raise ValueError("transfer report differs from owned worker output")


def _closed_storage_bytes(path: Path) -> int:
    size = 0
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.is_symlink():
            raise ValueError("owned study database must not be a symlink")
        if candidate.exists():
            size += candidate.stat().st_size
    return size


def _durable_state(path: Path, mode: str, quota: int) -> dict[str, Any]:
    """Parse the closed database without constructor writes or a WAL checkpoint."""
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
        raise ValueError("owned closed archive database required")
    cls = {
        "raw": RawEvidenceArchive,
        "compact": CompactEvidenceArchive,
        "whole": FullCalibrationArchive,
    }[mode]
    inspector: Any = object.__new__(cls)
    inspector.path, inspector.budget = path, quota
    inspector.metrics = {"read_ns": 0}
    inspector._ack_rows = inspector._ack_state = None
    inspector.db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        state = inspector._read()
        inspector._validate(state)
        return cast(dict[str, Any], state)
    finally:
        inspector.db.close()


def _check_closed_archive(row: dict[str, Any], directory: Path) -> None:
    path = directory / "archive.sqlite"
    if row["closed_storage_bytes"] != _closed_storage_bytes(path):
        raise ValueError("closed storage allocation differs")
    if row["mode"] in {"native", "hash"}:
        return
    if _durable_state(path, row["mode"], row["quota"]) != _witness_state(
        row["final_witness"], row["quota"]
    ):
        raise ValueError("closed durable state differs from final frontier")


def _check_costs(plan: dict[str, Any], report: dict[str, Any], directory: Path) -> None:
    from aletheia_lab.evaluation.calibration_audit_study import read

    if Counter(map(_config_key, report["costs"])) != Counter(
        map(_config_key, configurations(plan, report["phase"]))
    ):
        raise ValueError("planned cost configuration census differs")
    for index, row in enumerate(report["costs"]):
        if _config_key(row) != _config_key(read(directory / f"config-{index}.json")):
            raise ValueError("worker input binding differs")
        check_cost(row)
        if row["status"] == "complete" and row != read(directory / f"cost-{index}.json"):
            raise ValueError("cost report differs from owned worker output")
        if row["status"] == "complete":
            _check_closed_archive(row, directory / f"cost-store-{index}")


def _check_original_recovery(row: dict[str, Any]) -> bool:
    before, after = row["before_recovery"], row["after_recovery"]
    prior = witnessed_packet(before, "recover-0", 24000)
    recovered = witnessed_packet(after, "recover-0", 24000)
    if (
        prior is not None
        or recovered != row["original_packet"]
        or recovered != row["fetched_packet"]
    ):
        raise ValueError("authentic original recovery differs")
    if "recover-0" in before["state"]["entries"] or "recover-0" not in after["state"]["entries"]:
        raise ValueError("original recovery transition differs")
    return bool(
        row["evicted_original"]
        and row["restored"]
        and row["exact_original"]
        and row["unavailable_fetch_returns_none"]
    )


def _check_process_exit(row: dict[str, Any], directory: Path) -> bool:
    owned = directory / f"exit-{row['mode']}-{row['control']}"
    ack = (owned / "ack.json").exists()
    if ack != row["ack_observed"]:
        raise ValueError("process exit acknowledgement differs")
    observed = witnessed_packet(row["reopened_witness"], "crash-call", 131072)
    if observed != (row["expected_packet"] if row["control"] == "after_ack" else None):
        raise ValueError("process exit durable frontier differs")
    return bool(row["returncode"] == 19 and ack == (row["control"] == "after_ack"))


def _check_controls(report: dict[str, Any], directory: Path) -> None:
    expected_controls = (
        Counter(
            (mode, name)
            for mode in ("raw", "compact", "whole")
            for name in ("original_token_eviction_recovery", "before_commit", "after_ack")
        )
        if report["phase"] == "evaluation"
        else Counter()
    )
    if Counter((row["mode"], row["control"]) for row in report["controls"]) != expected_controls:
        raise ValueError("control census differs")
    for row in report["controls"]:
        if _worker_failed(row, "control failure reason missing"):
            continue
        if row["control"] == "original_token_eviction_recovery":
            expected = _check_original_recovery(row)
        else:
            expected = _check_process_exit(row, directory)
        if row["status"] != ("pass" if expected else "fail"):
            raise ValueError("control result differs")


def check_census(plan: dict[str, Any], report: dict[str, Any], directory: Path) -> None:
    _check_transfers(plan, report, directory)
    _check_costs(plan, report, directory)
    _check_controls(report, directory)
