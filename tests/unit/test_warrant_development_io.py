"""No-network rehearsal of the complete private development lifecycle."""

from __future__ import annotations

import json
import zipfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.warrant_development import (
    WRITER_PROMPT,
    DevelopmentCall,
    WarrantCase,
    WarrantClaim,
    WarrantEvidence,
    claim_sha256,
    writer_response_schema,
)
from aletheia_lab.evaluation.warrant_development_io import (
    CONCURRENCY,
    DESTINATION,
    MAX_CALL_USD,
    MAX_INPUT_TOKENS,
    MAX_OUTPUT_TOKENS,
    MODEL,
    SMOKE_CALL_CEILING,
    _code_identity,
    _private_dir,
    _read,
    _results,
    _write,
    analyze_private_development,
    compile_review_references,
    execute_warrant_development,
    reference_review_packet,
    reference_template,
)
from aletheia_lab.evaluation.warrant_development_live import OpenAIDevelopmentCaller


class FakeCaller:
    def __init__(self, *, malformed_writer: bool = False, abstaining_writer: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.malformed_writer = malformed_writer
        self.abstaining_writer = abstaining_writer

    def invoke(
        self, *, prompt: str, payload: dict[str, object], schema: dict[str, object]
    ) -> DevelopmentCall:
        self.calls.append((prompt, payload))
        if prompt == WRITER_PROMPT:
            value: object = (
                {"status": "completed", "claims": []}
                if self.malformed_writer
                else {"status": "abstained", "claims": []}
                if self.abstaining_writer
                else {
                    "status": "completed",
                    "claims": [
                        {
                            "claim_text": "The observed sample count equaled 2.",
                            "claim_type": "evidence_statement",
                        }
                    ],
                }
            )
        else:
            evidence = payload["visible_evidence"][0]
            value = {
                "decisions": [
                    {
                        "evidence_id": evidence["evidence_id"],
                        "relation_polarity": "supports",
                        "relation_scope": "entire",
                    }
                ]
            }
        return DevelopmentCall(
            status="completed",
            payload_json=json.dumps(value),
            input_tokens=20,
            output_tokens=10,
            estimated_cost_usd=0.0,
            latency_seconds=0.001,
            provider_attempted=False,
        )


def test_private_sibling_path_is_not_misread_as_the_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").write_text("gitdir: elsewhere\n")
    (tmp_path / "memory").mkdir()
    monkeypatch.chdir(checkout)
    private = _private_dir(Path("../memory/development"), new=True)
    assert private.resolve() == tmp_path / "memory/development"


def _prepared(directory: Path) -> str:
    directory.mkdir(mode=0o700)
    case = WarrantCase(
        case_id="development-case-1",
        family_id="family-1",
        source_output_id="source-output-1",
        component="synthetic",
        source_claim=WarrantClaim(
            claim_text="The observed sample count was 2.", claim_type="evidence_statement"
        ),
        visible_evidence=(
            WarrantEvidence(
                evidence_id="e1",
                kind="metric",
                title="Synthetic count",
                content="The observed sample count was 2.",
            ),
        ),
        required_unit_ids=("sample_count",),
    )
    case_data = [case.model_dump(mode="json")]
    seeds = [
        {
            "case_id": case.case_id,
            "claim_sha256": claim_sha256(case.source_claim),
            "evidence_sha256": case.evidence_sha256(),
            "label": "fully_supported",
        }
    ]
    plan = {
        "code_sha256": _code_identity(),
        "case_frame_sha256": canonical_execution_sha256(case_data),
        "seed_reference_sha256": canonical_execution_sha256(seeds),
        "case_count": 1,
        "model_snapshot": MODEL,
        "destination": DESTINATION,
        "max_input_tokens": MAX_INPUT_TOKENS,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "concurrency": CONCURRENCY,
        "temperature": 0.0,
        "seed": 731,
        "sdk_retries": 0,
        "store": False,
        "timeout_seconds": 90.0,
        "maximum_provider_calls": 9 + SMOKE_CALL_CEILING,
    }
    _write(directory / "cases.json", case_data)
    _write(directory / "seed-references.json", seeds)
    _write(directory / "plan.json", plan)
    return canonical_execution_sha256(plan)


def test_bounded_synthetic_smoke_then_cohort_checkpoint_and_reference_binding(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "private"
    digest = _prepared(directory)
    caller = FakeCaller()
    report = execute_warrant_development(directory=directory, confirm_sha256=digest, caller=caller)
    assert report["status"] == "development_only"
    assert report["synthetic_smoke_passed"]
    assert len(caller.calls) == 10
    assert _read(directory / "execution-receipt.json")["provider_invocation_count"] == 0
    assert len(_results(directory)) == 1
    rows = reference_template(directory)
    assert len(rows) == 2
    assert next(row for row in rows if row["basis"] == "locked_human")["labels"] == [
        "fully_supported"
    ]
    changed = next(row for row in rows if row["basis"] == "development_review")
    assert changed["labels"] == [None]
    assert changed["warranted_unit_ids"] is None
    packet = reference_review_packet(directory)
    assert len(packet["items"]) == 2
    assert all(
        "judgments" not in item and "human_final_label" not in item for item in packet["items"]
    )
    assert not analyze_private_development(directory=directory)[
        "candidate_selection_reference_complete"
    ]
    with pytest.raises(FileExistsError):
        execute_warrant_development(directory=directory, confirm_sha256=digest, caller=caller)


def test_completed_receipt_detects_tampered_result_and_forged_human_key(tmp_path: Path) -> None:
    directory = tmp_path / "private"
    digest = _prepared(directory)
    execute_warrant_development(directory=directory, confirm_sha256=digest, caller=FakeCaller())
    rows = reference_template(directory)
    changed = next(row for row in rows if row["basis"] == "development_review")
    changed["basis"] = "locked_human"
    changed["labels"] = ["fully_supported"]
    _write(directory / "forged-reference.json", rows)
    with pytest.raises(ValueError, match="locked human label"):
        analyze_private_development(
            directory=directory, references_path=directory / "forged-reference.json"
        )
    result_file = next((directory / "results").glob("*.json"))
    result_file.write_bytes(result_file.read_bytes() + b" ")
    with pytest.raises(ValueError, match="result bytes"):
        analyze_private_development(directory=directory)


def test_blind_review_compiles_only_after_exact_item_and_seed_check(tmp_path: Path) -> None:
    directory = tmp_path / "private"
    digest = _prepared(directory)
    execute_warrant_development(directory=directory, confirm_sha256=digest, caller=FakeCaller())
    packet = reference_review_packet(directory)
    assert "locked_human" not in json.dumps(packet)
    for item in packet["items"]:
        item["labels"] = ["fully_supported" for _ in item["claims"]]
        item["warranted_unit_ids"] = ["sample_count"] if item["claims"] else []
    reviewed_path = directory / "reviewed.json"
    _write(reviewed_path, packet)
    report = compile_review_references(directory=directory, reviewed_packet_path=reviewed_path)
    assert report["reference_output_count"] == 2
    assert report["analysis"]["candidate_selection_reference_complete"]
    assert (directory / "reference-reviewed.json").is_file()


@pytest.mark.parametrize("mutation", ["claim", "human_label", "incomplete_coverage"])
def test_review_compiler_rejects_changed_identity_or_unfinished_review(
    tmp_path: Path, mutation: str
) -> None:
    directory = tmp_path / "private"
    digest = _prepared(directory)
    execute_warrant_development(directory=directory, confirm_sha256=digest, caller=FakeCaller())
    packet = reference_review_packet(directory)
    for item in packet["items"]:
        item["labels"] = ["fully_supported" for _ in item["claims"]]
        item["warranted_unit_ids"] = ["sample_count"] if item["claims"] else []
    if mutation == "claim":
        packet["items"][0]["source_claim"]["claim_text"] = "Different claim."
    elif mutation == "human_label":
        locked = next(
            item
            for item in packet["items"]
            if item["claims"][0]["claim_text"] == "The observed sample count was 2."
        )
        locked["labels"] = ["contradicted"]
    else:
        packet["items"][0]["warranted_unit_ids"] = None
    reviewed_path = directory / "reviewed.json"
    _write(reviewed_path, packet)
    with pytest.raises(ValueError):
        compile_review_references(directory=directory, reviewed_packet_path=reviewed_path)
    assert not (directory / "reference-reviewed.json").exists()


@pytest.mark.parametrize("failure", ["malformed", "abstained"])
def test_failed_synthetic_smoke_never_starts_real_cohort_and_never_replays(
    tmp_path: Path, failure: str
) -> None:
    directory = tmp_path / "private"
    digest = _prepared(directory)
    caller = FakeCaller(
        malformed_writer=failure == "malformed", abstaining_writer=failure == "abstained"
    )
    report = execute_warrant_development(directory=directory, confirm_sha256=digest, caller=caller)
    assert report == {"status": "smoke_failed_closed", "cohort_cases_executed": 0}
    assert len(caller.calls) == 3
    assert list((directory / "results").iterdir()) == []
    with pytest.raises(FileExistsError):
        execute_warrant_development(directory=directory, confirm_sha256=digest, caller=caller)


def test_paid_caller_wire_and_usage_are_bounded_without_network() -> None:
    captured: list[dict[str, object]] = []

    def create(**kwargs: object) -> object:
        captured.append(kwargs)
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=120, completion_tokens=40),
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content='{"status":"abstained","claims":[]}', refusal=None
                    ),
                )
            ],
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    caller = OpenAIDevelopmentCaller(maximum_calls=1, client=client)
    call = caller.invoke(
        prompt="Synthetic evidence only.",
        payload={
            "source_claim": {"claim_text": "Two.", "claim_type": "evidence_statement"},
            "visible_evidence": [
                {"evidence_id": "e1", "kind": "metric", "title": "Synthetic", "content": "Two."}
            ],
        },
        schema=writer_response_schema(),
    )
    assert call.status == "completed"
    assert call.provider_attempted
    assert call.estimated_cost_usd == pytest.approx((120 * 2 + 40 * 8) / 1_000_000)
    assert captured[0]["model"] == MODEL
    assert captured[0]["store"] is False
    assert captured[0]["temperature"] == 0.0
    assert captured[0]["seed"] == 731
    assert captured[0]["response_format"]["json_schema"]["strict"] is True
    with pytest.raises(ValueError, match="ceiling"):
        caller.invoke(prompt="x", payload={}, schema=writer_response_schema())


def test_paid_caller_network_exception_is_sanitized_and_reserved() -> None:
    def broken(**kwargs: object) -> object:
        raise RuntimeError("sensitive provider output must not appear in receipt")

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=broken)))
    caller = OpenAIDevelopmentCaller(maximum_calls=1, client=client)
    call = caller.invoke(prompt="Synthetic.", payload={}, schema=writer_response_schema())
    assert call.status == "technical_failure"
    assert call.payload_json is None
    assert not call.usage_observed
    assert call.estimated_cost_usd == MAX_CALL_USD
    assert "sensitive" not in str(call.model_dump())


def _audit_frame() -> tuple[dict, list[dict], list[dict]]:
    mapping = [
        {
            "phase": "main",
            "rater_slot": "rater_1",
            "blind_claim_id": f"blind-{i}",
            "source_claim_id": f"source-{i}",
            "request_id": f"output-{i // 2}",
            "family_id": f"family-{i % 32}",
            "component": "probability" if i < 160 else "enriched",
            "claim_type": "evidence_statement",
        }
        for i in range(200)
    ]
    references = [{**row, "human_final_label": "fully_supported"} for row in mapping]
    packet = {
        "phase": "main",
        "rater_slot": "rater_1",
        "items": [
            {
                "blind_claim_id": row["blind_claim_id"],
                "claim_text": "The sample count is 2.",
                "visible_evidence": [
                    {"evidence_id": "e1", "kind": "metric", "title": "Count", "content": "Count=2"}
                ],
            }
            for row in mapping
        ],
    }
    return packet, mapping, references


@pytest.mark.parametrize("change", ["join", "duplicate", "component", "pilot"])
def test_real_case_frame_contract_rejects_bad_audit_join(change: str) -> None:
    from aletheia_lab.evaluation.warrant_development_io import _case_frame

    packet, mapping, references = _audit_frame()
    if change == "join":
        references[0]["request_id"] = "different-output"
    elif change == "duplicate":
        packet["items"][-1] = deepcopy(packet["items"][0])
    elif change == "component":
        mapping[0]["component"] = references[0]["component"] = "enriched"
    else:
        mapping[0]["phase"] = "pilot"
    with pytest.raises(ValueError):
        _case_frame(packet, mapping, references)


def test_prepare_binds_entire_audit_without_provider_or_label_in_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aletheia_lab.evaluation import warrant_development_io as io

    packet, mapping, references = _audit_frame()
    audit = tmp_path / "memory/p5-domain-human-audit-v1"
    sample = audit / "output/coordinator-only/sample-map.json"
    reference = audit / "adjudication-v1/analysis/private-item-level.json"
    delivery = audit / "output/delivery/test-220.zip"
    for path in (sample, reference, delivery):
        path.parent.mkdir(parents=True, exist_ok=True)
    sample.write_text(json.dumps({"mapping": mapping}), encoding="utf-8")
    reference.write_text(json.dumps({"rows": references}), encoding="utf-8")
    with zipfile.ZipFile(delivery, "w") as archive:
        archive.writestr("main.json", json.dumps(packet))
    hashes = {
        sample: "cb0d3a7a0e49aebbc4c305ef52d4a3128ffe55a6213938100ce40a8a98e7cf7f",
        reference: "db769c91660b48c94b55091e248e11ba6c6738e877dd0e21a366e9771f7f6f45",
        delivery: "857dfcdc19b6efc45e1795f801f116930f6644e74220f25010119dcc4a86dc01",
    }
    real_hash = io.file_sha256
    monkeypatch.setattr(io, "file_sha256", lambda path: hashes.get(path) or real_hash(path))
    output = tmp_path / "prepared"
    report = io.prepare_warrant_development(memory_root=tmp_path / "memory", output=output)
    plan, cases = io.checked_plan(output, confirm_sha256=report["plan_sha256"])
    assert plan["case_count"] == len(cases) == 200
    assert plan["components"] == {"probability": 160, "enriched": 40}
    assert plan["maximum_provider_calls"] == 1809
    assert not report["provider_calls_executed"]
    assert all(set(case.writer_payload()) == {"source_claim", "visible_evidence"} for case in cases)
    assert all("human_final_label" not in json.dumps(case.writer_payload()) for case in cases)
    for path in (sample, reference, delivery):
        original = hashes[path]
        hashes[path] = "0" * 64
        with pytest.raises(ValueError):
            io.prepare_warrant_development(
                memory_root=tmp_path / "memory", output=tmp_path / "must-not-be-created"
            )
        assert not (tmp_path / "must-not-be-created").exists()
        hashes[path] = original
