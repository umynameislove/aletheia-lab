"""HTTP admission over a synthetic shared counter; no services or IP headers."""

import asyncio
import logging

import pytest
from pydantic import SecretStr
from test_k03_auth_abuse import SyntheticAdmissionStore

from aletheia_lab.hosted_auth.abuse import AuthAdmission, RateDecision, RatePolicy
from aletheia_lab.hosted_auth.admission_http import AuthAdmissionApplication, admission_key
from aletheia_lab.hosted_auth.settings import AuthConfigurationRejected
from aletheia_lab.hosted_contract.session_capabilities import Problem


class Effect:
    def __init__(self):
        self.calls = self.reads = 0

    async def __call__(self, scope, receive, send):
        self.calls += 1
        await receive()
        self.reads += 1
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})


async def _request(app, *, host="192.0.2.1", port=1, path="/v1/auth/start", headers=(), method="GET"):
    events, reads = [], []

    async def receive():
        reads.append(1)
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(event):
        events.append(event)

    await app({"type": "http", "scheme": "https", "method": method, "path": path,
               "headers": headers, "client": (host, port) if host is not None else None}, receive, send)
    return events, reads


def _system(store=None):
    store = store or SyntheticAdmissionStore()
    app = Effect()
    gate = AuthAdmission(store, RatePolicy(3, 1, 10), timeout_seconds=0.02)
    return AuthAdmissionApplication(app, gate, subject_key=SecretStr("synthetic-rate-key-" + "x" * 43), timeout_seconds=1.0), app, store


def test_shared_http_gate_denies_before_body_or_business_and_sends_safe_retry_after():
    async def scenario():
        store = SyntheticAdmissionStore()
        first, app1, _ = _system(store)
        second, app2, _ = _system(store)
        assert (await _request(first))[0][0]["status"] == 204
        events, reads = await _request(second, port=54321,
            headers=[(b"x-forwarded-for", b"198.51.100.2"), (b"cookie", b"forged-new-cookie")])
        assert events[0]["status"] == 429 and reads == []
        value = Problem.model_validate_json(events[1]["body"])
        assert value.code == "rate_limited"
        assert "192.0.2.1" not in value.model_dump_json()
        headers = dict(events[0]["headers"])
        assert headers[b"retry-after"] == b"10" and headers[b"cache-control"] == b"no-store"
        assert b"set-cookie" not in headers
        assert app1.calls == 1 and app2.calls == app2.reads == 0
    asyncio.run(scenario())


def test_global_budget_cannot_be_bypassed_by_rotating_source_addresses():
    async def scenario():
        guard, app, _ = _system()
        for suffix in (1, 2, 3):
            assert (await _request(guard, host=f"192.0.2.{suffix}"))[0][0]["status"] == 204
        events, reads = await _request(guard, host="192.0.2.4")
        assert events[0]["status"] == 429 and reads == [] and app.calls == 3
    asyncio.run(scenario())


def test_mapped_ipv4_and_ipv6_spellings_cannot_create_new_source_budgets():
    async def scenario():
        guard, app, _ = _system()
        assert (await _request(guard))[0][0]["status"] == 204
        assert (await _request(guard, host="::ffff:192.0.2.1"))[0][0]["status"] == 429
        assert (await _request(guard, host="2001:db8::1"))[0][0]["status"] == 204
        assert (await _request(guard, host="2001:0db8:0:0:0:0:0:1"))[0][0]["status"] == 429
        assert app.calls == 2
    asyncio.run(scenario())


@pytest.mark.parametrize("host", [None, "not-an-ip", "localhost", "192.0.2.1%scope", "x" * 65])
def test_unknown_trusted_peer_fails_closed_without_forwarded_header_fallback(host):
    async def scenario():
        guard, app, store = _system()
        events, reads = await _request(guard, host=host, headers=[(b"x-forwarded-for", b"192.0.2.1")])
        assert events[0]["status"] == 503 and reads == []
        assert store.calls == app.calls == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["/v1/auth/callback", "/v1/auth/renew", "/v1/auth/logout", "/v1/auth/logout-all"])
def test_all_auth_paths_consume_their_own_operation_budget(path):
    async def scenario():
        guard, _, store = _system()
        assert (await _request(guard, path=path))[0][0]["status"] == 204
        assert (await _request(guard, path=path))[0][0]["status"] == 429
        assert (await _request(guard, path="/v1/auth/start"))[0][0]["status"] == 204
        assert store.calls == 3
    asyncio.run(scenario())


def test_store_outage_never_calls_business_or_exposes_diagnostics():
    class Store:
        async def consume(self, *args):
            raise RuntimeError("synthetic-private-database-value")

    async def scenario():
        guard, app, _ = _system(Store())
        events, reads = await _request(guard)
        assert events[0]["status"] == 503 and reads == [] and app.calls == 0
        assert b"synthetic-private-database-value" not in events[1]["body"]
    asyncio.run(scenario())


def test_cancellation_is_not_replaced_by_an_auth_response():
    class Store:
        async def consume(self, *args):
            raise asyncio.CancelledError()

    async def scenario():
        guard, app, _ = _system(Store())
        with pytest.raises(asyncio.CancelledError):
            await _request(guard)
        assert app.calls == 0
    asyncio.run(scenario())


def test_head_denial_has_no_body_and_unrelated_routes_do_not_use_auth_quota():
    async def scenario():
        guard, app, store = _system()
        await _request(guard)
        events, _ = await _request(guard, method="HEAD")
        assert events[0]["status"] == 429 and events[1]["body"] == b""
        assert (await _request(guard, path="/v1/session"))[0][0]["status"] == 204
        assert store.calls == 2 and app.calls == 2
    asyncio.run(scenario())


@pytest.mark.parametrize("value", [None, "", "short", "x" * 32 + "\n", " " * 43, "x" * 4097])
def test_shared_key_has_no_fallback_and_invalid_values_fail_closed(value):
    with pytest.raises(AuthConfigurationRejected):
        admission_key(value)


def test_total_deadline_includes_admission_and_business_and_never_sends_two_responses():
    class SlowStore:
        async def consume(self, *args):
            await asyncio.sleep(0.01)
            return RateDecision(True, 0)

    class ImmediateStore:
        async def consume(self, *args):
            return RateDecision(True, 0)

    async def scenario():
        started = []

        async def stalled_business(scope, receive, send):
            await asyncio.Event().wait()

        gate = AuthAdmission(SlowStore(), RatePolicy(3, 1, 60), timeout_seconds=1.0)
        guard = AuthAdmissionApplication(stalled_business, gate, subject_key=SecretStr("k" * 43), timeout_seconds=0.02)
        events, reads = await _request(guard)
        assert events[0]["status"] == 503 and reads == []

        async def stalled_send(scope, receive, send):
            started.append(1)
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await asyncio.Event().wait()

        immediate = AuthAdmission(ImmediateStore(), RatePolicy(3, 1, 60), timeout_seconds=1.0)
        guard = AuthAdmissionApplication(stalled_send, immediate, subject_key=SecretStr("k" * 43), timeout_seconds=0.1)
        with pytest.raises(TimeoutError):
            await _request(guard)
        assert started == [1]
    asyncio.run(scenario())


def test_rejection_audit_uses_only_safe_fields_and_same_server_request_id(caplog):
    class Store:
        async def consume(self, *args):
            raise RuntimeError("synthetic-private-driver-secret")

    async def scenario():
        guard, _, _ = _system(Store())
        with caplog.at_level(logging.INFO, logger="aletheia.auth.admission"):
            events, _ = await _request(guard, headers=[(b"cookie", b"synthetic-private-cookie"),
                (b"authorization", b"Bearer synthetic-private-token")])
        record = caplog.records[-1]
        value = Problem.model_validate_json(events[1]["body"])
        assert record.getMessage() == "auth_admission_rejected"
        assert record.request_id == value.request_id
        assert record.auth_operation == "login_start" and record.auth_outcome == "service_unavailable"
        assert record.http_status == 503 and record.exc_info is None
        assert "synthetic-private" not in repr(record.__dict__)
        assert "192.0.2.1" not in repr(record.__dict__)
    asyncio.run(scenario())
