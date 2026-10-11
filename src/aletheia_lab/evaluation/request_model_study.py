"""Execute bounded source-informed serving replication and paired audit retention.

Two fresh clusters per version/mode are nested robustness checks, not independent
incidents. Workload retention is SQLite replay of realized captures, not serving
backpressure or a wall-clock audit SLA. Existing heuristics are not novel methods.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import subprocess
import zlib
from collections import Counter
from importlib.metadata import version
from itertools import product
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.audit_bundle_policy import POLICIES
from aletheia_lab.evaluation.model_load_provenance import verify_chain
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.request_model_audit import (
    analyze,
    correspondence,
    digest,
    expand,
    frames,
    resolve,
)
from aletheia_lab.evaluation.request_model_reference import check_source, raw_analysis
from aletheia_lab.evaluation.request_model_retention import RequestModelArchive
from aletheia_lab.evaluation.request_model_source import planned_rows
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

VERSIONS = {"before": "2.48.0", "fixed": "2.54.0"}
MODES = ("original", "owned_ml")
BUDGETS = (2048, 4096, 8192, 16384)
SCHEDULES = {"early": (0, 2, 6), "late": (1, 3, 9)}
FILES = (
    "scripts/request_model_validation.py",
    "src/aletheia_lab/evaluation/request_model_source.py",
    "src/aletheia_lab/evaluation/request_model_audit.py",
    "src/aletheia_lab/evaluation/request_model_reference.py",
    "src/aletheia_lab/evaluation/request_model_retention.py",
    "src/aletheia_lab/evaluation/request_model_study.py",
    "src/aletheia_lab/evaluation/audit_bundle_policy.py",
    "src/aletheia_lab/evaluation/model_load_provenance.py",
    "src/aletheia_lab/evaluation/model_load_retention.py",
    "src/aletheia_lab/filesystem.py",
    "src/aletheia_lab/project/identity.py",
)


def design(root: Path) -> dict[str, Any]:
    return {
        "schema": "request-model-native-development/v1",
        "source_issue": "https://github.com/ray-project/ray/issues/56633",
        "upstream_repair": "https://github.com/ray-project/ray/pull/59334",
        "versions": VERSIONS,
        "repeats": 2,
        "planned": {mode: planned_rows(mode) for mode in MODES},
        "budgets": list(BUDGETS),
        "schedules": {key: list(value) for key, value in SCHEDULES.items()},
        "policies": sorted(POLICIES),
        "representations": ["full", "compact"],
        "hard_audit": "every third conclusive completed item; inclusive deadline arrival+4",
        "scope": "source-informed replication and controlled development; not unseen validation",
        "retention_scope": "same captured completed-item stream; no native backpressure/refetch",
        "provenance_packages": {key: version(key) for key in ("in-toto", "securesystemslib")},
        "source_bindings": {name: content_sha256((root / name).read_bytes()) for name in FILES},
        "provider_calls": 0,
        "protected_runs": 0,
    }


def signed_checks(source: dict[str, Any], directory: Path) -> list[str]:
    results = []
    for index, frame in enumerate(frames(source)):
        # Matching access; do not sign a guessed or incomplete correspondence.
        linked = correspondence(frame)
        if linked in {"unknown", "conflict"}:
            results.append(linked)
            continue
        load = frame["loads"][frame["uses"][0]["generation"]]
        requested = frame["requested"]
        expected = (
            source["artifacts"][requested]["artifact_sha256"]
            if source["mode"] == "owned_ml"
            else digest({"value": f"model_obj_for_{requested}"})
        )
        result = verify_chain(
            {f"model/{requested}": expected},
            {f"model/{load['model']}": load["artifact"]},
            directory / f"request-{index:02d}",
        )
        results.append("compliant" if result == "pass" else "violation")
    return results


def native_worker(root: Path, directory: Path, executable: Path, mode: str) -> dict[str, Any]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(root / "src")
    environment.pop("RAY_ADDRESS", None)
    command = [
        str(executable),
        str(root / "scripts/request_model_validation.py"),
        "worker",
        "--study-dir",
        str(directory),
        "--mode",
        mode,
    ]
    try:
        result = subprocess.run(
            command, cwd=root, env=environment, capture_output=True, timeout=240, check=False
        )
    except subprocess.TimeoutExpired as exc:
        write_new_file(directory.parent / f"{directory.name}-stdout.log", exc.stdout or b"")
        write_new_file(directory.parent / f"{directory.name}-stderr.log", exc.stderr or b"")
        return {"directory": directory.name, "worker_status": "timeout", "source": None}
    write_new_file(directory.parent / f"{directory.name}-stdout.log", result.stdout)
    write_new_file(directory.parent / f"{directory.name}-stderr.log", result.stderr)
    path = directory / "source.json"
    return {
        "directory": directory.name,
        "worker_status": "terminal" if result.returncode == 0 else "worker_failure",
        "source": json.loads(path.read_bytes()) if path.is_file() else None,
    }


def _events(admitted: list[dict[str, Any]], schedule: str) -> list[tuple[int, int, int, str]]:
    events = []
    for index, frame in enumerate(admitted):
        events.append((index, 1, index, "arrival"))
        for delay in SCHEDULES[schedule]:
            events.append((index + delay, 2, index, "optional"))
        if index % 3 == 0 and resolve(frame) in {"compliant", "violation"}:
            events.append((index + 4, 0, index, "hard"))
    return sorted(events)


def _snapshot(archive: RequestModelArchive) -> dict[str, Any]:
    snapshot = archive.snapshot()
    raw = snapshot.pop("raw").encode()
    return {
        **snapshot,
        "state_sha256": content_sha256(raw),
        "state_zlib": base64.b64encode(zlib.compress(raw)).decode("ascii"),
    }


def _query(
    archive: RequestModelArchive, frame: dict[str, Any], now: int, kind: str, accepted: bool
) -> dict[str, Any]:
    answer = archive.query(frame["token"], now)
    return {
        "token": frame["token"],
        "now": now,
        "kind": kind,
        "accepted_contract": accepted,
        "verdict": answer["verdict"] if answer is not None else None,
        "snapshot": _snapshot(archive),
    }


def replay(
    source: dict[str, Any], directory: Path, policy: str, mode: str, budget: int, schedule: str
) -> dict[str, Any]:
    directory.mkdir()
    archive = RequestModelArchive(directory / "archive.sqlite", policy, budget, mode)
    admitted = frames(source)
    accepted: set[str] = set()
    offers, queries = [], []
    failure = None
    try:
        for now, _, index, kind in _events(admitted, schedule):
            frame = admitted[index]
            if kind == "arrival":
                until = (
                    now + 4
                    if index % 3 == 0 and resolve(frame) in {"compliant", "violation"}
                    else None
                )
                success = archive.put(frame, now, until)
                if success and until is not None:
                    accepted.add(frame["token"])
                offers.append(
                    {
                        "token": frame["token"],
                        "now": now,
                        "until": until,
                        "retained": success,
                        "snapshot": _snapshot(archive),
                    }
                )
            else:
                queries.append(_query(archive, frame, now, kind, frame["token"] in accepted))
    except (sqlite3.Error, OSError, RuntimeError, ValueError) as exc:
        failure = type(exc).__name__
    finally:
        final = _snapshot(archive)
        archive.close()
    arm = {
        "directory": directory.name,
        "policy": policy,
        "mode": mode,
        "budget": budget,
        "schedule": schedule,
        "failure": failure,
        "offers": offers,
        "queries": queries,
        "final": final,
    }
    arm["analysis"] = arm_analysis(admitted, arm)
    return arm


def arm_analysis(admitted: list[dict[str, Any]], arm: dict[str, Any]) -> dict[str, Any]:
    truth = {frame["token"]: resolve(frame) for frame in admitted}
    optional = [query for query in arm["queries"] if query["kind"] == "optional"]
    hard = [
        query for query in arm["queries"] if query["kind"] == "hard" and query["accepted_contract"]
    ]
    accepted = sum(offer["retained"] and offer["until"] is not None for offer in arm["offers"])
    planned_hard = sum(
        index % 3 == 0 and truth[frame["token"]] in {"compliant", "violation"}
        for index, frame in enumerate(admitted)
    )

    def conclusive(query: dict[str, Any]) -> bool:
        return truth[query["token"]] in {"compliant", "violation"}

    def correct(query: dict[str, Any]) -> bool:
        return conclusive(query) and query["verdict"] == truth[query["token"]]

    def false(query: dict[str, Any]) -> bool:
        return (
            query["verdict"] in {"compliant", "violation"}
            and query["verdict"] != truth[query["token"]]
        )

    return {
        "offered_items": len(admitted),
        "processed_items": len(arm["offers"]),
        "optional_offered": 3 * len(admitted),
        "optional_processed": len(optional),
        "optional_correct": sum(map(correct, optional)),
        "optional_false": sum(map(false, optional)),
        "optional_missing_conclusive": sum(
            conclusive(q) and q["verdict"] is None for q in optional
        ),
        "optional_native_unknown": sum(not conclusive(q) for q in optional),
        "hard_offered": planned_hard,
        "hard_queries_processed": sum(query["kind"] == "hard" for query in arm["queries"]),
        "hard_accepted": accepted,
        "hard_refused_or_unprocessed": planned_hard - accepted,
        "hard_correct": sum(map(correct, hard)),
        "hard_false": sum(map(false, hard)),
        "hard_unserved": accepted - sum(map(correct, hard)),
        "failure": arm["failure"],
        **{
            key: arm["final"][key]
            for key in (
                "peak_logical_bytes",
                "sampled_storage_peak_bytes",
                "storage_sample_failures",
                "selection_ns",
                "write_ns",
                "query_ns",
            )
        },
    }


def audit_summary(source: dict[str, Any], native_version: str, adapter_hash: str) -> dict[str, Any]:
    truth = check_source(source, native_version, adapter_hash)
    result = analyze(source)
    if [row["receipt"] for row in result["outcomes"]] != truth:
        raise ValueError("candidate audit differs from raw native reference")
    if [row["explicit_item_links"] for row in result["outcomes"]] != truth:
        raise ValueError("ordinary explicit-link comparator differs from raw native reference")
    return {**raw_analysis(source, truth), "audit": result}


def summarize(workers: list[dict[str, Any]], arms: list[dict[str, Any]]) -> dict[str, Any]:
    native = {}
    for variant in VERSIONS:
        for mode in MODES:
            selected = [
                worker
                for worker in workers
                if worker["variant"] == variant and worker["mode"] == mode
            ]
            counts: Counter[str] = Counter()
            output: Counter[str] = Counter()
            for worker in selected:
                summary = worker.get("analysis")
                counts.update(
                    summary["reference_counts"] if summary else {"unknown": len(planned_rows(mode))}
                )
                output.update(
                    summary["output_only_counts"]
                    if summary
                    else {"unknown": len(planned_rows(mode))}
                )
            native[f"{variant}/{mode}"] = {
                "planned_requests": 2 * len(planned_rows(mode)),
                "reference_counts": dict(counts),
                "output_only_counts": dict(output),
                "terminal_workers": sum(
                    worker["worker_status"] == "terminal" for worker in selected
                ),
            }
    groups: dict[str, dict[str, int]] = {}
    for arm in arms:
        key = f"{arm['policy']}/{arm['mode']}/{arm['budget']}/{arm['schedule']}"
        target = groups.setdefault(key, {})
        for field in (
            "optional_offered",
            "optional_correct",
            "optional_false",
            "hard_offered",
            "hard_accepted",
            "hard_correct",
            "hard_false",
            "hard_unserved",
        ):
            target[field] = target.get(field, 0) + arm["analysis"][field]
    return {
        "native": native,
        "retention_arm_count": len(arms),
        "retention": groups,
        "method_advantage": "compare ordinary policies; no novelty or dominance assumed",
        "scope": (
            "one known upstream fault family; two version arms and nested process repetitions; "
            "controlled ML extensions"
        ),
        "protected_runs": 0,
        "provider_calls": 0,
    }


def run(root: Path, directory: Path, before_python: Path, fixed_python: Path) -> dict[str, Any]:
    if directory.exists() or directory.is_symlink():
        raise ValueError("fresh private development study required")
    plan = design(root)
    _environments(root, {"before": before_python, "fixed": fixed_python})
    directory.mkdir(parents=True)
    write_new_file(directory / "plan.json", encode(plan).encode())
    for relative in FILES:
        path = directory / "implementation" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        write_new_file(path, (root / relative).read_bytes())
    workers, arms = [], []
    executables = {"before": before_python, "fixed": fixed_python}
    for variant, mode, repeat in product(VERSIONS, MODES, range(2)):
        name = f"native-{variant}-{mode}-{repeat}"
        worker = native_worker(root, directory / name, executables[variant], mode)
        worker.update(variant=variant, mode=mode, repeat=repeat)
        source = worker["source"]
        if source is not None:
            worker["analysis"] = audit_summary(
                source, VERSIONS[variant], plan["source_bindings"][FILES[1]]
            )
            worker["signed"] = signed_checks(source, directory / name / "signed")
            if worker["signed"] != worker["analysis"]["reference"]:
                raise ValueError("executed signed comparator differs from raw reference")
        workers.append(worker)
        print(
            encode(
                {
                    "status": "native_request_model_progress",
                    "terminal_workers": len(workers),
                    "planned_workers": 8,
                }
            ),
            flush=True,
        )
        if source is not None and mode == "owned_ml":
            arms.extend(_retention_arms(source, directory, name))
    if design(root) != plan:
        raise ValueError("consequential implementation changed during execution")
    result = {
        "schema": plan["schema"],
        "plan_sha256": digest(plan),
        "workers": workers,
        "arms": arms,
        "analysis": summarize(workers, arms),
    }
    result["results_sha256"] = digest(result)
    write_new_file(directory / "results.json", encode(result).encode())
    return verify(root, directory)


def _retention_arms(source: dict[str, Any], directory: Path, worker: str) -> list[dict[str, Any]]:
    arms = []
    for policy, mode, budget, schedule in product(
        sorted(POLICIES), ("full", "compact"), BUDGETS, SCHEDULES
    ):
        name = f"r-{worker}-{policy}-{mode}-{budget}-{schedule}"
        arm = replay(source, directory / name, policy, mode, budget, schedule)
        arm.update(worker=worker)
        arms.append(arm)
    return arms


def _state(snapshot: dict[str, Any]) -> dict[str, Any]:
    decoder = zlib.decompressobj()
    raw = decoder.decompress(base64.b64decode(snapshot["state_zlib"], validate=True), 1_000_001)
    if not decoder.eof or decoder.unused_data or len(raw) > 1_000_000:
        raise ValueError("corrupt or oversized replay state")
    document: dict[str, Any] = json.loads(raw)
    if (
        encode(document).encode() != raw
        or content_sha256(raw) != snapshot["state_sha256"]
        or len(raw) != snapshot["logical_bytes"]
    ):
        raise ValueError("replay state identity/accounting changed")
    return document


def _atom(key: str, data: str) -> dict[str, Any]:
    decoder = zlib.decompressobj()
    raw = decoder.decompress(base64.b64decode(data, validate=True), 65537)
    if not decoder.eof or decoder.unused_data or len(raw) > 65536 or content_sha256(raw) != key:
        raise ValueError("replay atom identity changed")
    return dict(json.loads(raw))


def verify_arm(arm: dict[str, Any], source: dict[str, Any], directory: Path) -> None:
    originals = {frame["token"]: frame for frame in frames(source)}
    _check_events(arm, list(originals.values()))
    final_state = None
    for snapshot in [
        *(offer["snapshot"] for offer in arm["offers"]),
        *(query["snapshot"] for query in arm["queries"]),
        arm["final"],
    ]:
        state = _state(snapshot)
        if (
            snapshot["logical_bytes"] > arm["budget"]
            or snapshot["peak_logical_bytes"] > arm["budget"]
        ):
            raise ValueError("replay exceeded total logical byte budget")
        if (state["mode"], state["policy"], int(state["budget"], 16)) != (
            arm["mode"],
            arm["policy"],
            arm["budget"],
        ):
            raise ValueError("matched replay capabilities changed")
        _check_state(state, originals, arm["mode"])
        final_state = state
    for query in arm["queries"]:
        present = query["token"] in _state(query["snapshot"])["entries"]
        expected = correspondence(originals[query["token"]]) if present else None
        if query["verdict"] != expected:
            raise ValueError("replay query lacks its actual durable basis")
    if arm_analysis(list(originals.values()), arm) != arm["analysis"]:
        raise ValueError("replay offered denominator or aggregate changed")
    uri = (directory / arm["directory"] / "archive.sqlite").resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        raw = bytes(db.execute("SELECT payload FROM state WHERE id=1").fetchone()[0])
    if raw != encode(final_state).encode():
        raise ValueError("actual durable final archive differs from reported replay")


def _environments(root: Path, executables: dict[str, Path]) -> None:
    for variant, executable in executables.items():
        completed = subprocess.run(
            [
                str(executable),
                "-c",
                "from importlib.metadata import version; print(version('ray'))",
            ],
            cwd=root,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if completed.returncode != 0 or completed.stdout.decode().strip() != VERSIONS[variant]:
            raise ValueError("explicit isolated native Python/Ray environment required")


def _check_events(arm: dict[str, Any], admitted: list[dict[str, Any]]) -> None:
    if arm["schedule"] not in SCHEDULES or arm["policy"] not in POLICIES:
        raise ValueError("unknown matched schedule or policy")
    expected_offers, expected_queries = [], []
    for now, _, index, kind in _events(admitted, arm["schedule"]):
        token = admitted[index]["token"]
        if kind == "arrival":
            until = (
                now + 4
                if index % 3 == 0 and resolve(admitted[index]) in {"compliant", "violation"}
                else None
            )
            expected_offers.append((token, now, until))
        else:
            expected_queries.append((token, now, kind))
    offered = [(row["token"], row["now"], row["until"]) for row in arm["offers"]]
    queries = [(row["token"], row["now"], row["kind"]) for row in arm["queries"]]
    if offered != expected_offers[: len(offered)] or queries != expected_queries[: len(queries)]:
        raise ValueError("offered event scope or deadline changed")
    if arm["failure"] is None and (offered != expected_offers or queries != expected_queries):
        raise ValueError("complete arm dropped offered queries or admissions")
    accepted = {
        row["token"] for row in arm["offers"] if row["retained"] and row["until"] is not None
    }
    if any(query["accepted_contract"] != (query["token"] in accepted) for query in arm["queries"]):
        raise ValueError("hard query responsibility changed after admission")
    if any(
        int(_state(event["snapshot"])["now"], 16) != event["now"]
        for event in [*arm["offers"], *arm["queries"]]
    ):
        raise ValueError("durable event clock changed")


def _check_state(state: dict[str, Any], originals: dict[str, Any], mode: str) -> None:
    dependencies = set()
    for token, entry in state["entries"].items():
        dependencies.update([entry["target"], *entry["loads"].values()])
        if digest([token, entry["target"], entry["loads"]]) != entry["seal"]:
            raise ValueError("replay manifest changed")
        target = _atom(entry["target"], state["atoms"][entry["target"]])
        loads = {
            generation: _atom(key, state["atoms"][key])["load"]
            for generation, key in entry["loads"].items()
        }
        frame = (
            expand({**target, "loads": loads}) if mode == "compact" else {**target, "loads": loads}
        )
        if frame != originals[token]:
            raise ValueError("retained basis differs from actual native capture")
    if dependencies != set(state["atoms"]):
        raise ValueError("uncharged or orphan replay dependency")


def verify(root: Path, directory: Path) -> dict[str, Any]:
    plan = json.loads((directory / "plan.json").read_bytes())
    result = json.loads((directory / "results.json").read_bytes())
    if plan != design(root) or result["plan_sha256"] != digest(plan):
        raise ValueError("executed development design/source identity changed")
    if result["results_sha256"] != digest(
        {key: value for key, value in result.items() if key != "results_sha256"}
    ):
        raise ValueError("development result identity changed")
    _check_census(result)
    sources = {}
    for worker in result["workers"]:
        if worker["source"] is None:
            continue
        source = json.loads((directory / worker["directory"] / "source.json").read_bytes())
        if (
            source != worker["source"]
            or audit_summary(source, VERSIONS[worker["variant"]], plan["source_bindings"][FILES[1]])
            != worker["analysis"]
        ):
            raise ValueError("aggregate differs from original native source/reference")
        if worker["signed"] != worker["analysis"]["reference"]:
            raise ValueError("recorded signed comparator changed")
        sources[worker["directory"]] = source
    for arm in result["arms"]:
        verify_arm(arm, sources[arm["worker"]], directory)
    if summarize(result["workers"], result["arms"]) != result["analysis"]:
        raise ValueError("native/retention analysis changed")
    return {
        "status": "request_model_development_verified",
        "verification": "pass",
        "plan_sha256": result["plan_sha256"],
        "results_sha256": result["results_sha256"],
        "analysis": result["analysis"],
        "provider_calls": 0,
        "protected_runs": 0,
    }


def _check_census(result: dict[str, Any]) -> None:
    expected = [
        (f"native-{variant}-{mode}-{repeat}", variant, mode, repeat)
        for variant, mode, repeat in product(VERSIONS, MODES, range(2))
    ]
    actual = [(w["directory"], w["variant"], w["mode"], w["repeat"]) for w in result["workers"]]
    if actual != expected:
        raise ValueError("planned native worker census changed")
    expected_arms = [
        (worker["directory"], policy, mode, budget, schedule)
        for worker in result["workers"]
        if worker["source"] is not None and worker["mode"] == "owned_ml"
        for policy, mode, budget, schedule in product(
            sorted(POLICIES), ("full", "compact"), BUDGETS, SCHEDULES
        )
    ]
    actual_arms = [
        (a["worker"], a["policy"], a["mode"], a["budget"], a["schedule"]) for a in result["arms"]
    ]
    if actual_arms != expected_arms:
        raise ValueError("matched retention arm census changed")
