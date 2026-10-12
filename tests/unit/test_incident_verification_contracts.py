"""Synthetic immutable receipt replay; never native execution or study admission."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from aletheia_lab.evaluation import incident_audit_analysis as analysis
from aletheia_lab.evaluation import incident_audit_verification as verification
from aletheia_lab.evaluation.cache_lifecycle_study import seal
from aletheia_lab.evaluation.incident_audit_archive import IncidentAuditArchive
from aletheia_lab.evaluation.incident_audit_service import RecoveryTier, offer, summarize
from aletheia_lab.evaluation.incident_audit_source import GRAPH_SHA256, source_summary
from aletheia_lab.evaluation.incident_audit_study import FILES, aggregate
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.request_model_audit import digest
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

ROOT = Path(verification.__file__).resolve().parents[3]
NAME = "65536-static-none"
NORMAL_A = [[1.0, 4.0], [9.0, 16.0], [25.0, 36.0]]
NORMAL_B = [[2.0, 2.0], [12.0, 12.0], [30.0, 30.0]]
ZERO = [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]]


def receipts(arm="observed"):
    """Fixed expected native arithmetic; outputs/verdicts do not call tested resolvers."""
    rows = []
    for ordinal in range(16):
        selected = "aaa" if ordinal % 2 == 0 else "bbb"
        requested = "aaa" if ordinal in {7, 15} else selected
        if arm == "route_repair":
            selected = requested
        failed = ordinal == 11
        values = deepcopy(ZERO if ordinal in {3, 15} else [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
        if failed:
            values = [[1.0, 1.0], [1.0, 1.0]]
        output = (
            None
            if failed
            else deepcopy(
                ZERO if ordinal in {3, 15} else NORMAL_A if selected == "aaa" else NORMAL_B
            )
        )
        truth = "unknown" if failed else "violation" if requested != selected else "compliant"
        token, generation = f"request-{ordinal:03d}", f"session-{selected}"
        weights = (
            [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]
            if selected == "aaa"
            else [[2.0, 1.0], [4.0, 3.0], [6.0, 5.0]]
        )
        fingerprint = digest(
            {
                "graph": GRAPH_SHA256,
                "W": weights,
                "dtype": "float32",
                "shape": [3, 2],
                "provider": "CPUExecutionProvider",
            }
        )
        captured = not failed and (arm == "complete_capture" or ordinal != 9)
        frame = {
            "token": token,
            "requested": requested,
            "kind": "non_batched",
            "input": digest(values),
            "output": digest(output) if output is not None else None,
            "closed": True,
            "failed": failed,
            "loads": {},
            "uses": [],
        }
        if captured:
            frame["loads"] = {
                generation: {
                    "model": selected,
                    "artifact": GRAPH_SHA256,
                    "fingerprint": fingerprint,
                }
            }
            frame["uses"] = [
                {
                    "token": token,
                    "batch": token,
                    "index": 0,
                    "generation": generation,
                    "input": frame["input"],
                    "output": frame["output"],
                    "fingerprint": fingerprint,
                }
            ]
        error = "SyntheticNativeShapeError" if failed else None
        rows.append(
            {
                "ordinal": ordinal,
                "input": values,
                "output": output,
                "actual_session": selected,
                "native_error": error,
                "frame": frame,
                "reference": truth,
                "strong_source_history": truth,
                "capture_answer": truth if captured else "unknown",
                "caller_receipt_sha256": digest({"token": token, "output": output, "error": error}),
                "native_ns": 10 + ordinal,
                "capture_and_live_resolve_ns": 2 + ordinal,
            }
        )
    return rows


def rewrite_sealed(path, document):
    document = {key: value for key, value in document.items() if key != "sha256"}
    path.write_text(encode(seal(document)))


def build_process(directory, rows, identity):
    directory.mkdir()
    secondary = RecoveryTier(directory / "secondary.sqlite", 65536)
    for row in rows:
        secondary.put(row["frame"])
    schedule = [
        {
            "id": "late-union",
            "kind": "incident",
            "scopes": ["request-007", "request-009", "request-011", "request-015"],
            "offered_at": 1,
            "deadline": 3,
        }
    ]
    archives = {}
    for name in (NAME, "full-reference"):
        archive = IncidentAuditArchive(directory / f"{name}.sqlite", "static", 65536)
        for ordinal in (0, 8):
            assert archive.reserve(f"request-{ordinal:03d}", now=0, until=3, bound=4096)
        for row in rows:
            assert archive.put(row["frame"], now=0)
        known = []
        for ordinal in (0, 8):
            scope = f"request-{ordinal:03d}"
            answers = archive.query([scope], now=0)
            known.append(
                {
                    "id": f"pre:{scope}",
                    "kind": "preknown",
                    "scopes": [scope],
                    "accepted": True,
                    "answers": answers,
                    "witness": archive.witness([scope]),
                    "queried_at": 0,
                    "deadline": 3,
                    "query_wall_ns": 10,
                    "response_budget_ns": 10**9,
                    "refetched": [],
                    "refetch_failed": [],
                }
            )
            archive.drain(f"pre:{scope}", now=0)
        late = [offer(archive, "none", secondary, item, 10**9) for item in schedule]
        storage = archive.snapshot()
        archive.close()
        path = directory / f"{name}.sqlite"
        archives[name] = {
            "prospective_admissions": known,
            "offers": late,
            "summary": summarize(known + late, rows),
            "late_summary": summarize(late, rows),
            "preknown_summary": summarize(known, rows),
            "storage": storage,
            "closed_db_bytes": path.stat().st_size,
            "db_sha256": content_sha256(path.read_bytes()),
        }
    return {
        "status": "development_executed",
        "source_arm": "observed",
        "source_identity": identity,
        "rows": rows,
        "source_summary": source_summary(rows),
        "schedule": schedule,
        "archives": archives,
        "secondary": secondary.finish(),
        "source_load_ns": {"aaa": 20, "bbb": 30},
        "memory": {"peak_process_rss_bytes": 1024},
    }


@pytest.fixture
def replay(tmp_path):
    identity = {"synthetic_owned_source": True}
    config = {
        "source_arms": ["observed", "unavailable"],
        "process_replicates": 1,
        "capture_omission_ordinal": 9,
        "native_calls_per_process": 16,
        "preknown_obligation_every_calls": 8,
        "logical_capacity_bytes": [65536],
        "policies": ["static"],
        "recovery_tiers": ["none"],
        "response_budget_sensitivity_ns": [0, 10**9],
    }
    code = {}
    for name in FILES:
        raw = (ROOT / name).read_bytes()
        code[name] = content_sha256(raw)
        write_new_file(tmp_path / "code-snapshot" / name, raw)
    plan = seal({"protocol": config, "source_identity": identity, "code": code})
    report = build_process(tmp_path / "observed-0", receipts(), identity)
    stdout, stderr = b"synthetic partial output", b"synthetic launch failure"
    write_new_file(tmp_path / "unavailable-0.stdout", stdout)
    write_new_file(tmp_path / "unavailable-0.stderr", stderr)
    failed = {
        "status": "worker_failed",
        "returncode": 1,
        "stdout_sha256": content_sha256(stdout),
        "stderr_sha256": content_sha256(stderr),
    }
    floor = {
        "status": "native_control_executed",
        "source_identity": identity,
        "rows": [
            {key: row[key] for key in ("ordinal", "output", "native_error", "native_ns")}
            for row in receipts()
        ],
    }
    reports = [seal(report), seal(failed), seal(floor)]
    write_new_file(tmp_path / "plan.json", encode(plan).encode())
    for arm, document in zip(("observed", "unavailable", "native_floor"), reports, strict=True):
        write_new_file(tmp_path / f"{arm}-0.json", encode(document).encode())
    result = seal(
        {"plan_sha256": plan["sha256"], "processes": reports, "summary": aggregate(reports)}
    )
    write_new_file(tmp_path / "results.json", encode(result).encode())
    return tmp_path, report, config


@pytest.mark.parametrize("arm", ["observed", "route_repair", "complete_capture"])
def test_independent_reference_preserves_fault_collision_missing_capture_and_failure(arm):
    rows = receipts(arm)
    answers = [verification.reference_row(row, arm)["reference"] for row in rows]
    assert answers.count("unknown") == 1
    assert answers.count("violation") == (0 if arm == "route_repair" else 2)
    assert rows[15]["output"] == ZERO
    assert rows[11]["capture_answer"] == "unknown"
    assert rows[9]["capture_answer"] == ("compliant" if arm == "complete_capture" else "unknown")


@pytest.mark.parametrize(
    "mutation",
    [
        "output",
        "session",
        "failure",
        "history",
        "scope",
        "input_hash",
        "terminal",
        "load",
        "use",
        "fabricated_capture",
        "caller",
        "answer",
    ],
)
def test_rehashed_receipt_cannot_override_independent_boundary(mutation):
    row = receipts()[9 if mutation == "fabricated_capture" else 0]
    if mutation == "output":
        row["output"] = ZERO
    elif mutation == "session":
        row["actual_session"] = "bbb"
    elif mutation == "failure":
        row["native_error"] = "fabricated failure"
    elif mutation == "history":
        row["strong_source_history"] = "violation"
    elif mutation == "scope":
        row["frame"]["token"] = "other-request"
    elif mutation == "input_hash":
        row["frame"]["input"] = digest("other operand")
    elif mutation == "terminal":
        row["frame"]["failed"] = True
    elif mutation == "load":
        row["frame"]["loads"]["session-aaa"]["fingerprint"] = "0" * 64
    elif mutation == "use":
        row["frame"]["uses"][0]["generation"] = "session-bbb"
    elif mutation == "fabricated_capture":
        row["frame"]["loads"] = deepcopy(receipts()[1]["frame"]["loads"])
    elif mutation == "caller":
        row["caller_receipt_sha256"] = digest("forged caller")
    else:
        row["capture_answer"] = "violation"
    with pytest.raises(ValueError):
        verification.reference_row(row, "observed")


def test_actual_database_reconstruction_retains_all_offers_and_failures(replay):
    directory, report, config = replay
    assert verification.verify_process(report, directory / "observed-0", config) == 6
    before = {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    checked = verification.verify(ROOT, directory)
    assert checked["verification"] == "pass" and checked["processes"] == 3
    assert checked["query_frontiers_verified"] == 6 and checked["provider_calls"] == 0
    result = analysis.analyze(directory)
    assert result["verification"]["processes"] == 3
    assert result["arms"]["unavailable"] == {"status": "all_processes_failed", "planned": 1}
    observed = result["arms"]["observed"]
    assert observed["native_call_median_ns"] == 17.5
    assert observed["capture_live_resolve_median_ns"] == 9.5
    assert observed["unique_offered_audits"] == 3
    assert result["native_floor_median_ns"] == 17.5
    candidate = next(point for point in observed["points"] if point["name"] == NAME)
    assert candidate["offered"] == candidate["accepted"] == 3
    assert (
        candidate["accepted_but_unserved"] == 1 and candidate["scope_census"]["unknown_scopes"] == 2
    )
    assert before == {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("native_census", "offered native census"),
        ("source_summary", "source summary"),
        ("archive_census", "archive arm census"),
        ("incident_census", "offered incident census"),
        ("preknown_census", "preknown census"),
        ("answer", "retained answer"),
        ("scope", "query scope"),
        ("clock", "query clock"),
        ("summary", "combined service summary"),
        ("late_summary", "incident summary"),
        ("preknown_summary", "prospective summary"),
        ("closed_bytes", "closed archive bytes"),
        ("state_hash", "archive frontier"),
        ("counter", "durable obligation counters"),
        ("admission", "admission census"),
        ("secondary_hash", "secondary bytes"),
    ],
)
def test_process_replay_rejects_changed_census_frontier_or_database_metadata(
    replay, mutation, expected
):
    directory, original, config = replay
    report = deepcopy(original)
    arm = report["archives"][NAME]
    if mutation == "native_census":
        report["rows"].pop()
    elif mutation == "source_summary":
        report["source_summary"]["captured_correct"] += 1
    elif mutation == "archive_census":
        del report["archives"][NAME]
    elif mutation == "incident_census":
        arm["offers"] = []
    elif mutation == "preknown_census":
        arm["prospective_admissions"] = []
    elif mutation == "answer":
        arm["offers"][0]["answers"]["request-007"] = "compliant"
    elif mutation == "scope":
        arm["offers"][0]["scopes"] = ["request-007"]
    elif mutation == "clock":
        arm["offers"][0]["queried_at"] += 1
    elif mutation in {"summary", "late_summary", "preknown_summary"}:
        arm[mutation]["offered"] += 1
    elif mutation == "closed_bytes":
        arm["closed_db_bytes"] += 1
    elif mutation == "state_hash":
        arm["storage"]["state_sha256"] = "0" * 64
    elif mutation == "counter":
        arm["storage"]["evicted"] += 1
    elif mutation == "admission":
        arm["offers"][0]["accepted"] = False
        arm["summary"] = summarize(arm["prospective_admissions"] + arm["offers"], report["rows"])
        arm["late_summary"] = summarize(arm["offers"], report["rows"])
    else:
        report["secondary"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match=expected):
        verification.verify_process(report, directory / "observed-0", config)


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("hash", "query frontier"),
        ("quota", "query quota"),
        ("orphan", "dependency union"),
        ("lease", "accepted closure"),
    ],
)
def test_query_witness_rejects_rehashed_invalid_resource_or_lease_state(replay, mutation, expected):
    _, report, _ = replay
    witness = deepcopy(report["archives"][NAME]["offers"][0]["witness"])
    state = IncidentAuditArchive._parse(witness["state"])
    if mutation == "hash":
        witness["state_sha256"] = "0" * 64
    else:
        if mutation == "quota":
            state["budget"] = 1
        elif mutation == "orphan":
            state["atoms"]["0" * 64] = "orphan"
        else:
            state["leases"]["lost"] = {"scopes": ["absent-request"], "until": 5}
        raw = IncidentAuditArchive._document(state)
        witness["state"], witness["state_sha256"] = json.loads(raw), content_sha256(raw)
    with pytest.raises(ValueError, match=expected):
        verification.witness_answers(witness)


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("plan_binding", "plan/results binding"),
        ("binding_census", "source binding census"),
        ("snapshot", "code snapshot"),
        ("source_identity", "source identity/arm"),
        ("floor_identity", "native floor source identity"),
        ("failure_status", "terminal worker disposition"),
        ("failure_stream", "failure stream"),
        ("aggregate", "aggregate reconstruction"),
    ],
)
def test_study_replay_does_not_accept_resealed_changed_identity_or_failure_census(
    replay, mutation, expected
):
    directory, _, _ = replay
    plan_path, results_path = directory / "plan.json", directory / "results.json"
    plan, results = json.loads(plan_path.read_bytes()), json.loads(results_path.read_bytes())
    if mutation == "plan_binding":
        results["plan_sha256"] = "0" * 64
        rewrite_sealed(results_path, results)
    elif mutation == "binding_census":
        plan["code"].pop(next(iter(plan["code"])))
        rewrite_sealed(plan_path, plan)
        results["plan_sha256"] = json.loads(plan_path.read_bytes())["sha256"]
        rewrite_sealed(results_path, results)
    elif mutation == "snapshot":
        path = directory / "code-snapshot" / FILES[0]
        path.write_bytes(path.read_bytes() + b"\n")
    elif mutation in {"source_identity", "floor_identity", "failure_status"}:
        path = directory / (
            "observed-0.json"
            if mutation == "source_identity"
            else "native_floor-0.json"
            if mutation == "floor_identity"
            else "unavailable-0.json"
        )
        document = json.loads(path.read_bytes())
        if mutation == "failure_status":
            document["status"] = "apparently_successful"
        else:
            document["source_identity"] = {"synthetic_owned_source": False}
        rewrite_sealed(path, document)
    elif mutation == "failure_stream":
        (directory / "unavailable-0.stderr").write_bytes(b"changed")
    else:
        results["summary"]["failed_processes"] = 0
        rewrite_sealed(results_path, results)
    with pytest.raises(ValueError, match=expected):
        verification.verify(ROOT, directory)


def test_closed_database_gate_rejects_symlink(replay):
    directory, _, _ = replay
    path = directory / "observed-0" / f"{NAME}.sqlite"
    alias = directory / "alias.sqlite"
    try:
        alias.symlink_to(path)
    except OSError:
        pytest.skip("symlink creation unavailable on this platform")
    with pytest.raises(ValueError, match="regular private"):
        verification._database(alias)


def test_closed_database_gate_rejects_live_wal(replay):
    directory, _, _ = replay
    path = directory / "observed-0" / f"{NAME}.sqlite"
    write_new_file(Path(str(path) + "-wal"), b"live WAL marker")
    with pytest.raises(ValueError, match="closed database"):
        verification._database(path)


def test_cost_point_keeps_false_unknown_missing_scopes_and_noncausal_refusal_classes():
    truth = {"a": "compliant", "b": "violation", "failed": "unknown"}
    rows = [{"frame": {"token": token}, "reference": verdict} for token, verdict in truth.items()]

    def item(identity, token, answer, accepted, kind="incident", duration=20):
        return {
            "id": identity,
            "kind": kind,
            "scopes": [token],
            "answers": {token: answer},
            "accepted": accepted,
            "queried_at": 2,
            "deadline": 2,
            "query_wall_ns": duration,
            "response_budget_ns": 50,
            "refetched": [],
            "refetch_failed": [],
        }

    items = [
        item("pre", "a", None, False, "preknown"),
        item("missing", "b", None, False),
        item("present", "a", "compliant", False),
        item("wrong", "b", "compliant", True),
        item("native", "failed", "unknown", True),
        item("correct", "a", "compliant", True),
    ]

    def archive():
        return {
            "prospective_admissions": items[:1],
            "offers": items[1:],
            "summary": summarize(items, rows),
            "closed_db_bytes": 100,
            "storage": {
                "peak_charge": 80,
                "logical_charge": 70,
                "peak_db_wal_shm_bytes": 300,
                "write_ns": 40,
            },
        }

    report = {
        "rows": rows,
        "archives": {NAME: archive(), "full-reference": archive()},
        "secondary": {"closed_db_bytes": 200},
    }
    point = analysis.point(NAME, [report], [19, 20])
    assert point["offered"] == 6 and point["refused"] == point["accepted"] == 3
    assert point["wrong"] == 1 and point["accepted_but_unserved"] == 2
    assert point["scope_census"] == {
        "offered_scopes": 6,
        "missing_scopes": 2,
        "unknown_scopes": 1,
        "native_failure_scopes": 1,
        "correct_conclusive_scopes": 2,
        "false_scopes": 1,
    }
    assert point["refusal_evidence_class"] == {
        "prospective_admission_refused_cause_not_recorded": 1,
        "missing_or_unrecoverable_evidence": 1,
        "admission_rejected_with_present_closure": 1,
    }
    assert point["response_budget_sensitivity"]["19"]["deadline_misses"] == 6
    assert point["response_budget_sensitivity"]["20"]["deadline_misses"] == 0
    assert analysis.envelope([point]) == {"status": "no_tested_ordinary_matched_service_candidate"}


def test_envelope_uses_storage_then_latency_among_only_matched_nonwrong_candidates():
    def candidate(name, storage, latency, same=True, wrong=0):
        return {
            "name": name,
            "same_full_service": same,
            "wrong": wrong,
            "median_matched_tier_closed_db_bytes": storage,
            "median_query_wall_ns": latency,
            "offered": 7,
        }

    points = [
        candidate("full-reference", 1, 1),
        candidate("cheaper-but-wrong", 2, 1, wrong=1),
        candidate("different-service", 3, 1, same=False),
        candidate("slower", 100, 20),
        candidate("selected", 100, 10),
        candidate("larger", 101, 1),
    ]
    chosen = analysis.envelope(points)
    assert chosen["candidate"] == "selected" and chosen["lowest_measured_closed_storage"] == 100
    assert chosen["query_wall_ns"] == 10 and chosen["offered"] == 7
