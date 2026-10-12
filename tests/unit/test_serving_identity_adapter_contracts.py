"""Owned identity adapter guards; no reserved trajectory or native SDK run."""

import hashlib
import json
import pickle
import socket
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from aletheia_lab.evaluation import serving_identity_native as native


@pytest.fixture(autouse=True)
def isolated_owned_modules():
    before = {k: v for k, v in sys.modules.items() if k.startswith("_aletheia_identity_")}
    yield
    for name in tuple(sys.modules):
        if name.startswith("_aletheia_identity_") and name not in before:
            del sys.modules[name]
    sys.modules.update(before)


@pytest.mark.parametrize("drift", [None, "bentoml", "scikit-learn"])
def test_runtime_requires_private_home_opt_out_and_exact_versions(tmp_path, monkeypatch, drift):
    monkeypatch.setenv("BENTOML_HOME", str(tmp_path))
    monkeypatch.setenv("BENTOML_DO_NOT_TRACK", "1")
    modules = {
        "bentoml": ModuleType("bentoml"),
        "bentoml._internal.configuration.containers": SimpleNamespace(BentoMLContainer=object()),
        "bentoml._internal.models": SimpleNamespace(ModelStore=object()),
    }
    imported = []
    monkeypatch.setattr(
        native.importlib.metadata,
        "version",
        lambda name: "wrong" if name == drift else native.RUNTIME_PINS[name],
    )
    monkeypatch.setattr(
        native.importlib, "import_module", lambda name: imported.append(name) or modules[name]
    )
    if drift:
        with pytest.raises(ValueError, match="identity adapter requires"):
            native._runtime()
        assert imported == []
    else:
        assert native._runtime() == (
            modules["bentoml"],
            modules["bentoml._internal.configuration.containers"].BentoMLContainer,
            modules["bentoml._internal.models"].ModelStore,
        )
        assert imported == list(modules)
    monkeypatch.delenv("BENTOML_HOME")
    with pytest.raises(ValueError, match="private Bento home"):
        native._runtime()


def test_public_loader_imports_only_owned_pinned_bytes_and_checks_post_import(
    tmp_path, monkeypatch
):
    path = tmp_path / "affected-src-_bentoml_impl-loader.py"
    raw = b"MARKER = 'owned inert definitions'\n"
    path.write_bytes(raw)
    expected = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(native, "LOADER_PINS", {path.name: expected})
    monkeypatch.setattr(native, "_runtime", lambda: (None, None, None))
    loaded = native.checked_loader(path)
    assert loaded.MARKER == "owned inert definitions"
    assert loaded.__file__ == str(path)
    monkeypatch.setattr(native.importlib.util, "spec_from_file_location", lambda *args: None)
    with pytest.raises(ValueError, match="loader unavailable"):
        native.checked_loader(path)


def test_owned_request_persists_actual_pickle_and_does_not_infer_state_from_output(tmp_path):
    model = native._owned_model([1.0, 2.0])
    raw = pickle.dumps(model)
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    path = artifact / "saved_model.pkl"
    path.write_bytes(raw)
    output = tmp_path / "output"
    output.mkdir()
    state = {"coef": [1.0, 2.0], "intercept": 0.0, "features": 2}
    owner = SimpleNamespace(
        model=model,
        loaded_artifact=SimpleNamespace(path=artifact, path_of=lambda name: str(artifact / name)),
        loaded_pickle_bytes=raw,
        captured_load_state=state,
        captured_use_state=state,
        load_object_id=id(model),
        actual_use_object_id=id(model),
        predict=lambda values: model.predict(values).tolist(),
    )
    row = native._request(owner, "owned", 7, output)
    assert row["output"] == [3.0]
    assert row["caller_backend_state"] == row["caller_serialized_state"] == state
    assert row["token"] == "identity-owned" and row["load_generation"] == 7
    assert row["caller_object_id"] == row["actual_use_object_id"] == id(model)
    retained = Path(row["caller_retained_pickle"])
    assert retained.read_bytes() == raw
    assert row["selected_model_sha256"] == hashlib.sha256(raw).hexdigest()
    path.write_bytes(b"not the retained bytes")
    with pytest.raises(ValueError, match="retained native load bytes differ"):
        native._request(owner, "changed", 8, output)
    assert not (output / "changed-caller-owned-model.pkl").exists()


@pytest.mark.parametrize("mutation", ["model", "file"])
def test_state_or_artifact_mutation_during_use_cannot_get_closed_receipt(tmp_path, mutation):
    model = native._owned_model([1.0, 2.0])
    raw = pickle.dumps(model)
    path = tmp_path / "saved_model.pkl"
    path.write_bytes(raw)

    def predict(values):
        result = model.predict(values).tolist()
        if mutation == "model":
            model.coef_[1] = 19.0
        else:
            path.write_bytes(b"changed during use")
        return result

    owner = SimpleNamespace(
        model=model,
        loaded_artifact=SimpleNamespace(path_of=lambda name: str(path)),
        loaded_pickle_bytes=raw,
        predict=predict,
    )
    with pytest.raises(ValueError, match="state or source bytes changed"):
        native._request(owner, "changed", 1, tmp_path)
    assert list(tmp_path.iterdir()) == [path]


def test_network_guard_restores_descriptors_and_operation_keeps_failed_prefix():
    before = (
        socket.socket.connect,
        socket.socket.connect_ex,
        socket.getaddrinfo,
        sys.dont_write_bytecode,
    )
    with native._offline():
        assert sys.dont_write_bytecode
        for entry in (socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo):
            with pytest.raises(RuntimeError, match="forbids network"):
                entry(None, None)
    assert (
        socket.socket.connect,
        socket.socket.connect_ex,
        socket.getaddrinfo,
        sys.dont_write_bytecode,
    ) == before
    rows = []
    assert native._operation("success", "owned", lambda: 19, rows) == 19
    with pytest.raises(OSError):
        native._operation("failure", "offered", Mock(side_effect=OSError("owned failure")), rows)
    assert [(row["operation"], row["slot"], row["exception"]) for row in rows] == [
        ("success", "owned", None),
        ("failure", "offered", "OSError"),
    ]
    assert all(row["completed_ns"] >= row["started_ns"] for row in rows)


@pytest.fixture
def fixture_sdk(tmp_path, monkeypatch):
    class Store:
        def __init__(self, directory):
            self.directory = Path(directory)

    original = object()
    setting = SimpleNamespace(value=original)
    setting.get = lambda: setting.value
    setting.set = lambda value: setattr(setting, "value", value)

    def save(tag, model, signatures):
        assert tag == native.TAG and signatures == {"predict": {"batchable": False}}
        path = setting.value.directory / "owned-model"
        path.mkdir(parents=True)
        (path / "saved_model.pkl").write_bytes(pickle.dumps(model))
        return SimpleNamespace(path=path, path_of=lambda name: path / name)

    class Info:
        def __init__(self, **fields):
            self.fields = fields

        def dump(self, stream):
            stream.write(
                json.dumps(
                    {
                        "service": self.fields["service"],
                        "entry_service": self.fields["entry_service"],
                    }
                )
            )

    info = SimpleNamespace(
        BentoModelInfo=SimpleNamespace(from_bento_model=lambda artifact: artifact),
        BentoInfoV2=Info,
        BentoServiceInfo=lambda **fields: fields,
    )
    bento = SimpleNamespace(
        picklable_model=SimpleNamespace(save_model=save), Tag=lambda *args: args
    )
    monkeypatch.setattr(
        native, "_runtime", lambda: (bento, SimpleNamespace(model_store=setting), Store)
    )
    original_import = native.importlib.import_module
    monkeypatch.setattr(
        native.importlib,
        "import_module",
        lambda name: (
            info
            if name == "bentoml._internal.bento.bento"
            else bento.picklable_model
            if name == "bentoml.picklable_model"
            else original_import(name)
        ),
    )
    return setting, original


def test_owned_fixture_creation_and_replay_bind_all_files_and_restore_store(tmp_path, fixture_sdk):
    setting, original = fixture_sdk
    root = tmp_path / "fixtures"
    manifest = native.prepare_fixtures(root)
    assert setting.get() is original
    observed, closure = native._load_fixtures(root)
    assert observed == manifest and closure["total_bytes"] > 0
    assert manifest["selected_imports_executed"] == manifest["selected_predictions"] == 0
    for label, record in manifest["models"].items():
        assert record["owned_state"] == native.OWNED_STATES[label]
        assert (
            native._caller_state(pickle.loads(Path(record["pickle_path"]).read_bytes()))
            == native.OWNED_STATES[label]
        )
    with pytest.raises(FileExistsError, match="fresh owned"):
        native.prepare_fixtures(root)
    assert setting.get() is original
    (root / "package-a/models/owned-model/saved_model.pkl").write_bytes(b"changed")
    with pytest.raises(ValueError, match="package census"):
        native._load_fixtures(root)


def test_direct_control_imports_only_fresh_owned_source(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "identity_service_owned.py").write_bytes(
        b"class IdentityService:\n    marker = 'owned'\n"
    )
    service = native._direct_service(tmp_path)
    assert service.marker == "owned"
