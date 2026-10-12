"""Two synthetic application censuses; no native models, SDKs or signing."""

from __future__ import annotations

from copy import deepcopy

import pytest

from aletheia_lab.evaluation import application_audit_analysis as analysis
from aletheia_lab.evaluation.application_audit_workload import BUDGETS, POLICIES, REPEATS, STEPS
from aletheia_lab.project.identity import content_sha256


def releases():
    return {
        name: {
            "raw_hex": raw.hex(),
            "sha256": content_sha256(raw),
            "object_fingerprint": f"synthetic-object-{name}",
            "predictions": [0, 1] if name == "A" else [1, 0],
        }
        for name, raw in (("A", b"inert-owned-A"), ("B", b"inert-owned-B"))
    }


def native_response(model, manifest):
    return {
        "object_fingerprint": manifest[model]["object_fingerprint"],
        "predictions": manifest[model]["predictions"],
        "http_status": 200,
        "health_status": 200,
    }


def rows(manifest):
    truths = [
        "compliant",
        "compliant",
        "violation",
        "unknown",
        "compliant",
        "violation",
        "compliant",
    ]
    requested = ["A", "B", "B", "B", "B", "A", "A"]
    actual = ["A", "B", "A", None, "B", "B", "A"]
    result = []
    for index, (step, expected, observed, truth) in enumerate(
        zip(STEPS, requested, actual, truths, strict=True)
    ):
        scope = f"load-{index:02d}"
        raw = (
            [] if observed is None else [{"scope": scope, "raw_hex": manifest[observed]["raw_hex"]}]
        )
        frame = {
            "scope": scope,
            "step": index,
            "kind": "load",
            "domain": [manifest[model]["sha256"] for model in ("A", "B")],
            "expected": manifest[expected]["sha256"],
            "observed": [] if observed is None else [manifest[observed]["sha256"]],
            "count": len(raw),
            "closed": True,
            "generation": None,
        }
        result.append(
            {
                "scope": scope,
                "step": step,
                "frame": frame,
                "raw_reference": raw,
                "startup_error": "SyntheticStartupError" if observed is None else None,
                "native_response": None
                if observed is None
                else native_response(observed, manifest),
                "object_model": observed,
                "gold": truth,
                "signed_provenance": truth,
            }
        )
    return result


def controls(manifest):
    names = (
        "retained_application_selection_move",
        "fresh_application_selection",
        "path_replacement_not_resident",
        "failed_new_app_prior_app_survives",
    )
    return [
        {
            "case": name,
            "after": native_response("A" if index == 0 else "B", manifest),
            "startup_error": "SyntheticStartupError" if index == 3 else None,
        }
        for index, name in enumerate(names)
    ]


def arm(manifest, policy="drain_static", budget=8192, repeat=0):
    attempts = rows(manifest)
    attempts[1] = {"scope": "load-01", "step": "lawful_change", "status": "refused_before_dispatch"}
    for index, row in enumerate(attempts):
        if index != 1:
            row["audit_accepted"] = index in {0, 4, 5, 6}
    hard = [
        {
            "scope": f"load-{index:02d}",
            "gold": truth,
            "hard": True,
            "now": index + 4,
            "decision": None if index == 4 else {"verdict": truth},
        }
        for index, truth in ((0, "compliant"), (4, "compliant"), (5, "violation"), (6, "compliant"))
    ]
    optional = [
        {
            "scope": "load-00",
            "gold": "compliant",
            "hard": False,
            "now": 1,
            "decision": {"verdict": "compliant"},
        },
        {
            "scope": "load-02",
            "gold": "violation",
            "hard": False,
            "now": 3,
            "decision": {"verdict": "violation"},
        },
        {"scope": "load-03", "gold": "unknown", "hard": False, "now": 4, "decision": None},
        {
            "scope": "load-04",
            "gold": "compliant",
            "hard": False,
            "now": 5,
            "decision": {"verdict": "violation"},
        },
        {
            "scope": "load-05",
            "gold": "violation",
            "hard": False,
            "now": 6,
            "decision": {"verdict": "unknown"},
        },
    ]
    return {
        "policy": policy,
        "budget": budget,
        "repeat": repeat,
        "peak_charged_bytes": 1024,
        "rows": attempts,
        "queries": hard + optional,
        "overruns": 1,
        "releases": manifest,
        "start_attempts": 6,
        "http_requests": 10,
    }


def two_stack_census():
    data = []
    for stack in ("bentoml", "mlflow"):
        manifest = releases()
        data.append(
            {
                "stack": stack,
                "census": {
                    "releases": manifest,
                    "rows": rows(manifest),
                    "cache_controls": controls(manifest),
                    "start_attempts": 9,
                    "http_requests": 18,
                },
                "arms": [
                    arm(manifest, policy, budget, repeat)
                    for repeat in range(REPEATS)
                    for budget in BUDGETS
                    for policy in POLICIES
                ],
            }
        )
    return data


EXPECTED_ARM = {
    "offered": 7,
    "arms": 1,
    "dispatched": 6,
    "refused_before_dispatch": 1,
    "native_completed": 5,
    "native_failed": 1,
    "accepted_leases": 4,
    "new_audit_refused_after_completion": 1,
    "hard_queries": 4,
    "optional_queries": 5,
    "correct_queries": 5,
    "false_queries": 1,
    "unknown_queries": 3,
    "accepted_unserved": 1,
    "overruns": 1,
}


def test_runtime_arm_keeps_refusal_native_failure_and_accepted_unknown_in_denominator():
    candidate = arm(releases())
    assert dict(analysis.check_arm(candidate)) == EXPECTED_ARM
    assert candidate["queries"][1]["decision"] is None
    assert candidate["rows"][3]["startup_error"] == "SyntheticStartupError"
    assert (
        candidate["rows"][2]["gold"] == "violation"
        and candidate["rows"][2]["audit_accepted"] is False
    )


def test_two_stack_full_arm_census_preserves_independent_counts_without_superiority_claim():
    data = two_stack_census()
    before = deepcopy(data)
    result = analysis.summarize(data)
    assert data == before
    assert result["runtime_arms"] == 48
    assert result["native_totals"] == {"start_attempts": 306, "http_requests": 516}
    for stack in ("bentoml", "mlflow"):
        assert result["applications"][stack]["gold_states"] == {
            "compliant": 4,
            "violation": 2,
            "unknown": 1,
        }
        assert result["applications"][stack]["native_cache_controls_passed"] == 4
    for policy in POLICIES:
        assert result["policies"][policy] == {
            key: count * 12 for key, count in EXPECTED_ARM.items()
        }
    assert result["new_algorithm_superiority_established"] is False


@pytest.mark.parametrize(
    "change,expected",
    [
        ("stacks", "source census"),
        ("operation", "operation denominator"),
        ("missing_arm", "runtime-arm census"),
        ("duplicate_arm", "runtime-arm census"),
        ("replaced_policy", "runtime-arm census"),
    ],
)
def test_full_census_cannot_drop_or_duplicate_a_stack_operation_or_runtime_arm(change, expected):
    data = two_stack_census()
    if change == "stacks":
        data.reverse()
    elif change == "operation":
        data[0]["census"]["rows"].pop()
    elif change == "missing_arm":
        data[0]["arms"].pop()
    elif change == "duplicate_arm":
        data[0]["arms"].append(deepcopy(data[0]["arms"][0]))
    else:
        data[0]["arms"][0]["policy"] = "unplanned-policy"
    with pytest.raises(ValueError, match=expected):
        analysis.summarize(data)


@pytest.mark.parametrize(
    "change,expected",
    [
        ("budget", "logical budget"),
        ("operation", "operation census"),
        ("missing_hard", "obligation denominator"),
        ("duplicate_hard", "obligation denominator"),
        ("late_hard", "declared deadline"),
        ("early_hard", "declared deadline"),
        ("changed_gold", "query gold"),
    ],
)
def test_arm_replay_rejects_missing_accepted_query_or_changed_deadline_and_gold(change, expected):
    candidate = arm(releases())
    if change == "budget":
        candidate["peak_charged_bytes"] = candidate["budget"] + 1
    elif change == "operation":
        candidate["rows"].pop()
    elif change == "missing_hard":
        candidate["queries"].pop(0)
    elif change == "duplicate_hard":
        candidate["queries"].append(deepcopy(candidate["queries"][0]))
    elif change == "late_hard":
        candidate["queries"][0]["now"] += 1
    elif change == "early_hard":
        candidate["queries"][0]["now"] -= 1
    else:
        candidate["queries"][0]["gold"] = "violation"
    with pytest.raises(ValueError, match=expected):
        analysis.check_arm(candidate)


@pytest.mark.parametrize(
    "change,expected",
    [
        ("order", "cache-control census"),
        ("fingerprint", "generation control"),
        ("prediction", "cache-control request"),
        ("http", "cache-control request"),
        ("health", "cache-control request"),
        ("failure_not_reproduced", "not reproduced"),
    ],
)
def test_cache_generation_control_requires_resident_object_sink_and_failed_new_startup(
    change, expected
):
    manifest = releases()
    values = controls(manifest)
    if change == "order":
        values.reverse()
    elif change == "fingerprint":
        values[2]["after"]["object_fingerprint"] = manifest["A"]["object_fingerprint"]
    elif change == "prediction":
        values[2]["after"]["predictions"] = [0, 1]
    elif change == "http":
        values[2]["after"]["http_status"] = 500
    elif change == "health":
        values[2]["after"]["health_status"] = 500
    else:
        values[-1]["startup_error"] = None
    with pytest.raises(ValueError, match=expected):
        analysis.check_cache(values, manifest)


def test_refused_row_needs_no_fabricated_native_outcome_but_keeps_offered_identity():
    manifest = releases()
    refused = {"step": "initial", "status": "refused_before_dispatch"}
    assert analysis.check_row(refused, manifest, 0) == "refused"
    refused["step"] = "lawful_change"
    with pytest.raises(ValueError, match="operation census"):
        analysis.check_row(refused, manifest, 0)


@pytest.mark.parametrize("change", ["sufficient", "signed", "object_model", "raw_zero_loads"])
def test_baselines_and_raw_native_object_must_agree_with_independent_bytes(change):
    manifest = releases()
    value = rows(manifest)[0]
    if change == "sufficient":
        value["sufficient"] = {"verdict": "unknown"}
    elif change == "signed":
        value["signed_provenance"] = "unknown"
    elif change == "object_model":
        value["object_model"] = "B"
    else:
        value["raw_reference"] = []
        value["frame"].update(count=0, observed=[])
    with pytest.raises(ValueError):
        analysis.check_row(value, manifest, 0)


@pytest.mark.parametrize("index,truth", [(0, "compliant"), (2, "violation")])
def test_open_capture_cannot_establish_compliance_but_preserves_witnessed_violation(index, truth):
    manifest = releases()
    value = rows(manifest)[index]
    assert value["gold"] == truth
    value["frame"]["closed"] = False
    if truth == "compliant":
        with pytest.raises(
            ValueError, match="sufficient baseline disagrees with raw native reference"
        ):
            analysis.check_row(value, manifest, index)
    else:
        # The wrong digest is a positive witness; closure is needed only to
        # rule out an additional, unseen load before claiming compliance.
        assert analysis.check_row(value, manifest, index) == "violation"
