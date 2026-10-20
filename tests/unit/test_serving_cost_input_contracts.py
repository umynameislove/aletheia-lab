"""Owned byte seals and recovery inputs; no model qualification or cost run."""

import hashlib
import json
from pathlib import Path

import pytest

from aletheia_lab.evaluation import serving_audit_cost_study as study
from aletheia_lab.evaluation.serving_audit_cost_workload import INPUTS, encoded


def seal(payload):
    result = dict(payload)
    result["plan_sha256"] = hashlib.sha256(encoded(payload)).hexdigest()
    return result


def test_copy_is_exclusive_byte_identical_and_acknowledged(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"owned original\x00\n")
    target = tmp_path / "snapshot/source"
    result = study._copy(source, target)
    assert target.read_bytes() == source.read_bytes()
    assert result == {"bytes": 16, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    with pytest.raises(FileExistsError, match="already exists"):
        study._copy(source, target)
    assert target.read_bytes() == source.read_bytes()
    with pytest.raises(ValueError, match="regular file"):
        study._copy(tmp_path / "missing", tmp_path / "never-created")


def test_copy_rejects_changed_bytes_before_ack(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.write_bytes(b"owned")
    original_copy = study.shutil.copyfile

    def changed(before, after):
        original_copy(before, after)
        Path(after).write_bytes(b"different")

    monkeypatch.setattr(study.shutil, "copyfile", changed)
    with pytest.raises(ValueError, match="snapshot bytes differ"):
        study._copy(source, tmp_path / "copy")


def test_copy_ack_fsync_uses_a_writable_descriptor(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "copy"
    source.write_bytes(b"owned copy")
    real_open, modes = Path.open, []

    def opened(path, mode="r", *args, **kwargs):
        if path == target and "+" in mode:
            modes.append(mode)
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opened)
    study._copy(source, target)
    assert modes == ["r+b"]
    assert target.read_bytes() == b"owned copy"


def test_runtime_version_table_is_exact_not_minimum(monkeypatch):
    monkeypatch.setattr(study.importlib.metadata, "version", study.RUNTIME.__getitem__)
    assert study._runtime() == study.RUNTIME
    monkeypatch.setattr(study.importlib.metadata, "version", lambda name: "wrong")
    with pytest.raises(ValueError, match="qualified runtime differs"):
        study._runtime()


@pytest.fixture
def owned_plan(tmp_path, monkeypatch):
    root, recovery = tmp_path / "code", tmp_path / "recovery"
    root.mkdir()
    recovery.mkdir()
    bindings = {}
    for name in study.CODE:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("# synthetic owned " + name + "\n").encode())
        bindings[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    names = ["model/graph.onnx", "model.sig.json", "verification-public.pem"]
    names += [f"inputs/{name}.npy" for name in INPUTS]
    names += [f"outputs/qualification-torch-{name}.npy" for name in INPUTS]
    inputs = {}
    for name in names:
        path = recovery / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("inert " + name).encode())
        inputs[name] = {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    payload = {
        "root": str(root),
        "qualified_directory": str(recovery),
        "runtime": dict(study.RUNTIME),
        "artifact": {"files": [{"path": "graph.onnx"}]},
        "recovery_files": inputs,
        "code_sha256": bindings,
        "configs": study.configurations(),
        "calls_per_worker": 64,
    }
    path = tmp_path / "plan.json"
    path.write_bytes(encoded(seal(payload)))
    monkeypatch.setattr(study, "_runtime", lambda: dict(study.RUNTIME))
    return path, payload


def test_plan_checks_all_owned_inputs_without_rewriting_them(owned_plan):
    path, payload = owned_plan
    before = {file: file.read_bytes() for file in path.parent.rglob("*") if file.is_file()}
    assert study.check_plan(path) == seal(payload)
    assert before == {file: file.read_bytes() for file in path.parent.rglob("*") if file.is_file()}


@pytest.mark.parametrize(
    "mutation",
    [
        "seal",
        "census",
        "runtime",
        "code-membership",
        "recovery-membership",
        "code-bytes",
        "recovery-bytes",
    ],
)
def test_resealed_plan_cannot_hide_fixed_census_or_input_changes(owned_plan, mutation):
    path, payload = owned_plan
    expected = {
        "seal": "execution seal",
        "census": "worker census",
        "runtime": "runtime seal",
        "code-membership": "executable seal membership",
        "recovery-membership": "recovery seal membership",
        "code-bytes": "code changed",
        "recovery-bytes": "recovery bytes differ",
    }[mutation]
    if mutation == "seal":
        document = seal(payload)
        document["plan_sha256"] = "0" * 64
    else:
        if mutation == "census":
            payload["configs"].pop()
        elif mutation == "runtime":
            payload["runtime"]["numpy"] = "wrong"
        elif mutation == "code-membership":
            payload["code_sha256"].pop(study.CODE[0])
        elif mutation == "recovery-membership":
            payload["recovery_files"].pop("model.sig.json")
        elif mutation == "code-bytes":
            (Path(payload["root"]) / study.CODE[0]).write_bytes(b"changed")
        elif mutation == "recovery-bytes":
            (Path(payload["qualified_directory"]) / "model/graph.onnx").write_bytes(b"changed")
        document = seal(payload)
    path.write_bytes(encoded(document))
    with pytest.raises(ValueError, match=expected):
        study.check_plan(path)


def test_optional_recovery_check_does_not_skip_executable_integrity(owned_plan):
    path, payload = owned_plan
    (Path(payload["qualified_directory"]) / "model/graph.onnx").unlink()
    assert study.check_plan(path, recovery=False) == seal(payload)
    (Path(payload["root"]) / study.CODE[0]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="code changed"):
        study.check_plan(path, recovery=False)


def test_symbolic_recovery_input_is_rejected(owned_plan):
    path, payload = owned_plan
    target = Path(payload["qualified_directory"]) / "model/graph.onnx"
    replacement = target.with_name("saved.onnx")
    target.rename(replacement)
    try:
        target.symlink_to(replacement)
    except (OSError, NotImplementedError):
        pytest.skip("host lacks symlink capability")
    with pytest.raises(ValueError, match="regular file"):
        study.check_plan(path)
    assert json.loads(path.read_bytes())["plan_sha256"] == seal(payload)["plan_sha256"]
