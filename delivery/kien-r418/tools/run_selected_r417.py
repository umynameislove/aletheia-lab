"""Run an explicit selection against sealed R4.17 and retain an immutable receipt."""
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "Release-K01-K18-R4.17"
sys.path.insert(0, str(KIT / "tools"))
import run_checks as checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("label")
    parser.add_argument("--database", action="store_true")
    parser.add_argument("--project-contracts", action="store_true")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    if not args.label.isascii() or not args.label.replace("-", "").isalnum():
        raise ValueError("A simple evidence label is required.")
    manifest = checks.verify()
    identity = {key: manifest[key] for key in ("source_identity", "runtime_identity")}
    output = ROOT / "evidence" / args.label
    output.mkdir(exist_ok=False)
    source = KIT / "source"
    env = dict(os.environ)
    for name in tuple(env):
        if (name.upper().startswith("PG") or name.startswith(tuple(f"K{n:02}_" for n in range(2, 19)))
                or name.endswith("_TEST_DATABASE_URL") or name in ("PYTHONHOME", "PYTHONPATH", "PYTEST_ADDOPTS")):
            env.pop(name)
    env.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1", RUFF_NO_CACHE="true",
               PYTHONPATH=str(source / "src"), TEMP=str(ROOT / "tmp"), TMP=str(ROOT / "tmp"))
    database_file = ROOT / ".private/database-url.txt"
    private_values = checks.sensitive_values(database_file)
    if args.database:
        database = database_file.read_text(encoding="utf-8-sig").strip()
        if urlsplit(database).hostname not in ("127.0.0.1", "localhost"):
            raise ValueError("Owned loopback PostgreSQL is required.")
        env.update({f"K{n:02}_TEST_DATABASE_URL": database for n in range(2, 19)})
        env["K17_EVIDENCE_DIR"] = str(output / "faults")
    selected = args.paths
    if args.project_contracts:
        if selected or args.database:
            raise ValueError("Choose one explicit selection.")
        selected = ["-m", "not integration", *sorted(
            p.relative_to(source).as_posix() for p in (source / "tests/unit").glob("test_project_*.py")),
            "tests/unit/test_maintainability_contract.py"]
    if not selected:
        raise ValueError("At least one explicit test is required.")
    command = [sys.executable, "-B", "-m", "pytest", "-q", "-ra", "--tb=short", "-o", "addopts=",
               "-p", "no:cacheprovider", "--basetemp=" + str(ROOT / "tmp" / args.label),
               "--junitxml=" + str(output / "junit.xml"), *selected]
    attempt = dict(**identity, command=command, started_utc=datetime.now(timezone.utc).isoformat(),
                   local_owned_database=args.database, automatic_retry=False)
    (output / "attempt.json").write_text(json.dumps(attempt, indent=2), encoding="utf-8")
    started = time.monotonic()
    with (output / "pytest.log").open("x", encoding="utf-8") as log:
        result = checks.run_logged(command, cwd=source, env=env, log=log, secrets=private_values)
    checks.sanitize_junit(output / "junit.xml", private_values)
    cases = list(ElementTree.parse(output / "junit.xml").iter("testcase"))
    failed = sum(any(c.find(tag) is not None for tag in ("failure", "error")) for c in cases)
    skipped = sum(c.find("skipped") is not None for c in cases)
    stable = checks.verify() == manifest
    qualified = result.returncode == 0 and bool(cases) and failed == skipped == 0 and stable
    receipt = dict(**identity, executed=len(cases), failed=failed, skipped=skipped,
                   passed=len(cases) - failed - skipped, exit_code=result.returncode, qualified=qualified,
                   source_stable=stable, elapsed_seconds=time.monotonic() - started,
                   finished_utc=datetime.now(timezone.utc).isoformat(), full_release_or_primary50k_pass=False)
    (output / "result.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt))
    return 0 if qualified else 1


if __name__ == "__main__":
    raise SystemExit(main())
