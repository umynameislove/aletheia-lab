"""Real SQLite receipt delivery, identity and declared retention boundaries."""

from __future__ import annotations

from dataclasses import replace

import pytest

from aletheia_lab.evaluation.model_load_contract import LoadContract, Record, Scope, receipt_checker
from aletheia_lab.evaluation.model_load_retention import ReceiptStore, encode

A, B = "a" * 64, "b" * 64
CONTRACT = LoadContract("pin_at_acceptance", (A, B))
ROOT = Scope("request", 0)
SELECT = Record("select", ROOT, "selection", A, "root-token", 1, CONTRACT.policy)
LOAD = Record("entry", ROOT, "load", A, SELECT.selection)
CLOSE = Record("close", ROOT, "closure", load_count=1)


@pytest.fixture
def store_factory(tmp_path):
    stores = []

    def make(**options):
        store = ReceiptStore(tmp_path / f"receipt-{len(stores)}.sqlite", **options)
        stores.append(store)
        return store

    yield make
    for store in stores:
        store.close()


def child_records():
    child = Scope(ROOT.request, 1)
    selected = Record(
        "child-select",
        child,
        "selection",
        A,
        "child-token",
        1,
        "inherit",
        parent_scope=ROOT,
        parent_selection=SELECT.selection,
    )
    return child, (
        SELECT,
        selected,
        Record("child-entry", child, "load", A, selected.selection),
        Record("child-close", child, "closure", load_count=1),
    )


def submit_complete(store, request="request"):
    scope = Scope(request, 0)
    for record in (SELECT, LOAD, CLOSE):
        store.submit(replace(record, scope=scope), scope)
    return scope


def test_delayed_duplicate_becomes_visible_without_an_extra_occurrence(store_factory):
    store = store_factory()
    store.submit(SELECT, ROOT)
    store.submit(LOAD, ROOT, delayed=True)
    store.submit(CLOSE, ROOT)
    assert receipt_checker(store.read(CONTRACT, ROOT)).reason == "missing_buffer_witness"

    store.submit(LOAD, ROOT)
    assert receipt_checker(store.read(CONTRACT, ROOT)).verdict == "compliant"
    assert store.db.execute("SELECT count(*) FROM receipt").fetchone()[0] == 3
    assert store.metrics["inserted_records"] == 3
    assert store.metrics["duplicate_deliveries"] == 1

    store.submit(LOAD, ROOT, delayed=True)
    assert receipt_checker(store.read(CONTRACT, ROOT)).verdict == "compliant"
    assert store.metrics["inserted_records"] == 3


@pytest.mark.parametrize("routing", ["mailbox", "trusted_scope"])
def test_wrong_then_correct_mailbox_delivery_keeps_one_logical_load(store_factory, routing):
    store = store_factory(routing=routing)
    store.submit(SELECT, ROOT)
    store.submit(LOAD, ROOT, mailbox="wrong")
    store.submit(CLOSE, ROOT)
    before = receipt_checker(store.read(CONTRACT, ROOT))
    assert before.verdict == ("unknown" if routing == "mailbox" else "compliant")

    store.submit(LOAD, ROOT, mailbox=ROOT.request)
    assert receipt_checker(store.read(CONTRACT, ROOT)).verdict == "compliant"
    assert store.db.execute("SELECT count(*) FROM receipt").fetchone()[0] == 4
    assert store.metrics["inserted_records"] == 4


@pytest.mark.parametrize("selection", ["full", "static_sufficient"])
@pytest.mark.parametrize("conflict", [False, True], ids=["distinct-occurrences", "conflicting-id"])
def test_receipt_variants_are_not_collapsed_into_one_occurrence(store_factory, selection, conflict):
    store = store_factory(selection=selection)
    second = replace(LOAD, digest=B) if conflict else replace(LOAD, identifier="entry-two")
    closed = CLOSE if conflict else replace(CLOSE, load_count=2)
    for record in (SELECT, LOAD, second, closed):
        store.submit(record, ROOT)
    decision = receipt_checker(store.read(CONTRACT, ROOT))
    assert decision.verdict == ("conflict" if conflict else "violation")
    assert store.metrics["inserted_records"] == 4
    assert store.metrics["duplicate_deliveries"] == 0


@pytest.mark.parametrize("selection", ["full", "static_sufficient"])
@pytest.mark.parametrize("root_lifetime", [False, True])
def test_root_lifetime_spans_child_query_after_expiry(store_factory, selection, root_lifetime):
    store = store_factory(selection=selection, root_lifetime=root_lifetime)
    child, records = child_records()
    store.submit(records[0], child)
    store.submit(Record("root-close", ROOT, "closure", load_count=0), child)
    store.expire_root(ROOT)
    for record in records[1:]:
        store.submit(record, child)
    decision = receipt_checker(store.read(CONTRACT, child))
    assert decision.verdict == ("compliant" if root_lifetime else "unknown")
    if not root_lifetime:
        assert decision.reason == "missing_selection"


@pytest.mark.parametrize("selection", ["full", "static_sufficient"])
def test_conflicting_authoritative_parents_survive_static_selection(store_factory, selection):
    store = store_factory(selection=selection)
    child, records = child_records()
    for record in (*records, replace(SELECT, identifier="other-root", digest=B)):
        store.submit(record, child)
    decision = receipt_checker(store.read(CONTRACT, child))
    assert decision.verdict == "conflict"
    assert decision.reason == "conflicting_parent_receipts"


def test_request_drain_requires_released_deliveries_and_rejects_future_delivery(store_factory):
    store = store_factory(horizon=2)
    store.submit(SELECT, ROOT)
    store.submit(LOAD, ROOT, delayed=True)
    store.submit(CLOSE, ROOT)
    with pytest.raises(ValueError, match="pending deliveries"):
        store.drain(ROOT.request)
    assert store.completions == 0
    store.release(ROOT.request)
    store.drain(ROOT.request)
    assert receipt_checker(store.read(CONTRACT, ROOT)).verdict == "compliant"
    for operation in (
        lambda: store.submit(LOAD, ROOT),
        lambda: store.release(ROOT.request),
        lambda: store.drain(ROOT.request),
    ):
        with pytest.raises(ValueError, match="drain"):
            operation()


@pytest.mark.parametrize("horizon", [0, 2])
def test_audit_horizon_expires_on_subsequent_request_drains(store_factory, horizon):
    store = store_factory(horizon=horizon)
    first = submit_complete(store)
    store.drain(first.request)
    if horizon:
        assert receipt_checker(store.read(CONTRACT, first)).verdict == "compliant"
        second = submit_complete(store, "second")
        store.drain(second.request)
        assert receipt_checker(store.read(CONTRACT, first)).verdict == "compliant"
        third = submit_complete(store, "third")
        store.drain(third.request)
        assert receipt_checker(store.read(CONTRACT, second)).verdict == "compliant"
    with pytest.raises(ValueError, match="audit horizon"):
        store.read(CONTRACT, first)
    assert (
        store.db.execute(
            "SELECT count(*) FROM receipt WHERE request=?", (first.request,)
        ).fetchone()[0]
        == 0
    )
    result = store.finish()
    assert result["deleted_records"] == 3
    assert result["final_database_bytes"] > 0
    assert result["bookkeeping_included"]


@pytest.mark.parametrize("selection", ["full", "static_sufficient"])
def test_foreign_scope_is_not_repaired_by_a_matching_mailbox(store_factory, selection):
    store = store_factory(selection=selection, routing="mailbox")
    for record in (SELECT, replace(LOAD, scope=Scope("foreign", 0), digest=B), CLOSE):
        store.submit(record, ROOT, mailbox=ROOT.request)
    decision = receipt_checker(store.read(CONTRACT, ROOT))
    assert decision.verdict == "unknown"
    assert decision.reason == "missing_buffer_witness"


@pytest.mark.parametrize(
    "options",
    [
        {"selection": "adaptive"},
        {"routing": "latest"},
        {"root_lifetime": 1},
        {"horizon": True},
        {"horizon": -1},
        {"horizon": 9},
        {"horizon": 2.0},
    ],
)
def test_invalid_configuration_is_rejected_before_database_creation(tmp_path, options):
    path = tmp_path / "invalid.sqlite"
    with pytest.raises(ValueError, match="configuration"):
        ReceiptStore(path, **options)
    assert not path.exists()


def test_store_requires_a_new_database_with_an_existing_parent(tmp_path):
    path = tmp_path / "exists.sqlite"
    path.touch()
    with pytest.raises(ValueError, match="new local database"):
        ReceiptStore(path)
    with pytest.raises(ValueError, match="new local database"):
        ReceiptStore(tmp_path / "absent" / "new.sqlite")


@pytest.mark.parametrize(
    "options", [{"delayed": 1}, {"mailbox": ""}, {"mailbox": "x" * 101}, {"mailbox": 1}]
)
def test_invalid_delivery_envelope_is_rejected_without_capture(store_factory, options):
    store = store_factory()
    with pytest.raises(ValueError, match="delivery envelope"):
        store.submit(SELECT, ROOT, **options)
    assert store.metrics["capture_events"] == 0
    assert store.metrics["inserted_records"] == 0
    assert store.targets == {}


def test_record_byte_and_event_allowances_are_bounded(store_factory):
    store = store_factory()
    with pytest.raises(ValueError, match="input allowance"):
        store.submit(replace(SELECT, identifier="x" * 8193), ROOT)
    assert store.metrics["capture_events"] == 0
    assert store.targets == {}
    store.metrics["capture_events"] = 4096
    with pytest.raises(ValueError, match="input allowance"):
        store.submit(SELECT, ROOT)
    assert store.metrics["inserted_records"] == 0


def test_request_census_and_one_query_attempt_are_bounded(store_factory):
    store = store_factory()
    for index in range(64):
        scope = Scope(f"request-{index}", 0)
        store.submit(replace(SELECT, scope=scope), scope)
    scope = Scope("request-64", 0)
    with pytest.raises(ValueError, match="request census"):
        store.submit(replace(SELECT, scope=scope), scope)
    assert store.metrics["capture_events"] == 64
    assert store.metrics["inserted_records"] == 64
    assert len(store.targets) == 64
    with pytest.raises(ValueError, match="one declared query attempt"):
        store.submit(replace(SELECT, scope=Scope("request-0", 1)), Scope("request-0", 1))


def test_request_drain_retires_owned_foreign_receipts_without_admitting_them(store_factory):
    store = store_factory(selection="full", horizon=0)
    foreign = replace(LOAD, scope=Scope("foreign", 0), digest=B)
    for record in (SELECT, foreign, CLOSE):
        store.submit(record, ROOT, mailbox=ROOT.request)
    assert receipt_checker(store.read(CONTRACT, ROOT)).verdict == "unknown"
    assert store.db.execute("SELECT count(*) FROM receipt").fetchone()[0] == 3
    store.drain(ROOT.request)
    assert store.db.execute("SELECT count(*) FROM receipt").fetchone()[0] == 0
    assert store.finish()["final_live_payload_bytes"] == 0


def test_owned_foreign_delayed_receipt_also_blocks_request_drain(store_factory):
    store = store_factory(selection="full", horizon=0)
    foreign = replace(LOAD, scope=Scope("foreign", 0))
    for record in (SELECT, CLOSE):
        store.submit(record, ROOT)
    store.submit(foreign, ROOT, delayed=True)
    with pytest.raises(ValueError, match="pending deliveries"):
        store.drain(ROOT.request)
    store.release(ROOT.request)
    store.drain(ROOT.request)
    assert store.finish()["final_live_payload_bytes"] == 0


def test_query_and_expiry_must_use_declared_scope(store_factory):
    store = store_factory()
    store.submit(SELECT, ROOT)
    with pytest.raises(ValueError, match="declared target attempt"):
        store.read(CONTRACT, Scope(ROOT.request, 1))
    with pytest.raises(ValueError, match="root scope"):
        store.expire_root(Scope(ROOT.request, 1))
    with pytest.raises(ValueError, match="declared query target"):
        store.drain("unbound")
    with pytest.raises(ValueError):
        encode({"invalid": float("nan")})
