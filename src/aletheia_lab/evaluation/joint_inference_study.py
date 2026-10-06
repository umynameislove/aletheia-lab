"""Opt-in native development capture followed by matched durable audit replay."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import socket
import subprocess
import sys
import zlib
from collections import Counter
from pathlib import Path
from time import perf_counter_ns
from typing import Any
from unittest.mock import patch

from aletheia_lab.evaluation.application_audit_study import child_environment
from aletheia_lab.evaluation.joint_inference_audit import (
    JointInferenceAudit,
    expand,
    materialize,
    resolve,
)
from aletheia_lab.evaluation.joint_inference_source import CASES, digest, native_capture
from aletheia_lab.evaluation.model_load_provenance import verify_chain
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

BUDGETS = (2048, 4096, 8192)
POLICIES = ("static", "lru", "ttl")
MODES = ("full", "compact")
AGES = (0, 2, 6)
CODE = (
    "src/aletheia_lab/evaluation/joint_inference_source.py",
    "src/aletheia_lab/evaluation/joint_inference_audit.py",
    "src/aletheia_lab/evaluation/joint_inference_study.py",
    "scripts/joint_inference_development.py",
    "src/aletheia_lab/evaluation/application_audit_study.py",
    "src/aletheia_lab/evaluation/application_audit_sources.py",
    "src/aletheia_lab/evaluation/model_load_application_capture.py",
    "src/aletheia_lab/evaluation/model_load_application.py",
    "src/aletheia_lab/evaluation/model_load_serving_capture.py",
    "src/aletheia_lab/evaluation/model_load_retention.py",
    "src/aletheia_lab/evaluation/model_load_provenance.py",
    "src/aletheia_lab/filesystem.py",
    "src/aletheia_lab/project/identity.py",
)


def design(root: Path) -> dict[str, Any]:
    return {
        "schema": "joint-inference-development/v1",
        "source": "BentoML1.4.39-local-native-depends",
        "selection": "inspectable native composition and retained model objects; owned artifacts",
        "contract": "operator ordered released-component tuple pinned before serial request",
        "query": "completed fixed request/attempt/revision authorization and dataflow",
        "cases": [list(case) for case in CASES],
        "native_processes": 2,
        "budgets": list(BUDGETS),
        "policies": list(POLICIES),
        "representations": list(MODES),
        "optional_ages": list(AGES),
        "inclusive_lease_age": 4,
        "admission": "post-completion audit only; never framework dispatch refusal",
        "code_sha256": {name: content_sha256((root / name).read_bytes()) for name in CODE},
        "provider_calls": 0,
        "protected_execution": False,
    }


def worker(directory: Path) -> None:
    for key in tuple(os.environ):
        if key.startswith(("BENTOCLOUD_", "OTEL_", "PROMETHEUS_", "MLFLOW_")):
            os.environ.pop(key)
    os.environ.update(BENTOML_DO_NOT_TRACK="true", OTEL_SDK_DISABLED="true")
    logging.disable(logging.CRITICAL)
    directory.mkdir(parents=True, exist_ok=False)

    def deny(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("joint development cannot use network sockets")

    runner = asyncio.Runner()
    runner.get_loop()
    try:
        with (
            patch.object(socket.socket, "connect", deny),
            patch.object(socket.socket, "connect_ex", deny),
            patch.object(socket, "create_connection", deny),
        ):
            try:
                source = runner.run(native_capture(directory))
            finally:
                runner.close()
    finally:
        runner.close()
    # Execute real package artifact rules after native capture. Only complete
    # admitted invocation bindings become signed materials; signatures are not
    # claimed to provide an independent observation boundary.
    for index, row in enumerate(source["rows"]):
        frame = row["frame"]
        if frame["failed"] or not frame["closed"] or len(frame["uses"]) != 2:
            row["signed"] = "unknown"
            continue
        # Eligibility is visible evidence completeness, never the gold verdict.
        # Native invocation order/dataflow is checked by the trusted adapter;
        # in-toto itself only verifies the two role/artifact rules here.
        expected = dict(zip(("encoder", "classifier"), frame["expected"], strict=True))
        observed = {use["role"]: use["artifact"] for use in frame["uses"]}
        result = verify_chain(expected, observed, directory / "signed" / f"request-{index:02d}")
        row["signed"] = "compliant" if result == "pass" else "violation"
    write_new_file(directory / "source.json", encode(source).encode())


def reference(source: dict[str, Any]) -> list[str]:
    """Raw-descriptor/object/operand checks without using the tested resolver."""
    if [row["case"] for row in source["rows"]] != [case[0] for case in CASES]:
        raise ValueError("native case census changed")
    truth = [reference_row(source, row, index) for index, row in enumerate(source["rows"])]
    check_pipeline(source["pipeline"])
    return truth


def reference_row(source: dict[str, Any], row: dict[str, Any], index: int) -> str:
    releases = source["releases"]
    authorized = CASES[index][2]
    expected = [
        bytes.fromhex(releases[key]["raw_hex"])
        for key in (f"e{authorized[0]}", f"c{authorized[1]}")
    ]
    frame = row["frame"]
    if frame["expected"] != [content_sha256(raw) for raw in expected]:
        raise ValueError("request authority changed after design")
    if frame["input"] != digest(row["input_values"]) or frame["attempt"] != CASES[index][3]:
        raise ValueError("request operands or attempt identity changed")
    if frame["failed"]:
        if row["http_status"] == 200 or (index == 10 and not row.get("startup_error")):
            raise ValueError("declared native failure is not realized")
        return "unknown"
    if row["http_status"] != 200 or row["health_status"] != 200:
        raise ValueError("successful native request lacks successful HTTP sink")
    uses = row["reference_uses"]
    if len(uses) != 2 or len(frame["uses"]) != 2:
        raise ValueError("native joint use census changed")
    actual = reference_uses(source, row)
    return "compliant" if actual == expected else "violation"


def reference_uses(source: dict[str, Any], row: dict[str, Any]) -> list[bytes]:
    releases = source["releases"]
    actual = []
    previous = row["input_values"]
    for ordinal, use in enumerate(row["reference_uses"]):
        load = source["reference_loads"][use["generation"]]
        raw = bytes.fromhex(load["raw_hex"])
        selected = releases[load["selected_key"]]
        if (
            raw != bytes.fromhex(selected["raw_hex"])
            or use["object_fingerprint"] != selected["fingerprint"]
        ):
            raise ValueError("individual native load/object differs from owned release")
        if use["input_values"] != previous:
            raise ValueError("native transform/predict operands were misjoined")
        previous = use["output_values"]
        actual.append(raw)
        recorded = row["frame"]["uses"][ordinal]
        if recorded["generation"] != use["generation"] or recorded["artifact"] != content_sha256(
            raw
        ):
            raise ValueError("tested invocation differs from original native reference")
        if recorded["input"] != digest(use["input_values"]) or recorded["output"] != digest(
            previous
        ):
            raise ValueError("tested operand digest differs from actual invocation")
    if previous != row["output_values"] or row["frame"]["output"] != digest(previous):
        raise ValueError("actual HTTP result differs from predictor sink")
    return actual


def check_pipeline(pipeline: dict[str, Any]) -> None:
    if (
        pipeline["stage_count"] != 2
        or len(pipeline["reference"]) != 1
        or pipeline["reference"][0]["raw_hex"] != pipeline["raw_hex"]
        or pipeline["output"] != pipeline["independent_output"]
        or pipeline["http_status"] != 200
        or pipeline["health_status"] != 200
    ):
        raise ValueError("whole-pipeline single-load architectural control failed")


def replay(
    source: dict[str, Any], directory: Path, policy: str, mode: str, budget: int
) -> dict[str, Any]:
    directory.mkdir()
    service = JointInferenceAudit(directory / "archive.sqlite", policy, budget, mode=mode)
    truth = reference(source)
    offered: list[dict[str, Any]] = []
    leases: dict[int, tuple[str, str]] = {}
    admissions = []
    try:
        for now in range(len(CASES) + max(AGES)):
            service.advance(now)
            if now < len(CASES):
                frame = source["rows"][now]["frame"]
                before = service.refused
                accepted = service.put(frame, now=now, until=now + 4)
                admissions.append(
                    {
                        "slot": now,
                        "request": frame["request"],
                        "accepted": accepted,
                        "eligible": truth[now] != "unknown",
                        "storage_refused": service.refused - before,
                    }
                )
                if accepted:
                    leases[now + 4] = (frame["request"], truth[now])
            if now in leases:
                request, gold = leases[now]
                offered.append(audit_query(service, request, now, gold, True))
            for age in AGES:
                index = now - age
                if 0 <= index < len(CASES):
                    frame = source["rows"][index]["frame"]
                    offered.append(audit_query(service, frame["request"], now, truth[index], False))
        physical = sum(path.stat().st_size for path in directory.glob("archive.sqlite*"))
        return {
            "policy": policy,
            "mode": mode,
            "budget": budget,
            "admissions": admissions,
            "queries": offered,
            "snapshot": service.snapshot(),
            "physical_bytes": physical,
        }
    finally:
        service.close()


def audit_query(service: Any, request: str, now: int, gold: str, hard: bool) -> dict[str, Any]:
    started = perf_counter_ns()
    decision = service.query(request, now=now)
    return {
        "request": request,
        "now": now,
        "gold": gold,
        "hard": hard,
        "decision": decision,
        "query_ns": perf_counter_ns() - started,
        "archive": service.snapshot(),
    }


def summarize(sources: list[dict[str, Any]], arms: list[dict[str, Any]]) -> dict[str, Any]:
    native: Counter[str] = Counter()
    for source in sources:
        truth = reference(source)
        for row, gold in zip(source["rows"], truth, strict=True):
            frame = row["frame"]
            decision = resolve(frame)
            if (
                expand(materialize(frame)) != frame
                or resolve(expand(materialize(frame))) != decision
            ):
                raise ValueError("compact certificate changed warranted evidence")
            if decision["verdict"] != gold or row["signed"] != gold:
                raise ValueError("strong joint baselines differ from independent native truth")
        native.update(truth)
        native.update(
            startups=source["startups"],
            http_requests=source["http_requests"],
            descriptor_observations=len(source["descriptor_observations"]),
        )
    expected = {(p, m, b, r) for p in POLICIES for m in MODES for b in BUDGETS for r in range(2)}
    if {
        (arm["policy"], arm["mode"], arm["budget"], arm["repeat"]) for arm in arms
    } != expected or len(arms) != len(expected):
        raise ValueError("matched retention replay census changed")
    comparisons = []
    for arm in arms:
        counts = check_arm(arm, sources[arm["repeat"]])
        comparisons.append(
            {
                **{key: arm[key] for key in ("repeat", "policy", "mode", "budget")},
                **dict(counts),
                "physical_bytes": arm["physical_bytes"],
                "peak_logical_bytes": arm["snapshot"]["peak"],
            }
        )
    return {
        "native": dict(native),
        "replay_arms": len(arms),
        "comparisons": comparisons,
        "query_family": "fixed completed request authorization/dataflow only",
        "new_method_advantage": False,
        "disposition": "joint_audit_and_ordinary_materialization_development",
    }


def check_arm(arm: dict[str, Any], source: dict[str, Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    truths = dict(
        zip((row["frame"]["request"] for row in source["rows"]), reference(source), strict=True)
    )
    originals = {row["frame"]["request"]: row["frame"] for row in source["rows"]}
    accepted = {row["request"]: row["slot"] for row in arm["admissions"] if row["accepted"]}
    expected_optional = Counter(
        (frame["request"], index + age)
        for index, frame in enumerate(originals.values())
        for age in AGES
    )
    observed_optional = Counter((q["request"], q["now"]) for q in arm["queries"] if not q["hard"])
    if expected_optional != observed_optional or len(arm["admissions"]) != len(CASES):
        raise ValueError("common fixed offered-query or admission census changed")
    counts.update(
        accepted=len(accepted),
        offered_leases=len(CASES),
        ineligible=sum(not row["eligible"] for row in arm["admissions"]),
        eligible_refused=sum(row["eligible"] and not row["accepted"] for row in arm["admissions"]),
        storage_refused=sum(row["storage_refused"] for row in arm["admissions"]),
    )
    if (
        arm["snapshot"]["accepted"] != counts["accepted"]
        or arm["snapshot"]["refused"] != counts["storage_refused"]
        or any(
            row["eligible"] != (truths[row["request"]] != "unknown")
            or (row["accepted"] and not row["eligible"])
            for row in arm["admissions"]
        )
    ):
        raise ValueError("failure eligibility or durable admission counts changed")
    hard_seen = []
    for query in arm["queries"]:
        if query["gold"] != truths.get(query["request"]):
            raise ValueError("query gold differs from independent native reference")
        check_query_basis(query, originals[query["request"]], arm)
        counts["hard_offered" if query["hard"] else "optional_offered"] += 1
        decision = query["decision"]
        verdict = decision["verdict"] if decision is not None else "unknown"
        conclusive = query["gold"] in {"compliant", "violation", "conflict"}
        correct = conclusive and verdict == query["gold"]
        kind = "hard" if query["hard"] else "optional"
        counts[f"{kind}_correct"] += int(correct)
        counts[f"{kind}_native_unknown"] += int(not conclusive)
        counts[f"{kind}_evidence_missing"] += int(conclusive and verdict == "unknown")
        counts["correct"] += int(correct)
        counts["false"] += int(verdict != "unknown" and verdict != query["gold"])
        counts["unresolved"] += int(not correct and verdict == "unknown")
        if query["hard"]:
            if query["now"] != accepted[query["request"]] + 4:
                raise ValueError("accepted joint audit missed inclusive deadline")
            hard_seen.append(query["request"])
            counts["accepted_unserved"] += int(not correct)
    if sorted(hard_seen) != sorted(accepted) or counts["optional_offered"] != len(CASES) * len(
        AGES
    ):
        raise ValueError("complete common query/accepted-obligation census changed")
    return counts


def decode_atom(key: str, value: str) -> dict[str, Any]:
    decoder = zlib.decompressobj()
    raw = decoder.decompress(base64.b64decode(value, validate=True), 65537)
    if not decoder.eof or decoder.unused_data or len(raw) > 65536 or content_sha256(raw) != key:
        raise ValueError("durable audit basis identity is invalid")
    document = json.loads(raw)
    if type(document) is not dict or encode(document).encode() != raw:
        raise ValueError("durable basis is noncanonical")
    return document


def check_query_basis(query: dict[str, Any], original: dict[str, Any], arm: dict[str, Any]) -> None:
    snapshot = query["archive"]
    state = snapshot["state"]
    if (
        len(encode(state).encode()) != snapshot["logical_bytes"]
        or snapshot["logical_bytes"] > arm["budget"]
        or snapshot["peak"] > arm["budget"]
        or any(state[key] != arm[key] for key in ("mode", "policy", "budget"))
    ):
        raise ValueError("complete durable accounting or matched capabilities changed")
    entry = state["entries"].get(query["request"])
    decision = query["decision"]
    if entry is None:
        if decision is not None:
            raise ValueError("answer lacks retained warranted basis")
        return
    if digest([entry["request"], entry["target"], entry["loads"]]) != entry["seal"]:
        raise ValueError("durable basis manifest changed")
    target = decode_atom(entry["target"], state["atoms"][entry["target"]])
    loads = {g: decode_atom(key, state["atoms"][key])["load"] for g, key in entry["loads"].items()}
    frame = (
        expand({**target, "loads": loads})
        if arm["mode"] == "compact"
        else {**target, "loads": loads}
    )
    if frame != original or decision != {**resolve(frame), "frame": frame}:
        raise ValueError("durable certificate differs from original native frame/decision")


def run(root: Path, directory: Path, dependencies: list[Path]) -> dict[str, Any]:
    if (
        directory.exists()
        or directory.is_symlink()
        or any(not path.is_dir() for path in dependencies)
    ):
        raise ValueError("fresh private study and explicit installed dependencies required")
    directory.mkdir(parents=True)
    plan = design(root)
    write_new_file(directory / "plan.json", encode(plan).encode())
    sources, arms = [], []
    for repeat in range(2):
        worker_dir = directory / f"native-{repeat}"
        command = [
            sys.executable,
            str(root / "scripts/joint_inference_development.py"),
            "worker",
            "--study-dir",
            str(worker_dir),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                env=child_environment(root, dependencies),
                capture_output=True,
                timeout=300,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            write_new_file(directory / f"native-{repeat}-stdout.log", exc.stdout or b"")
            write_new_file(directory / f"native-{repeat}-stderr.log", exc.stderr or b"")
            raise RuntimeError("native worker timed out; private logs retained") from exc
        write_new_file(directory / f"native-{repeat}-stdout.log", completed.stdout)
        write_new_file(directory / f"native-{repeat}-stderr.log", completed.stderr)
        if completed.returncode != 0:
            raise RuntimeError("native worker failed; private logs retained")
        source = json.loads((worker_dir / "source.json").read_bytes())
        sources.append(source)
        for policy in POLICIES:
            for mode in MODES:
                for budget in BUDGETS:
                    arm = replay(
                        source,
                        directory / f"r{repeat}-{policy}-{mode}-{budget}",
                        policy,
                        mode,
                        budget,
                    )
                    arm["repeat"] = repeat
                    arms.append(arm)
    result = {
        "schema": plan["schema"],
        "plan_sha256": digest(plan),
        "sources": sources,
        "arms": arms,
        "analysis": summarize(sources, arms),
    }
    result["results_sha256"] = digest(result)
    write_new_file(directory / "results.json", encode(result).encode())
    return verify(root, directory)


def verify(root: Path, directory: Path) -> dict[str, Any]:
    plan = json.loads((directory / "plan.json").read_bytes())
    result = json.loads((directory / "results.json").read_bytes())
    if plan != design(root) or result["plan_sha256"] != digest(plan):
        raise ValueError("executed design/source identity changed")
    if result["results_sha256"] != digest(
        {key: value for key, value in result.items() if key != "results_sha256"}
    ):
        raise ValueError("result identity changed")
    sources = [
        json.loads((directory / f"native-{r}" / "source.json").read_bytes()) for r in range(2)
    ]
    if sources != result["sources"] or summarize(sources, result["arms"]) != result["analysis"]:
        raise ValueError("aggregate differs from original native evidence")
    return {
        "status": "joint_inference_development_verified",
        "verification": "pass",
        "plan_sha256": result["plan_sha256"],
        "results_sha256": result["results_sha256"],
        "analysis": result["analysis"],
        "provider_calls": 0,
        "protected_runs": 0,
    }
