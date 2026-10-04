"""Multi-attempt query-service, transport and snapshot safety on real SQLite."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from aletheia_lab.evaluation import model_load_attempt_retention as retention
from aletheia_lab.evaluation.model_load_attempt_retention import AttemptReceiptStore
from aletheia_lab.evaluation.model_load_contract import (
    LoadContract,
    Record,
    Scope,
    completion_monitor,
    receipt_checker,
)

A, B = "a" * 64, "b" * 64
CONTRACT = LoadContract("pin_at_acceptance", (A, B))


@pytest.fixture
def store_factory(tmp_path):
    stores = []

    def make(selection="static_sufficient", horizon=2):
        store = AttemptReceiptStore(
            tmp_path / f"attempts-{len(stores)}.sqlite", selection=selection, horizon=horizon
        )
        stores.append(store)
        return store

    yield make
    for store in stores:
        store.db.close()


def selected(scope, digest=A, parent=None, phase=None):
    return Record(
        "selection",
        scope,
        "selection",
        digest,
        f"{scope.request}:{scope.attempt}:selected",
        1,
        phase or ("inherit" if scope.attempt else CONTRACT.policy),
        parent_scope=parent.scope if parent else None,
        parent_selection=parent.selection if parent else None,
    )


def complete(store, scope, *, parent=None, digest=A, cache=False, settle=True):
    selection = selected(scope, parent=parent)
    load = (
        Record("cache", scope, "cache_hit", A)
        if cache
        else Record("entry", scope, "load", digest, selection.selection)
    )
    closure = Record("close", scope, "closure", load_count=0 if cache else 1)
    for record in (selection, load, closure):
        store.submit(scope.request, record)
    if settle:
        store.settle(scope)
    return selection


def decision_tuple(observation):
    decisions = []
    for checker in (receipt_checker, completion_monitor):
        value = checker(observation)
        decisions.append((value.verdict, value.reason, value.eligibility))
    return tuple(decisions)


@pytest.mark.parametrize("selection", ["full", "static_sufficient"])
def test_root_closure_preserves_two_later_inherited_children_and_root_audit(
    store_factory, selection
):
    store = store_factory(selection)
    root, first, second = (Scope("request", index) for index in range(3))
    store.register((root, first, second), (root, first, second))
    parent = complete(store, root)
    assert receipt_checker(store.snapshot(CONTRACT, root)).verdict == "compliant"
    with pytest.raises(ValueError, match="unsettled"):
        store.drain(root.request)
    complete(store, first, parent=parent)
    with pytest.raises(ValueError, match="unsettled"):
        store.drain(root.request)
    complete(store, second, parent=parent, digest=B)
    expected = ("compliant", "compliant", "violation")
    for scope, verdict in zip((root, first, second), expected, strict=True):
        assert receipt_checker(store.snapshot(CONTRACT, scope)).verdict == verdict
        assert completion_monitor(store.snapshot(CONTRACT, scope)).verdict == verdict
    store.drain(root.request)
    for scope, verdict in zip((root, first, second), expected, strict=True):
        assert receipt_checker(store.snapshot(CONTRACT, scope)).verdict == verdict


def test_full_and_static_match_expanded_query_service_and_never_drop_root_loads(store_factory):
    stores = [store_factory(selection) for selection in ("full", "static_sufficient")]
    root, child, lifecycle_only = (Scope("request", index) for index in range(3))
    for store in stores:
        store.register((root, child, lifecycle_only), (root, child))
        parent = complete(store, root)
        complete(store, child, parent=parent)
        complete(store, lifecycle_only, parent=parent)
    for scope in (root, child):
        full, static = (store.snapshot(CONTRACT, scope) for store in stores)
        assert decision_tuple(full) == decision_tuple(static)
        assert receipt_checker(static).verdict == "compliant"
    root_records = stores[1].snapshot(CONTRACT, root).records
    assert any(record.scope == root and record.kind == "load" for record in root_records)
    assert any(record.scope == root and record.kind == "closure" for record in root_records)
    for store in stores:
        store.drain(root.request)
    for scope in (root, child):
        assert decision_tuple(stores[0].snapshot(CONTRACT, scope)) == decision_tuple(
            stores[1].snapshot(CONTRACT, scope)
        )
    full, static = (store.sample() for store in stores)
    assert static["inserts"] < full["inserts"]
    assert static["live_payload_bytes"] < full["live_payload_bytes"]


def test_static_has_no_artificial_write_saving_when_every_attempt_is_queried(store_factory):
    stores = [store_factory(selection) for selection in ("full", "static_sufficient")]
    root, child = Scope("request", 0), Scope("request", 1)
    for store in stores:
        store.register((root, child), (root, child))
        parent = complete(store, root)
        complete(store, child, parent=parent)
    full, static = (store.sample() for store in stores)
    assert static["inserts"] == full["inserts"] == 6
    assert static["written_bytes"] == full["written_bytes"]
    assert static["live_payload_bytes"] == full["live_payload_bytes"]


@pytest.mark.parametrize("selection", ["full", "static_sufficient"])
def test_settling_producer_does_not_make_delayed_load_complete(store_factory, selection):
    store = store_factory(selection)
    root = Scope("request", 0)
    store.register((root,), (root,))
    selection_record = selected(root)
    load = Record("entry", root, "load", A, selection_record.selection)
    store.submit(root.request, selection_record)
    store.submit(root.request, load, delayed=True)
    store.submit(root.request, Record("close", root, "closure", load_count=1))
    store.settle(root)
    assert receipt_checker(store.snapshot(CONTRACT, root)).reason == "missing_buffer_witness"
    with pytest.raises(ValueError, match="delayed"):
        store.drain(root.request)
    store.release(root.request)
    assert receipt_checker(store.snapshot(CONTRACT, root)).verdict == "compliant"
    store.drain(root.request)


@pytest.mark.parametrize("selection", ["full", "static_sufficient"])
@pytest.mark.parametrize("filtered", [False, True], ids=["queried", "filtered"])
def test_visible_then_delayed_duplicate_keeps_visibility_but_requires_queue_ack(
    store_factory, selection, filtered
):
    store = store_factory(selection)
    root, auxiliary = Scope("request", 0), Scope("request", 1)
    store.register((root, auxiliary), (root,))
    parent = complete(store, root)
    store.settle(auxiliary)
    scope = auxiliary if filtered else root
    load = Record("duplicate", scope, "load", A, parent.selection)
    store.submit(root.request, load)
    initial = decision_tuple(store.snapshot(CONTRACT, root))
    store.submit(root.request, load, delayed=True)
    assert decision_tuple(store.snapshot(CONTRACT, root)) == initial
    with pytest.raises(ValueError, match="delayed"):
        store.drain(root.request)
    store.release(root.request)
    store.drain(root.request)
    assert decision_tuple(store.snapshot(CONTRACT, root)) == initial


@pytest.mark.parametrize("selection", ["full", "static_sufficient"])
def test_delayed_then_visible_redelivery_acknowledges_pending_without_extra_occurrence(
    store_factory, selection
):
    store = store_factory(selection)
    root = Scope("request", 0)
    store.register((root,), (root,))
    chosen = selected(root)
    load = Record("entry", root, "load", A, chosen.selection)
    store.submit(root.request, chosen)
    store.submit(root.request, load, delayed=True)
    store.submit(root.request, Record("close", root, "closure", load_count=1))
    store.settle(root)
    store.submit(root.request, load)
    assert receipt_checker(store.snapshot(CONTRACT, root)).verdict == "compliant"
    assert store.metrics["inserts"] == 3
    assert store.metrics["duplicates"] == 1
    store.drain(root.request)


@pytest.mark.parametrize("selection", ["full", "static_sufficient"])
def test_filtered_foreign_delayed_delivery_blocks_drain_and_reclaims_by_owner(
    store_factory, selection
):
    store = store_factory(selection, horizon=0)
    root = Scope("request", 0)
    store.register((root,), (root,))
    complete(store, root)
    foreign = Record("foreign", Scope("other-request", 0), "load", B, "foreign-token")
    store.submit(root.request, foreign, delayed=True)
    before = store.sample()
    assert before["captures"] == 4
    assert before["filtered"] == int(selection == "static_sufficient")
    assert receipt_checker(store.snapshot(CONTRACT, root)).verdict == "compliant"
    with pytest.raises(ValueError, match="delayed"):
        store.drain(root.request)
    assert store.completions == 0
    store.release(root.request)
    store.drain(root.request)
    assert store.sample()["live_payload_bytes"] == 0
    assert store.pending == set()


@pytest.mark.parametrize("selection", ["full", "static_sufficient"])
@pytest.mark.parametrize("conflict", [False, True], ids=["distinct-occurrences", "conflicting-id"])
def test_equal_digests_do_not_merge_occurrences_and_conflicting_ids_survive(
    store_factory, selection, conflict
):
    store = store_factory(selection)
    root = Scope("request", 0)
    store.register((root,), (root,))
    chosen = selected(root)
    load = Record("entry", root, "load", A, chosen.selection)
    second = replace(load, digest=B) if conflict else replace(load, identifier="second-entry")
    closure = Record("close", root, "closure", load_count=1 if conflict else 2)
    for record in (chosen, load, load, second, closure):
        store.submit(root.request, record)
    decision = receipt_checker(store.snapshot(CONTRACT, root))
    assert decision.verdict == ("conflict" if conflict else "violation")
    assert store.metrics["inserts"] == 4
    assert store.metrics["duplicates"] == 1


@pytest.mark.parametrize("selection", ["full", "static_sufficient"])
def test_all_parent_variants_survive_and_foreign_scope_never_replaces_child_witness(
    store_factory, selection
):
    store = store_factory(selection)
    root, child = Scope("request", 0), Scope("request", 1)
    store.register((root, child), (child,))
    parent = selected(root)
    chosen = selected(child, parent=parent)
    store.submit(root.request, parent)
    store.submit(root.request, chosen)
    store.submit(root.request, Record("entry", Scope("foreign", 1), "load", A, chosen.selection))
    store.submit(root.request, Record("close", child, "closure", load_count=1))
    assert receipt_checker(store.snapshot(CONTRACT, child)).reason == "missing_buffer_witness"
    store.submit(root.request, replace(parent, identifier="other-parent", digest=B))
    assert receipt_checker(store.snapshot(CONTRACT, child)).reason == "conflicting_parent_receipts"
    assert completion_monitor(store.snapshot(CONTRACT, child)).verdict == "conflict"


@pytest.mark.parametrize("selection", ["full", "static_sufficient"])
def test_cache_only_child_is_no_new_load_not_completed_compliance(store_factory, selection):
    store = store_factory(selection)
    root, child = Scope("request", 0), Scope("request", 1)
    store.register((root, child), (root, child))
    parent = complete(store, root)
    complete(store, child, parent=parent, cache=True)
    decision = receipt_checker(store.snapshot(CONTRACT, child))
    assert decision.verdict is None
    assert decision.eligibility == "no_new_load"
    assert receipt_checker(store.snapshot(CONTRACT, root)).verdict == "compliant"


@pytest.mark.parametrize("selection", ["full", "static_sufficient"])
def test_two_worker_threads_share_root_without_crossing_attempts(store_factory, selection):
    store = store_factory(selection)
    root, first, second = (Scope("request", index) for index in range(3))
    store.register((root, first, second), (root, first, second))
    parent = complete(store, root)
    reached_selection = threading.Barrier(3, timeout=5)
    allow_loads = threading.Event()

    def producer(scope, digest):
        chosen = selected(scope, parent=parent)
        store.submit(root.request, chosen)
        reached_selection.wait()
        if not allow_loads.wait(5):
            raise TimeoutError("test did not release producer")
        store.submit(root.request, Record("entry", scope, "load", digest, chosen.selection))
        store.submit(root.request, Record("close", scope, "closure", load_count=1))
        store.settle(scope)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(producer, first, A), executor.submit(producer, second, B)]
        try:
            reached_selection.wait()
            for scope in (first, second):
                assert receipt_checker(store.snapshot(CONTRACT, scope)).verdict == "unknown"
            with pytest.raises(ValueError, match="unsettled"):
                store.drain(root.request)
        finally:
            allow_loads.set()
        for future in futures:
            future.result(timeout=5)
    assert receipt_checker(store.snapshot(CONTRACT, first)).verdict == "compliant"
    assert receipt_checker(store.snapshot(CONTRACT, second)).verdict == "violation"
    assert receipt_checker(store.snapshot(CONTRACT, root)).verdict == "compliant"
    store.drain(root.request)


@pytest.mark.parametrize("selection", ["full", "static_sufficient"])
def test_snapshot_is_copied_atomically_before_concurrent_request_retirement(
    store_factory, monkeypatch, selection
):
    store = store_factory(selection, horizon=0)
    root = Scope("request", 0)
    store.register((root,), (root,))
    complete(store, root)
    copying, allow_copy, retirement_started, retirement_done = (threading.Event() for _ in range(4))
    original = retention.observation

    def pause_copy(value):
        copying.set()
        if not allow_copy.wait(5):
            raise TimeoutError("test did not release snapshot copy")
        return original(value)

    def retire():
        retirement_started.set()
        store.drain(root.request)
        retirement_done.set()

    monkeypatch.setattr(retention, "observation", pause_copy)
    with ThreadPoolExecutor(max_workers=2) as executor:
        snapshot = executor.submit(store.snapshot, CONTRACT, root)
        assert copying.wait(5)
        retirement = executor.submit(retire)
        try:
            assert retirement_started.wait(5)
            assert not retirement_done.is_set()
        finally:
            allow_copy.set()
        copied = snapshot.result(timeout=5)
        retirement.result(timeout=5)
    assert retirement_done.is_set()
    assert receipt_checker(copied).verdict == "compliant"
    assert completion_monitor(copied).verdict == "compliant"
    assert len(copied.records) == 3
    assert store.sample()["live_payload_bytes"] == 0
    with pytest.raises(ValueError, match="horizon"):
        store.snapshot(CONTRACT, root)


@pytest.mark.parametrize("selection", ["full", "static_sufficient"])
def test_saved_snapshot_stays_valid_after_new_conflicting_delivery(store_factory, selection):
    store = store_factory(selection)
    root = Scope("request", 0)
    store.register((root,), (root,))
    chosen = complete(store, root)
    saved = store.snapshot(CONTRACT, root)
    store.submit(root.request, replace(chosen, digest=B))
    assert receipt_checker(store.snapshot(CONTRACT, root)).verdict == "conflict"
    assert receipt_checker(saved).verdict == "compliant"
    assert len(saved.records) == 3


@pytest.mark.parametrize("horizon", [0, 2])
def test_audit_horizon_matches_request_drains_and_rejects_future_deliveries(store_factory, horizon):
    store = store_factory(horizon=horizon)
    first = Scope("first", 0)
    store.register((first,), (first,))
    complete(store, first)
    store.drain(first.request)
    with pytest.raises(ValueError, match="drained"):
        store.submit(first.request, selected(first))
    with pytest.raises(ValueError, match="drained"):
        store.release(first.request)
    if horizon:
        assert receipt_checker(store.snapshot(CONTRACT, first)).verdict == "compliant"
        for request in ("second", "third"):
            scope = Scope(request, 0)
            store.register((scope,), (scope,))
            complete(store, scope)
            store.drain(request)
            if request == "second":
                assert receipt_checker(store.snapshot(CONTRACT, first)).verdict == "compliant"
    with pytest.raises(ValueError, match="horizon"):
        store.snapshot(CONTRACT, first)


@pytest.mark.parametrize(
    "scopes,queries",
    [
        ((), ()),
        ((Scope("request", 0),), ()),
        ((Scope("request", 0), Scope("request", 0)), (Scope("request", 0),)),
        ((Scope("request", 1),), (Scope("request", 1),)),
        ((Scope("request", 0), Scope("other", 1)), (Scope("request", 0),)),
        ((Scope("request", 0),), (Scope("request", 0), Scope("request", 0))),
        ((Scope("request", 0),), (Scope("request", 1),)),
    ],
)
def test_registration_rejects_incomplete_or_mutable_query_census(store_factory, scopes, queries):
    store = store_factory()
    with pytest.raises(ValueError):
        store.register(scopes, queries)
    assert store.scopes == store.queries == {}
    assert store.metrics["captures"] == 0


def test_registration_and_queries_cannot_expand_after_capture(store_factory):
    store = store_factory()
    root, child = Scope("request", 0), Scope("request", 1)
    store.register((root, child), (child,))
    store.submit(root.request, selected(root))
    with pytest.raises(ValueError, match="duplicate"):
        store.register((root, child), (root, child))
    with pytest.raises(ValueError, match="query"):
        store.snapshot(CONTRACT, root)
    with pytest.raises(ValueError, match="unknown"):
        store.settle(Scope(root.request, 2))
    store.settle(root)
    with pytest.raises(ValueError, match="settled"):
        store.settle(root)


@pytest.mark.parametrize("delayed", [1, "yes", None])
def test_invalid_delivery_does_not_mutate_capture_or_pending_state(store_factory, delayed):
    store = store_factory()
    root = Scope("request", 0)
    store.register((root,), (root,))
    with pytest.raises(ValueError, match="allowance"):
        store.submit(root.request, selected(root), delayed=delayed)
    assert store.metrics["captures"] == store.metrics["inserts"] == 0
    assert store.pending == set()


def test_input_attempt_and_request_censuses_are_bounded(store_factory):
    store = store_factory()
    root = Scope("request", 0)
    store.register((root,), (root,))
    with pytest.raises(ValueError, match="allowance"):
        store.submit(root.request, replace(selected(root), identifier="x" * 8193))
    assert store.metrics["captures"] == 0
    store.metrics["captures"] = 4096
    with pytest.raises(ValueError, match="allowance"):
        store.submit(root.request, selected(root))
    with pytest.raises(ValueError, match="census"):
        scopes = tuple(Scope("too-many", index) for index in range(129))
        store.register(scopes, (scopes[0],))
    assert "too-many" not in store.scopes
    for index in range(63):
        scope = Scope(f"other-{index}", 0)
        store.register((scope,), (scope,))
    last = Scope("over-budget", 0)
    with pytest.raises(ValueError, match="budget"):
        store.register((last,), (last,))


@pytest.mark.parametrize(
    "options",
    [
        {"selection": "adaptive"},
        {"horizon": True},
        {"horizon": -1},
        {"horizon": 9},
        {"horizon": 2.0},
    ],
)
def test_invalid_configuration_does_not_create_a_database(tmp_path, options):
    path = tmp_path / "not-created.sqlite"
    with pytest.raises(ValueError):
        AttemptReceiptStore(path, **{"selection": "full", **options})
    assert not path.exists()


def test_finished_report_counts_service_and_serialized_bookkeeping(store_factory):
    store = store_factory(horizon=0)
    root, child = Scope("request", 0), Scope("request", 1)
    store.register((root, child), (child,))
    parent = complete(store, root)
    complete(store, child, parent=parent)
    before = store.sample()
    store.drain(root.request)
    report = store.finish()
    assert report["request_count"] == 1
    assert report["attempt_count"] == 2
    assert report["query_attempt_count"] == 1
    assert report["retired_request_count"] == 1
    assert report["live_payload_bytes"] == 0
    assert report["samples"] == 2
    assert report["payload_byte_steps"] == before["live_payload_bytes"]
    assert report["bookkeeping_byte_steps"] > 0
    assert report["peak_database_bytes"] > 0
