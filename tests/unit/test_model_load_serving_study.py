"""Bounded opt-in study contracts; no MLServer or historical study execution."""

from __future__ import annotations

import copy
import json
import subprocess
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aletheia_lab.evaluation import model_load_serving_analysis as analysis
from aletheia_lab.evaluation import model_load_serving_study as study
from aletheia_lab.project.identity import content_sha256

ROOT = Path(__file__).resolve().parents[2]


def test_fixed_balanced_census_and_protocol() -> None:
    configs = study.configurations()
    assert len(configs) == 144
    assert len({tuple(c.values()) for c in configs}) == 144
    assert Counter(c["arm"] for c in configs) == {
        "native": 18,
        "hash": 18,
        "static": 54,
        "full": 54,
    }
    assert all(c["horizon"] == 0 for c in configs if c["arm"] in ("native", "hash"))
    assert sum(12 * (1 + c["inferences"]) for c in configs) == 15552
    protocol = study.protocol(ROOT)
    assert protocol["qualified_missing_file_http_status"] == 422
    assert protocol["provider_calls"] == 0


def test_protocol_disagreement_fails_before_environment(tmp_path: Path) -> None:
    path = tmp_path / study.PROTOCOL
    path.parent.mkdir(parents=True)
    payload = study.protocol(ROOT)
    payload["repetitions"] = 100
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="disagree"):
        study.protocol(tmp_path)


def test_child_credentials_filtered_and_thread_budgets_fixed(monkeypatch: Any) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("PRIVATE_PASSWORD", "not-a-real-password")
    monkeypatch.setenv("TEST_TOKEN", "not-a-real-token")
    env = study.child_environment(ROOT)
    assert not any(key in env for key in ("OPENAI_API_KEY", "PRIVATE_PASSWORD", "TEST_TOKEN"))
    assert env["OMP_NUM_THREADS"] == "1" and env["PYTHONHASHSEED"] == "1"


@pytest.mark.parametrize("stdout", ["", "[]", "null", '"bad"', '{"config":{}}'])
def test_invalid_worker_shape_retained(monkeypatch: Any, tmp_path: Path, stdout: str) -> None:
    monkeypatch.setattr(
        study.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=stdout)
    )
    result = study.execute_worker(ROOT, tmp_path, 0, study.configurations()[0])
    assert result["status"] == "invalid_worker_result"


def test_worker_timeout_failure_and_complete(monkeypatch: Any, tmp_path: Path) -> None:
    config = study.configurations()[0]

    def timeout(*args: Any, **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired("private-command", 90)

    monkeypatch.setattr(study.subprocess, "run", timeout)
    assert study.execute_worker(ROOT, tmp_path, 0, config)["status"] == "timeout"
    monkeypatch.setattr(
        study.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="")
    )
    assert study.execute_worker(ROOT, tmp_path, 0, config)["status"] == "worker_failure"
    monkeypatch.setattr(
        study.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps({"config": config})),
    )
    assert study.execute_worker(ROOT, tmp_path, 0, config)["status"] == "complete"
    monkeypatch.setattr(
        study.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0, stdout=json.dumps({"config": config, "status": "runtime_worker_failure"})
        ),
    )
    assert study.execute_worker(ROOT, tmp_path, 0, config)["status"] == "runtime_worker_failure"


def test_failed_workers_remain_in_audit_denominators() -> None:
    workers = [{"config": config, "status": "timeout"} for config in study.configurations()]
    result = analysis.analyze(workers, {})
    assert result["worker_counts"] == {"timeout": 144}
    assert sum(a["denominator"] for cell in result["cells"] for a in cell["audit"].values()) == 5616
    assert sum(a["unknown"] for cell in result["cells"] for a in cell["audit"].values()) == 5616
    assert all(row["cheapest_measured_key"] is None for row in result["frontier"])


@pytest.fixture
def sealed(tmp_path: Path, monkeypatch: Any) -> tuple[Path, Path]:
    root, store = tmp_path / "root", tmp_path / "private"
    root.mkdir()
    store.mkdir()
    (store / "artifacts").mkdir()
    code = root / "source.py"
    code.write_text("# fixed\n", encoding="utf-8")
    path = root / study.PROTOCOL
    path.parent.mkdir(parents=True)
    path.write_bytes((ROOT / study.PROTOCOL).read_bytes())
    monkeypatch.setattr(study, "CODE", ("source.py",))
    plan = {
        "protocol": study.protocol(root),
        "configs": study.configurations(),
        "code_sha256": study.bindings(root),
    }
    plan["plan_sha256"] = study.digest(plan)
    study.write_new(store / "plan.json", plan)
    models: dict[str, Any] = {"bands": {}}
    for depth in (2, 6, 10):
        band = {}
        for name in ("A", "B"):
            payload = f"owned-test-{depth}-{name}".encode()
            file = f"{depth}-{name}.test-bytes"
            (store / "artifacts" / file).write_bytes(payload)
            band[name] = {"path": file, "bytes": len(payload), "digest": content_sha256(payload)}
        models["bands"][str(depth)] = band
    study.write_new(store / "models.json", models)
    workers = [{"config": cfg, "status": "timeout"} for cfg in plan["configs"]]
    for index, result in enumerate(workers):
        study.write_new(store / f"result-{index:03d}.json", result)
    result = {
        "plan_sha256": plan["plan_sha256"],
        "models_sha256": study.digest(models),
        "workers": workers,
        "analysis": analysis.analyze(workers, models),
    }
    result["results_sha256"] = study.digest(result)
    study.write_new(store / "results.json", result)
    return root, store


def test_hash_only_replay_no_execution_and_no_mutation(sealed: Any, monkeypatch: Any) -> None:
    root, store = sealed
    before = {path.name: path.read_bytes() for path in store.glob("*.json")}
    monkeypatch.setattr(
        study, "environment", lambda: pytest.fail("replay must not need optional runtime")
    )
    monkeypatch.setattr(
        study.subprocess, "run", lambda *a, **k: pytest.fail("replay cannot start worker")
    )
    result = study.verify(root, store)
    assert result["verification"] == "pass" and result["native_calls_during_verify"] == 0
    assert {path.name: path.read_bytes() for path in store.glob("*.json")} == before


@pytest.mark.parametrize(
    "target",
    ["plan.json", "models.json", "results.json", "result-001.json", "source.py", "artifact"],
)
def test_tamper_fails_closed(sealed: Any, target: str) -> None:
    root, store = sealed
    if target == "source.py":
        path = root / target
    elif target == "artifact":
        path = next((store / "artifacts").iterdir())
    else:
        path = store / target
    path.write_bytes(path.read_bytes() + b" ")
    if target in ("plan.json", "models.json", "results.json", "result-001.json"):
        # JSON whitespace is irrelevant to canonical content, so mutate an actual value.
        value = json.loads(path.read_text(encoding="utf-8"))
        value["changed"] = True
        path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        study.verify(root, store)


def test_analysis_tamper_even_with_resealed_result_rejected(sealed: Any) -> None:
    root, store = sealed
    path = store / "results.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value.pop("results_sha256")
    value["analysis"]["disposition"] = "invented_gain"
    value["results_sha256"] = study.digest(value)
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="analysis replay"):
        study.verify(root, store)


def test_nearest_rank_and_commitment_separation() -> None:
    assert analysis.quantile([], 0.95) is None
    assert analysis.quantile(list(range(9)), 0.95) == 8
    assert analysis.metrics([1, 3])["median_ns"] == 2
    truth = {"verdict": "violation", "eligibility": "load", "resident": None}
    assert analysis.compare(copy.deepcopy(truth), truth) == (True, False)
    assert analysis.compare(
        {"verdict": "unknown", "eligibility": "load", "resident": None}, truth
    ) == (False, False)
    assert analysis.compare(
        {"verdict": "compliant", "eligibility": "load", "resident": None}, truth
    ) == (False, True)
