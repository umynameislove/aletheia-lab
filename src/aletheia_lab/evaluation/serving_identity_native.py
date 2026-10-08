"""Source-informed BentoML store ownership and actual object use.

Only the full untouched, byte-pinned public loader modules are executed. Native
BentoML 1.4.0 stores, model get/load, SDK service construction and sklearn
prediction provide the execution path. This is a local causal-module transfer,
not a historic deployment, source-blind discovery or framework hot-reload API.
The worker produces observations; forecast decisions and verdicts live elsewhere.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import io
import json
import os
import pickle
import socket
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter_ns
from types import ModuleType
from typing import Any
from unittest.mock import patch

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.official_model_signing import artifact_closure
from aletheia_lab.filesystem import write_new_file

TAG = "state_model:immutable"
LOADER_PINS = {
    "affected-src-_bentoml_impl-loader.py": "0f3104cd2b7cde014af2a818980b8a40e77e6ff63b4943a90bb661b0a886aea8",
    "fixed-src-_bentoml_impl-loader.py": "205f66675476afb0e88c6d9d6282424d01eb31bf7d4632e6d440ab01448b0572",
}
RUNTIME_PINS = {"bentoml": "1.4.0", "scikit-learn": "1.7.2"}
SLOTS = tuple(f"t{index}" for index in range(8))
INPUT = [[3.0, 0.0]]
OWNED_STATES = {
    "A": {"coef": [1.0, 0.0], "intercept": 0.0, "features": 2},
    "B": {"coef": [1.0, 2.0], "intercept": 0.0, "features": 2},
}


def _json_file(path: Path, value: dict[str, Any]) -> None:
    write_new_file(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def _runtime() -> tuple[Any, Any, Any]:
    if os.environ.get("BENTOML_DO_NOT_TRACK") != "1" or not os.environ.get("BENTOML_HOME"):
        raise ValueError("explicit private Bento home and telemetry opt-out required")
    for name, expected in RUNTIME_PINS.items():
        if importlib.metadata.version(name) != expected:
            raise ValueError(f"identity adapter requires {name} {expected}")
    bento = importlib.import_module("bentoml")
    containers = importlib.import_module("bentoml._internal.configuration.containers")
    models = importlib.import_module("bentoml._internal.models")
    return bento, containers.BentoMLContainer, models.ModelStore


def checked_loader(source_path: Path) -> ModuleType:
    """Import an exact full public module, without calling its loader function."""
    expected = LOADER_PINS.get(source_path.name)
    if expected is None or source_path.is_symlink() or source_path.parent.is_symlink():
        raise ValueError("pinned regular public loader source required")
    if file_sha256(source_path) != expected:
        raise ValueError("public loader bytes differ from the accepted source pin")
    _runtime()
    name = f"_aletheia_identity_{source_path.name.split('-')[0]}_loader"
    spec = importlib.util.spec_from_file_location(name, source_path)
    if spec is None or spec.loader is None:
        raise ValueError("public source module loader unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if file_sha256(source_path) != expected:
        raise ValueError("public loader bytes changed during module import")
    return module


def qualify_native(source_root: Path) -> dict[str, Any]:
    """Import-only qualification; never call import_service or predict."""
    modules = {name: checked_loader(source_root / name) for name in LOADER_PINS}
    bento, _, _ = _runtime()
    importlib.import_module("bentoml.picklable_model")
    importlib.import_module("sklearn.linear_model")
    native_loader = importlib.import_module("_bentoml_impl.loader")
    assert native_loader.__file__ is not None
    installed = sorted(
        (distribution.metadata["Name"], distribution.version)
        for distribution in importlib.metadata.distributions()
    )
    return {
        "schema": "serving-identity-native-qualification/v1",
        "status": "import_only_qualified",
        "python": sys.version,
        "runtime": dict(installed),
        "bento_package_path": str(Path(bento.__file__).resolve()),
        "installed_loader_sha256": file_sha256(Path(native_loader.__file__)),
        "source_sha256": dict(LOADER_PINS),
        "full_modules_imported": sorted(modules),
        "selected_import_service_calls": 0,
        "selected_predictions": 0,
        "compatibility_note": "BentoML1.4.0 with independently resolved pinned environment; fs2.4.16 requires setuptools80.9.0 pkg_resources",
    }


def _service_source() -> str:
    return f"""import bentoml
from pathlib import Path
from bentoml.models import BentoModel

@bentoml.service
class IdentityService:
    artifact_ref = BentoModel({TAG!r})

    def __init__(self):
        self.reload_from_tag()

    def reload_from_tag(self):
        self.install_model(bentoml.models.get({TAG!r}))

    def install_model(self, artifact):
        path = Path(artifact.path_of("saved_model.pkl"))
        retained = path.read_bytes()
        backend = bentoml.picklable_model.load_model(artifact)
        if path.read_bytes() != retained:
            raise ValueError("owned model bytes changed during native load")
        self.model = backend
        self.loaded_artifact = artifact
        self.loaded_pickle_bytes = retained
        self.load_object_id = id(backend)
        self.captured_load_state = self.snapshot(backend)

    @staticmethod
    def snapshot(backend):
        return {{
            "coef": [float(value) for value in backend.__dict__["coef_"]],
            "intercept": float(backend.__dict__["intercept_"]),
            "features": int(backend.__dict__["n_features_in_"]),
        }}

    def predict(self, values):
        backend = self.model
        self.actual_use_object_id = id(backend)
        self.captured_use_state = self.snapshot(backend)
        return backend.predict(values).tolist()
"""


def _owned_model(coefficients: list[float]) -> Any:
    numpy = importlib.import_module("numpy")
    linear = importlib.import_module("sklearn.linear_model")
    model = linear.LinearRegression()
    model.coef_ = numpy.asarray(coefficients, dtype=numpy.float64)
    model.intercept_ = 0.0
    model.n_features_in_ = 2
    return model


def prepare_fixtures(root: Path) -> dict[str, Any]:
    """Save owned state and native package metadata, without selected imports/use."""
    bento, container, store_type = _runtime()
    bento_info = importlib.import_module("bentoml._internal.bento.bento")
    importlib.import_module("bentoml.picklable_model")
    if root.exists() or root.is_symlink():
        raise FileExistsError("fresh owned fixture root required")
    root.mkdir(parents=True)
    original = container.model_store.get()
    models: dict[str, Any] = {}
    try:
        for label, coefficients in (("A", [1.0, 0.0]), ("B", [1.0, 2.0])):
            package = root / f"package-{label.lower()}"
            source_dir = package / "src"
            source_dir.mkdir(parents=True)
            (package / "models").mkdir()
            store = store_type(package / "models")
            container.model_store.set(store)
            artifact = bento.picklable_model.save_model(
                TAG, _owned_model(coefficients), signatures={"predict": {"batchable": False}}
            )
            module = f"identity_service_{label.lower()}"
            write_new_file(source_dir / f"{module}.py", _service_source().encode())
            model_info = bento_info.BentoModelInfo.from_bento_model(artifact)
            info = bento_info.BentoInfoV2(
                tag=bento.Tag(f"identity_package_{label.lower()}", "immutable"),
                service=f"{module}:IdentityService",
                entry_service="IdentityService",
                services=[
                    bento_info.BentoServiceInfo(
                        name="IdentityService", service="", models=[model_info]
                    )
                ],
            )
            stream = io.StringIO()
            info.dump(stream)
            write_new_file(package / "bento.yaml", stream.getvalue().encode())
            models[label] = {
                "package": str(package.resolve()),
                "service_identifier": f"{module}:IdentityService",
                "model_store": str((package / "models").resolve()),
                "model_path": str(Path(artifact.path).resolve()),
                "pickle_path": str(Path(artifact.path_of("saved_model.pkl")).resolve()),
                "pickle_sha256": file_sha256(Path(artifact.path_of("saved_model.pkl"))),
                "closure": artifact_closure(Path(artifact.path)),
                "owned_state": {"coef": coefficients, "intercept": 0.0, "features": 2},
            }
    finally:
        container.model_store.set(original)
    manifest = {
        "schema": "serving-identity-owned-fixtures/v1",
        "tag": TAG,
        "tag_scope": "store-scoped name/version; not global cryptographic identity",
        "models": models,
        "input": INPUT,
        "package_closure": artifact_closure(root),
        "selected_imports_executed": 0,
        "selected_predictions": 0,
    }
    _json_file(root / "fixtures.json", manifest)
    return manifest


def _direct_service(package: Path) -> Any:
    path = next((package / "src").glob("identity_service_*.py"))
    name = "_aletheia_identity_direct_control"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("owned direct control source unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.IdentityService


def _caller_state(model: Any) -> dict[str, Any]:
    return {
        "coef": model.coef_.tolist(),
        "intercept": float(model.intercept_),
        "features": int(model.n_features_in_),
    }


def _load_fixtures(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    before = artifact_closure(root)
    manifest = json.loads((root / "fixtures.json").read_text())
    observed = [row for row in before["files"] if row["path"] != "fixtures.json"]
    if (
        observed != manifest["package_closure"]["files"]
        or manifest["tag"] != TAG
        or set(manifest["models"]) != {"A", "B"}
        or manifest["input"] != INPUT
    ):
        raise ValueError("owned package census changed after fixture preparation")
    root_path = root.resolve()
    for label, info in manifest["models"].items():
        if info["owned_state"] != OWNED_STATES[label]:
            raise ValueError("fixture state differs from the frozen owned state")
        for key in ("package", "model_store", "model_path", "pickle_path"):
            if root_path not in Path(info[key]).resolve().parents:
                raise ValueError("owned fixture paths must remain inside the fixture root")
        if artifact_closure(Path(info["model_path"])) != info["closure"]:
            raise ValueError("owned model bytes changed before worker")
        if file_sha256(Path(info["pickle_path"])) != info["pickle_sha256"]:
            raise ValueError("owned pickle hash changed before worker")
    return manifest, before


def _request(instance: Any, slot: str, generation: int, output: Path) -> dict[str, Any]:
    path = Path(instance.loaded_artifact.path_of("saved_model.pkl"))
    raw = path.read_bytes()
    if raw != instance.loaded_pickle_bytes:
        raise ValueError("retained native load bytes differ from the owned source")
    state_before = _caller_state(instance.model)
    object_id = id(instance.model)
    started = perf_counter_ns()
    result = instance.predict(INPUT)
    completed = perf_counter_ns()
    if path.read_bytes() != raw or _caller_state(instance.model) != state_before:
        raise ValueError("owned loaded state or source bytes changed during use")
    retained = output / f"{slot}-caller-owned-model.pkl"
    write_new_file(retained, raw)
    # This independent reader uses the owned retained pickle, not capture fields.
    caller_serialized = pickle.loads(raw)  # nosec B301: freshly owned, byte-pinned fixture
    return {
        "slot": slot,
        "token": f"identity-{slot}",
        "load_generation": generation,
        "selected_model_path": str(Path(instance.loaded_artifact.path).resolve()),
        "selected_pickle_path": str(path.resolve()),
        "selected_model_bytes": len(raw),
        "selected_model_sha256": file_sha256(retained),
        "caller_retained_pickle": str(retained.resolve()),
        "caller_backend_state": state_before,
        "caller_serialized_state": _caller_state(caller_serialized),
        "captured_load_state": instance.captured_load_state,
        "captured_use_state": instance.captured_use_state,
        "load_object_id": instance.load_object_id,
        "caller_object_id": object_id,
        "actual_use_object_id": instance.actual_use_object_id,
        "input": INPUT,
        "output": result,
        "native_entry": "Bento SDK Service.__call__ -> owned service -> sklearn LinearRegression.predict",
        "native_started_ns": started,
        "native_completed_ns": completed,
        "closed": True,
        "exception": None,
    }


@contextmanager
def _offline() -> Iterator[None]:
    def blocked(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("identity worker forbids network access")

    with (
        patch.object(socket.socket, "connect", blocked),
        patch.object(socket.socket, "connect_ex", blocked),
        patch.object(socket, "getaddrinfo", blocked),
        patch.object(sys, "dont_write_bytecode", True),
    ):
        yield


def _operation(
    name: str, slot: str | None, action: Callable[[], Any], rows: list[dict[str, Any]]
) -> Any:
    row = {"operation": name, "slot": slot, "started_ns": perf_counter_ns()}
    try:
        result = action()
    except Exception as error:
        row["exception"] = type(error).__name__
        raise
    else:
        row["exception"] = None
        return result
    finally:
        row["completed_ns"] = perf_counter_ns()
        rows.append(row)


def _rollback_control(loader: Any, package_a: Path, rows: list[dict[str, Any]]) -> None:
    bento, container, _ = _runtime()
    previous = container.model_store.get()
    try:
        _operation(
            "failed_import_rollback",
            None,
            lambda: loader.import_service(
                "identity_service_a:MissingService", bento_path=package_a / "src"
            ),
            rows,
        )
    except bento.exceptions.ImportServiceError:
        rows[-1]["store_object_restored"] = container.model_store.get() is previous
    else:
        raise ValueError("declared failed-import control unexpectedly succeeded")


def _trajectory(
    loader: Any,
    manifest: dict[str, Any],
    output: Path,
    requests: list[dict[str, Any]],
    operations: list[dict[str, Any]],
    progress: dict[str, Any],
) -> None:
    _, container, store_type = _runtime()
    original = container.model_store.get()
    store_a = store_type(manifest["models"]["A"]["model_store"])
    store_b = store_type(manifest["models"]["B"]["model_store"])
    package_a = Path(manifest["models"]["A"]["package"])
    package_b = Path(manifest["models"]["B"]["package"])
    held: list[Any] = []  # prevent object-id reuse while associations are evaluated
    services: dict[str, Any] = {}
    generation = 0

    def observe(slot: str, create: Callable[[], Any], new_generation: bool = True) -> Any:
        nonlocal generation
        progress["active_slot"] = slot
        progress["entered_slots"].append(slot)
        instance = create()
        if new_generation:
            generation += 1
        held.append(instance.model)
        requests.append(_request(instance, slot, generation, output))
        progress["active_slot"] = None
        return instance

    def import_package(label: str, slot: str) -> Any:
        info = manifest["models"][label]
        services[label] = _operation(
            f"import_package_{label}",
            slot,
            lambda: loader.import_service(
                info["service_identifier"], bento_path=Path(info["package"]) / "src"
            ),
            operations,
        )
        return services[label]()

    def set_context(instance: Any) -> Any:
        container.model_store.set(store_b)
        return instance

    def reload_tag(instance: Any) -> Any:
        _operation("reload_from_tag", "t3", instance.reload_from_tag, operations)
        return instance

    def repair_explicit(instance: Any) -> Any:
        _operation(
            "explicit_Model_object_repair",
            "t4",
            lambda: instance.install_model(store_a.get(TAG)),
            operations,
        )
        return instance

    try:
        container.model_store.set(store_b)
        observe("t0", lambda: _direct_service(package_b)())
        instance = observe("t1", lambda: import_package("A", "t1"))
        observe("t2", lambda: set_context(instance), False)
        observe("t3", lambda: reload_tag(instance))
        observe("t4", lambda: repair_explicit(instance))
        observe("t5", lambda: services["A"]())
        observe("t6", lambda: import_package("A", "t6"))
        observe("t7", lambda: import_package("B", "t7"))
        _rollback_control(loader, package_a, operations)
    finally:
        container.model_store.set(original)


def native_worker(source_path: Path, fixture_root: Path, output_dir: Path) -> dict[str, Any]:
    """Run the fixed eight observations only after the lead's external code/plan seal.

    Fresh process/output and byte-sealed, locally generated owned fixtures are
    required. The fixture JSON alone is not authority for arbitrary pickle input.
    Explicit Model-object repair replaces this service's held object; it does
    not repair global tag routing or imply a framework hot-reload facility.
    """
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("fresh worker output required")
    if fixture_root.resolve() in output_dir.resolve().parents:
        raise ValueError("worker output must be outside immutable fixtures")
    output_dir.mkdir(parents=True)
    manifest, before = _load_fixtures(fixture_root)
    result: dict[str, Any] = {
        "schema": "serving-identity-native-worker/v1",
        "version": source_path.name.split("-")[0],
        "source_sha256": file_sha256(source_path),
        "requests": [],
        "operations": [],
        "fixture_closure": before,
        "planned_slots": list(SLOTS),
        "entered_slots": [],
        "active_slot": None,
        "network_policy": "blocked; all used models local and present",
    }
    try:
        with _offline():
            loader = checked_loader(source_path)
            _trajectory(
                loader, manifest, output_dir, result["requests"], result["operations"], result
            )
        if tuple(row["slot"] for row in result["requests"]) != SLOTS:
            raise ValueError("worker request census differs from the fixed eight slots")
        if artifact_closure(fixture_root) != before:
            raise ValueError("worker changed immutable owned fixture bytes")
    except Exception as error:
        result["terminal"] = "failed"
        result["terminal_error"] = {"type": type(error).__name__, "message": str(error)}
        result["unattempted_slots"] = [
            slot for slot in SLOTS if slot not in result["entered_slots"]
        ]
        _json_file(output_dir / "native-results.json", result)
        raise
    result.update(terminal="complete", terminal_error=None, unattempted_slots=[])
    _json_file(output_dir / "native-results.json", result)
    return result
