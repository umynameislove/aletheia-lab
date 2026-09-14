"""Private, reproducible V3.2 packet preparation; no delivery or human judgments."""

from __future__ import annotations

import io
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.evaluation.claim_support_v3_2_closeout import Candidate, build_closeout
from aletheia_lab.evaluation.claim_support_v3_2_relation_cohort import seal
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256 as digest
from aletheia_lab.evaluation.human_workflow import (
    HumanWorkflowProtocol,
    RaterSlot,
    StudyPhase,
    _build_blank_packet,
    load_human_workflow,
    submission_template,
)
from aletheia_lab.evaluation.instrument_validation import (
    BlindClaim,
    BlindEvidenceExcerpt,
    EvaluatorMappingEntry,
    EvaluatorMappingPacket,
    PreparedStudyReceipt,
    SupportLabel,
)
from aletheia_lab.filesystem import (
    fsync_directory_tree,
    publish_staged_directory,
    write_new_file,
)
from aletheia_lab.project.identity import content_sha256

GUIDE_PATH = "docs/claim-support-rater-guide-v2.md"
SUPPLEMENT_PATH = "docs/claim-support-v3-2-rater-guide.md"
WORKFLOW_PATH = "configs/evaluation/claim_support_human_workflow_v2.json"
IMPLEMENTATION_PATHS = (
    "src/aletheia_lab/evaluation/claim_support_v3_2_closeout.py",
    "src/aletheia_lab/evaluation/claim_support_v3_2_packets.py",
    "src/aletheia_lab/evaluation/claim_sample_selection.py",
    "src/aletheia_lab/evaluation/instrument_validation.py",
    "src/aletheia_lab/evaluation/human_workflow.py",
    "scripts/claim_support_validation_v3_2_closeout.py",
    GUIDE_PATH,
    SUPPLEMENT_PATH,
    WORKFLOW_PATH,
)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def blind_claim(candidate: Candidate, sample_hash: str) -> BlindClaim:
    evidence = []
    for item in candidate.task["framed_context"]["items"]:
        # The evidence ID is part of the claim's literal report-field address.
        # Preserve it and exact content; withhold source/context hashes and metadata.
        payload = {
            "evidence_id": item["evidence_id"],
            "artifact_ref": item["evidence_id"],
            "excerpt": item["content"],
        }
        evidence.append(BlindEvidenceExcerpt(**payload, excerpt_sha256=canonical_sha256(payload)))
    return BlindClaim(
        blind_claim_id=f"blind-claim-{digest({'sample': sample_hash, 'entry': candidate.entry_sha256})}",
        claim_text=candidate.claim_text,
        visible_evidence=tuple(evidence),
    )


def _job(phase: StudyPhase, slot: RaterSlot, count: int) -> bytes:
    gate = (
        "Đây là bộ làm quen trên dữ liệu thật, không thuộc mẫu kết quả khoa học. "
        "Hoàn thành bộ này và chờ điều phối xác nhận trước khi nhận bộ chính."
        if phase == "onboarding"
        else "Đây là bộ chấm chính. Chỉ bắt đầu sau khi điều phối đã xác nhận "
        "đạt bước làm quen trên dữ liệu thật. Không tự bỏ qua bước này."
    )
    return (
        f"# JOB — Chấm claim báo cáo đo lường\n\nMã người chấm: `{slot}`. "
        f"Số câu: **{count}**.\n\n{gate}\n\n"
        "Đọc `RATER_GUIDE.md` và `V3_2_SUPPLEMENT.md` trước, rồi làm độc lập "
        "từ `blind-packet.json`. "
        "Sao chép `submission-template.json` thành `completed-submission.json` "
        "và chỉ điền quyết định, citation, rationale và attestation trung thực.\n\n"
        "Không dùng AI, không hỏi người chấm khác, không xem repo/nhãn tự động "
        "và không suy đoán đáp án từ lịch sử dự án. Đọc đủ từng mệnh đề; kiểm tra "
        "mâu thuẫn trước. Thiếu trường khác với trường có giá trị trái claim. "
        "Nhãn khác unsupported phải có citation hợp lệ.\n\n"
        "Kiểm tra bài hai lượt, rồi gửi riêng file `completed-submission.json` "
        "cho điều phối. Nếu có câu không đọc được hoặc mơ hồ, báo ID và vấn đề; "
        "không sửa packet hoặc đoán. Deadline do điều phối thông báo riêng.\n"
    ).encode()


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return buffer.getvalue()


def packet_artifacts(
    candidates: tuple[Candidate, ...],
    *,
    phase: StudyPhase,
    workflow: HumanWorkflowProtocol,
    guide: bytes,
    supplement: bytes,
) -> tuple[dict[str, bytes], EvaluatorMappingPacket, dict[RaterSlot, str]]:
    sample_hash = digest({"phase": phase, "entries": [item.entry_sha256 for item in candidates]})
    claims = tuple(blind_claim(item, sample_hash) for item in candidates)
    files = {}
    packet_hashes: dict[RaterSlot, str] = {}
    for slot in workflow.rater_slots:
        packet = _build_blank_packet(claims, slot=slot, workflow=workflow)
        surface = {
            "JOB.md": _job(phase, slot, len(claims)),
            "RATER_GUIDE.md": guide,
            "V3_2_SUPPLEMENT.md": supplement,
            "blind-packet.json": _json_bytes(packet.model_dump(mode="json")),
            "submission-template.json": _json_bytes(
                submission_template(packet, workflow, phase=phase)
            ),
        }
        # Main packets are staged, not released before real onboarding qualification.
        prefix = "held-main" if phase == "main" else "onboarding"
        for name, content in surface.items():
            files[f"{prefix}/source/{slot}/{name}"] = content
        files[f"{prefix}/deliveries/{slot}.zip"] = _zip(surface)
        packet_hashes[slot] = packet.packet_sha256
    entries = tuple(
        EvaluatorMappingEntry(
            blind_claim_id=claim.blind_claim_id,
            claim_id=item.claim_id,
            output_id=item.output_id,
            case_family_id=item.case_family_id,
            claim_type=item.claim_type,
            evidence_condition=item.evidence_condition,
            variant=item.variant,
            automatic_label=item.automatic_label,
            source_record_sha256=item.source_record_sha256,
            pool_entry_sha256=item.entry_sha256,
        )
        for claim, item in zip(claims, candidates, strict=True)
    )
    payload = {
        "schema_version": "claim-support-evaluator-mapping/v1",
        "protocol_sha256": workflow.validation_protocol_sha256,
        "entries": tuple(item.model_dump(mode="json") for item in entries),
    }
    mapping = EvaluatorMappingPacket(
        protocol_sha256=workflow.validation_protocol_sha256,
        entries=entries,
        mapping_sha256=canonical_sha256(payload),
    )
    return files, mapping, packet_hashes


def build_artifacts(root: Path, run: Path, qualification_run: Path) -> dict[str, bytes]:
    report, pool, main, onboarding = build_closeout(root, run, qualification_run)
    files = {"sealed-evaluator/closeout.json": _json_bytes(report)}
    if report["selection_blocker"] is not None:
        return _with_manifest(files, report, root, packets=False)
    workflow = load_human_workflow(root, Path(WORKFLOW_PATH))
    guide = (root / GUIDE_PATH).read_bytes()
    supplement = (root / SUPPLEMENT_PATH).read_bytes()
    files["sealed-evaluator/labeled-pool.json"] = _json_bytes([item.record() for item in pool])
    main_packets: dict[RaterSlot, str] | None = None
    main_mapping: EvaluatorMappingPacket | None = None
    onboarding_packets: dict[RaterSlot, str] | None = None
    onboarding_reference_ids: tuple[str, ...] | None = None
    for phase, candidates in (("main", main), ("onboarding", onboarding)):
        phase_name: StudyPhase = "main" if phase == "main" else "onboarding"
        packets, mapping, packet_hashes = packet_artifacts(
            candidates,
            phase=phase_name,
            workflow=workflow,
            guide=guide,
            supplement=supplement,
        )
        files.update(packets)
        if phase == "main":
            main_packets = packet_hashes
            main_mapping = mapping
            files["sealed-evaluator/main-mapping.json"] = _json_bytes(
                mapping.model_dump(mode="json")
            )
        else:
            # The real onboarding key must be authored independently from the
            # automatic reducer.  Preserve only blind IDs and packet bindings
            # on the coordinator surface; never publish an automatic key.
            onboarding_packets = packet_hashes
            onboarding_reference_ids = tuple(item.blind_claim_id for item in mapping.entries)
    if (
        main_packets is None
        or main_mapping is None
        or onboarding_packets is None
        or onboarding_reference_ids is None
    ):
        raise ValueError("main packet preparation did not complete")
    labels: tuple[SupportLabel, ...] = (
        "contradicted",
        "unsupported",
        "partially_supported",
        "fully_supported",
    )
    label_census: dict[SupportLabel, int] = {label: 50 for label in labels}
    receipt_payload: dict[str, object] = {
        "schema_version": "claim-support-prepared-study-receipt/v1",
        "protocol_sha256": workflow.validation_protocol_sha256,
        "sample_count": 200,
        "label_census": label_census,
        "rater_1_packet_sha256": main_packets["rater_1"],
        "rater_2_packet_sha256": main_packets["rater_2"],
        "evaluator_mapping_sha256": main_mapping.mapping_sha256,
        "human_annotations_collected": False,
        "validation_metrics_generated": False,
        "main_outcomes_opened": False,
        "status": "prepared_for_independent_human_annotation",
    }
    prepared = PreparedStudyReceipt(
        protocol_sha256=workflow.validation_protocol_sha256,
        sample_count=200,
        label_census=label_census,
        rater_1_packet_sha256=main_packets["rater_1"],
        rater_2_packet_sha256=main_packets["rater_2"],
        evaluator_mapping_sha256=main_mapping.mapping_sha256,
        receipt_sha256=canonical_sha256(receipt_payload),
    )
    files["sealed-evaluator/prepared-study-receipt.json"] = _json_bytes(
        prepared.model_dump(mode="json")
    )
    # Never manufacture a human reference key from the instrument being validated.
    files["coordinator-private/onboarding-reference-template.json"] = _json_bytes(
        {
            "schema_version": "claim-support-real-onboarding-reference-template/1",
            "status": "independent_coordinator_review_required",
            "automatic_labels_are_not_gold": True,
            "claim_count": len(onboarding_reference_ids),
            "rater_1_packet_sha256": onboarding_packets["rater_1"],
            "rater_2_packet_sha256": onboarding_packets["rater_2"],
            "entries": [
                {
                    "blind_claim_id": blind_claim_id,
                    "reference_label": None,
                    "rationale": None,
                }
                for blind_claim_id in onboarding_reference_ids
            ],
        }
    )
    files["coordinator-private/README.md"] = (
        "# Điều phối riêng — không gửi thư mục này cho người chấm\n\n"
        "Mẫu chính 200 và bộ làm quen thật 20 đã được tách, không trùng nội dung claim. "
        "Tên người nhận không được ghi vào artifact theo dõi; điều phối phải xác nhận "
        "ánh xạ slot-người chấm riêng trước khi giao.\n\n"
        "Không mở hoặc chia sẻ thư mục sealed-evaluator trước khi hai bài chấm chính "
        "đã khóa. Thư mục đó chứa nhãn tự động và mapping chỉ dùng cho phân tích sau.\n\n"
        "Trước khi giao onboarding: điều phối đủ năng lực tự đọc bộ 20 claim/evidence, "
        "điền bản sao onboarding-reference-template.json và khóa reference key. "
        "Không sao chép nhãn tự động làm gold. Không dùng AI làm người chấm.\n\n"
        "Chỉ giao ZIP onboarding riêng cho từng người. Sau khi chấm và khóa hai "
        "submission, kiểm tra macro-F1 >= 0.80 và không có false-support ở claim "
        "reference-contradicted. Bộ 20 này không vào denominator khoa học. "
        "Onboarding synthetic cũ không được ghi thành đã hoàn thành bộ thật này.\n\n"
        "Chỉ sau khi đạt mới giao ZIP trong held-main. Hiện chưa gửi file nào, "
        "chưa có reference key/human judgment hoặc quyền mở main/sealed outcomes. "
        "Không chia sẻ closeout, labeled-pool hoặc mapping. Giữ mapping chính "
        "kín với raters; không dùng để feedback trước khi cả hai bài chính khóa.\n\n"
        "Sau bài chính: adjudicate mọi bất đồng và mọi câu ít nhất một rater chọn "
        "contradicted; giữ family là đơn vị phụ thuộc thống kê. Mẫu cân bằng thử "
        "thách đo lường không chứng minh tỷ lệ hallucination tự nhiên, năng lực "
        "chẩn đoán tự do, ưu thế variant hay đã đạt AH-02B. Hai lỗi cấu trúc vẫn "
        "nằm trong denominator 240 của lần relation cohort.\n"
    ).encode()
    return _with_manifest(files, report, root, packets=True)


def _with_manifest(
    files: dict[str, bytes], report: dict[str, Any], root: Path, *, packets: bool
) -> dict[str, bytes]:
    manifest = seal(
        {
            "schema_version": "claim-support-v3.2-private-packets/1",
            "closeout_sha256": report["closeout_sha256"],
            "estimand": report["estimand"],
            "implementation_bindings": {
                path: content_sha256((root / path).read_bytes()) for path in IMPLEMENTATION_PATHS
            },
            "files": {path: content_sha256(content) for path, content in sorted(files.items())},
            "sample_materialized": packets,
            "main_claim_count": 200 if packets else 0,
            "onboarding_claim_count": 20 if packets else 0,
            "blind_packets_generated": packets,
            "human_annotations_collected": False,
            "main_or_sealed_outcomes_opened": False,
            "provider_calls_executed": False,
            "externally_delivered": False,
            "main_packet_release_authorized": False,
            "onboarding_reference_key_human_review_pending": packets,
            "legacy_scientific_admission_authorized": False,
            "status": "prepared_private_packets_human_onboarding_pending"
            if packets
            else "fixed_selection_blocked",
        },
        "manifest_sha256",
    )
    return {**files, "manifest.json": _json_bytes(manifest)}


def checked_destination(destination: Path, protected: tuple[Path, ...]) -> Path:
    for path in (destination, *destination.parents):
        if path.is_symlink():
            raise ValueError("private packet destination contains a symlink")
    destination = destination.resolve()
    for source in protected:
        source = source.resolve()
        if destination.is_relative_to(source) or source.is_relative_to(destination):
            raise ValueError("private packet destination overlaps protected input")
    return destination


def verify_artifacts(destination: Path, expected: dict[str, bytes]) -> None:
    if destination.is_symlink() or not destination.is_dir():
        raise ValueError("private packet directory is absent or linked")
    paths = tuple(destination.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError("private packet directory contains a symlink")
    actual = {
        path.relative_to(destination).as_posix(): path.read_bytes()
        for path in paths
        if path.is_file()
    }
    if actual != expected:
        raise ValueError("private packet contents differ from independent reconstruction")


def publish_artifacts(destination: Path, expected: dict[str, bytes]) -> str:
    if destination.exists():
        verify_artifacts(destination, expected)
        return "identical"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".claim-packets-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        for name, content in sorted(expected.items()):
            write_new_file(stage / name, content)
        fsync_directory_tree(stage)
        publish_staged_directory(stage, destination)
    verify_artifacts(destination, expected)
    return "created"
