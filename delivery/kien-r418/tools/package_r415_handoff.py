"""Build the R4.15 technical handoff with verified source and measured evidence."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence"
KIT = ROOT / "Release-K01-K18-R4.15"
HANDOFF = ROOT / "handoff"


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def archive(target, entries):
    assert not target.exists(), target
    assert all(not Path(name).is_absolute() and ".." not in Path(name).parts
               and ".private" not in Path(name).parts and chr(92) not in name
               and Path(name).name not in (".env", "actors.jsonl") for name in entries)
    hashes = {name: digest(path) for name, path in entries.items()}
    secrets = set()
    for name in ("database-url.txt", "linux-database-url.txt", "k03-database-password.txt", "recovery-r41-url.txt"):
        path = ROOT / ".private" / name
        if path.is_file():
            value = path.read_text(encoding="utf-8-sig").strip()
            secrets.add(value.encode())
            password = urlsplit(value).password if "://" in value else None
            if password:
                secrets.add(unquote(password).encode())
    for path in entries.values():
        payload = path.read_bytes()
        assert not any(value and value in payload for value in secrets), "A private credential was found."
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for name, path in sorted(entries.items()):
            output.write(path, name)
        output.writestr("SHA256SUMS.json", json.dumps(hashes, sort_keys=True, indent=2))
    with zipfile.ZipFile(target) as output:
        assert output.testzip() is None
        assert set(output.namelist()) == set(entries) | {"SHA256SUMS.json"}
        assert all(hashlib.sha256(output.read(name)).hexdigest() == expected for name, expected in hashes.items())
    target.with_suffix(target.suffix + ".sha256").write_text(digest(target) + "  " + target.name + chr(10), encoding="ascii")


def main():
    manifest = read(KIT / "source-manifest.json")
    assert all(digest(KIT / "source" / name) == expected for name, expected in manifest["files"].items())
    status = read(EVIDENCE / "r415-working-evidence-status.json")
    assert manifest["source_identity"] == status["source_identity"]
    assert status["suites"]["unit_serial"]["passed"] == 2968
    assert status["suites"]["native_serial"]["passed"] == 81
    assert status["all_k01_k18_pass"] is False and status["final_bao_report_exported"] is False

    entries = {"Release-K01-K18-R4.15/source/" + name: KIT / "source" / name for name in manifest["files"]}
    for name in ("source-manifest.json", "changes-from-r31.json", "installed_wheel_check.py", "test-constraints.txt"):
        entries["Release-K01-K18-R4.15/" + name] = KIT / name
    for folder in ("dist", "tests", "tools"):
        for path in (KIT / folder).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                entries["Release-K01-K18-R4.15/" + path.relative_to(KIT).as_posix()] = path
    source_zip = HANDOFF / "Source-Kien-R4.15-cd3f65970f46.zip"
    archive(source_zip, entries)

    packet = {}
    for path in (source_zip, source_zip.with_suffix(".zip.sha256")):
        packet[path.name] = path
    for name in ("PHAN_HOI_GUI_QUAN_R415.md", "TIN_NHAN_GUI_QUAN_R415.txt", "R4.14-to-R4.15.patch"):
        packet[name] = HANDOFF / name
    for name in ("Ca-kiem-danh-tinh.csv", "Nghiem-thu-chung.csv", "Gate-phat-hanh.csv", "k17-primary-v3-proposal.md", "k17-expanded-workload.md"):
        packet["can-phoi-hop/" + name] = HANDOFF / name

    selected = {path.name for path in EVIDENCE.glob("r415-*.json")}
    selected.update(("internal-k01-k18-evidence-index-r415.json", "internal-test-run-ledger-r415.json",
                     "image-build-r415-result.json", "image-r415-security-summary.json", "image-r415-trivy-offline.json",
                     "image-r415-sbom.cdx.json", "capacity-campaign-comparison-r411.json", "quan-r411-peer-evidence-review.json"))
    index = read(EVIDENCE / "internal-k01-k18-evidence-index-r415.json")
    for row in index["items"]:
        for item in row["local_evidence"]:
            assert digest(EVIDENCE / item["path"]) == item["sha256"]
            selected.add(item["path"])
    ledger = read(EVIDENCE / "internal-test-run-ledger-r415.json")
    for row in ledger["runs"]:
        assert digest(EVIDENCE / row["path"]) == row["raw_sha256"]
        selected.add(row["path"])
    for pattern in ("r415-*.log", "r415-*.xml"):
        selected.update(path.name for path in EVIDENCE.glob(pattern))
    for name in selected:
        packet["evidence/" + name] = EVIDENCE / name
    folders = ("windows-unit-r4.15", "windows-unit-r415-serial", "windows-r415-native-regression",
               "windows-r415-native-regression-serial", "windows-r415-durable-export-serial",
               "windows-r415-logical-restore", "browser-api-pdf-r415-repeat-upload-sealed",
               "browser-api-pdf-r415-session-tabs", "linux-release-r4.15", "linux-r415-open5",
               "r415-local-idp-recovery", "r415-local-idp-recovery-serial", "k17-window-contract-prototype-r412")
    for folder in folders:
        for path in (EVIDENCE / folder).rglob("*"):
            if path.is_file():
                packet["evidence/" + path.relative_to(EVIDENCE).as_posix()] = path
    for name in ("run_browser_pdf.py", "browser_pdf.mjs", "browser_session_cases.mjs", "linux_r415_open5.sh",
                 "k17_window_contract_prototype.py", "summarize_capacity.py", "package_r415_handoff.py"):
        packet["actual-reproduction-tools/" + name] = ROOT / "tools" / name
    target = HANDOFF / "Gui-Quan-Bu-R4.15-20261114.zip"
    archive(target, packet)
    receipt = dict(source_identity=manifest["source_identity"], runtime_identity=manifest["runtime_identity"],
                   source_archive=dict(name=source_zip.name, bytes=source_zip.stat().st_size, sha256=digest(source_zip)),
                   technical_packet=dict(name=target.name, bytes=target.stat().st_size, sha256=digest(target), entries=len(packet)+1),
                   crc_and_every_entry_sha256_verified=True, known_private_credentials_absent=True,
                   all_k01_k18_pass=False, final_bao_report_exported=False, sent_to_anyone=False,
                   recorded_utc=datetime.now(timezone.utc).isoformat())
    receipt_path = EVIDENCE / "r415-handoff-package-receipt.json"
    assert not receipt_path.exists()
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=True))


if __name__ == "__main__":
    main()
