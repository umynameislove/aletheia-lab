"""A debug fixture cannot cause a real HTTP run labelled as primary."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from test_k17_capacity import _actor, _profile


@pytest.mark.parametrize("declared_qualified", [False, True, "incomplete"])
def test_primary_debug_manifest_refused_before_client_and_output_creation(
    tmp_path, monkeypatch, capsys, declared_qualified
):
    spec = importlib.util.spec_from_file_location(
        "k17_capacity_cli", Path(__file__).resolve().parents[2] / "scripts/k17_capacity.py"
    )
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    actor = _actor()
    raw = {
        **actor.model_dump(mode="json"),
        "cookie": actor.cookie.get_secret_value(),
        "csrf": actor.csrf.get_secret_value(),
    }
    actors = tmp_path / "actors.jsonl"
    actors.write_text(json.dumps(raw) + "\n")
    actors.chmod(0o600)
    manifest = {
        "actors_file_sha256": hashlib.sha256(actors.read_bytes()).hexdigest(),
        "primary_dataset_qualified": declared_qualified,
        "logical_counts": {"principals": 1},
        "strata": {},
        "physical": {},
    }
    if declared_qualified == "incomplete":
        manifest.update(primary_dataset_qualified=True,
                        logical_counts=dict(principals=50000, workspaces=2000, projects=10000,
                                            snapshots=200000, results=2000000),
                        strata={size: dict(source_rows_per_generation=rows, actual_claims_per_result=rows // 10,
                                           actual_graph_nodes_per_result=rows // 5 + 2)
                                for size, rows in (("S", 1000), ("M", 10000), ("L", 100000))},
                        physical=dict(all_source_payload_hashes_verified=True, all_object_file_hashes_verified=True),
                        shared_workspace_active_memberships={"shared": 50, "hot": 500})
        assert cli._primary_dataset(manifest)
        (tmp_path / "INCOMPLETE").write_text("Synthetic interrupted catalog\n")
    data = tmp_path / "dataset.json"
    data.write_text(json.dumps(manifest))
    profile = _profile().model_copy(
        update={"scope": "primary", "dataset_sha256": hashlib.sha256(data.read_bytes()).hexdigest()}
    )
    settings = tmp_path / "profile.json"
    settings.write_text(profile.model_dump_json())
    called = []

    def forbidden(*args, **kwargs):
        called.append(True)
        raise AssertionError("No HTTP client/harness can start with a debug primary dataset")

    monkeypatch.setattr(cli, "CapacityHarness", forbidden)
    target = tmp_path / "run"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "k17_capacity.py",
            "run",
            "--profile",
            str(settings),
            "--actors",
            str(actors),
            "--dataset",
            str(data),
            "--output",
            str(target),
        ],
    )
    assert cli.main() == 2 and not called and not target.exists()
    assert "PRIVATE" not in capsys.readouterr().out
