"""Package the verified backend change and unmodified raw receipts for coordination."""
from datetime import datetime, timezone
import csv
import json
from pathlib import Path
import statistics
import xml.etree.ElementTree as ET
import zipfile

from package_r415_handoff import archive, digest, read

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "evidence"
H = ROOT / "handoff"
KIT = ROOT / "Release-K01-K18-R4.18"


def main():
    status = read(E / "r418-working-evidence-status.json")
    m = read(KIT / "source-manifest.json")
    assert status["source_identity"] == m["source_identity"]
    assert status["backend_53_functions_and_4_modules_pass"]
    assert not status["all_k01_k18_pass"] and not status["final_bao_report_exported"]
    assert all(digest(KIT / "source" / n) == h for n, h in m["files"].items())
    raw = read(E / "linux-r418-open5/calibration100-result.json")
    workers = [json.loads(line) for line in (E / "linux-r418-open5/worker.safe.jsonl").read_text().splitlines()]
    jobs = [row for row in workers if row["event"] == "job.execution"]
    assert len(jobs) == 32 and all(row["outcome"] == "settled" for row in jobs)
    assert raw["runtime_tree_sha256_before"] == raw["runtime_tree_sha256_after"]
    missed = [dict(journey=r["operation"], size=r["size"], n=r["deadline_inclusive"]["n"],
                   p95_upper_seconds=r["deadline_inclusive"]["p95_upper_seconds"], target_seconds=r["slo_targets"]["p95_seconds"])
              for r in raw["strata"] if r["deadline_inclusive"]["n"] and
              r["deadline_inclusive"]["p95_upper_seconds"] > r["slo_targets"]["p95_seconds"]]
    interpretation = dict(source_identity=m["source_identity"], runtime_identity=m["runtime_identity"],
        harness_runtime_tree_hash=raw["runtime_tree_sha256_before"],
        tree_hash_note="Harness uses its own tree algorithm. Manifest-verified copy and attempt.json bind this execution to sealed R4.18; hashes from different algorithms are not equated.",
        measured_positive=raw["measured_positive"], whole_run_clean=False,
        total_counts=raw["counts"], http=raw["http"],
        worker_settled_log_records=32, harness_terminal_confirmations=30,
        distinction="Settled telemetry has no terminal result body/hash witness. Two jobs remain unconfirmed by the harness after warmup deadline failure; settled does not turn those actions into deadline successes.",
        failures_outside_measured_positive_window=True, postrun_reconciliation=raw["postrun_reconciliation"],
        latency_target_exceedances=missed, all_strata_under_primary_precision_minimum=True,
        measured_subset_receipt_is_not_entire_campaign_acceptance=True, full_release_or_primary50k_pass=False)
    assert max(r["deadline_inclusive"]["n"] for r in raw["strata"]) < 10000
    (E / "r418-calibration-interpretation.json").write_text(json.dumps(interpretation, ensure_ascii=False, indent=2), encoding="utf-8")
    index_old = read(E / "internal-k01-k18-evidence-index-r415.json")
    items = []
    common = ["r418-change-scope.json", "r418-refactor-function-results.csv", "r418-refactor-module-results.csv",
              "r418-sealed-quality-result.json", "r418-working-evidence-status.json", "r418-line-ending-parity.json",
              "windows-unit-r418-sealed/junit.xml", "windows-r417-native-regression/junit.xml"]
    unitcases = list(ET.parse(E / "windows-unit-r418-sealed/junit.xml").getroot().iter("testcase"))
    for row in index_old["items"]:
        task = row["task_id"]
        number = int(task.split("-")[-1])
        matched = [c for c in unitcases if f"test_k{number:02d}_" in c.get("classname", "")]
        refs = list(common)
        if number == 17:
            refs += ["linux-r418-open5/observed-summary.json", "linux-r418-open5/observed-strata.csv", "r418-calibration-interpretation.json"]
        if number in (13, 18):
            refs += ["windows-r418-restore-delivery-complete/junit.xml", "windows-r418-restore-delivery-complete/result.json",
                     "linux-ext4-r4.18-pitr/physical-pitr.json", "linux-ext4-r4.18-pitr/result.json"]
        if number in (8, 10, 14, 15):
            refs += ["windows-r418-k08-k10-k14-k15/junit.xml", "windows-r418-k08-k10-k14-k15/result.json"]
        if number == 14:
            refs += ["windows-r418-k14-export-isolated/result.json", "windows-r415-k14-export-comparison/result.json",
                     "linux-r418-k14-export/result.json"]
        if number in (16, 18):
            refs += ["image-r418-security-summary.json", "image-r418-smoke.json"]
        refs = [n for n in refs if (E/n).is_file()]
        items.append(dict(task_id=task, canonical_method=row["canonical_method"], canonical_expected=row["canonical_expected"],
            historical_r415_remaining=row.get("remaining"), historical_receipts_location="Previous Gui-Quan-Bu-R4.15-20261114.zip; original index retained in this packet for provenance.",
            current_candidate_source_identity=m["source_identity"],
            unit_cases_with_matching_filename= len(matched),
            coverage_note="Filename grouping is a navigation aid, not complete requirement coverage or K acceptance. Joint gates remain open.",
            backend_maintainability_gate="pass", k_acceptance_status="pending_joint_evidence",
            current_evidence=[dict(path=n, sha256=digest(E/n)) for n in refs]))
    index = dict(scope="Internal working index; no final acceptance for Bao.", items=items,
                 latest_candidate=dict(revision="R4.18", source_identity=m["source_identity"], runtime_identity=m["runtime_identity"],
                 all_k01_k18_pass=False, final_bao_report_exported=False))
    assert len(items) == 18 and len({r["task_id"] for r in items}) == 18
    (E / "internal-k01-k18-evidence-index-r418.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    appendix = "\n## Phụ lục tải R4.18: số đo đã kết thúc\n\n"
    appendix += "Open 5 action/s; 100 principal synthetic, 20 mỗi role owner/admin/analyst/reviewer/viewer; actor S/M/L=60/35/5. Warm-up 30s, cửa sổ đo 120s, drain tối đa 120s; seed 170013; max inflight 32, pending 128; poll 2s, jitter 0,2. API pool4, worker pool2/2slot; PG16 qua loopback; generator/API/worker/DB cùng laptop. Source chạy ext4; hash trước/sau giữ nguyên. Dataset debug 32/128/512 dòng, 2/16/64 claim theo S/M/L, chưa là dataset primary. Cache state unknown.\n\n"
    appendix += f"750 action được lên lịch và gửi, 150 warm-up và 600 đo. Cửa sổ đo: 590/590 hợp lệ đúng deadline, 10/10 deny đúng. Toàn lượt: 729 completed, 17 deny, 3 timeout, 1 rejected; 1.079 HTTP, gồm 956 mã200, 71 mã201, 32 mã202, 17 mã404, 1 mã400 và 1 mã503. Hoàn tất response 1.078/1.079. Verify đủ byte/hash 71 bản tải. Harness xác nhận 30/32 job; telemetry ghi 32 settled (19 analyze, 13 follow-up), duration {min(r['duration_ms'] for r in jobs)}–{max(r['duration_ms'] for r in jobs)}ms, median {statistics.median(r['duration_ms'] for r in jobs):.0f}ms, gồm warm-up. Không đổi timeout thành pass theo log settled.\n\n"
    appendix += "503 là POST results/export, 5.017ms; giữ nguyên lỗi. Mutation chưa rõ của action44/P23 được đối soát read-only sau khi cả API/worker do fixture sở hữu đã thoát: không có effect, 0 mutation còn chưa quan sát. Deadline ban đầu vẫn failed. Hai job thiếu xác nhận terminal vẫn thiếu body/hash witness từ harness. Không kết luận worker treo từ bộ đếm harness này. Chưa có witness tương quan từng ACK/queue/claim/commit/output để chốt nguyên nhân warm-up; cần đo tiếp trên topology tách máy.\n\n"
    appendix += f"Thời gian cửa sổ quan sát {raw['elapsed_seconds']:.6f}s; peak inflight {raw['peak_action_inflight']}. Generator peak RSS {max(r['max_rss_native_units'] for r in raw['generator_resources'])}KiB, peak FD {max(r['fd_count'] for r in raw['generator_resources'])}. Mẫu CPU cuối user={raw['generator_resources'][-1]['user_cpu_seconds']:.6f}s, system={raw['generator_resources'][-1]['system_cpu_seconds']:.6f}s; gồm chuẩn bị tiến trình, không phải CPU riêng cửa sổ đo. Pytest172,79s, wrapper250,228s gồm xác minh/copy/hash.\n\n"
    appendix += "22 strata, mỗi nhóm 1–160 mẫu. Histogram p95 là biên trên bucket, không phải raw exact percentile. Các nhóm vượt mục tiêu: " + "; ".join(f"{r['journey']} {r['size']} n={r['n']}: {r['p95_upper_seconds']:.6f}s > {r['target_seconds']}s" for r in missed) + ". Mọi nhóm đều dưới 10.000 mẫu, không đủ gate precision primary p99. CSV ghi n, deadline, target và p95/p99 từng nhóm. Vì lỗi warm-up và latency chưa đạt, toàn campaign chưa pass dù subset đo hợp lệ đạt 100%.\n\n"
    appendix += "Campaign lịch sử R4.10/R4.11 giữ đúng source: open10 đạt1.178/1.180; open20 đạt2.025/2.346; closed100 đạt924/968. Không relabel thành R4.18. Bộ hiện tại xác nhận độ đúng của subset local, chưa xác định sức tải tối đa hoặc primary50k.\n"
    note = H / "PHAN_HOI_GUI_QUAN_R418.md"
    if "## Phụ lục tải R4.18" not in note.read_text(encoding="utf-8"):
        with note.open("a", encoding="utf-8") as output:
            output.write(appendix)
    entries = {"Release-K01-K18-R4.18/source/"+n: KIT/"source"/n for n in m["files"]}
    for n in ("source-manifest.json", "changes-from-r31.json", "installed_wheel_check.py", "test-constraints.txt"):
        entries["Release-K01-K18-R4.18/"+n] = KIT/n
    for folder in ("dist", "tools", "tests"):
        for p in (KIT/folder).rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
                entries["Release-K01-K18-R4.18/"+p.relative_to(KIT).as_posix()] = p
    source_zip = H / "Source-Kien-R4.18-65c87fdb0a43.zip"
    if source_zip.exists():
        assert source_zip.with_suffix(".zip.sha256").read_text().split()[0] == digest(source_zip)
        with zipfile.ZipFile(source_zip) as package:
            expected = json.loads(package.read("SHA256SUMS.json"))
            assert expected == {name: digest(path) for name, path in entries.items()}
    else:
        archive(source_zip, entries)
    packet = {p.name:p for p in (source_zip, source_zip.with_suffix(".zip.sha256"), note, H/"TIN_NHAN_GUI_QUAN_R418.txt")}
    for n in ("DOI_CHIEU_NOI_BO_R418.md", "BO_SUNG_KIEM_TRA_R418.md"):
        path = H/n
        if path.is_file():
            packet[n] = path
    for p in (H/"R4.15-to-R4.18-patches-final").iterdir():
        if p.is_file(): packet["patches/"+p.name] = p
    for n in ("Ca-kiem-danh-tinh.csv", "Nghiem-thu-chung.csv", "Gate-phat-hanh.csv", "k17-primary-v3-proposal.md", "k17-expanded-workload.md"):
        packet["can-phoi-hop/"+n] = H/n
    selected = {p for pattern in ("r418-*", "r417-*", "r416-*") for p in E.glob(pattern) if p.is_file()}
    selected = {p for p in selected if "handoff-package-receipt" not in p.name}
    selected.update(p for pattern in ("image-*r418*.log", "image-r418*.json", "linux-r418-pitr-health*.log", "linux-ext4-r4.18-*.log", "windows-r418-k*-console.log",
                                      "windows-r415-k14-export-comparison-console.log", "linux-r418-k14-export-console.log")
                    for p in E.glob(pattern) if p.is_file())
    selected.update(E/n for n in ("internal-k01-k18-evidence-index-r418.json", "internal-k01-k18-evidence-index-r415.json", "internal-test-run-ledger-r415.json",
        "r415-maintainability-remaining.json", "image-r415-security-summary.json", "r415-debian-trixie-applicability.json", "capacity-campaign-comparison-r411.json"))
    for folder in ("windows-unit-r418-sealed", "windows-unit-r417-sealed", "windows-r417-native-regression", "windows-r417-project-contracts",
        "windows-r418-restore-delivery", "windows-r418-restore-delivery-complete", "windows-r418-fault-metrics", "linux-r418-fifo",
        "linux-release-r4.18", "linux-r418-open5", "browser-api-pdf-r418-repeat-sealed", "k17-window-contract-prototype-r412",
        "windows-r418-k08-k10-k14-k15", "windows-r418-k14-export-isolated", "windows-r415-k14-export-comparison",
        "linux-r418-k14-export", "linux-ext4-r4.18-pitr", "linux-ext4-r4.18-health"):
        selected.update(p for p in (E/folder).rglob("*") if p.is_file() and not {"private", ".private", "__pycache__", "work"}.intersection(p.relative_to(E/folder).parts))
    for p in selected:
        assert p.is_file(), p
        packet["evidence/"+p.relative_to(E).as_posix()] = p
    for n in ("record_r418_closeout.py", "package_r418_handoff.py", "make_r418_patches.py", "run_selected_r418.py",
              "run_browser_pdf.py", "browser_pdf.mjs", "browser_session_cases.mjs", "linux_r418_open5.sh", "linux_release.sh",
              "summarize_capacity.py", "package_r415_handoff.py", "k17_window_contract_prototype.py", "scan_r418_image.ps1", "linux_qualification.sh"):
        packet["actual-reproduction-tools/"+n] = ROOT/"tools"/n
    target = H / ("Gui-Quan-Bu-R4.18-20261114-bo-sung.zip" if (H/"BO_SUNG_KIEM_TRA_R418.md").is_file() else "Gui-Quan-Bu-R4.18-20261114.zip")
    archive(target, packet)
    receipt = dict(source_identity=m["source_identity"], runtime_identity=m["runtime_identity"],
        source_archive=dict(name=source_zip.name, bytes=source_zip.stat().st_size, sha256=digest(source_zip)),
        technical_packet=dict(name=target.name, bytes=target.stat().st_size, sha256=digest(target), entries=len(packet)+1),
        crc_and_every_entry_sha256_verified=True, known_private_credentials_absent=True, sent_to_anyone=False,
        all_k01_k18_pass=False, final_bao_report_exported=False, recorded_utc=datetime.now(timezone.utc).isoformat())
    receipt_name = "r418-handoff-package-receipt-bo-sung.json" if "bo-sung" in target.stem else "r418-handoff-package-receipt.json"
    (E/receipt_name).write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
