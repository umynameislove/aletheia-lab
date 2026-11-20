"""Build small backend patch groups and prove the exact R4.18 source from R4.15."""
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "Release-K01-K18-R4.15"
NEW = ROOT / "Release-K01-K18-R4.18"
OUTPUT = ROOT / "handoff/R4.15-to-R4.18-patches-final"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    old = json.loads((OLD / "source-manifest.json").read_bytes())
    new = json.loads((NEW / "source-manifest.json").read_bytes())
    changed = sorted(name for name, digest in new["files"].items() if old["files"].get(name) != digest)
    assert not old["files"].keys() - new["files"].keys()
    OUTPUT.mkdir(exist_ok=False)
    groups = {}
    job_names = {"postgres_jobs", "job_admission", "job_output", "job_publication", "canonical_results", "upload_publication"}
    data_names = {"legacy_import", "retention", "postgres_objects", "capacity_seed", "capacity_seed_domain", "upload_project"}
    route_names = {"authorization", "postgres_catalog", "catalog_http", "imports_http", "job_http", "upload_runtime",
                   "upload_staging_http", "workspace_http", "workspace_management", "result_views", "source_bundle",
                   "recovery_points", "recovery_checkpoint"}
    for name in changed:
        stem = Path(name).stem
        if name.startswith("src/aletheia_lab/project/") or stem == "test_maintainability_contract":
            group = "01-project"
        elif stem in job_names:
            group = "02-jobs"
        elif stem in data_names:
            group = "03-import-seed-retention"
        elif stem in route_names:
            group = "04-http-authorization"
        else:
            group = "05-runtime-workers"
        groups.setdefault(group, []).append(name)
    patches = []
    for group, names in sorted(groups.items()):
        content = []
        added = deleted = 0
        for name in names:
            prior = OLD / "source" / name
            current = NEW / "source" / name
            before = prior.read_bytes().decode("utf-8").splitlines(keepends=True) if prior.is_file() else []
            after = current.read_bytes().decode("utf-8").splitlines(keepends=True)
            assert not before or before[-1].endswith(chr(10))
            assert after and after[-1].endswith(chr(10))
            lines = list(difflib.unified_diff(before, after, fromfile="a/" + name if before else "/dev/null",
                                             tofile="b/" + name))
            content.append("diff --git a/" + name + " b/" + name + chr(10))
            if not before:
                content.append("new file mode 100644" + chr(10))
            content.extend(lines)
            added += sum(line.startswith("+") and not line.startswith("+++") for line in lines)
            deleted += sum(line.startswith("-") and not line.startswith("---") for line in lines)
        path = OUTPUT / (group + ".patch")
        path.write_bytes("".join(content).encode("utf-8"))
        patches.append(dict(path=path.name, sha256=sha(path), files=names, added_lines=added,
                            deleted_lines=deleted, changed_lines=added+deleted))
    work = ROOT / "tmp/r418-patch-verification-clean"
    work.mkdir(exist_ok=False)
    for name, expected in old["files"].items():
        assert sha(OLD / "source" / name) == expected
        target = work / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((OLD / "source" / name).read_bytes())
    git = shutil.which("git")
    assert git
    environment = dict(os.environ, GIT_CEILING_DIRECTORIES=str(work.parent.resolve()))
    environment.pop("GIT_DIR", None)
    environment.pop("GIT_WORK_TREE", None)
    for patch in patches:
        command = [git, "-c", "core.autocrlf=false", "-c", "core.eol=lf", "apply"]
        for arguments in (["--check"], ["--apply"]):
            result = subprocess.run(command + arguments + [str(OUTPUT / patch["path"])],
                                    cwd=work, env=environment, capture_output=True, text=True, encoding="utf-8", check=False)
            assert result.returncode == 0, result.stderr
            assert "Skipped patch" not in result.stderr + result.stdout
    actual = {p.relative_to(work).as_posix(): sha(p) for p in work.rglob("*") if p.is_file()}
    assert actual == new["files"]
    receipt = dict(baseline_source_identity=old["source_identity"], source_identity=new["source_identity"],
                   runtime_identity=new["runtime_identity"], patches=patches, file_count=len(actual),
                   all_entries_match_sealed_manifest=True, git_apply_check_and_actual_apply_passed=True,
                   line_ending_policy="LF, core.autocrlf=false, core.eol=lf")
    (OUTPUT / "patch-manifest.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    (ROOT / "evidence/r418-patch-verification.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps([{"name": p["path"], "changed_lines": p["changed_lines"]} for p in patches]))


if __name__ == "__main__":
    main()
