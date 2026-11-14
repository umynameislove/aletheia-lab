"""Stage maintained source into a fresh release kit and seal every byte."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
GENERATED = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".hypothesis",
             "dist", "dist-live", "dist-hosted", "dist-bench"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("revision", choices=("R4", "R4.1", "R4.2", "R4.3", "R4.4", "R4.5", "R4.6", "R4.7", "R4.8", "R4.9", "R4.10", "R4.11", "R4.12", "R4.13", "R4.14", "R4.15", "R4.16", "R4.17", "R4.18"), nargs="?", default="R4")
    args = parser.parse_args()
    source = KIT / "source"
    output = KIT / ("Release-K01-K18-" + args.revision)
    if output.exists():
        raise RuntimeError("Release output already exists; preserve it and use a new revision.")
    files = {}
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in GENERATED or part.endswith(".egg-info") for part in relative.parts) or path.suffix in {".pyc", ".tsbuildinfo"}:
            continue
        if path.is_symlink() or ".private" in relative.parts or ".env" in relative.parts or path.name == "actors.jsonl":
            raise ValueError("Unexpected private or linked source input.")
        if path.is_file():
            files[relative.as_posix()] = digest(path.read_bytes())
    baseline = json.loads((KIT / "baseline-source-manifest.json").read_bytes())
    assert files and "pyproject.toml" in files and "src/aletheia_lab/__init__.py" in files
    for name, expected in files.items():
        data = (source / name).read_bytes()
        assert digest(data) == expected, "Source changed during sealing."
        target = output / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    runtime = {name: value for name, value in files.items() if name.startswith("src/aletheia_lab/")}
    identity = lambda mapping: digest(json.dumps(mapping, sort_keys=True, separators=(",", ":")).encode())
    manifest = {"schema_version": "aletheia-sealed-source/v1", "source_date_epoch": baseline.get("source_date_epoch", 1794555434),
                "file_count": len(files), "source_identity": identity(files), "files": files,
                "runtime_count": len(runtime), "runtime_identity": identity(runtime), "runtime": runtime,
                "baseline_source_identity": baseline["source_identity"], "baseline_runtime_identity": baseline["runtime_identity"]}
    for subdirectory in ("tools", "tests"):
        shutil.copytree(KIT / subdirectory, output / subdirectory, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("installed_wheel_check.py", "test-constraints.txt"):
        shutil.copy2(KIT / name, output / name)
    (output / "source-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    changes = {"baseline_source_identity": baseline["source_identity"], "source_identity": manifest["source_identity"],
               "added": sorted(files.keys() - baseline["files"].keys()), "removed": sorted(baseline["files"].keys() - files.keys()),
               "changed": sorted(name for name in files.keys() & baseline["files"].keys() if files[name] != baseline["files"][name])}
    (output / "changes-from-r31.json").write_text(json.dumps(changes, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: manifest[key] for key in ("file_count", "runtime_count", "source_identity", "runtime_identity")}))


if __name__ == "__main__":
    main()
