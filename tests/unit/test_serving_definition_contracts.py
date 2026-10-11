"""Owned definition/identity guards, not native framework qualification.

Fixture AST and SDK descriptors are inert. Source selection, byte checks and
namespace construction use the real adapter; reserved predictors never run.
"""

import base64
import hashlib
import inspect
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from aletheia_lab.evaluation import model_load_application_analysis as application
from aletheia_lab.evaluation import official_model_signing as signing
from aletheia_lab.evaluation import serving_protocol_native as native


@pytest.fixture(autouse=True)
def isolated_namespaces():
    prefixes = ("kserve", "inference_pb2", "gprc_utils", "test_protocol_fixture")
    before = {k: v for k, v in sys.modules.items() if k.startswith(prefixes)}
    yield
    for name in tuple(sys.modules):
        if name.startswith(prefixes) and name not in before:
            del sys.modules[name]
    sys.modules.update(before)


@pytest.fixture
def sources(tmp_path, monkeypatch):
    reserved, dependencies = tmp_path / "reserved", tmp_path / "dependencies"
    reserved.mkdir()
    dependencies.mkdir()
    mapping = """
class BatchDimMapping(BatchDimSuper):
    def __getitem__(self, key): return key
    def __iter__(self): return iter(())
    def __len__(self): return 0
"""
    local = """
class LocalRunnerRef(RunnerHandle):
    def run_method(self, method, *args):
        raise AssertionError('reserved method must not run')
"""
    torch = """
class PredictorProtocol(Enum):
    GRPC = 'grpc'
class TorchserveModel(Model):
    marker = 'owned'
    def __init__(self, name): super().__init__(name)
    def grpc_client(self): return None
    async def _grpc_predict(self, request: InferRequest):
        raise AssertionError('reserved predictor must not run')
    def postprocess(self, response): return response
    def register(self): raise AssertionError('transport must be omitted')
"""
    documents = {
        "bento-affected-0.py": mapping,
        "bento-fixed-0.py": mapping,
        "bento-affected-1.py": local,
        "bento-fixed-1.py": local,
        "bento-handle.py": "class RunnerHandle(ABC): pass\n",
        "bento-utils.py": "class Params: pass\n",
        "bento-container.py": """
class DataContainer: pass
class NdarrayContainer(DataContainer):
    def batches_to_batch(self, batches): return batches
    def batch_to_batches(self, batch): return batch
    def deserialize(self): raise AssertionError('deserialization must be omitted')
""",
        "torchserve-affected-0.py": torch,
        "torchserve-fixed-0.py": torch,
        "kserve-model.py": """
class PredictorProtocol(Enum): GRPC = 'grpc'
class Model:
    def __init__(self, name): self.name = name
    def predict(self): raise AssertionError('base predictor must be omitted')
""",
        "kserve-errors.py": "class InvalidInput(Exception): pass\nclass ModelMissingError(Exception): pass\n",
        "kserve-constants.py": "VERSION = 'owned'\n",
        "kserve-numpy_codec.py": "CODEC = 'owned'\n",
        "kserve-grpc_predict_v2_pb2.py": """
from types import SimpleNamespace
class ModelInferRequest: pass
class ModelInferResponse:
    DESCRIPTOR = SimpleNamespace(full_name='owned.ModelInferResponse')
""",
        "kserve-infer_type.py": "class InferRequest: pass\nclass InferResponse: pass\n",
        "torchserve-gprc_utils.py": "def to_ts_grpc(value): return value\ndef from_ts_grpc(value): return value\n",
    }
    pins = {}
    for name, text in documents.items():
        root = (
            reserved
            if name.startswith(
                ("bento-affected", "bento-fixed", "torchserve-affected", "torchserve-fixed")
            )
            else dependencies
        )
        raw = text.encode()
        (root / name).write_bytes(raw)
        pins[name] = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(native, "PINS", pins)
    prediction = SimpleNamespace(
        PredictionResponse=SimpleNamespace(
            DESCRIPTOR=SimpleNamespace(full_name="owned.PredictionResponse")
        )
    )
    monkeypatch.setattr(native, "load_prediction_schema", lambda roots: prediction)
    return native.NativeSources(reserved, dependencies)


def test_missing_pinned_source_fails_before_compilation(sources):
    (sources.dependencies / "bento-utils.py").unlink()
    with pytest.raises(ValueError, match="pinned regular source"):
        native.checked_source(sources, "bento-utils.py")


def test_rechecked_buffer_rejects_change_after_first_hash(sources, monkeypatch):
    path = sources.dependencies / "bento-utils.py"
    checked = native.checked_source

    def mutate(roots, name):
        result = checked(roots, name)
        path.write_bytes(b"raise AssertionError('must not execute')\n")
        return result

    monkeypatch.setattr(native, "checked_source", mutate)
    with pytest.raises(ValueError, match="bytes changed"):
        native._source_bytes(sources, "bento-utils.py")


def test_occupied_namespace_is_rejected_and_owned_children_bind(monkeypatch):
    monkeypatch.setitem(sys.modules, "test_protocol_fixture", ModuleType("foreign"))
    with pytest.raises(ValueError, match="already occupied"):
        native._module_shell("test_protocol_fixture")
    monkeypatch.delitem(sys.modules, "test_protocol_fixture")
    child = native._module_shell("test_protocol_fixture.child")
    assert sys.modules["test_protocol_fixture"].child is child
    assert child.__path__ == [] and native._module_shell(child.__name__) is child


def test_checked_module_has_exact_file_package_and_parent(sources):
    result = native._source_module(
        sources, "test_protocol_fixture.constants", "kserve-constants.py"
    )
    assert result.VERSION == "owned"
    assert result.__package__ == "test_protocol_fixture"
    assert result.__file__ == str(sources.dependencies / "kserve-constants.py")
    assert sys.modules["test_protocol_fixture"].constants is result


def test_ast_selection_keeps_fields_bases_and_defaults_not_unused_methods(sources):
    deps = sources.torch_dependencies()
    model = sources.torch_model("fixed", deps)
    assert model.marker == "owned" and model("name").name == "name"
    assert str(inspect.signature(model._grpc_predict)) == "(self, request: 'InferRequest')"
    assert not hasattr(model, "register") and not hasattr(model, "predict")
    with pytest.raises(ValueError, match="method census"):
        native._exact_class(sources, "bento-utils.py", "Params", {}, {"missing"})


@pytest.mark.parametrize("loader", ["bento_mapping", "bento_local_ref", "torch_model"])
def test_unknown_side_fails_before_source_read(sources, monkeypatch, loader):
    monkeypatch.setattr(
        native, "checked_source", lambda *args: pytest.fail("unknown side cannot read source")
    )
    call = getattr(sources, loader)
    with pytest.raises(ValueError, match="affected or fixed"):
        call("not-a-side", None) if loader == "torch_model" else call("not-a-side")


def test_definition_orchestration_reports_inert_fixture_identities_without_invocation(sources):
    params, arrays = sources.bento_params_and_ndarray()
    assert params.__name__ == "Params" and arrays.__bases__[0].__name__ == "DataContainer"
    assert not hasattr(arrays, "deserialize")
    report = sources.qualify_definitions()
    assert report["definition_loading_only"] and not report["reserved_methods_invoked"]
    assert report["classes"] == {
        "PredictionResponse": "owned.PredictionResponse",
        "ModelInferResponse": "owned.ModelInferResponse",
        "InferResponse": "kserve.protocol.infer_type.InferResponse",
        "Params": "Params",
        "NdarrayContainer": "NdarrayContainer",
    }
    assert set(report["definitions"]) == {"affected", "fixed"}
    assert report["definitions"]["fixed"]["local_signature"] == "(self, method, *args)"
    assert sources.pins == native.PINS and sources.pins is not native.PINS


def test_signing_package_pin_precedes_component_import(monkeypatch):
    calls, module = [], ModuleType("owned.signing")
    monkeypatch.setattr(
        signing.importlib.metadata, "version", lambda name: calls.append(name) or "1.1.1"
    )
    monkeypatch.setattr(
        signing.importlib, "import_module", lambda name: calls.append(name) or module
    )
    assert signing._package("signing") is module
    assert calls == ["model-signing", "model_signing.signing"]


@pytest.mark.parametrize("material", ["bundle", "key"])
def test_symbolic_verification_material_fails_before_sdk(tmp_path, monkeypatch, material):
    root = tmp_path / "model"
    root.mkdir()
    (root / "graph").write_bytes(b"owned")
    bundle, key = tmp_path / "bundle", tmp_path / "key"
    bundle.write_bytes(b"bundle")
    key.write_bytes(b"key")
    target = bundle if material == "bundle" else key
    replacement = tmp_path / "replacement"
    target.rename(replacement)
    try:
        target.symlink_to(replacement)
    except (OSError, NotImplementedError):
        pytest.skip("host lacks symlink capability")
    monkeypatch.setattr(signing, "_package", lambda *args: pytest.fail("symlink cannot import SDK"))
    with pytest.raises(ValueError, match="symbolic links"):
        signing.verify_local(root, bundle, key)


@pytest.mark.parametrize(
    "field,value", [("method", "archive"), ("hash_type", "sha512"), ("allow_symlinks", True)]
)
def test_signed_payload_policy_cannot_be_promoted_to_full_closure(tmp_path, field, value):
    serialization = {"method": "files", "hash_type": "sha256", "allow_symlinks": False}
    serialization[field] = value
    payload = {"predicate": {"serialization": serialization, "resources": []}}
    path = tmp_path / "bundle.json"
    path.write_text(
        json.dumps(
            {"dsseEnvelope": {"payload": base64.b64encode(json.dumps(payload).encode()).decode()}}
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="serialization differs"):
        signing._check_signed_closure(path, {"files": []})


@pytest.fixture
def app_identity(tmp_path, monkeypatch):
    modules, pins = {}, {}
    for key, (name, _) in application.UPSTREAM.items():
        path = tmp_path / (key + ".py")
        raw = ("# synthetic " + key + "\n").encode()
        path.write_bytes(raw)
        modules[name] = SimpleNamespace(__file__=str(path))
        pins[key] = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(
        application, "version", lambda name: application.VERSIONS.get(name, "1.9.0")
    )
    monkeypatch.setattr(application.importlib, "import_module", modules.__getitem__)
    monkeypatch.setattr(application, "UPSTREAM_SHA256", pins)
    return modules, pins


def test_application_identity_binds_all_six_owned_sources(app_identity):
    _, expected = app_identity
    result = application.environment()
    assert result["versions"] == application.VERSIONS
    assert result["upstream_source_sha256"] == expected and len(expected) == 6
    assert result["scikit_learn"] == "1.9.0"


def test_application_version_mismatch_precedes_import(app_identity, monkeypatch):
    monkeypatch.setattr(application, "version", lambda name: "wrong")
    monkeypatch.setattr(
        application.importlib, "import_module", lambda name: pytest.fail("mismatch cannot import")
    )
    with pytest.raises(ValueError, match="pinned application"):
        application.environment()


@pytest.mark.parametrize("mutation", ["missing-file", "changed-bytes"])
def test_application_source_identity_rejects_missing_or_changed_file(app_identity, mutation):
    modules, _ = app_identity
    selected = modules[application.UPSTREAM["registry"][0]]
    if mutation == "missing-file":
        selected.__file__ = None
    else:
        from pathlib import Path

        Path(selected.__file__).write_bytes(b"changed")
    with pytest.raises(ValueError, match="source unavailable|differs from pinned"):
        application.environment()
