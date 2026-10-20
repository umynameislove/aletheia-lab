"""Deterministic offline report renderers for authorized product views."""

from __future__ import annotations

import json
import textwrap
from collections.abc import Iterable
from typing import Final

from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.view import ProductMetricObservation, ProductView

_SUPPORTED_FORMATS: Final[frozenset[str]] = frozenset({"json", "markdown", "pdf"})
_UNSUPPORTED_FORMAT_MESSAGE: Final[str] = "The requested report format is not supported."
_PDF_LINES_PER_PAGE: Final[int] = 55
_PDF_LINE_WIDTH: Final[int] = 86


def render_product_report(view: ProductView, report_format: str) -> bytes:
    """Render one already-authorized immutable view without external I/O."""

    if report_format not in _SUPPORTED_FORMATS:
        raise ProductError("invalid_request", _UNSUPPORTED_FORMAT_MESSAGE)
    if report_format == "json":
        return _canonical_json(view)
    markdown = _markdown_report(view)
    if report_format == "markdown":
        return markdown.encode("utf-8")
    return _pdf_report(markdown)


def _canonical_json(view: ProductView) -> bytes:
    return json.dumps(
        view.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _markdown_report(view: ProductView) -> str:
    lines = [
        "# Aletheia Lab Product Report",
        "",
        "Generated from one immutable diagnosis-visible ProductView.",
        "",
        "## Scope",
        "",
        f"- Schema version: {_code(view.schema_version)}",
        f"- Project ID: {_code(view.project.id)}",
        f"- Project name: {_text(view.project.display_name)}",
        f"- Mode: {_code(view.mode)}",
        f"- Visibility: {_code(view.visibility)}",
        f"- Demo only: {_scalar(view.demo_only)}",
        f"- Snapshot ID: {_code(view.snapshot.id)}",
        f"- Baseline ID: {_code(view.snapshot.baseline_id)}",
        f"- Snapshot SHA-256: {_code(view.snapshot.sha256)}",
        "",
        "## Runtime",
        "",
        f"- Provider: {_code(view.runtime.provider)}",
        f"- Model: {_code(view.runtime.model)}",
        f"- External call: {_scalar(view.runtime.external_call)}",
        "",
        "## Result",
        "",
        f"- Result ID: {_code(view.result.id)}",
        f"- Status: {_code(view.result.status)}",
        f"- Disposition: {_nullable(view.result.disposition)}",
        f"- Causal status: {_code(view.result.causal_status)}",
        f"- Summary: {_text(view.result.summary)}",
        f"- Independent families: {_not_estimated(view.result.denominators.independent_families)}",
        f"- Contexts: {view.result.denominators.contexts}",
        f"- Outputs: {view.result.denominators.outputs}",
        f"- Claims: {view.result.denominators.claims}",
        f"- Allowed wording: {_text(view.result.allowed_wording)}",
        f"- Forbidden wording: {_text(view.result.forbidden_wording)}",
        f"- Caveat: {_text(view.result.caveat)}",
        "",
        "## Metric definitions",
        "",
    ]
    if view.snapshot.metric_definitions:
        for definition in view.snapshot.metric_definitions:
            lines.append(
                "- "
                f"{_code(definition.metric_name)}; direction={_code(definition.direction)}; "
                f"regression_threshold={_scalar(definition.regression_threshold)}"
            )
    else:
        lines.append("- None.")
    lines.extend(["", "## Metric changes", ""])
    if view.snapshot.metric_changes:
        for change in view.snapshot.metric_changes:
            lines.extend(
                [
                    f"### Metric change {_code(change.evidence_id)}",
                    "",
                    f"- Metric: {_code(change.metric_name)}",
                    f"- Kind: {_code(change.kind)}",
                    f"- Before: {_observation(change.before)}",
                    f"- After: {_observation(change.after)}",
                    f"- Delta: {_nullable(change.delta)}",
                    f"- Adverse status: {_code(change.adverse_status)}",
                    "",
                ]
            )
    else:
        lines.extend(["- None.", ""])
    _append_turns(lines, view)
    _append_claims(lines, view)
    _append_evidence(lines, view)
    _append_graph(lines, view)
    _append_limitations(lines, view)
    return "\n".join(lines).rstrip() + "\n"


def _append_turns(lines: list[str], view: ProductView) -> None:
    lines.extend(["## Conversation", "", f"- Conversation ID: {_code(view.conversation.id)}", ""])
    for turn in view.conversation.turns:
        lines.extend(
            [
                f"### Turn {_code(turn.id)}",
                "",
                f"- Snapshot ID: {_code(turn.snapshot_id)}",
                f"- Result ID: {_code(turn.result_id)}",
                f"- Runtime provider: {_code(turn.runtime.provider)}",
                f"- Runtime model: {_code(turn.runtime.model)}",
                f"- External call: {_scalar(turn.runtime.external_call)}",
                f"- Question: {_text(turn.question)}",
                f"- Answer: {_text(turn.answer)}",
                f"- Abstained: {_scalar(turn.abstained)}",
                f"- Visible evidence IDs: {_ids(turn.visible_evidence_ids)}",
                f"- Missing evidence: {_values(turn.missing_evidence)}",
                "",
            ]
        )


def _append_claims(lines: list[str], view: ProductView) -> None:
    lines.extend(["## Claims", ""])
    if not view.claims:
        lines.extend(["- None; this result contains no diagnosis claim.", ""])
        return
    for claim in view.claims:
        lines.extend(
            [
                f"### Claim {_code(claim.id)}",
                "",
                f"- Turn ID: {_code(claim.turn_id)}",
                f"- Text: {_text(claim.text)}",
                f"- Claim type: {_code(claim.claim_type)}",
                f"- Epistemic state: {_code(claim.epistemic_state)}",
                f"- Support: {_code(claim.support)}",
                f"- Citation IDs: {_ids(claim.citation_ids)}",
                f"- Counterevidence IDs: {_ids(claim.counterevidence_ids)}",
                f"- Missing evidence: {_values(claim.missing_evidence)}",
                f"- Maximum strength: {_code(claim.max_strength)}",
                "",
            ]
        )


def _append_evidence(lines: list[str], view: ProductView) -> None:
    lines.extend(["## Visible evidence", ""])
    if not view.evidence:
        lines.extend(["- None.", ""])
        return
    for evidence in view.evidence:
        lines.extend(
            [
                f"### Evidence {_code(evidence.id)}",
                "",
                f"- Kind: {_code(evidence.kind)}",
                f"- Title: {_text(evidence.title)}",
                f"- Text: {_text(evidence.text)}",
                f"- Reproduction record ID: {_code(evidence.reproduction_ref.record_id)}",
                f"- Reproduction record kind: {_code(evidence.reproduction_ref.record_kind)}",
                f"- Source SHA-256: {_code(evidence.source_sha256)}",
                f"- Visibility: {_code(evidence.visibility)}",
                f"- Redacted: {_scalar(evidence.redacted)}",
                "",
            ]
        )


def _append_graph(lines: list[str], view: ProductView) -> None:
    lines.extend(["## Graph and lineage references", "", "### Nodes", ""])
    for node in view.graph.nodes:
        lines.append(
            f"- ID={_code(node.id)}; kind={_code(node.kind)}; "
            f"source_id={_code(node.source_id)}; visibility={_code(node.visibility)}"
        )
    lines.extend(["", "### Edges", ""])
    for edge in view.graph.edges:
        lines.append(
            f"- ID={_code(edge.id)}; kind={_code(edge.kind)}; "
            f"source={_code(edge.source)}; target={_code(edge.target)}; "
            f"visibility={_code(edge.visibility)}"
        )
    lines.append("")


def _append_limitations(lines: list[str], view: ProductView) -> None:
    limitations = [view.result.caveat]
    limitations.extend(
        missing for turn in view.conversation.turns for missing in turn.missing_evidence
    )
    limitations.extend(missing for claim in view.claims for missing in claim.missing_evidence)
    lines.extend(["## Limitations", ""])
    for limitation in dict.fromkeys(limitations):
        lines.append(f"- {_text(limitation)}")
    lines.append("")


def _observation(value: ProductMetricObservation | None) -> str:
    if value is None:
        return "`null`"
    step = _not_estimated(value.step)
    return (
        f"snapshot_id={_code(value.snapshot_id)}; run_id={_code(value.run_id)}; "
        f"step={step}; value={_scalar(value.value)}"
    )


def _not_estimated(value: object | None) -> str:
    return "`not_estimated` (`null`)" if value is None else _scalar(value)


def _nullable(value: object | None) -> str:
    return "`null`" if value is None else _scalar(value)


def _scalar(value: object) -> str:
    if isinstance(value, str):
        return _code(value)
    return f"`{json.dumps(value, ensure_ascii=False, allow_nan=False)}`"


def _ids(values: Iterable[str]) -> str:
    items = tuple(values)
    return ", ".join(_code(value) for value in items) if items else "`none`"


def _values(values: Iterable[str]) -> str:
    items = tuple(values)
    return "; ".join(_text(value) for value in items) if items else "`none`"


def _code(value: str) -> str:
    return f"`{value}`"


def _text(value: str) -> str:
    compact = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    for marker in ("\\", "`", "*", "[", "]", "<", ">", "|"):
        compact = compact.replace(marker, f"\\{marker}")
    return compact


def _pdf_report(markdown: str) -> bytes:
    lines = _pdf_lines(markdown)
    pages = _paginate_pdf_lines(lines)
    return _build_pdf(pages)


def _pdf_lines(markdown: str) -> tuple[str, ...]:
    rendered: list[str] = []
    for source_line in markdown.splitlines():
        plain = source_line
        if plain.startswith("#"):
            plain = plain.lstrip("#").strip().upper()
        plain = plain.replace("`", "")
        ascii_line = plain.encode("ascii", "backslashreplace").decode("ascii")
        wrapped = textwrap.wrap(
            ascii_line,
            width=_PDF_LINE_WIDTH,
            replace_whitespace=False,
            drop_whitespace=False,
            break_long_words=True,
            break_on_hyphens=False,
        )
        rendered.extend(line.rstrip() for line in wrapped or [""])
    return tuple(rendered)


def _paginate_pdf_lines(lines: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    pages: list[tuple[str, ...]] = []
    page: list[str] = []
    for line in lines:
        keep_with_next = (
            bool(line)
            and len(line) <= 60
            and any(character.isalpha() for character in line)
            and line == line.upper()
            and not line.startswith("-")
        )
        if page and (
            len(page) >= _PDF_LINES_PER_PAGE
            or (keep_with_next and len(page) > _PDF_LINES_PER_PAGE - 3)
        ):
            pages.append(tuple(page))
            page = []
        page.append(line)
    if page:
        pages.append(tuple(page))
    return tuple(pages) or (("Aletheia Lab Product Report",),)


def _build_pdf(pages: tuple[tuple[str, ...], ...]) -> bytes:
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>",
    }
    page_ids = tuple(4 + index * 2 for index in range(len(pages)))
    kids = b" ".join(f"{page_id} 0 R".encode("ascii") for page_id in page_ids)
    objects[2] = (
        b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(pages)).encode() + b" >>"
    )
    for index, lines in enumerate(pages):
        page_id = page_ids[index]
        content_id = page_id + 1
        stream = _pdf_content_stream(lines, page_number=index + 1, page_count=len(pages))
        objects[page_id] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents "
            + f"{content_id} 0 R".encode("ascii")
            + b" >>"
        )
        objects[content_id] = (
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )
    return _serialize_pdf(objects)


def _pdf_content_stream(
    lines: tuple[str, ...],
    *,
    page_number: int,
    page_count: int,
) -> bytes:
    commands = [
        b"BT",
        b"/F1 8 Tf",
        b"54 812 Td",
        b"(Aletheia Lab Product Report) Tj",
        b"ET",
        b"BT",
        b"/F1 9 Tf",
        b"12 TL",
        b"54 788 Td",
    ]
    for line in lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.extend((f"({escaped}) Tj".encode("ascii"), b"T*"))
    commands.extend(
        (
            b"ET",
            b"BT",
            b"/F1 8 Tf",
            b"260 30 Td",
            f"(Page {page_number} of {page_count}) Tj".encode("ascii"),
            b"ET",
        )
    )
    return b"\n".join(commands)


def _serialize_pdf(objects: dict[int, bytes]) -> bytes:
    output = bytearray(b"%PDF-1.4\n% deterministic product report\n")
    offsets = [0] * (max(objects) + 1)
    for object_id in range(1, len(offsets)):
        offsets[object_id] = len(output)
        output.extend(f"{object_id} 0 obj\n".encode("ascii"))
        output.extend(objects[object_id])
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        b"trailer\n<< /Size "
        + str(len(offsets)).encode("ascii")
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(xref_offset).encode("ascii")
        + b"\n%%EOF\n"
    )
    return bytes(output)


__all__ = ["render_product_report"]
