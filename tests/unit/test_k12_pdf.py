"""Real text PDFs preserve authorized v2 meaning and contain no executable content."""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import subprocess
import sys
import unicodedata
from decimal import Decimal
from pathlib import Path

import pytest
from pypdf import PdfReader
from test_product_metric_dimensions import _view

from aletheia_lab.hosted.report_renderer import ReportRenderer
from aletheia_lab.hosted.request_boundary import RequestRejected
from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.reports import render_product_report
from aletheia_lab.product.view import parse_product_view_json

pytest.importorskip("reportlab")
ROOT = Path(__file__).resolve().parents[2]
VIETNAMESE = "Dự án kiểm chứng tiếng Việt: dữ liệu, bằng chứng, kết quả"


@pytest.mark.parametrize("normalization", ["NFC", "NFD"])
def test_real_unicode_pdf_embeds_font_and_copyable_vietnamese_with_v2_parity(normalization):
    payload = _view().model_dump(mode="json")
    payload["project"]["display_name"] = unicodedata.normalize(normalization, VIETNAMESE)
    view = parse_product_view_json(json.dumps(payload, ensure_ascii=False))
    pdf = render_product_report(view, "pdf")
    assert pdf == render_product_report(view, "pdf")
    reader = PdfReader(io.BytesIO(pdf), strict=True)
    copied = unicodedata.normalize("NFC", "\n".join(page.extract_text() for page in reader.pages))
    assert VIETNAMESE in copied
    assert "không bệnh" in " ".join(copied.split())
    assert re.search(r"unit_origin=\s*source", copied) and re.search(r"unit=\s*%", copied)
    for identity in (view.project.id, view.snapshot.id, view.result.id,
                     *(claim.id for claim in view.claims), *(evidence.id for evidence in view.evidence)):
        assert identity in copied
    assert json.loads(render_product_report(view, "json")) == view.model_dump(mode="json")
    assert unicodedata.normalize(normalization, VIETNAMESE) in render_product_report(view, "markdown").decode()
    fonts = [font.get_object() for page in reader.pages for font in page["/Resources"]["/Font"].values()]
    assert any("/ToUnicode" in font and "/FontFile2" in font["/FontDescriptor"] for font in fonts)
    assert not any(page.get("/Annots") for page in reader.pages)
    assert "/OpenAction" not in reader.trailer["/Root"]


def test_links_images_long_tokens_and_project_text_are_inert_pdf_text():
    payload = _view().model_dump(mode="json")
    token = "x" * 800
    payload["conversation"]["turns"][0]["answer"] = (
        "https://127.0.0.1:9/pixel.png <img src=file:///etc/passwd> `literal``ticks` " + token
    )
    pdf = render_product_report(parse_product_view_json(json.dumps(payload)), "pdf")
    reader = PdfReader(io.BytesIO(pdf), strict=True)
    text = "".join(page.extract_text() for page in reader.pages)
    assert "https://127.0.0.1:9/pixel.png" in text
    assert "<img src=file:///etc/passwd> `literal``ticks`" in text
    assert token in "".join(text.split())
    assert not any(page.get("/Annots") for page in reader.pages)
    assert "/EmbeddedFiles" not in reader.trailer["/Root"].get("/Names", {})


def test_missing_pdf_dependency_and_glyph_are_typed_refusals(monkeypatch):
    from aletheia_lab.product import pdf

    with pytest.raises(ProductError) as missing_glyph:
        pdf.render_text_pdf((("\U0001f680",),))
    assert missing_glyph.value.code == "pdf_unsupported_text"
    monkeypatch.setattr(pdf, "pdf_available", lambda: False)
    with pytest.raises(ProductError) as missing:
        render_product_report(_view(), "pdf")
    assert missing.value.code == "feature_unavailable"


@pytest.mark.parametrize("before,after", [(0.0, 1e-16), (-0.5, -0.6), (1e-16, 2e-16)])
def test_report_formats_do_not_round_zero_negative_or_tiny_metric_operands(before, after):
    payload = _view().model_dump(mode="json")
    definition = payload["snapshot"]["metric_definitions"][0]
    definition.update(direction="higher_is_better", regression_threshold=0.0)
    change = payload["snapshot"]["metric_changes"][0]
    change["before"]["value"], change["after"]["value"] = before, after
    change["delta"] = float(Decimal(str(after)) - Decimal(str(before)))
    change["kind"] = "decreased" if after < before else "increased"
    change["adverse_status"] = "adverse" if after < before else "not_adverse"
    view = parse_product_view_json(json.dumps(payload))
    reader = PdfReader(io.BytesIO(render_product_report(view, "pdf")))
    text = "\n".join(page.extract_text() for page in reader.pages)
    markdown = render_product_report(view, "markdown").decode()
    for value in (before, after, change["delta"]):
        assert json.dumps(value) in text and json.dumps(value) in markdown
    assert json.loads(render_product_report(view, "json"))["snapshot"]["metric_changes"][0] == change


def test_v2_pdf_uses_real_isolated_child_and_preserves_venv_interpreter(monkeypatch):
    children = []
    original = asyncio.create_subprocess_exec

    async def tracked(*args, **kwargs):
        assert args[0] == str(Path(sys.executable).absolute())
        assert "K12_PRIVATE_MARKER" not in kwargs["env"]
        child = await original(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setenv("K12_PRIVATE_MARKER", "synthetic-not-for-child")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", tracked)

    async def run():
        renderer = ReportRenderer(timeout_seconds=15)
        try:
            view = _view()
            assert await renderer.render("pdf", view.model_dump_json().encode()) == render_product_report(view, "pdf")
        finally:
            await renderer.aclose()
        assert len(children) == 1 and children[0].pid != os.getpid() and children[0].returncode == 0
        assert not renderer._worker._tasks

    asyncio.run(run())


@pytest.mark.parametrize("operation", ["socket.socket()", "subprocess.Popen([sys.executable, '-c', 'pass'])"])
def test_actual_child_policy_denies_network_and_program_launch(operation):
    program = ("import socket,subprocess,sys\n"
               "from aletheia_lab.hosted.report_sandbox import restrict_report_process\n"
               "restrict_report_process()\n"
               "try:\n " + operation + "\n"
               "except PermissionError:\n sys.stdout.buffer.write(b'blocked\\n')\n"
               "else:\n raise AssertionError('External effect was allowed')\n")
    result = subprocess.run([sys.executable, "-B", "-c", program], cwd=ROOT, capture_output=True, timeout=10)
    assert result.returncode == 0 and result.stdout == b"blocked\n" and result.stderr == b""


def test_real_child_unsupported_glyph_is_safe_refusal_and_json_still_works():
    async def run():
        payload = _view().model_dump(mode="json")
        payload["project"]["display_name"] = "Unsupported font glyph \U0001f680"
        raw = json.dumps(payload, ensure_ascii=False).encode()
        renderer = ReportRenderer()
        try:
            with pytest.raises(RequestRejected) as error:
                await renderer.render("pdf", raw)
            assert (error.value.status, error.value.code) == (400, "invalid_request")
            assert "\U0001f680" not in error.value.safe_message
            assert json.loads(await renderer.render("json", raw)) == payload
        finally:
            await renderer.aclose()
    asyncio.run(run())
