"""Report table helpers."""

from __future__ import annotations

from collections.abc import Iterable

from aletheia_lab.evaluation.judge import JudgeResult


def metric_rows(results: Iterable[JudgeResult]) -> list[dict[str, object]]:
    """Convert judge results to table rows."""

    return [
        {
            "case_id": result.case_id,
            "variant": result.variant,
            "correctness": result.correctness,
            "faithfulness": result.faithfulness,
            "abstention": result.abstention,
            "judge_id": result.judge_id,
        }
        for result in results
    ]
