"""Controlled development through MLflow's real registry and sklearn flavor loader.

The instrumented pickle boundary snapshots one owned, newly created model file.
This is not an untouched-native observer or a whole-package security assessment.
"""

from __future__ import annotations

import importlib
import io
import os
import pickle
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from unittest.mock import patch

from sklearn.tree import DecisionTreeClassifier  # type: ignore[import-untyped]

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.model_load_contract import LoadContract, Observation, Record, Scope
from aletheia_lab.evaluation.model_load_runtime import Transport
from aletheia_lab.project.identity import content_sha256


@dataclass(frozen=True)
class Schedule:
    name: str
    policy: Literal["pin_at_acceptance", "resolve_at_load"] = "pin_at_acceptance"
    retry: Literal["inherit", "reselect"] = "inherit"
    mode: Literal["version", "alias", "restored_path", "drop", "cache"] = "version"
    retried: bool = False


SCHEDULES = (
    Schedule("version_pin_legal"),
    Schedule("alias_late_legal", "resolve_at_load", mode="alias"),
    Schedule("pin_via_alias_fault", mode="alias"),
    Schedule("same_path_restored_fault", mode="restored_path"),
    Schedule("retry_inherit_fault", mode="alias", retried=True),
    Schedule("retry_reselect_legal", retry="reselect", mode="alias", retried=True),
    Schedule("dropped_target_load", mode="drop", retried=True),
    Schedule("cache_only", mode="cache"),
)


@contextmanager
def local_sdk(workspace: Path) -> Iterator[Any]:
    """No server or external registry; deny socket connections including telemetry."""

    def no_network(*args: object, **kwargs: object) -> None:
        raise RuntimeError("model-load development forbids network connections")

    environment = {
        "MLFLOW_DISABLE_TELEMETRY": "true",
        "MLFLOW_ALLOW_PICKLE_DESERIALIZATION": "true",
        "MLFLOW_ENABLE_ARTIFACTS_PROGRESS_BAR": "false",
    }
    with (
        patch.dict(os.environ, environment),
        patch.object(socket.socket, "connect", no_network),
        patch.object(socket.socket, "connect_ex", no_network),
        patch.object(socket, "create_connection", no_network),
    ):
        mlflow = importlib.import_module("mlflow")
        importlib.import_module("mlflow.sklearn")
        previous_tracking, previous_registry = mlflow.get_tracking_uri(), mlflow.get_registry_uri()
        uri = "sqlite:///" + str(workspace / "registry.sqlite")
        mlflow.set_tracking_uri(uri)
        mlflow.set_registry_uri(uri)
        try:
            yield mlflow
        finally:
            mlflow.set_tracking_uri(previous_tracking)
            mlflow.set_registry_uri(previous_registry)
            # Pinned SDK caches SQLite engines; release only this owned URI for
            # Windows cleanup, never another task's database or global cache.
            for name in ("tracking", "model_registry"):
                module = importlib.import_module(f"mlflow.store.{name}.sqlalchemy_store")
                engine = module.SqlAlchemyStore._engine_map.pop(uri, None)
                if engine is not None:
                    engine.dispose()


class NativeWorkflow:
    def __init__(self, workspace: Path, sdk: Any) -> None:
        self.workspace, self.sdk = workspace, sdk
        self.name = "model-load-development"
        uri = "sqlite:///" + str(workspace / "registry.sqlite")
        self.client = sdk.MlflowClient(tracking_uri=uri, registry_uri=uri)
        self.sources: dict[str, Path] = {}
        self.digests: dict[str, str] = {}
        self.versions: dict[str, str] = {}
        self.client.create_registered_model(self.name)
        for name, labels in (("A", [0, 0, 1, 1]), ("B", [1, 1, 0, 0])):
            model = DecisionTreeClassifier(max_depth=1, random_state=1729).fit(
                [[0], [1], [2], [3]], labels
            )
            directory = workspace / f"source-{name}"
            sdk.sklearn.save_model(
                model, str(directory), serialization_format="pickle", pip_requirements=[]
            )
            self.sources[name] = directory
            self.digests[name] = file_sha256(directory / "model.pkl")
            returned = self.client.create_model_version(
                self.name,
                directory.as_uri(),
                tags={"weights_sha256": self.digests[name]},
                await_creation_for=0,
            )
            self.versions[name] = str(returned.version)

    def alias(self, name: str) -> None:
        self.client.set_registered_model_alias(self.name, "champion", self.versions[name])

    def snapshot(self) -> dict[str, Any]:
        value = self.client.get_model_version_by_alias(self.name, "champion")
        return {
            "model_name": value.name,
            "version": str(value.version),
            "source": Path(value.source.removeprefix("file://")).name,
            "digest": value.tags["weights_sha256"],
        }

    def consume(
        self,
        case: Path,
        scope: Scope,
        selected: Record,
        spool: Transport,
        *,
        alias: bool = False,
        restored_path: bool = False,
    ) -> dict[str, Any]:
        destination = case / f"consumer-{scope.attempt}"
        destination.mkdir()
        uri = (
            f"models:/{self.name}@champion" if alias else f"models:/{self.name}/{selected.revision}"
        )
        captured: list[dict[str, Any]] = []
        native_unpickler = pickle.load

        def capture(stream: Any, *args: Any, **kwargs: Any) -> Any:
            path = _owned_model_path(stream, destination)
            before = path.read_bytes()
            if content_sha256(before) not in self.digests.values():
                raise ValueError("model bytes differ from the freshly generated artifacts")
            if restored_path:
                path.write_bytes((self.sources["B"] / "model.pkl").read_bytes())
            try:
                raw = stream.read(2_000_001)
            finally:
                if restored_path:
                    path.write_bytes(before)
            if len(raw) > 2_000_000 or content_sha256(raw) not in self.digests.values():
                raise ValueError("unexpected or oversized deserializer input")
            buffer = io.BytesIO(raw)
            captured.append(
                {
                    "raw_hex": raw.hex(),
                    "before_sha256": content_sha256(before),
                    "after_sha256": file_sha256(path),
                    "uri": uri,
                    "model_metadata_sha256": file_sha256(path.parent / "MLmodel"),
                    "model_metadata_text": (path.parent / "MLmodel").read_text(encoding="utf-8"),
                    "registered_model_meta": (path.parent / "registered_model_meta").read_text(
                        encoding="utf-8"
                    ),
                }
            )
            spool.submit(
                Record(
                    f"load:{scope.attempt}",
                    scope,
                    "load",
                    content_sha256(buffer.getvalue()),
                    selected.selection,
                )
            )
            # Only verified bytes of models generated above enter the native
            # unpickler; no foreign pickle or historical artifact is accepted.
            return native_unpickler(buffer, *args, **kwargs)

        with patch.object(pickle, "load", capture):
            model = self.sdk.sklearn.load_model(uri, dst_path=str(destination))
        if len(captured) != 1:
            raise RuntimeError("native flavor did not invoke the supported deserializer once")
        captured[0]["prediction"] = model.predict([[0], [3]]).tolist()
        return captured[0]

    def run(self, schedule: Schedule) -> dict[str, Any]:
        case = self.workspace / schedule.name
        case.mkdir()
        scope = Scope(schedule.name, int(schedule.retried))
        fault: Literal["drop_load", "complete"] = (
            "drop_load" if schedule.mode == "drop" else "complete"
        )
        spool = Transport(case / "observer.sqlite", fault, scope)
        try:
            return self._execute(case, schedule, scope, spool)
        finally:
            spool.close()

    def _execute(
        self,
        case: Path,
        schedule: Schedule,
        scope: Scope,
        spool: Transport,
    ) -> dict[str, Any]:
        self.alias("A")
        accepted = self.snapshot()
        root = selection(Scope(scope.request, 0), accepted, schedule.policy)
        if schedule.policy == "pin_at_acceptance" or schedule.retried:
            spool.submit(root)
        auxiliary: list[dict[str, Any]] = []
        if schedule.retried:
            auxiliary.append(self.consume(case, root.scope, root, spool))
            spool.submit(Record("parent-close", root.scope, "closure", load_count=1))
        if schedule.mode == "cache":
            auxiliary.append(self.consume(case, Scope(scope.request + ":warm", 0), root, spool))
        self.alias("B")
        actual = self.snapshot()
        inherited = schedule.retried and schedule.retry == "inherit"
        pinned = schedule.policy == "pin_at_acceptance" and not schedule.retried
        selected = selection(
            scope,
            accepted if inherited or pinned else actual,
            schedule.retry if schedule.retried else schedule.policy,
            root if inherited else None,
        )
        if selected != root:
            spool.submit(selected)
        buffers: list[dict[str, Any]] = []
        if schedule.mode == "cache":
            spool.submit(Record("cache", scope, "cache_hit", digest=self.digests["A"]))
        else:
            buffers.append(
                self.consume(
                    case,
                    scope,
                    selected,
                    spool,
                    alias=schedule.mode in {"alias", "drop"},
                    restored_path=schedule.mode == "restored_path",
                )
            )
        spool.submit(Record("close", scope, "closure", load_count=len(buffers)))
        observation = Observation(
            LoadContract(schedule.policy, tuple(self.digests.values()), schedule.retry),
            scope,
            spool.read(scope),
        )
        return {
            "schedule": asdict(schedule),
            "terminal": "completed",
            "scope": asdict(scope),
            "accepted_snapshot": accepted,
            "load_boundary_snapshot": actual,
            "target_buffers": buffers,
            "auxiliary_buffers": auxiliary,
            "observation": asdict(observation),
            "transport": spool.counts,
            "native_fact_frame": native_frame(accepted, actual, buffers),
        }


def _owned_model_path(stream: Any, destination: Path) -> Path:
    path = Path(str(stream.name))
    if (
        path.name != "model.pkl"
        or path.is_symlink()
        or not path.resolve().is_relative_to(destination)
    ):
        raise ValueError("deserializer opened an unowned model path")
    if any(parent.is_symlink() for parent in path.parents) or stream.tell() != 0:
        raise ValueError("unsupported model path or deserializer offset")
    return path


def selection(
    scope: Scope, snapshot: dict[str, Any], phase: str, parent: Record | None = None
) -> Record:
    return Record(
        f"selection:{scope.attempt}",
        scope,
        "selection",
        snapshot["digest"],
        f"{scope.request}:{scope.attempt}:{snapshot['version']}",
        int(snapshot["version"]),
        phase,
        parent_scope=parent.scope if parent else None,
        parent_selection=parent.selection if parent else None,
    )


def native_frame(
    accepted: dict[str, Any],
    current: dict[str, Any],
    buffers: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "accepted_version": accepted,
        "current_alias": current,
        "loads": [
            {
                "uri": item["uri"],
                "pre_sha256": item["before_sha256"],
                "post_sha256": item["after_sha256"],
                "MLmodel_sha256": item["model_metadata_sha256"],
                "registered_model_meta": item["registered_model_meta"],
            }
            for item in buffers
        ],
    }
