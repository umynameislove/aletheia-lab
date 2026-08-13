"""Prepare and validate the two-stage P2 blind human-review workflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from aletheia_lab.benchmark.p2.artifacts import load_contract_store
from aletheia_lab.benchmark.p2.evidence_conditions import rebuild_evidence_bundles_from_census
from aletheia_lab.benchmark.p2.human_validity_review import (
    BlindReviewPacket,
    BlindStageRecord,
    BlindStageWorksheet,
    HumanReviewWorksheet,
    ReviewMappingPacket,
    build_blind_stage_worksheet,
    build_human_review_packets,
    evaluate_human_review,
    finalize_blind_stage_worksheet,
    finalize_human_review_worksheet,
    open_mapped_review_stage,
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_model(path: Path, model: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(model.model_dump_json(indent=2))
        handle.write("\n")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text.rstrip() + "\n")


def _reviewer_guide(reviewer_id: str) -> str:
    return f"""# P2 blind human validity review — {reviewer_id}

## Strict boundary

During stage one, open only `blind-packet.json`, this guide, and
`blind-stage-worksheet.json`. Do not open or request the mapping packet, alpha
store, source code, another reviewer's answers, or evaluator metadata.

For each of the nine opaque entries, inspect only `diagnosis_projection` and
fill the matching worksheet fields:

- `hidden_answer_cue_found`: `yes`, `no`, or `uncertain`;
- `expected_judgment_cue_found`: `yes`, `no`, or `uncertain`;
- `unsupported_causal_wording_found`: `yes`, `no`, or `uncertain`;
- `rationale`: at least 20 characters, based only on visible evidence.

Use `uncertain` whenever the projection cannot be judged reliably. Do not infer
an expected answer from field order, ID, missingness, or another item. Do not
discuss answers with the other reviewer before both stage-one records are
submitted.

After all nine entries are complete, set:

```json
"reviewer_id": "{reviewer_id}",
"judgments_personally_recorded": true,
"mapping_packet_not_opened": true
```

Return only the completed `blind-stage-worksheet.json` to the coordinator. The
coordinator validates and freezes it before releasing stage two.
"""


def _coordinator_guide(reviewer_ids: tuple[str, ...]) -> str:
    reviewers = ", ".join(reviewer_ids)
    return f"""# Coordinator protocol

Reviewers: {reviewers}

1. Keep `coordinator-private/mapping-packet.json` inaccessible to both reviewers.
2. Send each reviewer only their own directory under `reviewer-packs/`.
3. Require independent completion; do not discuss entries before both blind
   worksheets are returned.
4. Run `freeze-blind` separately for each returned worksheet.
5. Only after both blind records validate, run `open-mapping` and send each
   reviewer their own stage-two mapping/worksheet directory.
6. Reviewers complete mapped sufficiency, claim-boundary, threat, deviation,
   and paired-family fields independently.
7. Run `finalize` for each reviewer. Any finding, `uncertain`, incomplete form,
   broken hash, protocol deviation, or reviewer disagreement remains blocking
   until explicit human adjudication.

Never edit hashes, IDs, expected rubric values, or reviewer answers to make the
gate pass. Never use an AI system to manufacture human judgments.
"""


def prepare(store: Path, output: Path, reviewer_ids: tuple[str, ...]) -> None:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing review root: {output}")
    if len(reviewer_ids) < 2 or len(set(reviewer_ids)) != len(reviewer_ids):
        raise ValueError("prepare requires at least two unique reviewer IDs")
    loaded = load_contract_store(store)
    artifacts = loaded.artifacts
    bundles = rebuild_evidence_bundles_from_census(
        execution=artifacts.execution,
        census=artifacts.census,
        contexts=artifacts.contexts,
    )
    blind, mapping = build_human_review_packets(bundles)
    worksheet = build_blind_stage_worksheet(blind)
    output.mkdir(parents=True)
    _write_model(output / "coordinator-private" / "mapping-packet.json", mapping)
    _write_text(
        output / "coordinator-private" / "COORDINATOR.md",
        _coordinator_guide(reviewer_ids),
    )
    for reviewer_id in reviewer_ids:
        root = output / "reviewer-packs" / reviewer_id
        _write_model(root / "blind-packet.json", blind)
        _write_model(root / "blind-stage-worksheet.json", worksheet)
        _write_text(root / "REVIEW_GUIDE.md", _reviewer_guide(reviewer_id))


def freeze_blind(blind_path: Path, worksheet_path: Path, output: Path) -> None:
    blind = BlindReviewPacket.model_validate(_read_json(blind_path))
    worksheet = BlindStageWorksheet.model_validate(_read_json(worksheet_path))
    record = finalize_blind_stage_worksheet(blind, worksheet)
    _write_model(output, record)


def open_mapping(
    blind_path: Path,
    mapping_path: Path,
    blind_record_path: Path,
    output: Path,
) -> None:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite stage-two directory: {output}")
    blind = BlindReviewPacket.model_validate(_read_json(blind_path))
    mapping = ReviewMappingPacket.model_validate(_read_json(mapping_path))
    blind_record = BlindStageRecord.model_validate(_read_json(blind_record_path))
    worksheet = open_mapped_review_stage(blind, mapping, blind_record)
    output.mkdir(parents=True)
    _write_model(output / "mapping-packet.json", mapping)
    _write_model(output / "mapped-review-worksheet.json", worksheet)
    _write_text(
        output / "STAGE_TWO.md",
        """# Stage two — mapped sufficiency and paired-family review

The blind record is already frozen. Open the mapping packet and complete every
remaining `null` field in the mapped worksheet. Preserve all pre-filled blind
answers and hashes. Record threats/deviations even when they block the gate.
Use `uncertain` or `cannot_assess` rather than guessing. Return only the
completed mapped worksheet to the coordinator.
""",
    )


def finalize(
    blind_path: Path,
    mapping_path: Path,
    worksheet_path: Path,
    output: Path,
) -> None:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite final review directory: {output}")
    blind = BlindReviewPacket.model_validate(_read_json(blind_path))
    mapping = ReviewMappingPacket.model_validate(_read_json(mapping_path))
    worksheet = HumanReviewWorksheet.model_validate(_read_json(worksheet_path))
    record = finalize_human_review_worksheet(worksheet)
    report = evaluate_human_review(blind, mapping, record)
    output.mkdir(parents=True)
    _write_model(output / "human-review-record.json", record)
    _write_model(output / "human-validity-report.json", report)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--store", type=Path, required=True)
    prepare_parser.add_argument("--output", type=Path, required=True)
    prepare_parser.add_argument("--reviewer", action="append", required=True)

    freeze_parser = subparsers.add_parser("freeze-blind")
    freeze_parser.add_argument("--blind", type=Path, required=True)
    freeze_parser.add_argument("--worksheet", type=Path, required=True)
    freeze_parser.add_argument("--output", type=Path, required=True)

    open_parser = subparsers.add_parser("open-mapping")
    open_parser.add_argument("--blind", type=Path, required=True)
    open_parser.add_argument("--mapping", type=Path, required=True)
    open_parser.add_argument("--blind-record", type=Path, required=True)
    open_parser.add_argument("--output", type=Path, required=True)

    final_parser = subparsers.add_parser("finalize")
    final_parser.add_argument("--blind", type=Path, required=True)
    final_parser.add_argument("--mapping", type=Path, required=True)
    final_parser.add_argument("--worksheet", type=Path, required=True)
    final_parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "prepare":
        prepare(args.store, args.output, tuple(args.reviewer))
    elif args.command == "freeze-blind":
        freeze_blind(args.blind, args.worksheet, args.output)
    elif args.command == "open-mapping":
        open_mapping(args.blind, args.mapping, args.blind_record, args.output)
    else:
        finalize(args.blind, args.mapping, args.worksheet, args.output)


if __name__ == "__main__":
    main()
