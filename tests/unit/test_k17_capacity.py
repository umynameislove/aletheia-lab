"""Scheduler semantics, real HTTP client behaviour and safe independent actors."""

from __future__ import annotations

import asyncio
import hashlib
import json

import httpx2
import pytest
from pydantic import SecretStr, ValidationError

from aletheia_lab.hosted.capacity import CapacityHarness, CorrectnessFailure, checked_path, template
from aletheia_lab.hosted.capacity_models import (
    Actor,
    Assertion,
    Journey,
    Profile,
    Step,
    load_actors,
)


def _actor(index=0, *, role="viewer", proof=None):
    proof = proof or f"PRIVATE_PROOF_{index}"
    return Actor(
        actor_id=f"actor_{index}",
        role=role,
        size="S",
        cookie=SecretStr(
            f"__Host-aletheia_session=PRIVATE_SESSION_{index}; __Host-aletheia_csrf={proof}"
        ),
        csrf=SecretStr(proof),
        values={
            "workspace_id": f"ws_{index}",
            "project_id": f"project_{index}",
            "result_id": f"result_{index}",
        },
    )


def _profile(*, mode="open", journey=None, **changes):
    journey = journey or Journey(
        name="result_read",
        weight=1,
        roles=("viewer",),
        deadline_seconds=0.1,
        steps=(
            Step(
                operation_id="P15",
                path="/v1/workspaces/{workspace_id}/results/{result_id}",
                assertions=(
                    Assertion(pointer="/workspace_id", check="equals", value="$workspace_id"),
                    Assertion(pointer="/result_id", check="equals", value="$result_id"),
                ),
            ),
        ),
    )
    values = dict(
        target="http://127.0.0.1:32109",
        origin="https://app.example.invalid",
        host="app.example.invalid",
        release_sha256="a" * 64,
        dataset_sha256="b" * 64,
        seed=17,
        mode=mode,
        duration_seconds=0.12,
        warmup_seconds=0.0,
        drain_seconds=0.3,
        max_inflight=2,
        max_pending=4,
        arrivals_per_second=50.0,
        think_seconds=0.01,
        journeys=(journey,),
        operation_dispositions={f"P{n:02}": "not_run" for n in range(1, 39)},
        run_label="unit",
    )
    values.update(changes)
    return Profile(**values)


def _run(profile, actors, handler):
    return asyncio.run(
        CapacityHarness(
            profile, actors, transport=httpx2.MockTransport(handler), verify_principals=False
        ).run()
    )


def test_closed_cohort_has_distinct_explicit_cookies_and_does_not_inherit_response_cookie_jar():
    seen = []

    async def handler(request):
        cookie = request.headers["cookie"]
        index = 0 if "PRIVATE_SESSION_0;" in cookie else 1
        assert cookie == _actor(index).cookie.get_secret_value()
        seen.append(index)
        await asyncio.sleep(0.005)
        return httpx2.Response(
            200,
            json={"workspace_id": f"ws_{index}", "result_id": f"result_{index}"},
            headers={"set-cookie": "__Host-aletheia_session=FORGED_SHARED_SESSION; Path=/"},
        )

    result = _run(_profile(mode="closed"), [_actor(0), _actor(1)], handler)
    assert set(seen) == {0, 1}
    assert result["counts"]["completed"] == result["counts"]["offered"]
    assert result["counts"]["pending"] == 0 and result["peak_action_inflight"] <= 2
    assert result["reconciliation"]["terminal_outcomes_match"]
    assert "PRIVATE" not in json.dumps(result) and "FORGED" not in json.dumps(result)
    assert result["primary_campaign_passed"] is False


def test_independent_open_arrivals_keep_drops_timeouts_and_queue_wait_in_denominator():
    async def handler(request):
        await asyncio.sleep(0.065)
        return httpx2.Response(200, json={"workspace_id": "ws_0", "result_id": "result_0"})

    result = _run(
        _profile(arrivals_per_second=200.0, max_inflight=1, max_pending=2), [_actor()], handler
    )
    counts = result["counts"]
    assert counts["offered"] == counts["planned"] == 24
    assert counts["dropped"] > 0 and counts["timeout"] > 0
    assert result["strata"][0]["deadline_inclusive"]["n"] == 24
    assert result["strata"][0]["completed_response"]["n"] < 24
    assert result["measured_positive"]["success_fraction"] < 1
    assert result["peak_action_inflight"] == 1
    assert all(
        result["reconciliation"][k]
        for k in (
            "offered_equals_terminal_plus_pending",
            "planned_equals_offered_plus_not_offered",
            "terminal_outcomes_match",
        )
    )


@pytest.mark.parametrize(
    ("corrupt", "download_status"),
    [(False, 200), (True, 200), (False, 429), (False, 500), (False, 503), (False, 404)],
)
def test_ack_followed_to_terminal_and_complete_export_hash_is_checked(corrupt, download_status):
    payload = b"Unicode synthetic export: " + "Tiếng Việt\n".encode() * 20000
    digest = hashlib.sha256(payload).hexdigest()
    polls = 0
    journey = Journey(
        name="analysis_export",
        weight=1,
        roles=("analyst",),
        deadline_seconds=1.0,
        steps=(
            Step(
                operation_id="P13",
                method="POST",
                path="/v1/workspaces/{workspace_id}/projects/{project_id}/analyses",
                statuses=(202,),
                body={"question": "$action_question"},
                follow_job=True,
                assertions=(
                    Assertion(pointer="/result/result_id", check="equals", value="new_result"),
                ),
                captures={"result_id": "/result/result_id"},
            ),
            Step(
                operation_id="P23",
                method="POST",
                path="/v1/workspaces/{workspace_id}/results/{result_id}/exports",
                statuses=(201,),
                body={"format": "json"},
                download_descriptor=True,
            ),
        ),
    )

    async def handler(request):
        nonlocal polls
        if request.url.path.endswith("/analyses"):
            return httpx2.Response(202, json={"workspace_id": "ws_0", "job_id": "job_one"})
        if request.url.path.endswith("/jobs/job_one"):
            polls += 1
            return httpx2.Response(
                200,
                json={
                    "workspace_id": "ws_0",
                    "job_id": "job_one",
                    "status": "running" if polls == 1 else "succeeded",
                },
            )
        if request.url.path.endswith("/output"):
            return httpx2.Response(
                200,
                json={
                    "workspace_id": "ws_0",
                    "job_id": "job_one",
                    "result": {"result_id": "new_result"},
                },
            )
        if request.url.path.endswith("/exports"):
            return httpx2.Response(
                201,
                json={
                    "workspace_id": "ws_0",
                    "project_id": "project_0",
                    "result_id": "new_result",
                    "download_url": "/v1/workspaces/ws_0/exports/export_one/download",
                    "sha256": digest,
                    "byte_size": len(payload),
                },
            )
        assert request.url.path.endswith("/download")
        return httpx2.Response(download_status, content=payload[:-1] if corrupt else payload)

    result = _run(
        _profile(
            journey=journey, duration_seconds=0.01, arrivals_per_second=1.0, poll_seconds=0.05
        ),
        [_actor(role="analyst")],
        handler,
    )
    assert result["counts"]["jobs_admitted"] == result["counts"]["jobs_terminal"] == 1
    assert result["jobs_pending_terminal"] == 0
    assert result["operation38"]["actual_step_counts"]["P20"] == 3
    if download_status in (429, 500, 503):
        assert result["fatal_correctness_code"] is None
        assert result["counts"]["rejected"] == 1 and not result["counts"].get("exports_verified", 0)
        assert result["measured_positive"]["correct_by_deadline"] == 0
    elif download_status != 200:
        assert result["fatal_correctness_code"] == "export_download_unavailable"
        assert result["counts"]["failed"] == 1
    elif corrupt:
        assert result["fatal_correctness_code"] == "whole_export_hash_or_length_mismatch"
        assert result["counts"]["failed"] == 1 and not result["counts"].get("completed", 0)
    else:
        assert result["counts"]["completed"] == result["counts"]["exports_verified"] == 1


@pytest.mark.parametrize("fail_twice", [False, True])
def test_lost_transport_retries_same_key_and_unresolved_mutation_remains_unknown(fail_twice):
    keys = []
    journey = Journey(
        name="mutation",
        weight=1,
        roles=("viewer",),
        deadline_seconds=0.2,
        steps=(
            Step(
                operation_id="P26",
                method="PUT",
                path="/v1/workspaces/{workspace_id}/results/{result_id}/annotations/review",
                body={"synthetic": True},
                assertions=(Assertion(pointer="/ok", check="equals", value=True),),
            ),
        ),
    )

    async def handler(request):
        keys.append(request.headers["idempotency-key"])
        if len(keys) == 1 or fail_twice:
            raise httpx2.ConnectError("PRIVATE_CREDENTIAL_DRIVER_DETAILS", request=request)
        return httpx2.Response(200, json={"ok": True})

    result = _run(
        _profile(journey=journey, duration_seconds=0.01, arrivals_per_second=1.0),
        [_actor()],
        handler,
    )
    assert len(keys) == 2 and keys[0] == keys[1]
    assert result["http"]["transport_retries_same_key"] == 1
    assert result["counts"]["outcome_unknown" if fail_twice else "completed"] == 1
    assert "PRIVATE" not in json.dumps(result)


def test_actor_file_private_sharded_distinct_session_and_fixed_operation_inventory(tmp_path):
    path = tmp_path / "actors.jsonl"

    def write(actors):
        path.write_text(
            "\n".join(
                json.dumps(
                    {
                        **a.model_dump(mode="json"),
                        "cookie": a.cookie.get_secret_value(),
                        "csrf": a.csrf.get_secret_value(),
                    }
                )
                for a in actors
            )
            + "\n"
        )
        path.chmod(0o600)

    write([_actor(i) for i in range(6)])
    left, identity = load_actors(path, _profile(shard_index=0, shard_count=2))
    right, other = load_actors(path, _profile(shard_index=1, shard_count=2))
    assert identity == other and not ({a.actor_id for a in left} & {a.actor_id for a in right})
    assert len(left) == len(right) == 3
    duplicate = _actor(1).model_copy(
        update={
            "cookie": SecretStr(
                "__Host-aletheia_session=PRIVATE_SESSION_0; __Host-aletheia_csrf=PRIVATE_PROOF_1"
            )
        }
    )
    write([_actor(), duplicate])
    with pytest.raises(ValueError, match="distinct"):
        load_actors(path, _profile())
    with pytest.raises(ValidationError):
        _profile(operation_dispositions={"P01": "recipe"})
    with pytest.raises(ValidationError):
        _profile(operation_dispositions={f"P{n:02}": "recipe" for n in range(1, 39)})


def test_templates_never_evaluate_python_attributes_indexing_or_target_escape():
    with pytest.raises(CorrectnessFailure, match="invalid_path_template"):
        checked_path("/v1/workspaces/{actor.__class__}", {"actor": object()})
    with pytest.raises(CorrectnessFailure, match="invalid_path_binding"):
        checked_path("/v1/workspaces/{workspace_id}", {"workspace_id": "../../PRIVATE"})
    assert template(
        {"question": "$value"}, {"value": "__import__('os').system('DO_NOT_EXECUTE')"}
    ) == {"question": "__import__('os').system('DO_NOT_EXECUTE')"}
    with pytest.raises(ValidationError):
        Step(operation_id="P15", path="/v1/session", headers={"cookie": "PRIVATE_SHARED_ADMIN"})


def test_mutation_deadline_after_possible_commit_remains_timeout_and_unknown():
    committed = []
    journey = Journey(
        name="ack_lost",
        weight=1,
        roles=("viewer",),
        deadline_seconds=0.02,
        steps=(
            Step(
                operation_id="P13",
                method="POST",
                path="/v1/workspaces/{workspace_id}/projects/{project_id}/analyses",
                statuses=(202,),
                body={"synthetic": True},
                follow_job=True,
            ),
        ),
    )

    async def handler(request):
        committed.append(request.headers["idempotency-key"])
        await asyncio.sleep(0.5)
        return httpx2.Response(202, json={"workspace_id": "ws_0", "job_id": "durable_but_ack_lost"})

    result = _run(
        _profile(journey=journey, duration_seconds=0.01, arrivals_per_second=1.0),
        [_actor()],
        handler,
    )
    assert len(committed) == 1
    assert result["counts"]["timeout"] == result["counts"]["unknown_mutations"] == 1
    assert result["mutation_reconciliation"]["unresolved"] == 1
    assert (
        result["mutation_reconciliation"]["witnesses"][0]["request_key_sha256"]
        == hashlib.sha256(committed[0].encode()).hexdigest()
    )


def test_drain_pending_remains_in_positive_denominator_and_deadline_histogram():
    journey = Journey(
        name="long_read",
        weight=1,
        roles=("viewer",),
        deadline_seconds=5.0,
        steps=(
            Step(
                operation_id="P15",
                path="/v1/session",
                assertions=(Assertion(pointer="/ok", check="equals", value=True),),
            ),
        ),
    )

    async def handler(request):
        await asyncio.sleep(1)
        return httpx2.Response(200, json={"ok": True})

    result = _run(
        _profile(
            journey=journey,
            duration_seconds=0.003,
            drain_seconds=0.02,
            arrivals_per_second=1000.0,
            max_inflight=1,
            max_pending=3,
        ),
        [_actor()],
        handler,
    )
    assert result["counts"]["offered"] == result["measured_positive"]["offered"] == 3
    assert result["counts"]["pending"] == 2 and result["counts"]["interrupted"] == 1
    assert result["strata"][0]["deadline_inclusive"]["n"] == 3
    assert result["strata"][0]["pending"] == 2


def test_open_model_holds_pinned_window_and_uses_size_weights():
    actors = [_actor(i).model_copy(update={"size": size}) for i, size in enumerate(("S", "M", "L"))]

    async def handler(request):
        index = int(request.url.path.split("/")[3].split("_")[1])
        return httpx2.Response(
            200, json={"workspace_id": f"ws_{index}", "result_id": f"result_{index}"}
        )

    result = _run(
        _profile(
            duration_seconds=0.2,
            arrivals_per_second=500.0,
            max_inflight=8,
            size_weights={"S": 60, "M": 35, "L": 5},
        ),
        actors,
        handler,
    )
    sizes = result["workload"]["offered_size_counts"]
    assert sizes["S"] > sizes["M"] > sizes["L"] > 0
    assert result["elapsed_seconds"] >= 0.2
    assert sum(sizes.values()) == 100


def test_open_arrivals_and_final_window_recheck_clock_after_early_timer(monkeypatch):
    from aletheia_lab.hosted import capacity

    clock = [100.0]
    started = clock[0]
    offered = []

    async def early_sleep(delay):
        clock[0] += max(0.0001, delay - 0.015625)

    monkeypatch.setattr(capacity.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(capacity.asyncio, "sleep", early_sleep)
    profile = _profile(duration_seconds=0.2, arrivals_per_second=500.0, max_pending=128)
    harness = CapacityHarness(
        profile,
        [_actor()],
        transport=httpx2.MockTransport(lambda request: None),
        verify_principals=False,
    )
    harness._started = started
    original_offer = harness._offer

    def offer(actor, journey, due):
        assert clock[0] >= due
        offered.append(due)
        return original_offer(actor, journey, due)

    monkeypatch.setattr(harness, "_offer", offer)
    # The controlled sleep completes synchronously, so no real event loop or wall clock is needed.
    coroutine = harness._open()
    with pytest.raises(StopIteration):
        coroutine.send(None)
    assert len(offered) == 100
    assert clock[0] >= started + profile.duration_seconds
    assert harness.counts["planned"] == harness.counts["offered"] == 100


def test_positive_overload_is_rejected_even_when_custom_recipe_lists_503():
    journey = Journey(
        name="overload",
        weight=1,
        roles=("viewer",),
        deadline_seconds=0.1,
        steps=(
            Step(
                operation_id="P15",
                path="/v1/session",
                statuses=(503,),
                assertions=(Assertion(pointer="/code", check="nonempty"),),
            ),
        ),
    )

    def handler(request):
        return httpx2.Response(503, json={"code": "service_unavailable"})

    result = _run(
        _profile(journey=journey, duration_seconds=0.01, arrivals_per_second=1.0),
        [_actor()],
        handler,
    )
    assert result["counts"]["rejected"] == 1 and result["counts"].get("completed", 0) == 0
    assert result["measured_positive"]["correct_by_deadline"] == 0


@pytest.mark.parametrize(
    ("failure", "outcome", "completed_response_count"),
    [("rejected", "rejected", 1), ("timeout", "timeout", 0), ("transport", "failed", 1)],
)
def test_later_step_failure_keeps_original_response_accounting(
    failure, outcome, completed_response_count
):
    journey = Journey(
        name="two_step_accounting",
        weight=1,
        roles=("viewer",),
        deadline_seconds=1,
        steps=(
            Step(operation_id="P15", path="/v1/session", assertions=(
                Assertion(pointer="/workspace_id", check="equals", value="$workspace_id"),)),
            Step(operation_id="P15", path="/v1/session"),
        ),
    )
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx2.Response(200, json={"workspace_id": "ws_0"})
        if failure == "rejected":
            return httpx2.Response(503, json={"code": "service_unavailable"})
        if failure == "timeout":
            raise TimeoutError()
        raise httpx2.ConnectError("synthetic transport failure", request=request)

    result = _run(
        _profile(journey=journey, duration_seconds=0.01, arrivals_per_second=1), [_actor()], handler
    )
    assert calls == (3 if failure == "transport" else 2)
    assert result["counts"][outcome] == 1 and result["counts"]["terminal"] == 1
    assert result["strata"][0]["completed_response"]["n"] == completed_response_count
    assert result["strata"][0]["deadline_inclusive"]["n"] == 1
    assert result["measured_positive"]["offered"] == 1
    assert result["measured_positive"]["correct_by_deadline"] == 0
    assert result["primary_campaign_passed"] is False


@pytest.mark.parametrize(
    ("code", "recover"), [("cursor_invalid", True), ("invalid_request", False)]
)
def test_first_claim_page_restarts_once_on_catalog_generation_race(code, recover):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx2.Response(400, json={"code": code, "detail": "PRIVATE_UNEXPECTED_DETAIL"})
        return httpx2.Response(200, json={"workspace_id": "ws_0"})

    journey = Journey(
        name="claim_page",
        weight=1,
        roles=("viewer",),
        deadline_seconds=0.5,
        steps=(
            Step(
                operation_id="P16",
                path="/v1/workspaces/{workspace_id}/results/{result_id}/claims?limit=50",
                assertions=(
                    Assertion(pointer="/workspace_id", check="equals", value="$workspace_id"),
                ),
            ),
        ),
    )
    result = _run(
        _profile(journey=journey, duration_seconds=0.1, arrivals_per_second=10), [_actor()], handler
    )
    assert calls == (2 if recover else 1)
    assert result["http"].get("catalog_page_restarts", 0) == int(recover)
    assert result["counts"].get("completed", 0) == int(recover)
    if not recover:
        assert result["first_unexpected_http"]["known_problem_code"] == code
    assert "PRIVATE_UNEXPECTED_DETAIL" not in json.dumps(result)


def test_wrong_workspace_ack_never_becomes_an_admitted_tracked_job():
    journey = Journey(
        name="wrong_ack",
        weight=1,
        roles=("viewer",),
        deadline_seconds=0.1,
        steps=(
            Step(
                operation_id="P13",
                method="POST",
                path="/v1/workspaces/{workspace_id}/jobs",
                statuses=(202,),
                body={},
                follow_job=True,
            ),
        ),
    )

    def handler(request):
        return httpx2.Response(202, json={"workspace_id": "foreign_ws", "job_id": "valid_job"})

    result = _run(
        _profile(journey=journey, duration_seconds=0.01, arrivals_per_second=1.0),
        [_actor()],
        handler,
    )
    assert result["fatal_correctness_code"] == "receipt_scope_mismatch"
    assert result["counts"].get("jobs_admitted", 0) == 0


def test_job_update_resume_cursor_and_snapshot_resync_keep_one_original_action():
    polls = []
    journey = Journey(
        name="poll_resume",
        weight=1,
        roles=("viewer",),
        deadline_seconds=3.0,
        steps=(
            Step(
                operation_id="P13",
                method="POST",
                path="/v1/workspaces/{workspace_id}/jobs",
                statuses=(202,),
                body={},
                follow_job=True,
            ),
        ),
    )

    def handler(request):
        if request.method == "POST":
            return httpx2.Response(202, json={"workspace_id": "ws_0", "job_id": "job_one"})
        if request.url.path.endswith("/output"):
            return httpx2.Response(
                200, json={"workspace_id": "ws_0", "job_id": "job_one", "result": {"ok": True}}
            )
        assert request.url.path.endswith("/updates")
        polls.append(request.url.params.get("cursor"))
        terminal = len(polls) > 1
        return httpx2.Response(
            200,
            json={
                "workspace_id": "ws_0",
                "job_id": "job_one",
                "revision": 2 if terminal else 1,
                "cursor": "opaque+/cursor==",
                "disposition": "resync" if terminal else "snapshot",
                "job": {
                    "workspace_id": "ws_0",
                    "job_id": "job_one",
                    "status": "succeeded" if terminal else "running",
                },
                "poll_after_ms": None if terminal else 2000,
            },
        )

    result = _run(
        _profile(
            journey=journey,
            duration_seconds=0.01,
            arrivals_per_second=1.0,
            job_polling="updates",
            drain_seconds=3.5,
        ),
        [_actor()],
        handler,
    )
    assert polls == [None, "opaque+/cursor=="]
    assert (
        result["counts"]["completed"]
        == result["counts"]["jobs_admitted"]
        == result["counts"]["jobs_terminal"]
        == 1
    )
    assert result["http"]["job_poll_resync"] == 1


def test_negative_status_does_not_hide_foreign_data_inside_denial_body():
    journey = Journey(
        name="deny",
        weight=1,
        roles=("viewer",),
        deadline_seconds=0.1,
        expected="deny",
        steps=(
            Step(
                operation_id="P15",
                path="/v1/session",
                statuses=(404,),
                assertions=(Assertion(pointer="/code", check="equals", value="not_found"),),
            ),
        ),
    )

    def handler(request):
        return httpx2.Response(
            404,
            json={
                "schema_version": "product-api/v1-draft",
                "type": "urn:aletheia:problem:not-found",
                "title": "Request could not be completed",
                "status": 404,
                "code": "not_found",
                "detail": "Unavailable",
                "request_id": "req_" + "a" * 32,
                "foreign_data": "PRIVATE_FOREIGN_RESULT",
            },
        )

    result = _run(
        _profile(journey=journey, duration_seconds=0.01, arrivals_per_second=1.0),
        [_actor()],
        handler,
    )
    assert result["fatal_correctness_code"] == "unsafe_denial_payload"
    assert result["counts"].get("denied", 0) == 0 and "PRIVATE" not in json.dumps(result)
