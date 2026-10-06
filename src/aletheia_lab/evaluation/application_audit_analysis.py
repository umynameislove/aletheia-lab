"""Read-only development census checks using raw reference, not stored gold labels."""

from __future__ import annotations

from collections import Counter
from typing import Any

from aletheia_lab.evaluation.application_audit_workload import BUDGETS, REPEATS, STEPS
from aletheia_lab.evaluation.audit_obligation_service import POLICIES
from aletheia_lab.evaluation.model_load_serving_workload import decide
from aletheia_lab.project.identity import content_sha256


def check_row(row: dict[str, Any], releases: dict[str, Any], index: int) -> str:
    if row["step"] != STEPS[index]:
        raise ValueError("planned operation census changed")
    if row.get("status") == "refused_before_dispatch":
        return "refused"
    name = "A" if row["step"] in {"initial", "wrong_a", "restore_a"} else "B"
    expected = releases[name]["sha256"]
    if any(
        content_sha256(bytes.fromhex(value["raw_hex"])) != value["sha256"]
        for value in releases.values()
    ):
        raise ValueError("trusted release manifest changed")
    frame = row["frame"]
    raw = [bytes.fromhex(value["raw_hex"]) for value in row["raw_reference"]]
    observed = [content_sha256(value) for value in raw]
    if frame["expected"] != expected or frame["observed"] != observed or frame["count"] != len(raw):
        raise ValueError("selection/capture differs from independent raw reference")
    gold = (
        "unknown"
        if row["startup_error"] is not None or len(raw) != 1
        else ("compliant" if raw[0] == bytes.fromhex(releases[name]["raw_hex"]) else "violation")
    )
    if row["gold"] != gold:
        raise ValueError("stored gold disagrees with original native input")
    check_response(row, releases)
    if "sufficient" in row and row["sufficient"] != decide(frame):
        raise ValueError("sufficient baseline result changed")
    if gold != "unknown" and decide(frame)["verdict"] != gold:
        raise ValueError("sufficient baseline disagrees with raw native reference")
    if "signed_provenance" in row and row["signed_provenance"] != gold:
        raise ValueError("signed provenance baseline differs from raw reference")
    return gold


def check_response(row: dict[str, Any], releases: dict[str, Any]) -> None:
    response = row["native_response"]
    if response is not None:
        model = next(
            (
                key
                for key, value in releases.items()
                if value["object_fingerprint"] == response["object_fingerprint"]
            ),
            None,
        )
        if model is None or model != row["object_model"]:
            raise ValueError("native model object cannot be aligned independently")
        if (
            response["predictions"] != releases[model]["predictions"]
            or response["http_status"] != 200
            or response["health_status"] != 200
        ):
            raise ValueError("actual application prediction sink differs from resident model")


def check_cache(controls: list[dict[str, Any]], releases: dict[str, Any]) -> None:
    names = (
        "retained_application_selection_move",
        "fresh_application_selection",
        "path_replacement_not_resident",
        "failed_new_app_prior_app_survives",
    )
    if tuple(row["case"] for row in controls) != names:
        raise ValueError("native cache-control census changed")
    for row in controls:
        expected = "B" if row["case"] != names[0] else "A"
        after = row["after"]
        if after["object_fingerprint"] != releases[expected]["object_fingerprint"]:
            raise ValueError("native cached generation control failed")
        if (
            after["predictions"] != releases[expected]["predictions"]
            or after["http_status"] != 200
            or after["health_status"] != 200
        ):
            raise ValueError("native cache-control request failed")
    if controls[-1]["startup_error"] is None:
        raise ValueError("failed-new-application control was not reproduced")


def summarize(data: list[dict[str, Any]]) -> dict[str, Any]:
    if tuple(value["stack"] for value in data) != ("bentoml", "mlflow"):
        raise ValueError("selected application source census changed")
    application = {}
    totals: Counter[str] = Counter()
    policies: dict[str, Counter[str]] = {policy: Counter() for policy in POLICIES}
    for source in data:
        census = source["census"]
        check_cache(census["cache_controls"], census["releases"])
        if len(census["rows"]) != len(STEPS):
            raise ValueError("application operation denominator changed")
        gold = Counter(
            check_row(row, census["releases"], i) for i, row in enumerate(census["rows"])
        )
        totals.update(
            start_attempts=census["start_attempts"], http_requests=census["http_requests"]
        )
        expected = {
            (repeat, budget, policy)
            for repeat in range(REPEATS)
            for budget in BUDGETS
            for policy in POLICIES
        }
        observed = {(arm["repeat"], arm["budget"], arm["policy"]) for arm in source["arms"]}
        if observed != expected or len(source["arms"]) != len(expected):
            raise ValueError("matched runtime-arm census changed")
        for arm in source["arms"]:
            count = check_arm(arm)
            policies[arm["policy"]].update(count)
            totals.update(start_attempts=arm["start_attempts"], http_requests=arm["http_requests"])
        application[source["stack"]] = {
            "gold_states": dict(gold),
            "sufficient_and_signed_match_raw_reference": True,
            "native_cache_controls_passed": 4,
            "native_metadata_is_not_a_released_byte_commitment": True,
        }
    return {
        "applications": application,
        "runtime_arms": sum(len(value["arms"]) for value in data),
        "native_totals": dict(totals),
        "policies": {key: dict(value) for key, value in policies.items()},
        "new_algorithm_superiority_established": False,
        "disposition": "application_transfer_and_ordinary_admission_development",
        "limitations": "two local serving paths; MLflow framework seen earlier; serial authored workloads; "
        "operator-byte overlay; trusted capture; logical-byte/event bounds; no population/SLA/crash guarantee",
    }


def check_arm(arm: dict[str, Any]) -> Counter[str]:
    if arm["peak_charged_bytes"] > arm["budget"] or len(arm["rows"]) != len(STEPS):
        raise ValueError("runtime logical budget or operation census violated")
    count: Counter[str] = Counter(offered=len(STEPS), arms=1)
    gold = {}
    accepted = set()
    for index, row in enumerate(arm["rows"]):
        truth = check_row(row, arm["releases"], index)
        count["refused_before_dispatch" if truth == "refused" else "dispatched"] += 1
        if truth == "refused":
            continue
        gold[row["scope"]] = truth
        count["native_failed" if row["startup_error"] else "native_completed"] += 1
        if row["audit_accepted"]:
            accepted.add(row["scope"])
            count["accepted_leases"] += 1
        elif row["startup_error"] is None:
            count["new_audit_refused_after_completion"] += 1
    hard_seen = check_queries(arm["queries"], gold, count)
    if set(hard_seen) != accepted or len(hard_seen) != len(accepted):
        raise ValueError("accepted-obligation denominator changed")
    count["overruns"] += arm["overruns"]
    return count


def check_queries(
    queries: list[dict[str, Any]], gold: dict[str, str], count: Counter[str]
) -> list[str]:
    hard_seen = []
    for query in queries:
        if query["gold"] != gold.get(query["scope"]):
            raise ValueError("query gold differs from native reference")
        decision = query["decision"]
        verdict = decision["verdict"] if decision is not None else "unknown"
        correct = verdict == query["gold"] and verdict in {"compliant", "violation"}
        wrong = verdict in {"compliant", "violation"} and verdict != query["gold"]
        count["hard_queries" if query["hard"] else "optional_queries"] += 1
        count["correct_queries"] += int(correct)
        count["false_queries"] += int(wrong)
        count["unknown_queries"] += int(not correct and not wrong)
        if query["hard"]:
            index = int(query["scope"].removeprefix("load-"))
            if query["now"] != index + 4:
                raise ValueError("accepted obligation was not queried at its declared deadline")
            hard_seen.append(query["scope"])
            count["accepted_unserved"] += int(not correct)
    return hard_seen
