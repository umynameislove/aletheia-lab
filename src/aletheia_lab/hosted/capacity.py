"""Bounded real-HTTP capacity scheduler with explicit offered-action accounting."""

from __future__ import annotations

import asyncio
import json
import math
import random
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import quote

import httpx2
from pydantic import ValidationError

from aletheia_lab.hosted_contract.session_capabilities import Problem
from aletheia_lab.project.identity import content_hasher

from .capacity_assertions import CorrectnessFailure, assert_json, checked_path, pointer, template
from .capacity_models import Actor, Journey, Profile, Step
from .capacity_observations import Histogram as Histogram
from .capacity_observations import Offered, Stratum, build_report, coverage_sample, resource_sample
from .capacity_preflight import verify_actors
from .capacity_sessions import CapacitySessionRejected, CapacitySessions


class ActionRejected(RuntimeError):
    pass


class OutcomeUnknown(RuntimeError):
    pass


class ActionConflict(RuntimeError):
    pass


async def _wait_until(deadline: float) -> None:
    # asyncio timers can wake one clock-resolution early on Windows.
    while (remaining := deadline - time.monotonic()) > 0:
        await asyncio.sleep(remaining)


@dataclass
class _JobPoll:
    cursor: str | None = None
    revision: int = 0
    cached: dict[str, Any] | None = None


@dataclass
class _Execution:
    bindings: dict[str, object]
    rng: random.Random
    response: bool = False


class CapacityHarness:
    def __init__(
        self,
        profile: Profile,
        actors: list[Actor],
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        verify_principals: bool = True,
    ) -> None:
        self.profile, self.actors, self.transport = profile, actors, transport
        if not actors or len(actors) > 100000 or not verify_principals and transport is None:
            raise ValueError(
                "Bounded actors and mandatory real-HTTP principal verification required."
            )
        self._verify_principals = verify_principals
        self._preflight: dict[str, object] = {"verified_principals": 0, "status": "not_run"}
        self._started_at: str | None = None
        self.sessions = CapacitySessions(profile, actors)
        self._profile_identity = profile.fingerprint()
        self.counts: Counter[str] = Counter()
        self.strata: dict[tuple[str, str, str], Stratum] = {}
        self.http: Counter[str] = Counter()
        self.coverage: list[dict[str, object]] = []
        self.last: dict[str, dict[str, float]] = {
            n: {} for n in ("intended", "started", "completing")
        }
        self.waiting: dict[int, tuple[str, float]] = {}
        self.actual_operations: Counter[str] = Counter()
        self._stop = asyncio.Event()
        self._active = 0
        self._peak = 0
        self._sequence = 0
        self._fatal: str | None = None
        self._unexpected_http: dict[str, str | int] | None = None
        self._started = 0.0
        self._queue: asyncio.Queue[Offered | None] = asyncio.Queue(maxsize=profile.max_pending)
        self._resources: list[dict[str, object]] = []
        self._closed_done: dict[int, asyncio.Future[None]] = {}
        self._sent: set[int] = set()
        self._mutation_inflight: dict[int, dict[str, object]] = {}
        self._unknown_mutation_witnesses: list[dict[str, object]] = []
        self._offered_sizes: Counter[str] = Counter()
        self._offered_roles: Counter[str] = Counter()
        self._size_actors = {s: [a for a in actors if a.size == s] for s in ("S", "M", "L")}
        total_weight = sum(profile.size_weights.values())
        self._cadence = {
            s: profile.think_seconds
            * (len(group) / len(actors))
            / (profile.size_weights[s] / total_weight)
            for s, group in self._size_actors.items()
            if group
        }

    def _choose(self, actor: Actor, rng: random.Random) -> Journey:
        eligible = [
            j for j in self.profile.journeys if actor.role in j.roles and actor.size in j.sizes
        ]
        if not eligible:
            raise ValueError("Actor has no authorized journey.")
        return rng.choices(eligible, weights=[j.weight for j in eligible], k=1)[0]

    def _offer(self, actor: Actor, journey: Journey, due: float) -> Offered:
        self._sequence += 1
        measured = due >= self._started + self.profile.warmup_seconds
        self.counts["offered"] += 1
        self._offered_sizes[actor.size] += 1
        self._offered_roles[actor.role] += 1
        self.counts["measured_offered" if measured else "warmup_offered"] += 1
        self.last["intended"][actor.actor_id] = due
        if measured:
            self.strata.setdefault(
                (journey.name, actor.size, journey.expected), Stratum()
            ).offered += 1
        return Offered(actor, journey, due, self._sequence, measured)

    def _finish(self, item: Offered, outcome: str, elapsed: float, *, response: bool) -> None:
        self.counts["terminal"] += 1
        self.counts[outcome] += 1
        if item.measured:
            key = item.journey.name, item.actor.size, item.journey.expected
            stratum = self.strata.setdefault(key, Stratum())
            stratum.outcomes[outcome] += 1
            stratum.inclusive.add(
                max(elapsed, item.journey.deadline(item.actor.size)) if not response else elapsed
            )
            if response:
                stratum.responses.add(elapsed)
        if outcome in ("completed", "denied"):
            self.last["completing"][item.actor.actor_id] = time.monotonic()
        future = self._closed_done.get(item.sequence)
        if future is not None and not future.done():
            future.set_result(None)

    def _drop(self, item: Offered, reason: str) -> None:
        self.counts["drop_" + reason] += 1
        self._finish(item, "dropped", 0.0, response=False)

    def _note_unknown_mutation(self, sequence: int) -> None:
        witness = self._mutation_inflight.pop(sequence, None)
        if witness is not None:
            self.counts["unknown_mutations"] += 1
            if len(self._unknown_mutation_witnesses) < 100:
                self._unknown_mutation_witnesses.append(witness)

    async def _request(
        self,
        client: httpx2.AsyncClient,
        item: Offered,
        *,
        path: str,
        method: str = "GET",
        body: object = None,
        headers: dict[str, str] | None = None,
        operation: str,
        binary: bool = False,
        _retry: bool = False,
    ) -> tuple[int, Any]:
        p, a = self.profile, self.sessions.current(item.actor)
        actual_headers = {
            "host": p.host,
            "origin": p.origin,
            "cookie": a.cookie.get_secret_value(),
            "x-csrf-token": a.csrf.get_secret_value(),
            "accept": "application/json",
        }
        actual_headers.update(headers or {})
        self.http["requests"] += 1
        self.actual_operations[operation] += 1
        if item.sequence not in self._sent:
            self._sent.add(item.sequence)
            self.counts["sent"] += 1
        content = (
            None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        )
        if content is not None:
            actual_headers.setdefault("content-type", "application/json")
            self.http["request_body_bytes"] += len(content)
        if method not in ("GET", "HEAD"):
            signature = content_hasher(path.encode() + b"\0" + (content or b"")).hexdigest()[:16]
            actual_headers["idempotency-key"] = (
                f"k17-{p.run_label}-{p.shard_index}-{item.sequence}-{operation}-{signature}"
            )
            self._mutation_inflight[item.sequence] = {
                "action_sequence": item.sequence,
                "operation_id": operation,
                "workspace_id": a.values.get("workspace_id"),
                "request_key_sha256": content_hasher(
                    actual_headers["idempotency-key"].encode()
                ).hexdigest(),
            }
        started = time.monotonic()
        # Shared connection pool; identity is always an explicit per-request header,
        # never the client's mutable cookie jar or a shared administrator session.
        try:
            result = await self._response(
                client,
                item,
                path=path,
                method=method,
                headers=actual_headers,
                content=content,
                binary=binary,
                started=started,
            )
            if result[0] < 500:
                self._mutation_inflight.pop(item.sequence, None)
            return result
        except httpx2.TransportError:
            if not _retry:
                self.http["transport_retries_same_key"] += 1
                await asyncio.sleep(0.02 + (item.sequence % 50) / 1000)
                return await self._request(
                    client,
                    item,
                    path=path,
                    method=method,
                    body=body,
                    headers=headers,
                    operation=operation,
                    binary=binary,
                    _retry=True,
                )
            if method not in ("GET", "HEAD"):
                self.http["unresolved_mutation_transport_failures"] += 1
                raise OutcomeUnknown() from None
            raise

    async def _response(
        self,
        client: httpx2.AsyncClient,
        item: Offered,
        *,
        path: str,
        method: str,
        headers: dict[str, str],
        content: bytes | None,
        binary: bool,
        started: float,
    ) -> tuple[int, Any]:
        async with client.stream(method, path, headers=headers, content=content) as response:
            self.http[f"status_{response.status_code}"] += 1
            data, digest, size = bytearray(), content_hasher(), 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > self.profile.max_response_bytes:
                    raise CorrectnessFailure("response_bound_exceeded")
                digest.update(chunk)
                if not binary:
                    data.extend(chunk)
            self.http["response_body_bytes"] += size
            self.http["response_completed"] += 1
            self.http["response_duration_microseconds"] += int((time.monotonic() - started) * 1e6)
            if binary:
                return response.status_code, {"byte_size": size, "sha256": digest.hexdigest()}
            try:
                decoded = json.loads(data) if data else None
            except (ValueError, UnicodeError):
                raise CorrectnessFailure("invalid_json_response") from None
            return response.status_code, decoded

    def _acknowledge(self, item: Offered, receipt: object, bindings: dict[str, object]) -> str:
        if not isinstance(receipt, dict) or not isinstance(receipt.get("job_id"), str):
            raise CorrectnessFailure("ack_missing_durable_job")
        bindings["job_id"] = receipt["job_id"]
        if receipt.get("workspace_id") != bindings["workspace_id"]:
            raise CorrectnessFailure("receipt_scope_mismatch")
        path = checked_path("/v1/workspaces/{workspace_id}/jobs/{job_id}", bindings)
        self.counts["jobs_admitted"] += 1
        self.waiting[item.sequence] = (
            item.actor.actor_id,
            item.due + item.journey.deadline(item.actor.size),
        )
        return path

    def _poll_path(self, path: str, cursor: str | None) -> str:
        if self.profile.job_polling == "updates":
            return path + "/updates" + ("?cursor=" + quote(cursor, safe="") if cursor else "")
        return path

    @staticmethod
    def _job_scope(value: object, bindings: dict[str, object], code: str) -> dict[str, Any]:
        if (
            not isinstance(value, dict)
            or value.get("workspace_id") != bindings["workspace_id"]
            or value.get("job_id") != bindings["job_id"]
        ):
            raise CorrectnessFailure(code)
        return value

    def _job_update(
        self,
        state: dict[str, Any],
        poll: _JobPoll,
        bindings: dict[str, object],
        rng: random.Random,
        delay: float,
    ) -> tuple[dict[str, Any], float]:
        revision, cursor = state.get("revision"), state.get("cursor")
        if (
            type(revision) is not int
            or revision < poll.revision
            or not isinstance(cursor, str)
            or not 1 <= len(cursor) <= 4096
        ):
            raise CorrectnessFailure("job_update_order_or_cursor")
        disposition = state.get("disposition")
        if disposition == "unchanged":
            if poll.cached is None or state.get("job") is not None or revision != poll.revision:
                raise CorrectnessFailure("job_update_unchanged_without_snapshot")
        elif disposition in ("snapshot", "resync") and isinstance(state.get("job"), dict):
            poll.cached = state["job"]
            self.http["job_poll_" + disposition] += 1
        else:
            raise CorrectnessFailure("invalid_job_update_snapshot")
        poll.revision, poll.cursor = revision, cursor
        poll_ms = state.get("poll_after_ms")
        if poll_ms is not None:
            if type(poll_ms) is not int or not 2000 <= poll_ms <= 3500:
                raise CorrectnessFailure("invalid_poll_delay")
            delay = poll_ms / 1000 + rng.uniform(0, 0.25)
        return self._job_scope(poll.cached, bindings, "job_update_nested_scope_mismatch"), delay

    async def _job_output(
        self,
        client: httpx2.AsyncClient,
        item: Offered,
        path: str,
        terminal: str,
        bindings: dict[str, object],
    ) -> Any:
        self.counts["jobs_terminal"] += 1
        self.counts["job_" + terminal] += 1
        if terminal != "succeeded":
            raise CorrectnessFailure("job_terminal_not_successful")
        status, output = await self._request(client, item, path=path + "/output", operation="P20")
        if status != 200:
            raise CorrectnessFailure("successful_job_output_unavailable")
        return self._job_scope(output, bindings, "job_output_scope_mismatch")

    async def _follow(
        self,
        client: httpx2.AsyncClient,
        item: Offered,
        receipt: object,
        bindings: dict[str, object],
        rng: random.Random,
    ) -> Any:
        path, poll = self._acknowledge(item, receipt, bindings), _JobPoll()
        try:
            while True:
                if self._stop.is_set():
                    raise CorrectnessFailure("peer_correctness_stop")
                status, state = await self._request(
                    client, item, path=self._poll_path(path, poll.cursor), operation="P20"
                )
                delay = self.profile.poll_seconds * rng.uniform(1.0, 1.2)
                if status == 410 and self.profile.job_polling == "updates":
                    poll.cursor = None
                    self.http["poll_cursor_expired_resync"] += 1
                    await asyncio.sleep(delay)
                    continue
                if status in (429, 503):
                    # Poll backoff preserves the existing ACK and original action deadline.
                    self.http["poll_rejections"] += 1
                elif status != 200 or not isinstance(state, dict):
                    raise CorrectnessFailure("acknowledged_job_unavailable")
                else:
                    state = self._job_scope(state, bindings, "job_scope_mismatch")
                    if self.profile.job_polling == "updates":
                        state, delay = self._job_update(state, poll, bindings, rng, delay)
                    terminal = state.get("status")
                    if terminal in ("succeeded", "failed", "cancelled", "expired"):
                        return await self._job_output(client, item, path, terminal, bindings)
                await asyncio.sleep(delay)
        finally:
            self.waiting.pop(item.sequence, None)

    @staticmethod
    def _step_headers(step: Step, bindings: dict[str, object]) -> dict[str, str]:
        headers = template(step.headers, bindings)
        for name, value in headers.items():
            if (
                name.lower() == "if-match"
                and type(value) is int
                and 0 <= value <= 9223372036854775806
            ):
                headers[name] = f'"{value}"'
            elif not isinstance(value, str):
                raise CorrectnessFailure("invalid_header_binding")
        return cast(dict[str, str], headers)

    async def _step_request(
        self,
        client: httpx2.AsyncClient,
        item: Offered,
        step: Step,
        bindings: dict[str, object],
        headers: dict[str, str],
        path: str,
    ) -> tuple[int, Any]:
        status, data = await self._request(
            client,
            item,
            path=path,
            method=step.method,
            body=template(step.body, bindings),
            headers=headers,
            operation=step.operation_id,
        )
        # Restart only a first catalog page, under the unchanged enclosing deadline.
        if (
            step.method == "GET"
            and step.operation_id in ("P16", "P18")
            and "cursor=" not in path
            and status == 400
            and isinstance(data, dict)
            and data.get("code") == "cursor_invalid"
        ):
            self.http["catalog_page_restarts"] += 1
            status, data = await self._request(
                client,
                item,
                path=path,
                method=step.method,
                body=None,
                headers=headers,
                operation=step.operation_id,
            )
        return status, data

    def _step_status(
        self, item: Offered, step: Step, status: int, data: object, headers: dict[str, str]
    ) -> None:
        if status == 412 and any(k.lower() == "if-match" for k in headers):
            raise ActionConflict()
        if status in (429, 503) or status >= 500:
            raise ActionRejected()
        if status not in step.statuses:
            if self._unexpected_http is None:
                self._unexpected_http = {
                    "journey": item.journey.name,
                    "operation_id": step.operation_id,
                    "status": status,
                    "action_sequence": item.sequence,
                }
                if isinstance(data, dict) and data.get("code") in (
                    "cursor_invalid",
                    "cursor_expired",
                    "invalid_request",
                    "not_found",
                ):
                    self._unexpected_http["known_problem_code"] = data["code"]
            raise CorrectnessFailure("unexpected_http_status")

    @staticmethod
    def _deny_payload(data: object) -> None:
        try:
            problem = Problem.model_validate_json(json.dumps(data))
        except (ValidationError, ValueError, TypeError):
            raise CorrectnessFailure("unsafe_denial_payload") from None
        if re.fullmatch(r"req_[0-9a-f]{32}", problem.request_id) is None:
            raise CorrectnessFailure("unsafe_denial_request_identity")

    async def _execute_steps(
        self, client: httpx2.AsyncClient, item: Offered, execution: _Execution
    ) -> None:
        for index, step in enumerate(item.journey.steps):
            if self._stop.is_set():
                raise CorrectnessFailure("peer_correctness_stop")
            path = checked_path(step.path, execution.bindings)
            headers = self._step_headers(step, execution.bindings)
            status, data = await self._step_request(
                client, item, step, execution.bindings, headers, path
            )
            execution.response = True
            self._step_status(item, step, status, data, headers)
            if item.journey.expected == "deny":
                self._deny_payload(data)
            if index == 0 and item.journey.expected == "positive":
                self.counts["admitted"] += 1
            if step.follow_job:
                data = await self._follow(client, item, data, execution.bindings, execution.rng)
            assert_json(data, step.assertions, execution.bindings)
            for name, field_path in step.captures.items():
                execution.bindings[name] = pointer(data, field_path)
            if step.download_descriptor:
                await self._download(client, item, data, execution.bindings)

    def _execution_failure(self, error: BaseException, response: bool) -> tuple[str, bool]:
        if isinstance(error, TimeoutError):
            return "timeout", False
        if isinstance(error, ActionRejected):
            return "rejected", response
        if isinstance(error, ActionConflict):
            return "conflict", response
        if isinstance(error, OutcomeUnknown):
            return "outcome_unknown", False
        if isinstance(error, asyncio.CancelledError):
            return "interrupted", False
        if isinstance(error, CapacitySessionRejected):
            if str(error) in (
                "renewal_scope_or_absolute_expiry",
                "renewal_csrf_mismatch",
                "invalid_renewal_cookie_scope",
            ):
                self._fatal = "session_renewal_protocol_violation"
                self._stop.set()
            return "failed", response
        if self._fatal is None:
            self._fatal = str(error)  # CorrectnessFailure messages are fixed internal codes.
        self._stop.set()
        return "failed", response

    async def _execute(self, client: httpx2.AsyncClient, item: Offered) -> None:
        self.counts["started"] += 1
        self.last["started"][item.actor.actor_id] = time.monotonic()
        self._active += 1
        self._peak = max(self._peak, self._active)
        outcome = "failed"
        execution = _Execution(
            {
                **item.actor.values,
                "actor_id": item.actor.actor_id,
                "action_id": f"k17-{self.profile.run_label}-{self.profile.shard_index}-{item.sequence}",
                "action_question": f"Synthetic capacity {self.profile.run_label} shard {self.profile.shard_index} action {item.sequence}: what is observed in these source metrics?",
            },
            random.Random(self.profile.seed + item.sequence * 1009 + self.profile.shard_index),
        )
        try:
            remaining = item.due + item.journey.deadline(item.actor.size) - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            async with (
                asyncio.timeout(remaining),
                self.sessions.action(item.actor, client, verified=self._verify_principals),
            ):
                await self._execute_steps(client, item, execution)
                outcome = "completed" if item.journey.expected == "positive" else "denied"
        except (
            TimeoutError,
            ActionRejected,
            ActionConflict,
            CapacitySessionRejected,
            OutcomeUnknown,
            CorrectnessFailure,
            asyncio.CancelledError,
        ) as error:
            outcome, execution.response = self._execution_failure(error, execution.response)
            if isinstance(error, asyncio.CancelledError):
                raise
        except Exception:
            self.counts["transport_or_harness_failure"] += 1
        finally:
            self._note_unknown_mutation(item.sequence)
            self._active -= 1
            self._sent.discard(item.sequence)
            elapsed = max(0.0, time.monotonic() - item.due)
            if outcome in ("completed", "denied") and elapsed > item.journey.deadline(
                item.actor.size
            ):
                outcome = "timeout"
            self._finish(item, outcome, elapsed, response=execution.response)

    async def _download(
        self,
        client: httpx2.AsyncClient,
        item: Offered,
        descriptor: object,
        bindings: dict[str, object],
    ) -> None:
        if not isinstance(descriptor, dict):
            raise CorrectnessFailure("invalid_export_descriptor")
        for key in ("workspace_id", "project_id", "result_id"):
            if descriptor.get(key) != bindings.get(key):
                raise CorrectnessFailure("export_scope_mismatch")
        path, expected_hash, expected_size = (
            descriptor.get("download_url"),
            descriptor.get("sha256"),
            descriptor.get("byte_size"),
        )
        if (
            not isinstance(path, str)
            or not path.startswith("/v1/")
            or ".." in path
            or "://" in path
        ):
            raise CorrectnessFailure("invalid_download_target")
        if (
            not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or type(expected_size) is not int
        ):
            raise CorrectnessFailure("invalid_export_integrity_descriptor")
        status, actual = await self._request(client, item, path=path, operation="P24", binary=True)
        if status in (429, 503) or status >= 500:
            raise ActionRejected()
        if status != 200:
            raise CorrectnessFailure("export_download_unavailable")
        if actual != {"sha256": expected_hash, "byte_size": expected_size}:
            raise CorrectnessFailure("whole_export_hash_or_length_mismatch")
        self.counts["exports_verified"] += 1

    async def _worker(self, client: httpx2.AsyncClient) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    return
                if self._stop.is_set():
                    self._drop(item, "correctness_stop")
                else:
                    await self._execute(client, item)
            finally:
                self._queue.task_done()

    async def _open(self) -> None:
        rng = random.Random(self.profile.seed + self.profile.shard_index)
        duration = self.profile.warmup_seconds + self.profile.duration_seconds
        rate = self.profile.arrivals_per_second / self.profile.shard_count
        planned = math.ceil(duration * rate)
        self.counts["planned"] = planned
        sizes = [s for s, group in self._size_actors.items() if group]
        positions: Counter[str] = Counter()
        for n in range(planned):
            due = self._started + n / rate
            if self._stop.is_set():
                self.counts["not_offered_after_stop"] += planned - n
                break
            await _wait_until(due)
            size = rng.choices(sizes, weights=[self.profile.size_weights[s] for s in sizes], k=1)[0]
            group = self._size_actors[size]
            actor = group[positions[size] % len(group)]
            positions[size] += 1
            item = self._offer(actor, self._choose(actor, rng), due)
            if time.monotonic() - due >= item.journey.deadline(item.actor.size):
                self._drop(item, "generator_late")
            else:
                try:
                    self._queue.put_nowait(item)
                except asyncio.QueueFull:
                    self._drop(item, "queue_full")
        if not self._stop.is_set():
            await _wait_until(self._started + duration)

    async def _closed_actor(self, actor: Actor, index: int) -> None:
        rng = random.Random(
            self.profile.seed + index * self.profile.shard_count + self.profile.shard_index
        )
        deadline = self._started + self.profile.warmup_seconds + self.profile.duration_seconds
        await asyncio.sleep(
            rng.uniform(0.0, min(self.profile.think_seconds, self.profile.duration_seconds))
        )
        while time.monotonic() < deadline and not self._stop.is_set():
            item = self._offer(actor, self._choose(actor, rng), time.monotonic())
            completion = asyncio.get_running_loop().create_future()
            self._closed_done[item.sequence] = completion
            try:
                self._queue.put_nowait(item)
            except asyncio.QueueFull:
                self._drop(item, "queue_full")
            try:
                await completion
            finally:
                self._closed_done.pop(item.sequence, None)
            think = self._cadence[actor.size] * rng.uniform(
                1 - self.profile.jitter_fraction, 1 + self.profile.jitter_fraction
            )
            await asyncio.sleep(min(think, max(0.0, deadline - time.monotonic())))

    async def _sample(self) -> None:
        while True:
            now = time.monotonic()
            self.coverage.append(coverage_sample(self, now))
            # One sample/sec remains bounded by the pinned maximum eight-hour profile.
            self._resources.append(resource_sample(self, now))
            await asyncio.sleep(1)

    async def run(self) -> dict[str, object]:
        limits = httpx2.Limits(
            max_connections=self.profile.max_inflight,
            max_keepalive_connections=self.profile.max_inflight,
        )
        import ssl

        verification = ssl.create_default_context(cafile=self.profile.tls_ca_file)
        async with httpx2.AsyncClient(
            base_url=self.profile.target,
            limits=limits,
            timeout=None,
            follow_redirects=False,
            trust_env=False,
            verify=verification,
            transport=self.transport,
        ) as client:
            if self._verify_principals:
                self._preflight = await verify_actors(
                    self.profile, self.actors, client, lease_observer=self.sessions.observe_session
                )
                await self.sessions.bootstrap(client)
            self._started = time.monotonic()
            self._started_at = datetime.now(UTC).isoformat()
            self._sample_task = asyncio.create_task(self._sample())
            workers = [
                asyncio.create_task(self._worker(client)) for _ in range(self.profile.max_inflight)
            ]
            actors: list[asyncio.Task[None]] = []
            try:
                if self.profile.mode == "open":
                    await self._open()
                else:
                    actors = [
                        asyncio.create_task(self._closed_actor(a, n))
                        for n, a in enumerate(self.actors)
                    ]
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(*actors),
                            self.profile.warmup_seconds
                            + self.profile.duration_seconds
                            + self.profile.drain_seconds,
                        )
                    except TimeoutError:
                        self.counts["drain_timeout"] += 1
                    self.counts["planned"] = self.counts["offered"]
                try:
                    drain_left = (
                        self._started
                        + self.profile.warmup_seconds
                        + self.profile.duration_seconds
                        + self.profile.drain_seconds
                        - time.monotonic()
                    )
                    await asyncio.wait_for(self._queue.join(), max(0.001, drain_left))
                except TimeoutError:
                    self.counts["drain_timeout"] += 1
            finally:
                for task in actors + workers:
                    task.cancel()
                await asyncio.gather(*actors, *workers, return_exceptions=True)
                self._sample_task.cancel()
                await asyncio.gather(self._sample_task, return_exceptions=True)
        pending = self._queue.qsize()
        return self.report(pending)

    def report(self, pending: int) -> dict[str, object]:
        return build_report(self, pending)
