"""Run an existing strict operator phase on this revision; never overwrite an attempt."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("platform", choices=("windows", "linux"))
    parser.add_argument("phase", choices=("verify", "unit", "postgres", "metric100k", "large100k", "health", "pitr", "calibration", "renewal"))
    parser.add_argument("--revision", choices=("R4", "R4.1", "R4.2", "R4.3", "R4.4", "R4.5", "R4.6", "R4.7", "R4.8", "R4.9", "R4.10", "R4.11", "R4.12", "R4.13", "R4.14", "R4.15"), default="R4")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    kit = root / ("Release-K01-K18-" + args.revision)
    attempt = args.platform + "-" + args.phase + ("-" + args.revision.lower() if args.revision != "R4" else "")
    output = root / "evidence" / attempt
    if args.platform == "linux" and sys.platform == "win32":
        runtime = (root / "evidence/linux-runtime-path.txt").read_text().strip()
        linux_script = "/mnt/" + __file__[0].lower() + __file__[2:].replace("\\", "/")
        return subprocess.call(["wsl", "-d", "Ubuntu", "--exec", runtime + "/venv/bin/python", "-B", linux_script, args.platform, args.phase, "--revision", args.revision])
    if (args.platform == "windows") != (sys.platform == "win32"):
        raise RuntimeError("Phase platform and interpreter do not match.")
    if output.exists():
        raise RuntimeError("Attempt already exists; preserve it rather than overwrite or automatically retry.")
    command = [sys.executable, "-B", str(kit / "tools/run_checks.py"), args.phase]
    if args.phase != "verify":
        command += ["--output", str(output)]
    if args.phase in {"postgres", "large100k", "metric100k", "calibration", "renewal"}:
        database = root / ".private" / ("database-url.txt" if args.platform == "windows" else "linux-database-url.txt")
        command += ["--database-file", str(database)]
    if args.phase in {"postgres", "pitr"}:
        if args.platform == "windows":
            pg_bin = Path("C:/Aletheia/pg16-client-20261113-r31-01/pgsql/bin")
        else:
            pg_root = Path((root / "evidence/linux-pg16-path.txt").read_text().strip())
            pg_bin = pg_root / "root/usr/lib/postgresql/16/bin"
        command += ["--pg-bin", str(pg_bin)]
    environment = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    for name in list(environment):
        if name in {"SOURCE_DATE_EPOCH", "PYTHONPATH", "PYTHONHOME"} or name.upper().startswith("PG") or name.endswith("_API_KEY"):
            environment.pop(name, None)
    if args.platform == "linux":
        pg_root = Path((root / "evidence/linux-pg16-path.txt").read_text().strip())
        environment["LD_LIBRARY_PATH"] = str(pg_root / "root/usr/lib/x86_64-linux-gnu")
    else:
        environment.update(TEMP=str(root / "tmp"), TMP=str(root / "tmp"))
    started = time.monotonic()
    with (root / "evidence" / (attempt + "-console.log")).open("x", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=kit, env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=5400)
    print(json.dumps({"platform": args.platform, "phase": args.phase, "exit_code": result.returncode, "elapsed_seconds": time.monotonic() - started}))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
