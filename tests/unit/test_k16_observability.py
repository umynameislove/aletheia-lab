"""Bounded observations across real ASGI error paths and worker completion."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import threading
import time

import pytest

from aletheia_lab.hosted.application import AdapterLimits, HostedApplication
from aletheia_lab.hosted.observability import (
    BoundedJsonHandler,
    ObservedApplication,
    RuntimeTelemetry,
    SafeJsonFormatter,
    route_family,
    timed_job,
)
from aletheia_lab.hosted.operational_alerts import AlertEvaluator


def test_pool_observation_uses_only_live_public_fixed_stats_and_does_not_keep_owner_alive():
    import gc

    class Pool:
        def get_stats(self):
            return {
                "pool_size": 2,
                "pool_available": 1,
                "requests_waiting": 3,
                "PRIVATE_SECRET": "PRIVATE_VALUE",
                "conninfo": "PRIVATE_DSN",
            }

    telemetry = RuntimeTelemetry()
    pool = Pool()
    assert telemetry.bind_pool(pool)
    assert not telemetry.bind_pool(object())
    snapshot = telemetry.snapshot()
    assert snapshot["gauges"]["pool_waiting"] == 3
    assert snapshot["gauges"]["pool_size"] == 2
    assert "PRIVATE" not in json.dumps(snapshot)
    del pool
    gc.collect()
    assert "pool_size" not in telemetry.snapshot()["gauges"]


def test_loop_watch_observes_actual_blocking_delay_and_close_cancels_one_timer():
    async def exercise():
        telemetry = RuntimeTelemetry()
        loop = asyncio.get_running_loop()
        telemetry.watch_loop(loop)
        first = telemetry._loop_timer
        telemetry.watch_loop(loop)
        assert telemetry._loop_timer is first
        loop.call_later(0.95, time.sleep, 0.15)
        await asyncio.sleep(1.2)
        assert telemetry.snapshot()["gauges"]["event_loop_lag_ms"] >= 20
        current = telemetry._loop_timer
        telemetry.close()
        assert current.cancelled() and telemetry._loop_timer is None

    asyncio.run(exercise())


def _request(app, path, *, headers=(), method="GET"):
    async def exercise():
        messages = []

        async def receive():
            raise AssertionError("Observability must not read a request body.")

        async def send(message):
            messages.append(message)

        await app(
            {
                "type": "http",
                "path": path,
                "method": method,
                "headers": headers,
                "query_string": b"question=PRIVATE_QUERY&token=PRIVATE_TOKEN",
            },
            receive,
            send,
        )
        return messages

    return asyncio.run(exercise())


def test_actual_adapter_error_response_and_logs_never_copy_private_input(caplog):
    secret = "PRIVATE_COOKIE_SOURCE_QUESTION_PATH\nforged_log_line"

    class Backend:
        async def load_session(self, cookie):
            raise RuntimeError(secret)

    telemetry = RuntimeTelemetry(release_id="a" * 64)
    app = ObservedApplication(
        HostedApplication(Backend(), limits=AdapterLimits(1, 10000, 4096)), telemetry=telemetry
    )
    with caplog.at_level(logging.INFO, logger="aletheia.telemetry"):
        messages = _request(
            app,
            "/v1/session",
            headers=[
                (b"cookie", b"session=PRIVATE_COOKIE"),
                (b"x-request-id", b"req_" + b"c" * 32),
                (b"x-user-id", b"PRIVATE_USER"),
            ],
        )
    assert messages[0]["status"] == 500
    actual = dict(messages[0]["headers"])[b"x-request-id"].decode()
    assert actual != "req_" + "c" * 32
    assert json.loads(messages[1]["body"])["request_id"] == actual
    formatted = [json.loads(SafeJsonFormatter(telemetry).format(row)) for row in caplog.records]
    assert formatted[-1]["request_id"] == actual and formatted[-1]["http_status"] == 500
    wire = json.dumps(formatted) + json.dumps(telemetry.snapshot()) + messages[1]["body"].decode()
    assert all(
        word not in wire
        for word in ("PRIVATE_", "forged_log_line", "cookie", "question=", "token=")
    )
    assert formatted[-1]["operation"] == "session"


def test_formatter_ignores_args_exception_and_arbitrary_extras_without_stringifying():
    class Private:
        def __str__(self):
            raise AssertionError("Private arguments must never be rendered.")

    row = logging.LogRecord(
        "private-logger-PRIVATE",
        logging.ERROR,
        "/PRIVATE/PATH",
        1,
        Private(),
        (Private(),),
        (ValueError, ValueError("PRIVATE_SECRET"), None),
    )
    row.request_id, row.job_id, row.raw_source = "bad\nPRIVATE", "job_safe", "PRIVATE"
    data = SafeJsonFormatter(RuntimeTelemetry()).format(row)
    assert "\n" not in data and "PRIVATE" not in data
    assert json.loads(data)["event"] == "runtime.diagnostic"
    assert json.loads(data)["job_id"] == "job_safe"
    assert "request_id" not in json.loads(data)


def test_many_private_object_paths_have_one_fixed_metric_dimension():
    async def missing(scope, receive, send):
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    telemetry = RuntimeTelemetry()
    app = ObservedApplication(missing, telemetry=telemetry)
    for i in range(250):
        _request(app, f"/v1/workspaces/PRIVATE_WORKSPACE_{i}/results/PRIVATE_RESULT_{i}")
    snapshot = telemetry.snapshot()
    assert len(snapshot["items"]) == 1 and snapshot["items"][0]["count"] == 250
    assert snapshot["items"][0]["operation"] == "results"
    assert "PRIVATE" not in json.dumps(snapshot)
    assert route_family("/v1/workspaces/x/attacker_label/y") == "workspaces"
    assert route_family("x" * 100000) == "other"


def test_every_http_counter_is_kept_while_success_logs_are_sampled(caplog):
    telemetry = RuntimeTelemetry()
    with caplog.at_level(logging.INFO, logger="aletheia.telemetry"):
        for _ in range(205):
            _request(
                ObservedApplication(
                    HostedApplication(None, limits=AdapterLimits(1, 10000, 4096)),
                    telemetry=telemetry,
                ),
                "/health/live",
            )
    assert telemetry.snapshot()["items"][0]["count"] == 205
    assert len(caplog.records) == 3
    assert telemetry.snapshot()["http_success_log_sample_every"] == 100


@pytest.mark.parametrize("what", ["cancel", "send", "partial"])
def test_cancellation_transport_errors_and_partial_bodies_are_not_reported_complete(what):
    telemetry = RuntimeTelemetry()

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        if what == "cancel":
            raise asyncio.CancelledError
        await send({"type": "http.response.body", "body": b"part", "more_body": True})

    async def exercise():
        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            if what == "send" and message["type"] == "http.response.body":
                raise OSError("PRIVATE transport exception")

        await ObservedApplication(app, telemetry=telemetry)(
            {"type": "http", "path": "/v1/workspaces/w/results/r", "method": "GET"}, receive, send
        )

    if what == "cancel":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(exercise())
    elif what == "send":
        with pytest.raises(OSError):
            asyncio.run(exercise())
    else:
        asyncio.run(exercise())
    assert (
        telemetry.snapshot()["items"][0]["outcome"]
        == {"cancel": "cancelled", "send": "transport_error", "partial": "interrupted"}[what]
    )


def test_log_writer_queue_is_bounded_under_real_blocked_output():
    entered, release = threading.Event(), threading.Event()

    class Blocked:
        def write(self, value):
            entered.set()
            assert release.wait(5)

        def flush(self):
            pass

    handler = BoundedJsonHandler(RuntimeTelemetry(), Blocked(), capacity=4)
    assert entered.wait(1)
    row = logging.LogRecord("aletheia.telemetry", logging.INFO, "", 1, "http.completed", (), None)
    started = time.monotonic()
    for _ in range(1000):
        handler.emit(row)
    elapsed = time.monotonic() - started
    stats = handler.stats()
    release.set()
    handler.close()
    assert elapsed < 1 and stats["queued"] == stats["capacity"] == 4
    assert stats["dropped"] == 996 and not handler.stats()["writer_alive"]


def test_periodic_metric_export_contains_fixed_counters_and_no_private_labels():
    telemetry, stream = RuntimeTelemetry(), io.StringIO()
    telemetry.increment("job_poll_requests", 3)
    telemetry.set_gauge("pool_waiting", 2)
    handler = BoundedJsonHandler(telemetry, stream)
    handler.close()
    values = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert values and values[0]["event"] == "metric.snapshot"
    assert values[0]["counters"] == {"job_poll_requests": 3}
    assert values[0]["gauges"]["pool_waiting"] == 2
    assert values[0]["log_buffer"]["capacity"] == 256
    for name in ("job_private", "request_id", "user_id", "workspace_id"):
        with pytest.raises(ValueError):
            telemetry.set_gauge(name, 1)
        with pytest.raises(ValueError):
            telemetry.increment(name)


def test_job_settled_flag_is_not_labelled_a_business_success(caplog):
    with caplog.at_level(logging.INFO, logger="aletheia.telemetry"):
        with timed_job("job_safe", "analyze") as result:
            result.settled = True
        with timed_job("job_safe2", "analyze"):
            pass
    assert [r.outcome for r in caplog.records] == ["settled", "unsettled"]
    assert all("success" not in r.__dict__ for r in caplog.records)


def test_actual_readiness_failure_burn_alert_and_consumer_recovery():
    class Backend:
        ready = True

        async def is_ready(self):
            return self.ready

    backend, telemetry, alerts = Backend(), RuntimeTelemetry(), AlertEvaluator()
    app = ObservedApplication(
        HostedApplication(backend, limits=AdapterLimits(1, 10000, 4096)), telemetry=telemetry
    )
    assert not alerts.evaluate(telemetry.snapshot(), observed_at=0)["window"]["valid"]
    backend.ready = False
    for _ in range(20):
        assert _request(app, "/health/ready")[0]["status"] == 503
    fired = alerts.evaluate(telemetry.snapshot(), observed_at=10)
    assert fired["window"]["http_requests"] == fired["window"]["http_server_failures"] == 20
    assert fired["window"]["error_budget_burn"] == 100
    assert fired["transitions"] == [{"code": "http_burn", "state": "firing"}]
    assert (
        fired["active"][0]["owner"] == "operator" and "readiness" in fired["active"][0]["runbook"]
    )
    # Operator recovery action restores the backend dependency, not the alert counter.
    backend.ready = True
    for _ in range(20):
        assert _request(app, "/health/ready")[0]["status"] == 200
    recovered = alerts.evaluate(telemetry.snapshot(), observed_at=20)
    assert (
        recovered["window"]["http_requests"] == 20
        and recovered["window"]["http_server_failures"] == 0
    )
    assert not recovered["active"] and recovered["transitions"] == [
        {"code": "http_burn", "state": "resolved"}
    ]
    # A new process identity is a baseline reset, not a fabricated rate spike.
    fresh = alerts.evaluate(RuntimeTelemetry().snapshot(), observed_at=30)
    assert not fresh["window"]["valid"] and fresh["window"]["http_requests"] == 0


def test_worker_transaction_logs_inherit_task_local_job_and_reset_without_raw_context():
    from aletheia_lab.hosted.action_audit import report_outcome

    captured = []
    formatter = SafeJsonFormatter(RuntimeTelemetry(role="worker"))

    class Capture(logging.Handler):
        def emit(self, record):
            captured.append(json.loads(formatter.format(record)))

    logger = logging.getLogger("aletheia.action")
    handler = Capture()
    old = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    async def run():
        entered = asyncio.Event()

        async def first():
            with timed_job("job_first", "confirm"):
                entered.set()
                await asyncio.sleep(0)
                report_outcome("rolled_back")

        async def second():
            await entered.wait()
            with timed_job("job_second", "analyze"):
                report_outcome("outcome_unknown")

        await asyncio.gather(first(), second())
        report_outcome("rejected")

    try:
        asyncio.run(run())
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old)
    assert {(r["action_outcome"], r.get("job_id")) for r in captured} == {
        ("rolled_back", "job_first"),
        ("outcome_unknown", "job_second"),
        ("rejected", None),
    }


@pytest.mark.parametrize("reason", ["jobs_schema", "polling_retention"])
def test_readiness_diagnostic_keeps_only_fixed_reason_and_operator_cli_redacts(
    capsys, monkeypatch, reason
):
    from aletheia_lab.hosted.operator_observation import main

    row = logging.LogRecord(
        "aletheia_lab.hosted.managed_runtime",
        logging.WARNING,
        "",
        1,
        "hosted_readiness_unavailable reason=%s",
        (reason,),
        None,
    )
    result = json.loads(SafeJsonFormatter(RuntimeTelemetry()).format(row))
    assert result["event"] == "readiness.unavailable" and result["reason"] == reason
    row.args = ("PRIVATE_DIAGNOSTIC",)
    assert "PRIVATE" not in SafeJsonFormatter(RuntimeTelemetry()).format(row)
    monkeypatch.setattr(
        "sys.argv",
        [
            "observe",
            "snapshot",
            "--schema",
            "bad\nPRIVATE",
            "--database-env",
            "K16_MISSING_OPERATOR_ENV",
        ],
    )
    monkeypatch.delenv("K16_MISSING_OPERATOR_ENV", raising=False)
    assert main() == 1
    assert json.loads(capsys.readouterr().err) == {"error": "operator_observation_unavailable"}


def test_counter_reset_does_not_fabricate_recovery_and_existing_log_loss_is_visible():
    first, alerts = RuntimeTelemetry(), AlertEvaluator()
    alerts.evaluate(first.snapshot(), observed_at=0)
    for _ in range(20):
        first.record(
            kind="http",
            operation="health",
            method="GET",
            status="5xx",
            outcome="complete",
            seconds=0.01,
        )
    assert alerts.evaluate(first.snapshot(), observed_at=1)["active"]
    reset = alerts.evaluate(RuntimeTelemetry().snapshot(), observed_at=2)
    assert reset["window"]["baseline_reset"] and not reset["window"]["valid"]
    assert not any(t["state"] == "resolved" for t in reset["transitions"])
    fresh = AlertEvaluator().evaluate(first.snapshot(), observed_at=3, log_dropped=5)
    assert [a["code"] for a in fresh["active"]] == ["log_drops"]
