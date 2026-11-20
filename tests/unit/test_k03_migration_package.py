"""Manual artifact check; pytest verifies the single approved package-data rule."""

import hashlib
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import traceback
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SQL_PATH = "aletheia_lab/hosted_auth/admission_schema.sql"


def _run(command, *, cwd, environment):
    result = subprocess.run(command, cwd=cwd, env=environment, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=180,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    if result.returncode != 0:
        print("K03_PACKAGE_COMMAND_EXIT=" + str(result.returncode), flush=True)
        raise RuntimeError("Local package command failed.")
    return result.stdout


def _build(source, directory, *, sdist):
    for name in ("egg", "build", "bdist", "dist"):
        (directory / name).mkdir(parents=True)
    command = [sys.executable, "-B", "-c", "from setuptools import setup; setup()",
        "egg_info", "--egg-base", str(directory / "egg"),
        "build", "--build-base", str(directory / "build"),
        "bdist_wheel", "--bdist-dir", str(directory / "bdist"), "--dist-dir", str(directory / "dist")]
    if sdist:
        command.extend(["sdist", "--dist-dir", str(directory / "dist")])
    _run(command, cwd=source, environment=dict(os.environ))
    wheels = list((directory / "dist").glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def _check_wheel(wheel, digest):
    with zipfile.ZipFile(wheel) as archive:
        assert archive.namelist().count(SQL_PATH) == 1
        assert hashlib.sha256(archive.read(SQL_PATH)).digest() == digest


def _extract_own_sdist(archive_path, destination):
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            resolved = (destination / member.name).resolve()
            if not resolved.is_relative_to(destination.resolve()) or not (member.isfile() or member.isdir()):
                raise ValueError("Unexpected source artifact member.")
        archive.extractall(destination, filter="data")
    roots = list(destination.iterdir())
    assert len(roots) == 1 and roots[0].is_dir()
    return roots[0]


def _live():
    directory = Path(tempfile.mkdtemp(prefix="aletheia-k03-package-"))
    digest = hashlib.sha256((ROOT / "src" / SQL_PATH).read_bytes()).digest()
    wheel = _build(ROOT, directory / "source-build", sdist=True)
    _check_wheel(wheel, digest)
    sdists = list(wheel.parent.glob("*.tar.gz"))
    assert len(sdists) == 1
    with tarfile.open(sdists[0], "r:gz") as archive:
        members = [member for member in archive.getmembers() if member.name.endswith("/src/" + SQL_PATH)]
        assert len(members) == 1
        assert hashlib.sha256(archive.extractfile(members[0]).read()).digest() == digest
    extracted = directory / "extracted"
    extracted.mkdir()
    source = _extract_own_sdist(sdists[0], extracted)
    rebuilt = _build(source, directory / "sdist-build", sdist=False)
    _check_wheel(rebuilt, digest)
    target = directory / "installed"
    _run([sys.executable, "-B", "-m", "pip", "--isolated", "--disable-pip-version-check", "install",
          "--no-index", "--no-deps", "--no-compile", "--target", str(target), str(rebuilt)],
         cwd=directory, environment=dict(os.environ))
    environment = dict(os.environ)
    paths = [str(target), *(item for item in environment.get("PYTHONPATH", "").split(os.pathsep)
                            if item and Path(item).resolve() != (ROOT / "src").resolve())]
    environment["PYTHONPATH"] = os.pathsep.join(paths)
    environment["K03_PACKAGE_TARGET"] = str(target)
    environment["K03_PACKAGE_SQL_DIGEST"] = digest.hex()
    probe = """import hashlib,os
from pathlib import Path
from aletheia_lab.hosted_auth import admission_migration as migration
root=Path(os.environ['K03_PACKAGE_TARGET']).resolve()
assert Path(migration.__file__).resolve().is_relative_to(root)
assert hashlib.sha256(migration._ddl().encode('utf-8')).hexdigest()==os.environ['K03_PACKAGE_SQL_DIGEST']
print('K03_INSTALLED_MIGRATION_SQL=PASS')
"""
    assert _run([sys.executable, "-B", "-c", probe], cwd=directory, environment=environment).strip() == "K03_INSTALLED_MIGRATION_SQL=PASS"
    _run([sys.executable, "-B", "-m", "aletheia_lab.hosted_auth.admission_migrate_cli", "--help"],
         cwd=directory, environment=environment)
    print("K03_WHEEL_AND_SDIST_SQL_CHECKSUM=PASS", flush=True)
    print("K03_SDIST_REBUILT_WHEEL_SQL=PASS", flush=True)
    print("K03_INSTALLED_MIGRATION_SQL_AND_CLI=PASS", flush=True)
    print("K03_PACKAGE_INSTALLED_TARGET=" + str(target), flush=True)


def test_migration_sql_is_explicit_package_data_and_remains_single_canonical_file():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["tool"]["setuptools"]["package-data"]["aletheia_lab.hosted_auth"] == ["admission_schema.sql"]
    assert (ROOT / "src" / SQL_PATH).is_file()


@pytest.mark.parametrize("kind", ["parent", "symlink"])
def test_sdist_probe_cannot_extract_outside_its_owned_build_directory(tmp_path, kind):
    artifact = tmp_path / "malformed.tar.gz"
    destination = tmp_path / "extract"
    destination.mkdir()
    with tarfile.open(artifact, "w:gz") as archive:
        member = tarfile.TarInfo("../escaped.txt" if kind == "parent" else "root/link")
        if kind == "symlink":
            member.type = tarfile.SYMTYPE
            member.linkname = "../../escaped.txt"
            archive.addfile(member)
        else:
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="Unexpected source artifact member"):
        _extract_own_sdist(artifact, destination)
    assert list(destination.iterdir()) == [] and not (tmp_path / "escaped.txt").exists()


if __name__ == "__main__":
    try:
        _live()
    except Exception as error:
        print("K03_PACKAGE_CHECK=FAIL:" + type(error).__name__, flush=True)
        frames = traceback.extract_tb(error.__traceback__)
        if frames:
            print("K03_PACKAGE_FAILURE_LINE=" + str(frames[-1].lineno), flush=True)
        sys.exit(1)
