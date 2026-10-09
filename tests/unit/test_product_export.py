"""Deterministic report exports from one authorized immutable ProductView."""

from __future__ import annotations

import json
import shutil
import socket
import sqlite3
import subprocess
from pathlib import Path

import pytest

from aletheia_lab.product import ProductError, ProductService
from aletheia_lab.product.results import persist_product_result
from aletheia_lab.product.view import ProductView

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "synthetic_p6_view.json"
_PROJECT_ID = "p3-project-" + "1" * 64
_FOREIGN_PROJECT_ID = "p3-project-" + "9" * 64
_SNAPSHOT_ID = "p3-snapshot-" + "2" * 64
_BASELINE_ID = "p3-snapshot-" + "3" * 64
_PENDING_RESULT_ID = "p6-result-" + "0" * 64


def _view(*, project_id: str = _PROJECT_ID) -> ProductView:
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    payload["demo_only"] = False
    payload["project"]["id"] = project_id
    payload["project"]["display_name"] = "Imported project"
    payload["snapshot"]["id"] = _SNAPSHOT_ID
    payload["snapshot"]["baseline_id"] = _BASELINE_ID
    for change in payload["snapshot"]["metric_changes"]:
        if change["before"] is not None:
            change["before"]["snapshot_id"] = _BASELINE_ID
        if change["after"] is not None:
            change["after"]["snapshot_id"] = _SNAPSHOT_ID
    payload["result"]["id"] = _PENDING_RESULT_ID
    payload["conversation"]["turns"][0]["snapshot_id"] = _SNAPSHOT_ID
    payload["conversation"]["turns"][0]["result_id"] = _PENDING_RESULT_ID
    for node in payload["graph"]["nodes"]:
        if node["kind"] == "Snapshot":
            node["source_id"] = _SNAPSHOT_ID
        elif node["kind"] == "Disposition":
            node["source_id"] = _PENDING_RESULT_ID
    return ProductView.model_validate_json(json.dumps(payload))


def _technical_failure_view() -> ProductView:
    payload = _view().model_dump(mode="json")
    payload["result"]["status"] = "technical_failure"
    payload["result"]["disposition"] = None
    payload["result"]["denominators"]["claims"] = 0
    payload["claims"] = []
    payload["conversation"]["turns"][-1]["missing_evidence"] = []
    payload["graph"]["nodes"] = [
        node for node in payload["graph"]["nodes"] if node["kind"] in {"Snapshot", "EvidenceItem"}
    ]
    payload["graph"]["edges"] = [
        edge for edge in payload["graph"]["edges"] if edge["kind"] == "OBSERVED_IN"
    ]
    return ProductView.model_validate_json(json.dumps(payload))


def _canonical_bytes(value: dict[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _semantic_text(value: str) -> str:
    return value.replace("`", "").replace("\\_", "_")


def _persist(store_root: Path, view: ProductView) -> ProductView:
    ProductService(store_root)
    return persist_product_result(store_root, view)


def _extract_pdf_text(tmp_path: Path, payload: bytes) -> str:
    pdfinfo = shutil.which("pdfinfo")
    pdftotext = shutil.which("pdftotext")
    assert pdfinfo is not None, "pdfinfo is required for the PDF contract test"
    assert pdftotext is not None, "pdftotext is required for the PDF contract test"
    report_path = tmp_path / "report.pdf"
    report_path.write_bytes(payload)
    info = subprocess.run(
        [pdfinfo, str(report_path)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert "PDF version:" in info.stdout
    extracted = subprocess.run(
        [pdftotext, "-layout", str(report_path), "-"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return extracted.stdout


def test_json_export_is_canonical_complete_and_restart_stable(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    original = _persist(store_root, _view())
    service = ProductService(store_root)

    first = service.export_report(original.result.id, "json")
    second = service.export_report(original.result.id, "json")
    restarted = ProductService(store_root).export_report(original.result.id, "json")

    assert first == second == restarted
    assert first == _canonical_bytes(original.model_dump(mode="json"))
    assert json.loads(first) == original.model_dump(mode="json")
    assert service.view(original.result.id) == original.model_dump(mode="json")


def test_markdown_and_pdf_have_semantic_parity_and_pdf_extracts(
    tmp_path: Path,
) -> None:
    store_root = tmp_path / "store"
    view = _persist(store_root, _view())
    service = ProductService(store_root)

    markdown_bytes = service.export_report(view.result.id, "markdown")
    pdf_bytes = service.export_report(view.result.id, "pdf")
    markdown = markdown_bytes.decode("utf-8")
    pdf_text = _extract_pdf_text(tmp_path, pdf_bytes)
    markdown_semantics = _semantic_text(markdown)
    pdf_semantics = _semantic_text(pdf_text)

    assert pdf_bytes.startswith(b"%PDF-1.4")
    assert pdf_bytes.endswith(b"%%EOF\n")
    shared_ids = {
        view.project.id,
        view.snapshot.id,
        view.snapshot.baseline_id,
        view.result.id,
        *(claim.id for claim in view.claims),
        *(evidence.id for evidence in view.evidence),
        *(evidence.reproduction_ref.record_id for evidence in view.evidence),
    }
    for identifier in shared_ids:
        assert identifier in markdown_semantics
        assert identifier in pdf_semantics
    for marker in (
        "deterministic_mock",
        "External call: false",
        "not_estimated",
        "null",
        "Allowed wording",
        "Forbidden wording",
        "Caveat",
        "Limitations",
        "Counterfactual comparison: not_available",
    ):
        assert marker in markdown_semantics
        assert marker.casefold() in pdf_semantics.casefold()
    for rendered in (markdown_semantics, pdf_semantics):
        assert f"Caveat: {view.result.caveat}" in rendered
        for claim in view.claims:
            citation_ids = ", ".join(claim.citation_ids) or "none"
            counterevidence_ids = ", ".join(claim.counterevidence_ids) or "none"
            assert f"Citation IDs: {citation_ids}" in rendered
            assert f"Counterevidence IDs: {counterevidence_ids}" in rendered


def test_pdf_and_markdown_are_byte_deterministic(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    view = _persist(store_root, _view())
    service = ProductService(store_root)

    for report_format in ("markdown", "pdf"):
        first = service.export_report(view.result.id, report_format)
        assert first == service.export_report(view.result.id, report_format)
        assert first == ProductService(store_root).export_report(view.result.id, report_format)


def test_technical_failure_exports_without_a_fabricated_diagnosis(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    view = _persist(store_root, _technical_failure_view())
    service = ProductService(store_root)

    exported_json = json.loads(service.export_report(view.result.id, "json"))
    markdown = service.export_report(view.result.id, "markdown").decode("utf-8")
    pdf_text = _extract_pdf_text(tmp_path, service.export_report(view.result.id, "pdf"))

    assert exported_json["result"]["status"] == "technical_failure"
    assert exported_json["result"]["disposition"] is None
    assert exported_json["claims"] == []
    for rendered in (markdown, pdf_text):
        semantics = _semantic_text(rendered)
        assert "technical_failure" in semantics
        assert "Disposition: null" in semantics
        assert "this result contains no diagnosis claim" in semantics


@pytest.mark.parametrize("report_format", ["", "JSON", "md", "pdf ", " text"])
def test_export_rejects_non_contract_formats_safely(
    tmp_path: Path,
    report_format: str,
) -> None:
    store_root = tmp_path / "store"
    view = _persist(store_root, _view())

    with pytest.raises(ProductError) as captured:
        ProductService(store_root).export_report(view.result.id, report_format)

    assert captured.value.code == "invalid_request"
    assert captured.value.safe_message == "The requested report format is not supported."
    if report_format:
        assert report_format not in captured.value.safe_message
    assert str(store_root) not in captured.value.safe_message


def test_export_inherits_foreign_and_tampered_result_fail_closed(tmp_path: Path) -> None:
    local_root = tmp_path / "local-store"
    foreign_root = tmp_path / "foreign-store"
    ProductService(local_root)
    ProductService(foreign_root)
    foreign = _persist(foreign_root, _view(project_id=_FOREIGN_PROJECT_ID))

    with pytest.raises(ProductError) as missing:
        ProductService(local_root).export_report(foreign.result.id, "json")
    assert missing.value.code == "record_not_found"
    assert foreign.result.id not in missing.value.safe_message

    local = _persist(local_root, _view())
    with sqlite3.connect(local_root / "project-store.sqlite3") as connection:
        connection.execute(
            "UPDATE product_records SET payload_json = ? WHERE record_id = ?",
            ('{"schema_version":"p6-result-envelope/v1","view":{}}', local.result.id),
        )
    with pytest.raises(ProductError) as tampered:
        ProductService(local_root).export_report(local.result.id, "markdown")
    assert tampered.value.code == "store_integrity_error"
    assert local.result.id not in tampered.value.safe_message
    assert str(local_root) not in tampered.value.safe_message


def test_export_rejects_malformed_result_id_without_reflection(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    malformed_id = "p6-result-private-text"

    with pytest.raises(ProductError) as captured:
        ProductService(store_root).export_report(malformed_id, "json")

    assert captured.value.code == "invalid_id"
    assert captured.value.safe_message == "The supplied product identifier is invalid."
    assert "private-text" not in captured.value.safe_message
    assert str(store_root) not in captured.value.safe_message


def test_export_has_no_network_or_source_write_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_root = tmp_path / "store"
    source_root = tmp_path / "source"
    source_root.mkdir()
    marker = source_root / "private-source-marker.txt"
    marker.write_bytes(b"must-remain-unchanged\n")
    before = (marker.read_bytes(), marker.stat().st_size, marker.stat().st_mtime_ns)
    view = _persist(store_root, _view())

    def reject_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("export attempted a network connection")

    monkeypatch.setattr(socket, "socket", reject_network)
    monkeypatch.setattr(socket, "create_connection", reject_network)
    service = ProductService(store_root)
    exports = b"\n".join(
        service.export_report(view.result.id, report_format)
        for report_format in ("json", "markdown", "pdf")
    )

    assert (marker.read_bytes(), marker.stat().st_size, marker.stat().st_mtime_ns) == before
    assert str(store_root).encode() not in exports
    assert str(source_root).encode() not in exports
    assert b"CAUSES" not in exports
    assert b"RELATED_TO" not in exports
