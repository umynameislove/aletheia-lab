"""Offline worker checks; subprocesses receive synthetic data through pipes."""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aletheia_lab.hosted import worker
from aletheia_lab.product.reports import render_product_report
from aletheia_lab.product.view import ProductView

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "src/aletheia_lab/product/synthetic_p6_view.json"


@pytest.mark.parametrize("report_format", ["json", "markdown", "pdf"])
def test_separate_worker_process_matches_existing_domain_renderer(report_format):
    raw = FIXTURE.read_bytes()
    view = ProductView.model_validate_json(raw)
    process = subprocess.Popen(
        [sys.executable, "-B", "-m", "aletheia_lab.hosted.worker", report_format],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=ROOT,
    )
    try:
        result, errors = process.communicate(input=raw, timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise
    assert process.pid != os.getpid()
    assert process.returncode == 0
    assert errors == b""
    assert result == render_product_report(view, report_format)


def test_invalid_input_returns_no_output_and_no_diagnostics():
    result = subprocess.run(
        [sys.executable, "-B", "-m", "aletheia_lab.hosted.worker", "json"],
        input=b'{"private":"synthetic-secret","private":"duplicate"}',
        capture_output=True, cwd=ROOT, timeout=30, check=False,
    )
    assert result.returncode == 2
    assert result.stdout == b""
    assert result.stderr.replace(b"\r\n", b"\n") == b"worker_invalid_input\n"
    assert b"synthetic-secret" not in result.stderr


def test_unsupported_format_is_not_enabled():
    out, err = io.BytesIO(), io.StringIO()
    code = worker.main(["html"], stdin=io.BytesIO(FIXTURE.read_bytes()), stdout=out, stderr=err)
    assert code == 2
    assert out.getvalue() == b""
    assert err.getvalue() == "worker_invalid_format\n"


def test_input_and_output_budgets_fail_before_publication(monkeypatch):
    out, err = io.BytesIO(), io.StringIO()
    monkeypatch.setattr(worker, "MAX_INPUT_BYTES", 5)
    assert worker.main(["json"], stdin=io.BytesIO(b"x" * 6), stdout=out, stderr=err) == 2
    assert out.getvalue() == b""
    assert err.getvalue() == "worker_invalid_input\n"

    monkeypatch.setattr(worker, "MAX_INPUT_BYTES", 16 * 1024 * 1024)
    monkeypatch.setattr(worker, "MAX_OUTPUT_BYTES", 5)
    out, err = io.BytesIO(), io.StringIO()
    assert worker.main(["markdown"], stdin=io.BytesIO(FIXTURE.read_bytes()), stdout=out, stderr=err) == 2
    assert out.getvalue() == b""
    assert err.getvalue() == "worker_output_too_large\n"


def test_partial_output_is_failure_not_success():
    class PartialOutput(io.BytesIO):
        def write(self, data):
            super().write(data[:1])
            return 1

    out, err = PartialOutput(), io.StringIO()
    code = worker.main(["json"], stdin=io.BytesIO(FIXTURE.read_bytes()), stdout=out, stderr=err)
    assert code == 2
    assert err.getvalue() == "worker_failed\n"
