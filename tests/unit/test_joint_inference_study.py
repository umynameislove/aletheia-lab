from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from aletheia_lab.evaluation.joint_inference_audit import resolve
from aletheia_lab.evaluation.joint_inference_source import CASES, digest
from aletheia_lab.evaluation.joint_inference_study import (
    BUDGETS,
    MODES,
    POLICIES,
    check_arm,
    design,
    reference,
    replay,
    run,
    summarize,
    verify,
)
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256


def source() -> dict[str, Any]:
    releases = {
        key: {"raw_hex": key.encode().hex(), "fingerprint": digest("object-" + key)}
        for key in ("e1", "c1", "e2", "c2")
    }
    loads, rows = {}, []
    for index, (name, actual, authorized, attempt) in enumerate(CASES):
        request = f"request-{index:02d}"
        uses, raw_uses, capsules = [], [], {}
        failed = index in {5, 10}
        for ordinal, key in enumerate((f"e{actual[0]}", f"c{actual[1]}")):
            if failed:
                break
            generation = f"g-{actual}-{ordinal}"
            role = "encoder" if ordinal == 0 else "classifier"
            before, after = ([[1.0]], [[2.0]]) if ordinal == 0 else ([[2.0]], [1])
            artifact = content_sha256(bytes.fromhex(releases[key]["raw_hex"]))
            capsules[generation] = {
                "role": role,
                "artifact": artifact,
                "selected": artifact,
                "fingerprint": releases[key]["fingerprint"],
                "count": 1,
                "closed": True,
            }
            loads[generation] = {**releases[key], "selected_key": key}
            uses.append(
                {
                    "request": request,
                    "attempt": attempt,
                    "ordinal": ordinal,
                    "role": role,
                    "generation": generation,
                    "artifact": artifact,
                    "fingerprint": releases[key]["fingerprint"],
                    "input": digest(before),
                    "output": digest(after),
                }
            )
            raw_uses.append(
                {
                    "generation": generation,
                    "object_fingerprint": releases[key]["fingerprint"],
                    "input_values": before,
                    "output_values": after,
                }
            )
        frame = {
            "request": request,
            "attempt": attempt,
            "revision": 0,
            "expected": [
                content_sha256(key.encode()) for key in (f"e{authorized[0]}", f"c{authorized[1]}")
            ],
            "input": digest([[1.0]]),
            "output": None if failed else digest([1]),
            "declared": 2,
            "closed": True,
            "failed": failed,
            "loads": capsules,
            "uses": uses,
        }
        rows.append(
            {
                "case": name,
                "frame": frame,
                "reference_uses": raw_uses,
                "http_status": (None if index == 10 else 500) if failed else 200,
                "health_status": None if index == 10 else 200,
                "startup_error": "ValueError" if index == 10 else None,
                "input_values": [[1.0]],
                "output_values": None if failed else [1],
                "signed": resolve(frame)["verdict"],
            }
        )
    return {
        "rows": rows,
        "reference_loads": loads,
        "releases": releases,
        "pipeline": {
            "stage_count": 2,
            "reference": [{"raw_hex": "abcd"}],
            "raw_hex": "abcd",
            "output": [1],
            "independent_output": [1],
            "http_status": 200,
            "health_status": 200,
        },
    }


def test_raw_joint_reference_distinguishes_route_not_individual_loads() -> None:
    assert reference(source()) == [
        "compliant",
        "compliant",
        "violation",
        "compliant",
        "compliant",
        "unknown",
        "violation",
        "compliant",
        "violation",
        "compliant",
        "unknown",
        "compliant",
    ]


@pytest.mark.parametrize(
    "mutation", ("authority", "object", "raw", "dataflow", "output", "pipeline", "failure")
)
def test_independent_native_reference_rejects_changed_witnesses(mutation: str) -> None:
    value = source()
    row = value["rows"][0]
    if mutation == "authority":
        row["frame"]["expected"][0] = digest("changed")
    elif mutation == "object":
        row["reference_uses"][0]["object_fingerprint"] = digest("foreign")
    elif mutation == "raw":
        value["reference_loads"]["g-11-0"]["raw_hex"] = b"changed".hex()
    elif mutation == "dataflow":
        row["reference_uses"][1]["input_values"] = [[9.0]]
    elif mutation == "output":
        row["output_values"] = [0]
    elif mutation == "pipeline":
        value["pipeline"]["reference"].append({"raw_hex": "abcd"})
    else:
        row["frame"]["failed"] = True
    with pytest.raises(ValueError):
        reference(value)


def test_common_query_census_and_retained_basis_are_checked(tmp_path: Path) -> None:
    value = source()
    arm = replay(value, tmp_path / "run", "static", "compact", 8192)
    assert check_arm(arm, value)["optional_offered"] == 36
    assert check_arm(arm, value)["accepted_unserved"] == 0
    missing = deepcopy(arm)
    missing["queries"].pop()
    with pytest.raises(ValueError, match="census"):
        check_arm(missing, value)
    changed = deepcopy(arm)
    changed["queries"][0]["gold"] = "violation"
    with pytest.raises(ValueError, match="gold"):
        check_arm(changed, value)
    changed = deepcopy(arm)
    changed["queries"][0]["archive"]["logical_bytes"] -= 1
    with pytest.raises(ValueError, match="accounting"):
        check_arm(changed, value)
    changed = deepcopy(arm)
    changed["queries"][0]["decision"]["frame"]["expected"][0] = digest("changed")
    with pytest.raises(ValueError, match="certificate"):
        check_arm(changed, value)


def test_existing_study_is_rejected_before_any_native_work(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="fresh private"):
        run(tmp_path, tmp_path, [])


@pytest.mark.parametrize("tamper", (None, "source", "query", "design"))
def test_sealed_replay_is_read_only_and_rejects_changed_evidence(
    tmp_path: Path, tamper: str | None
) -> None:
    root = Path(__file__).resolve().parents[2]
    value = {**source(), "startups": 7, "http_requests": 24, "descriptor_observations": [{}] * 13}
    sources, arms = [value, value], []
    for repeat in range(2):
        native = tmp_path / f"native-{repeat}"
        native.mkdir()
        write_new_file(native / "source.json", encode(value).encode())
        for policy in POLICIES:
            for mode in MODES:
                for budget in BUDGETS:
                    arm = replay(
                        value, tmp_path / f"{repeat}-{policy}-{mode}-{budget}", policy, mode, budget
                    )
                    arms.append({**arm, "repeat": repeat})
    plan = design(root)
    result = {
        "schema": plan["schema"],
        "plan_sha256": digest(plan),
        "sources": sources,
        "arms": arms,
        "analysis": summarize(sources, arms),
    }
    if tamper == "source":
        sources[0]["rows"][0]["frame"]["expected"][0] = digest("changed")
    elif tamper == "query":
        arms[0]["queries"].pop()
    elif tamper == "design":
        plan["optional_ages"] = [0]
        result["plan_sha256"] = digest(plan)
    result["results_sha256"] = digest(result)
    write_new_file(tmp_path / "plan.json", encode(plan).encode())
    write_new_file(tmp_path / "results.json", encode(result).encode())
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    if tamper:
        with pytest.raises(ValueError):
            verify(root, tmp_path)
    else:
        report = verify(root, tmp_path)
        assert report["verification"] == "pass"
        assert report["analysis"]["replay_arms"] == 36
        assert report["provider_calls"] == report["protected_runs"] == 0
        assert str(tmp_path) not in json.dumps(report)
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
