"""Local collector CLI publishes only private aggregate results, without any SDK."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from aletheia_lab.evaluation.model_load_provenance import document_digest

ROOT = Path(__file__).resolve().parents[2]


def run(output, *extra):
    env = {key: value for key, value in os.environ.items() if not key.startswith("OPENAI_")}
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/model_load_evidence_development.py"),
            "collector",
            "--root",
            str(ROOT),
            "--output",
            str(output),
            "--repeats",
            "1",
            *extra,
        ],
        env=env,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_cli_writes_self_hashing_aggregate_once_and_no_private_path_to_stdout(tmp_path):
    output = tmp_path / "report.json"
    completed = run(output)
    assert completed.returncode == 0, completed.stderr
    public = json.loads(completed.stdout)
    assert public["executed_episode_runs"] == 100
    assert str(tmp_path) not in completed.stdout
    report = json.loads(output.read_text())
    digest = report.pop("report_sha256")
    assert digest == document_digest(report)
    assert public["provider_calls"] == public["native_loader_entries"] == 0
    before = output.read_bytes()
    duplicate = run(output)
    assert duplicate.returncode == 1
    assert output.read_bytes() == before


def test_cli_rejects_public_destination_and_unbounded_work(tmp_path):
    output = ROOT / "not-created-private-report.json"
    result = run(output)
    assert result.returncode == 1 and not output.exists()
    output = tmp_path / "report.json"
    result = run(output, "--repeats", "6")
    assert result.returncode == 1 and not output.exists()
