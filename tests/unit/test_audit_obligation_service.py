from __future__ import annotations

import json
from pathlib import Path

import pytest

from aletheia_lab.evaluation.audit_bundle_archive import Bundle
from aletheia_lab.evaluation.audit_obligation_service import POLICIES, ObligationService
from aletheia_lab.evaluation.model_load_retention import encode

A, B = "a" * 64, "b" * 64


def frame(scope: str, index: int = 0, *, violation: bool = False) -> dict:
    return {
        "scope": scope,
        "step": index,
        "kind": "load",
        "domain": [A, B],
        "expected": A,
        "observed": [B if violation else A],
        "count": 1,
        "closed": True,
        "generation": None,
    }


@pytest.mark.parametrize("policy", POLICIES)
def test_accepted_lease_survives_pressure_and_drains_at_inclusive_deadline(
    tmp_path: Path, policy: str
) -> None:
    service = ObligationService(tmp_path / "state.sqlite", policy, 3072)
    try:
        assert service.begin("first", sequence=0, now=0, until=8)
        assert service.finish(Bundle.build(frame("first", violation=True)))
        for index in range(1, 8):
            scope = f"load-{index}"
            if service.begin(scope, sequence=index, now=index, until=16):
                service.finish(Bundle.build(frame(scope, index)))
            assert service.query("first")["verdict"] == "violation"
            assert service.peak <= service.budget
        service.advance(8)
        assert service.query("first")["verdict"] == "violation"
    finally:
        service.close()


@pytest.mark.parametrize("policy", POLICIES)
def test_no_native_dispatch_happens_without_reserved_admission(tmp_path: Path, policy: str) -> None:
    service = ObligationService(tmp_path / "state.sqlite", policy, 2048)
    try:
        admitted = service.begin("new", sequence=0, now=0, until=3)
        assert admitted is policy.startswith("drain")
        assert service._charge(service.entries, service.atoms, service.reservations) <= 2048
        if admitted:
            service.cancel("new")
    finally:
        service.close()


@pytest.mark.parametrize("policy", POLICIES)
def test_explicit_bound_overrun_never_breaks_previously_accepted_lease(
    tmp_path: Path, policy: str
) -> None:
    service = ObligationService(tmp_path / "state.sqlite", policy, 8192)
    try:
        assert service.begin("first", sequence=0, now=0, until=8)
        assert service.finish(Bundle.build(frame("first")))
        assert service.begin("oversized", sequence=1, now=1, until=8)
        large = frame("oversized", 1)
        large["diagnostic"] = "x" * 2048
        assert not service.finish(Bundle.build(large))
        assert service.overruns == 1 and not service.reservations
        assert service.query("first")["verdict"] == "compliant"
    finally:
        service.close()


def test_cancelled_operation_releases_only_its_reservation(tmp_path: Path) -> None:
    service = ObligationService(tmp_path / "state.sqlite", "reserve_static", 8192)
    try:
        assert service.begin("first", sequence=0, now=0, until=8)
        assert service.finish(Bundle.build(frame("first")))
        assert service.begin("failed", sequence=1, now=1, until=8)
        service.cancel("failed")
        assert service.query("first")["verdict"] == "compliant"
        with pytest.raises(ValueError):
            service.finish(Bundle.build(frame("failed")))
    finally:
        service.close()


def test_durable_tampering_cannot_change_audited_answer(tmp_path: Path) -> None:
    service = ObligationService(tmp_path / "state.sqlite", "reserve_lru", 8192)
    try:
        assert service.begin("first", sequence=0, now=0, until=8)
        assert service.finish(Bundle.build(frame("first")))
        row = service.db.execute("SELECT payload FROM state").fetchone()
        payload = json.loads(row[0])
        payload["now"] = "00000001"
        service.db.execute("UPDATE state SET payload=?", (encode(payload).encode(),))
        service.db.commit()
        with pytest.raises(ValueError, match="durable"):
            service.query("first")
    finally:
        service.close()


def test_duplicate_concurrent_and_nonmonotonic_ingress_is_rejected(tmp_path: Path) -> None:
    service = ObligationService(tmp_path / "state.sqlite", "reserve_static", 8192)
    try:
        assert service.begin("first", sequence=0, now=0, until=8)
        with pytest.raises(ValueError, match="concurrent"):
            service.begin("second", sequence=1, now=1, until=8)
        assert service.finish(Bundle.build(frame("first")))
        with pytest.raises(ValueError):
            service.begin("first", sequence=1, now=1, until=8)
        with pytest.raises(ValueError):
            service.advance(-1)
    finally:
        service.close()


def test_mandatory_durability_failure_does_not_accept_a_new_promise(tmp_path: Path) -> None:
    service = ObligationService(tmp_path / "state.sqlite", "reserve_static", 8192)
    service.db.execute(
        "CREATE TRIGGER fail BEFORE INSERT ON state BEGIN SELECT RAISE(FAIL,'fail'); END"
    )
    try:
        with pytest.raises(Exception, match="fail"):
            service.begin("first", sequence=0, now=0, until=8)
        assert not service.entries and not service.reservations and service.accepted == 0
    finally:
        service.close()
