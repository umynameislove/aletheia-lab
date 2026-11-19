"""Preserve local branch references and recoverable WIP before K01 integration."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
OUT = ROOT / "evidence/k0101-r418-integration"
PRIVATE = ROOT / ".private/k0101-r418-snapshots-v2"
REPOS = ("_repo_aletheia_ref", "aletheia-kien-code", "aletheia-quan-d4e7fb2-review",
         "aletheia-j01-api", "external/aletheia-lab")


def git(repo, *args):
    return subprocess.check_output(["git", "-c", "safe.directory="+repo.as_posix(), "-C", str(repo), *args])


def snapshot(name, destination):
    repo = WORKSPACE/name
    result = {"directory": name, "head": git(repo, "rev-parse", "HEAD").decode().strip()}
    for label, args in (("status", ("status", "--porcelain=v1", "-z", "--untracked-files=all")),
                        ("refs", ("show-ref",)),
                        ("tracked_patch", ("diff", "--binary", "HEAD")),
                        ("staged_patch", ("diff", "--binary", "--cached"))):
        value = git(repo, *args)
        result[label+"_sha256"] = hashlib.sha256(value).hexdigest()
        if destination is not None:
            (destination/(label+".bin")).write_bytes(value)
    names = set(git(repo, "diff", "--name-only", "-z", "HEAD").decode().split("\0"))
    names.update(git(repo, "ls-files", "--others", "--exclude-standard", "-z").decode().split("\0"))
    files = {}
    if destination is not None:
        archive = zipfile.ZipFile(destination/"working-files.zip", "x", compression=zipfile.ZIP_DEFLATED)
    else:
        archive = None
    try:
        for path in sorted(names - {""}):
            item = repo/path
            if item.is_symlink():
                link = os.readlink(item)
                files[path] = {"symlink": link}
                if archive is not None:
                    info = zipfile.ZipInfo(path)
                    info.create_system = 3
                    info.external_attr = 0o120777 << 16
                    archive.writestr(info, link.encode())
            elif item.is_file():
                payload = item.read_bytes()
                files[path] = {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
                if archive is not None:
                    archive.writestr(path, payload)
            else:
                files[path] = {"deleted": True}
    finally:
        if archive is not None:
            archive.close()
    result["wip"] = files
    return result


def main():
    OUT.mkdir(exist_ok=True)
    assert not (OUT/"before.json").exists()
    PRIVATE.mkdir(exist_ok=False)
    repositories = []
    for name in REPOS:
        repo = WORKSPACE/name
        if not (repo/".git").exists():
            continue
        folder = PRIVATE/name.replace("/", "-")
        folder.mkdir()
        repositories.append(snapshot(name, folder))
        bundle = folder/"refs.bundle"
        subprocess.run(["git", "-c", "safe.directory="+repo.as_posix(), "-C", str(repo),
                        "bundle", "create", str(bundle), "--all"], check=True, capture_output=True)
        result = subprocess.run(["git", "-c", "safe.directory="+repo.as_posix(), "-C", str(repo),
                                 "bundle", "verify", str(bundle)], check=True, capture_output=True)
        (folder/"bundle-verify.log").write_bytes(result.stdout+result.stderr)
        with zipfile.ZipFile(folder/"working-files.zip") as archive:
            assert archive.testzip() is None
            for path, info in repositories[-1]["wip"].items():
                if info.get("symlink") is not None:
                    assert archive.read(path).decode() == info["symlink"]
                elif not info.get("deleted"):
                    assert hashlib.sha256(archive.read(path)).hexdigest() == info["sha256"]
        (OUT/(name.replace("/", "-")+"-before.json")).write_text(
            json.dumps(repositories[-1], ensure_ascii=False, indent=2), encoding="utf-8")
    record = dict(captured_utc=datetime.now(timezone.utc).isoformat(), repositories=repositories,
                  recoverable_private_snapshots=str(PRIVATE), purpose="K01.01 branch and WIP preservation")
    (OUT/"before.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(repos=len(repositories), wip_paths=sum(len(r["wip"]) for r in repositories))))


if __name__ == "__main__":
    main()
