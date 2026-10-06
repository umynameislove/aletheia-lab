"""Actual application census and pre-dispatch admission development experiments."""

from __future__ import annotations

from pathlib import Path
from pickle import UnpicklingError
from time import perf_counter_ns
from typing import Any

from aletheia_lab.evaluation.application_audit_sources import (
    PROBE,
    ApplicationSource,
    NativeApplication,
)
from aletheia_lab.evaluation.audit_bundle_archive import Bundle
from aletheia_lab.evaluation.audit_obligation_service import POLICIES, ObligationService
from aletheia_lab.evaluation.model_load_provenance import verify_chain
from aletheia_lab.evaluation.model_load_serving_workload import decide
from aletheia_lab.project.identity import content_sha256

STEPS = ("initial", "lawful_change", "wrong_b", "failed_b", "restore_b", "wrong_a", "restore_a")
BUDGETS = (2048, 4096, 8192)
REPEATS = 2
LEASE_EVENTS = 4


def configure(source: ApplicationSource, step: str) -> str:
    name = "A" if step in {"initial", "wrong_a", "restore_a"} else "B"
    source.select(name)
    actual = "A" if step == "wrong_b" else "B" if step == "wrong_a" else name
    source.replace(name, None if step == "failed_b" else actual)
    return name


async def attempt(source: ApplicationSource, scope: str, step: str, index: int) -> dict[str, Any]:
    selected = configure(source, step)
    before = len(source.capture.receipts)
    raw_before = len(source.capture.reference)
    started = perf_counter_ns()
    app: NativeApplication | None = None
    error: str | None = None
    try:
        app = await source.start(scope)
    except (
        ValueError,
        OSError,
        RuntimeError,
        EOFError,
        ImportError,
        AttributeError,
        UnpicklingError,
    ) as exc:
        error = type(exc).__name__
    load_ns = perf_counter_ns() - started
    events = source.capture.receipts[before:]
    references = source.capture.reference[raw_before:]
    expected = content_sha256(source.payloads[selected])
    frame = {
        "scope": scope,
        "step": index,
        "kind": "load",
        "domain": source.domain,
        "expected": expected,
        "observed": [row["descriptor_sha256"] for row in events],
        "count": len(events),
        "closed": True,
        "generation": None,
    }
    response = None
    inference_ns = 0
    if app is not None:
        started = perf_counter_ns()
        try:
            response = await app.request()
        finally:
            inference_ns = perf_counter_ns() - started
            await app.close()
    raw = [bytes.fromhex(row["raw_hex"]) for row in references]
    # Independent reference does not call decide(), use the receipt digests, or
    # consult predictions to infer a violated byte contract.
    gold = (
        "unknown"
        if app is None or len(raw) != 1
        else ("compliant" if raw[0] == source.payloads[selected] else "violation")
    )
    object_match = None
    if response is not None:
        matches = [
            name
            for name, fingerprint in source.fingerprints.items()
            if fingerprint == response["object_fingerprint"]
        ]
        object_match = matches[0] if len(matches) == 1 else None
    return {
        "scope": scope,
        "step": step,
        "frame": frame,
        "gold": gold,
        "raw_reference": references,
        "native_response": response,
        "object_model": object_match,
        "startup_error": error,
        "load_ns": load_ns,
        "inference_ns": inference_ns,
    }


async def cache_control(source: ApplicationSource) -> list[dict[str, Any]]:
    """A retained native application keeps its actual model despite selection/path changes."""
    source.select("A")
    source.replace("A", "A")
    old = await source.start("cache-initial")
    try:
        first = await old.request()
        source.select("B")  # Native registry alias, or new native latest-store release.
        after_selection = await old.request()
        fresh = await source.start("cache-fresh")
        try:
            after_restart = await fresh.request()
            source.replace("B", "A")
            after_path = await fresh.request()
            source.replace("B", None)
            failure = None
            try:
                unexpected = await source.start("cache-failed")
            except (
                ValueError,
                OSError,
                RuntimeError,
                EOFError,
                ImportError,
                AttributeError,
                UnpicklingError,
            ) as exc:
                failure = type(exc).__name__
            else:
                await unexpected.close()
            after_failure = await fresh.request()
        finally:
            await fresh.close()
    finally:
        await old.close()
        source.replace("B", "B")
    return [
        {"case": "retained_application_selection_move", "before": first, "after": after_selection},
        {"case": "fresh_application_selection", "before": first, "after": after_restart},
        {"case": "path_replacement_not_resident", "before": after_restart, "after": after_path},
        {
            "case": "failed_new_app_prior_app_survives",
            "before": after_restart,
            "after": after_failure,
            "startup_error": failure,
        },
    ]


async def census(stack: str, directory: Path) -> dict[str, Any]:
    source = ApplicationSource(stack, directory / "source")
    cached = await cache_control(source)
    rows = []
    for index, step in enumerate(STEPS):
        row = await attempt(source, f"census-{index:02d}", step, index)
        row["sufficient"] = decide(row["frame"])
        if row["gold"] == "unknown":
            row["signed_provenance"] = "unknown"
        else:
            frame = row["frame"]
            status = verify_chain(
                {"model": frame["expected"]},
                {"model": frame["observed"][0]},
                directory / "signed" / row["scope"],
            )
            row["signed_provenance"] = "compliant" if status == "pass" else "violation"
        rows.append(row)
    return {
        "environment": source.environment(),
        "cache_controls": cached,
        "rows": rows,
        "reference_capture_count": len(source.capture.reference),
        "start_attempts": source.start_attempts,
        "http_requests": source.http_requests,
        "releases": release_manifest(source),
    }


async def service_arm(stack: str, directory: Path, policy: str, budget: int) -> dict[str, Any]:
    source = ApplicationSource(stack, directory / "source")
    service = ObligationService(directory / "archive.sqlite", policy, budget)
    rows: list[dict[str, Any]] = []
    due: list[dict[str, Any]] = []
    queries: list[dict[str, Any]] = []
    try:
        for index, step in enumerate(STEPS):
            service.advance(index)
            for target in [row for row in due if row["until"] == index]:
                queries.append(query(service, target, index))
            scope = f"load-{index:02d}"
            dispatched = service.begin(scope, sequence=index, now=index, until=index + LEASE_EVENTS)
            if not dispatched:
                rows.append({"scope": scope, "step": step, "status": "refused_before_dispatch"})
                continue
            row = await attempt(source, scope, step, index)
            if row["startup_error"] is not None:
                service.cancel(scope)
                row.update(status="native_startup_failed", audit_accepted=False)
            else:
                accepted = service.finish(Bundle.build(row["frame"]))
                row.update(status="completed", audit_accepted=accepted)
                if accepted:
                    due.append({"scope": scope, "gold": row["gold"], "until": index + LEASE_EVENTS})
            rows.append(row)
            # Offered optional retrospective request; not an accepted hard lease.
            if index:
                previous_target = next(
                    (value for value in rows if value["scope"] == f"load-{index - 1:02d}"), None
                )
                if previous_target is not None and "gold" in previous_target:
                    queries.append(query(service, {**previous_target, "until": None}, index))
        for now in range(len(STEPS), len(STEPS) + LEASE_EVENTS):
            service.advance(now)
            for target in [row for row in due if row["until"] == now]:
                queries.append(query(service, target, now))
        return {
            "policy": policy,
            "budget": budget,
            "rows": rows,
            "queries": queries,
            "peak_charged_bytes": service.peak,
            "overruns": service.overruns,
            "physical_database_bytes": sum(
                path.stat().st_size for path in directory.glob("archive.sqlite*")
            ),
            "environment": source.environment(),
            "releases": release_manifest(source),
            "start_attempts": source.start_attempts,
            "http_requests": source.http_requests,
        }
    finally:
        service.close()


def query(service: ObligationService, target: dict[str, Any], now: int) -> dict[str, Any]:
    started = perf_counter_ns()
    decision = service.query(target["scope"])
    return {
        "scope": target["scope"],
        "now": now,
        "hard": target.get("until") is not None,
        "gold": target["gold"],
        "decision": decision,
        "query_ns": perf_counter_ns() - started,
    }


def release_manifest(source: ApplicationSource) -> dict[str, Any]:
    return {
        name: {
            "raw_hex": raw.hex(),
            "sha256": content_sha256(raw),
            "object_fingerprint": source.fingerprints[name],
            "predictions": source.models[name].predict(PROBE).tolist(),
        }
        for name, raw in source.payloads.items()
    }


async def worker(stack: str, directory: Path) -> dict[str, Any]:
    directory.mkdir(exist_ok=False)
    source_census = await census(stack, directory / "census")
    arms = []
    for repeat in range(REPEATS):
        for budget in BUDGETS:
            for policy in POLICIES:
                arm = await service_arm(
                    stack, directory / f"r{repeat}-{budget}-{policy}", policy, budget
                )
                arm["repeat"] = repeat
                arms.append(arm)
    return {"stack": stack, "census": source_census, "arms": arms}
