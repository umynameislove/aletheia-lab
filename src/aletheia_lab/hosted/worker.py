"""One-shot report worker over trusted, already-authorized report inputs.

Run as a separate process under a future job supervisor; this does not receive
HTTP requests, select filesystem paths, authenticate callers or acknowledge a
durable job. Only publish a complete output after exit 0 and an independent
authorization/integrity recheck. Input/output limits are provisional budgets.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import Annotated, BinaryIO, TextIO

if __name__ == "__main__":
    from .report_sandbox import restrict_report_process

    restrict_report_process(windowed=sys.argv[1:] in (["json-windowed-v3"], ["markdown-windowed-v3"], ["pdf-windowed-v3"]))

from pydantic import Field, RootModel

from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.reports import render_product_report
from aletheia_lab.product.view import AnyProductView

from .request_boundary import RequestRejected, decode_json_model
from .worker_limits import MAX_INPUT_BYTES, MAX_OUTPUT_BYTES

_REVIEW_LIMIT = 64 * 1024


class _ReportView(RootModel[Annotated[AnyProductView, Field(discriminator="schema_version")]]):
    pass


def _render(source: BinaryIO, report_format: str, *, windowed: bool, claim_review: bool) -> bytes:
    if claim_review:
        from .claim_review_reports import render_claim_review_report

        raw = source.read(_REVIEW_LIMIT + 1)
        if len(raw) > _REVIEW_LIMIT:
            raise ValueError("Claim review report input exceeds its byte limit.")
        return render_claim_review_report(raw, report_format)
    limit = MAX_OUTPUT_BYTES if windowed else MAX_INPUT_BYTES
    view = decode_json_model(source.read(limit + 1), _ReportView, maximum_bytes=limit).root
    if windowed:
        from .upload_analysis import WINDOW_OBSERVATION_MODEL

        if view.runtime.model != WINDOW_OBSERVATION_MODEL:
            raise ValueError("Window report requires its pinned model.")
    return render_product_report(view, report_format)


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: BinaryIO | None = None,
    stdout: BinaryIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    source = stdin if stdin is not None else sys.stdin.buffer
    output = stdout if stdout is not None else sys.stdout.buffer
    errors = stderr if stderr is not None else sys.stderr
    windowed = len(args) == 1 and args[0].endswith("-windowed-v3")
    if windowed:
        args = [args[0].removesuffix("-windowed-v3")]
    claim_review = len(args) == 1 and args[0].startswith("claim-review-") and not windowed
    if claim_review:
        args = [args[0].removeprefix("claim-review-")]
    if args not in (["json"], ["markdown"], ["pdf"]):
        print("worker_invalid_format", file=errors)
        return 2
    try:
        result = _render(source, args[0], windowed=windowed, claim_review=claim_review)
        if len(result) > MAX_OUTPUT_BYTES:
            print("worker_output_too_large", file=errors)
            return 2
        if output.write(result) != len(result):
            raise OSError("incomplete worker output")
        output.flush()
        return 0
    except RequestRejected:
        print("worker_invalid_input", file=errors)
        return 2
    except ProductError as error:
        print("worker_report_unavailable", file=errors)
        return 3 if error.code == "pdf_unsupported_text" else 2
    except Exception:
        print("worker_failed", file=errors)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
