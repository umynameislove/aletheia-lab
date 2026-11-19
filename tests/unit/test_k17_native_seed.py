"""Seed geometry, physical source diversity and early unsupported-primary gate."""

from __future__ import annotations

import asyncio
import hashlib
import io
import zipfile

import pytest
from pydantic import SecretStr, ValidationError

from aletheia_lab.hosted.capacity_seed import SeedLimits, SeedRejected, _Budget, seed_native
from aletheia_lab.hosted.capacity_seed_domain import source_payload
from aletheia_lab.hosted.capacity_seed_models import SeedPlan


def test_real_source_bytes_are_repeatable_diverse_and_independently_stratified():
    plan = SeedPlan(namespace="k17_unitseed1")
    hashes = set()
    rows = []
    for project in range(3):
        for generation in range(2):
            payload = source_payload(plan, project, generation)
            assert payload == source_payload(plan, project, generation)
            hashes.add(hashlib.sha256(payload).hexdigest())
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                data = archive.read("dataset.csv").decode().splitlines()
                assert len(data) - 1 == plan.source_rows[plan.size(project)]
                metric = archive.read("metrics.csv").decode().splitlines()
                assert len(metric) - 1 == 2 * plan.metric_series[plan.size(project)]
                if generation == 0:
                    rows.append(len(payload))
    assert len(hashes) == 6 and rows[0] < rows[1] < rows[2]


@pytest.mark.parametrize(
    "change",
    [
        {"source_rows": {"S": 1, "M": 1, "L": 2}},
        {"metric_series": {"S": 1, "M": 2, "L": 33}},
        {"size_cycle": ["S"]},
        {"workspaces": 2001},
        {"idle_seconds": 900, "absolute_seconds": 60},
    ],
)
def test_invalid_or_false_strata_are_rejected(change):
    with pytest.raises(ValidationError):
        SeedPlan.model_validate_json(
            __import__("json").dumps({"namespace": "k17_unitseed1", **change})
        )


def test_full_cardinality_does_not_claim_unsupported_primary_dimensions():
    plan = SeedPlan(
        namespace="k17_fullseed1",
        workspaces=2000,
        projects_per_workspace=5,
        generations=20,
        results_per_generation=10,
        size_cycle=("S",) * 12 + ("M",) * 7 + ("L",),
        source_rows={"S": 1000, "M": 10000, "L": 100000},
    )
    assert (plan.principals, plan.projects, plan.snapshots, plan.results) == (
        50000,
        10000,
        200000,
        2000000,
    )
    assert plan.total_source_rows == 1820000000
    assert any("analyzer" in gap for gap in plan.primary_gaps())


def test_primary_unsupported_gate_has_no_database_or_filesystem_mutation(tmp_path):
    class NoDatabase:
        def connection(self):
            raise AssertionError("Primary gap must fail before database access.")

    plan = SeedPlan(namespace="k17_unitseed1")
    output = tmp_path / "must-not-exist"
    with pytest.raises(SeedRejected):
        asyncio.run(
            seed_native(
                NoDatabase(),
                schema="k17_seed_" + "a" * 32,
                output=output,
                plan=plan,
                csrf_secret=SecretStr("synthetic-test-csrf-key-with-at-least32chars"),
                limits=SeedLimits(500 * 1024**2, 10000, 100),
                require_primary=True,
            )
        )
    assert not output.exists()


def test_private_actor_inventory_is_included_in_the_finite_byte_guard(tmp_path):
    (tmp_path / "actors.jsonl").write_bytes(b"x" * 2048)
    budget = _Budget(SeedLimits(1024, 1000, 100, free_reserve_bytes=1), tmp_path, None)
    with pytest.raises(SeedRejected):
        budget.quick()
