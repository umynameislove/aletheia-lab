"""Record source-bound backend evidence and a technical handoff, never final acceptance."""
from datetime import datetime, timezone
import csv
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "evidence"
H = ROOT / "handoff"
KIT = ROOT / "Release-K01-K18-R4.18"


def read(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    assert not path.exists(), path
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")


def junit(path):
    cases = list(ET.parse(path).getroot().iter("testcase"))
    failed = [c for c in cases if c.find("failure") is not None or c.find("error") is not None]
    skipped = [c for c in cases if c.find("skipped") is not None]
    return dict(cases=len(cases), passed=len(cases)-len(failed)-len(skipped), failed=len(failed),
                skipped=len(skipped), testcase_seconds=sum(float(c.get("time", 0)) for c in cases),
                skipped_cases=[dict(name=c.get("name"), reason=c.find("skipped").get("message")) for c in skipped])


def main():
    m = read(KIT / "source-manifest.json")
    sid = m["source_identity"]
    assert all(sha(KIT / "source" / n) == h for n, h in m["files"].items())
    assert all(sha(ROOT / "source" / n) == h for n, h in m["files"].items())
    baseline = read(ROOT / "Release-K01-K18-R4.15/source-manifest.json")
    old = baseline["files"]
    changed = sorted(n for n in old.keys() & m["files"].keys() if old[n] != m["files"][n])
    added = sorted(m["files"].keys() - old.keys())
    assert len(changed) == 40 and len(added) == 6 and not old.keys() - m["files"].keys()
    scope = read(E / "r417-change-scope.json")
    assert scope["changed"] == changed and scope["added"] == added
    scope.update(source_identity=sid, runtime_identity=m["runtime_identity"])
    for prefix, count in (("dashboard/web/", 180),):
        paths = [n for n in old if n.startswith(prefix)]
        assert len(paths) == count and all(old[n] == m["files"][n] for n in paths)
    sql = [n for n in old if n.endswith(".sql")]
    assert len(sql) == 15 and all(old[n] == m["files"][n] for n in sql)
    for n in scope["preserved_guard_config_and_workflows"]:
        assert old[n] == m["files"][n]
    assert old["deploy/hosted/requirements.lock"] == m["files"]["deploy/hosted/requirements.lock"]
    write(E / "r418-change-scope.json", scope)
    audit = read(E / "r418-sealed-maintainability.json")
    assert audit["status"] == "pass" and not audit["findings"]
    assert audit["complexity"]["total_violations"] == 82
    functions = read(E / "r418-refactor-function-receipt.json")
    assert functions["baseline_functions_resolved"] == 53 and functions["hosted_max_function_score"] == 10
    budget = read(KIT / "source/configs/maintainability_budget.json")
    modules = []
    for name, before in (("hosted/postgres_jobs.py", 1241), ("project/importer.py", 1241),
                         ("project/mapping.py", 904), ("project/regression.py", 913)):
        path = "src/aletheia_lab/" + name
        after = len((KIT / "source" / path).read_bytes().splitlines())
        maximum = budget["module_size"]["exemptions"].get(path, {}).get("maximum_lines", 800)
        assert after <= maximum
        modules.append(dict(path=path, before_lines=before, after_lines=after, frozen_maximum=maximum, result="pass", source_identity=sid))
    with (E / "r418-refactor-module-results.csv").open("x", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(modules[0]))
        writer.writeheader(); writer.writerows(modules)
    quality = read(E / "r418-sealed-quality-result.json")
    assert len(quality) == 6 and all(v == 0 for v in quality.values())
    bandit = read(E / "r418-sealed-bandit.json")
    assert not bandit["results"] and not bandit["errors"]
    a, b = (read(E / f"linux-release-r4.18/build-{n}.json") for n in ("a", "b"))
    assert a == b and a["source_identity"] == sid
    installed = read(E / "linux-release-r4.18/installed-proof/result.json")
    previous = read(E / "linux-release-r4.15/installed-proof/result.json")
    assert installed["status"] == "passed" and installed["all_runtime_byte_parity"]
    assert installed["runtime_file_count"] == 676 and installed["runtime_pin_count"] == 52
    assert installed["child_origin_records"] == 4 and installed["no_source_pythonpath"]
    assert all(installed["outputs"][n]["sha256"] == previous["outputs"][n]["sha256"] for n in installed["outputs"])
    write(E / "r418-build-install-receipt.json", dict(source_identity=sid, runtime_identity=m["runtime_identity"],
          reproducible_artifacts=a["artifacts"], repeated_builds_byte_identical=True, runtime_files=676, runtime_pins=52,
          actual_installed_children=4, json_markdown_pdf_byte_identical_to_r415=True,
          pdf_parser=read(E / "linux-release-r4.18/installed-proof/pdf-parser.json"), full_release_or_primary50k_pass=False))
    browser = read(E / "browser-api-pdf-r418-repeat-sealed/result.json")
    assert browser["qualified"] and browser["source_identity"] == sid and len(browser["imports"]) == 3
    assert len({r["job_id"] for r in browser["imports"]}) == 3
    for key in ("project_id", "snapshot_id", "result_id"):
        assert len({r[key] for r in browser["imports"]}) == 1
    from pypdf import PdfReader
    pdf = E / "browser-api-pdf-r418-repeat-sealed/download" / browser["file"]
    assert sha(pdf) == browser["sha256"] and pdf.stat().st_size == browser["bytes"]
    parsed = PdfReader(pdf)
    assert len(parsed.pages) == 12 and all(p.extract_text().strip() for p in parsed.pages)
    write(E / "r418-browser-pdf-validation.json", dict(source_identity=sid, pages=12, every_page_has_text=True,
          sha256=sha(pdf), descriptor_and_browser_hash_match=True, source_and_working_copy_stable=True))
    suites = {}
    for label, folder in (("unit_exact_r418", "windows-unit-r418-sealed"),
                          ("postgres_r417_before_lf_normalization", "windows-r417-native-regression"),
                          ("project_contract_r417_before_lf_normalization", "windows-r417-project-contracts"),
                          ("restore_delivery_exact_r418", "windows-r418-restore-delivery-complete"),
                          ("faults_exact_r418", "windows-r418-fault-metrics"), ("fifo_linux_exact_r418", "linux-r418-fifo")):
        receipt = read(E / folder / "result.json")
        suite = junit(E / folder / "junit.xml")
        assert suite["failed"] == 0
        suites[label] = {**suite, "raw_result": folder+"/result.json", "raw_junit": folder+"/junit.xml", **receipt}
    assert suites["unit_exact_r418"]["passed"] == 2969
    bridge = read(E / "r418-line-ending-parity.json")
    assert bridge["all_normalized_python_asts_and_source_locations_equal"]
    load = read(E / "linux-r418-open5/observed-summary.json")
    assert load["source_identity"] == sid
    status = dict(scope="Internal backend closeout evidence; formal K01-K18 acceptance remains open.",
        recorded_utc=datetime.now(timezone.utc).isoformat(), source_identity=sid, runtime_identity=m["runtime_identity"],
        source_files=len(m["files"]), backend_53_functions_and_4_modules_pass=True,
        quality=quality, c901_global_before=135, c901_global_after=82, c901_global_ceiling=83,
        hosted_function_count=1121, hosted_maximum_score=10, modules=modules, suites=suites,
        equivalent_source_bridge="r418-line-ending-parity.json; R4.17 receipts retain their original identity. Only CRLF to LF changed; AST including source positions equal.",
        skipped_cases_resolution="Two PG_BIN skips rerun as two passing R4.18 cases; Windows FIFO skip rerun as one passing Linux R4.18 case. Original skips preserved.",
        historical_failed_attempts_preserved=["r416-worker-cli-before.xml", "r416-worker-cli-before-utf8.xml", "windows-r418-restore-delivery/junit.xml"],
        known_test_adjustment="test_maintainability_contract: exact line equality replaced by <= frozen maximum, preserving lower bound and numeric budgets; entrypoint regression added.",
        browser=browser, calibration=load,
        calibration_whole_run_clean=not any(load["counts"].get(k, 0) for k in ("timeout", "rejected", "unknown_mutations")) and load["jobs_pending_terminal"] == 0,
        all_k01_k18_pass=False, final_bao_report_exported=False,
        joint_open=["Integrated Git SHA and CI", "Shared IdP and remaining identity/browser matrix; URL hash IDs after logout; actual bfcache restoration",
                    "Product v3 decisions and consumer implementation", "Independent staging, primary50k, three campaigns and soak",
                    "Off-host independent restore and rollout/rollback", "47 HIGH /11 CVE historical image; rebuild/rescan R4.18 and deployment applicability review"])
    write(E / "r418-working-evidence-status.json", status)
    print(json.dumps(dict(source_identity=sid, modules=modules, suites={k:{n:v[n] for n in ("passed","failed","skipped")} for k,v in suites.items()}, calibration=load["measured_positive"])))


if __name__ == "__main__":
    main()
