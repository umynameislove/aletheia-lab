"""One-shot report worker over trusted, already-authorized ProductView bytes.

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

    restrict_report_process()

from pydantic import Field, RootModel

from aletheia_lab.product.boundary import ProductError
from aletheia_lab.product.reports import render_product_report
from aletheia_lab.product.view import AnyProductView

from .request_boundary import RequestRejected, decode_json_model
from .worker_limits import MAX_INPUT_BYTES, MAX_OUTPUT_BYTES


class _ReportView(RootModel[Annotated[AnyProductView, Field(discriminator="schema_version")]]):
    pass


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
    if args not in (["json"], ["markdown"], ["pdf"]):
        print("worker_invalid_format", file=errors)
        return 2
    try:
        raw = source.read(MAX_INPUT_BYTES + 1)
        view = decode_json_model(raw, _ReportView, maximum_bytes=MAX_INPUT_BYTES).root
        result = render_product_report(view, args[0])
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
