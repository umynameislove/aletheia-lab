"""Run the existing isolated Keycloak/Mailpit recovery acceptance, never an external IdP."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def runtime_identity(source: Path) -> str:
    files = {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted((source / "src/aletheia_lab").rglob("*"))
             if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    source = root / "Release-K01-K18-R4/source"
    if not source.is_dir():
        raise RuntimeError("Seal the release before the local identity run.")
    out = root / "evidence/local-idp-recovery"
    out.mkdir(exist_ok=False)
    fixture = json.loads((root / "evidence/postgres-fixture.json").read_text())
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
                       PYTHONPATH=os.pathsep.join((str(source / "src"), str(source / "tests/unit"))),
                       TEMP=str(root / "tmp"), TMP=str(root / "tmp"),
                       K03_DB_PORT=str(fixture["port"]),
                       K03_DB_PASSWORD=(root / ".private/k03-database-password.txt").read_text().strip())
    for name in tuple(environment):
        if name.endswith("_API_KEY") or name.upper().startswith("PG"):
            environment.pop(name, None)
    before, started = runtime_identity(source), time.monotonic()
    command = [sys.executable, "-B", str(source / "tests/unit/test_k03_keycloak_recovery.py"), "--run-recovery"]
    with (out / "safe-outcomes.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=source, env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=900)
    after = runtime_identity(source)
    receipt = {"exit_code": result.returncode, "elapsed_seconds": time.monotonic() - started,
               "runtime_before": before, "runtime_after": after, "runtime_stable": before == after,
               "qualified_local_recovery": result.returncode == 0 and before == after,
               "scope": "Real disposable local Keycloak/Mailpit, retained-OTP recovery and PG; not final hosted browser, production IdP, all32identitycases or50k."}
    (out / "result.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt))
    return 0 if receipt["qualified_local_recovery"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
