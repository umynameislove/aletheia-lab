from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from aletheia_lab.evaluation.model_load_provenance import document_digest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/accept_model_load_research.py"
FIXTURE = ROOT / "tests/fixtures/research_acceptance/synthetic_product_view.json"


def run(*args: str, seed: str = "1") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONHASHSEED": seed},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_synthetic_projection_is_process_hash_seed_stable_and_read_only() -> None:
    before = FIXTURE.read_bytes()
    args = ("synthetic-view", "--reference", str(FIXTURE), "--candidate", str(FIXTURE))
    first, second = run(*args), run(*args, seed="104729")
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    result = json.loads(first.stdout)
    assert result["product_consumer_integration"] == "not_executed"
    assert result["independent_families"] is None
    assert result["provider_calls"] == 0
    assert FIXTURE.read_bytes() == before


def test_cli_rejects_changed_scope_without_private_error_output(tmp_path: Path) -> None:
    candidate = json.loads(FIXTURE.read_text(encoding="utf-8"))
    candidate["result"]["raw_hex"] = "PRIVATE_ERROR_CANARY"
    path = tmp_path / "private-view.json"
    path.write_text(json.dumps(candidate), encoding="utf-8")
    result = run("synthetic-view", "--reference", str(FIXTURE), "--candidate", str(path))
    assert result.returncode == 1
    assert json.loads(result.stdout) == {"status": "research_acceptance_failed_closed"}
    assert "PRIVATE" not in result.stdout + result.stderr
    assert str(tmp_path) not in result.stdout + result.stderr


def test_cli_identity_only_never_claims_exact_replay(tmp_path: Path) -> None:
    counts = {
        "correct_identified": 0,
        "false_compliance": 0,
        "false_violation": 0,
        "verdict_counts": {"unknown": 1},
    }
    data = {
        "schema_version": "model-load-observability-development/v1",
        "provider_calls": 0,
        "summary": {
            "eligible_load_attempts": 1,
            "episode_count": 1,
            "no_new_load_attempts": 0,
            "comparisons": {"native": {"S": counts, "T": counts}},
        },
        "disposition": "narrow_controlled_feasibility_no_new_method_evidence",
        "raw_hex": "PRIVATE_REPORT_CANARY",
    }
    digest = document_digest(data)
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps({**data, "report_sha256": digest}), encoding="utf-8")
    before = path.read_bytes()
    args = (
        "receipt",
        "--root",
        str(ROOT),
        "--kind",
        "observability",
        "--receipt",
        str(path),
        "--expected-sha256",
        digest,
    )
    inspected = run(*args, "--identity-only")
    assert inspected.returncode == 0, inspected.stdout + inspected.stderr
    result = json.loads(inspected.stdout)
    assert result["status"] == "pinned_summary_only_not_replayed"
    assert result["fresh_local_rule_checks"] is False
    assert result["native_loads_replayed"] == 0
    assert "PRIVATE" not in inspected.stdout
    # A pin is not a substitute for the original row/code/environment replay.
    failed = run(*args)
    assert failed.returncode == 1 and "PRIVATE" not in failed.stdout + failed.stderr
    assert path.read_bytes() == before


def test_cli_invalid_json_and_missing_runtime_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    path.write_text('{"private":"PRIVATE_CANARY","x":1,"x":2}', encoding="utf-8")
    result = run(
        "receipt",
        "--root",
        str(ROOT),
        "--kind",
        "application",
        "--receipt",
        str(path),
        "--expected-sha256",
        "a" * 64,
    )
    assert result.returncode == 1
    assert result.stdout.strip() == '{"status": "research_acceptance_failed_closed"}'
    assert "PRIVATE" not in result.stdout + result.stderr


def test_cli_wrong_nested_types_do_not_expose_a_traceback(tmp_path: Path) -> None:
    data = {
        "schema_version": "model-load-observability-development/v1",
        "provider_calls": 0,
        "summary": [],
        "disposition": "narrow_controlled_feasibility_no_new_method_evidence",
    }
    digest = document_digest(data)
    path = tmp_path / "PRIVATE_CANARY.json"
    path.write_text(json.dumps({**data, "report_sha256": digest}), encoding="utf-8")
    result = run(
        "receipt",
        "--root",
        str(ROOT),
        "--kind",
        "observability",
        "--receipt",
        str(path),
        "--expected-sha256",
        digest,
        "--identity-only",
    )
    assert result.returncode == 1
    assert result.stdout.strip() == '{"status": "research_acceptance_failed_closed"}'
    assert "PRIVATE" not in result.stdout + result.stderr and "Traceback" not in result.stderr
