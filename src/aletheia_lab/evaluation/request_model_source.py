"""Local native Ray replication of upstream issue #56633, plus an owned ML sink.

The upstream string responses are preserved. The ML extension carries an explicit
audit token as ordinary input; model selection still uses Ray's native accessor.
This observer is trusted application instrumentation, not host attestation.
Source: https://github.com/ray-project/ray/issues/56633 (fix PR #59334).
"""

from __future__ import annotations

import importlib
import logging
import os
import platform
import socket
import tempfile
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from importlib.metadata import version
from io import BytesIO
from pathlib import Path
from time import perf_counter_ns, sleep
from typing import Any, TypeVar, cast

from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.project.identity import content_sha256

SOURCE_URL = "https://github.com/ray-project/ray/issues/56633"
TELEMETRY = {
    "RAY_USAGE_STATS_ENABLED": "0",
    "RAY_USAGE_STATS_PROMPT_ENABLED": "0",
    "RAY_USAGE_STATS_REPORT_URL": "http://127.0.0.1:1/disabled",
    "DO_NOT_TRACK": "1",
}
_Function = TypeVar("_Function", bound=Callable[..., Any])


def typed_decorator(value: Any) -> Callable[[_Function], _Function]:
    return cast(Callable[[_Function], _Function], value)


@contextmanager
def local_environment() -> Iterator[None]:
    """Disable telemetry before native imports and restore this process's values."""
    previous = {key: os.environ.get(key) for key in TELEMETRY}
    os.environ.update(TELEMETRY)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def planned_rows(mode: str) -> list[dict[str, Any]]:
    if mode not in {"original", "owned_ml"}:
        raise ValueError("mode must be original or owned_ml")
    rows: list[dict[str, Any]] = []
    sequences: dict[str, tuple[str, ...]] = {
        "non_batched": ("aaa", "bbb", "bbb", "aaa"),
        "first_aaa": ("aaa", "bbb", "bbb", "aaa"),
        "first_bbb": ("bbb", "aaa", "aaa", "bbb"),
    }
    if mode == "owned_ml":
        sequences.update({"same_concurrent": ("aaa", "aaa"), "mixed_concurrent": ("aaa", "bbb")})
    for scenario, models in sequences.items():
        for ordinal, model in enumerate(models):
            rows.append(
                {
                    "token": f"{mode}-{scenario}-{ordinal}",
                    "scenario": scenario,
                    "ordinal": ordinal,
                    "requested_model": model,
                    "arg": "1",
                    "kind": "non_batched" if scenario == "non_batched" else "batched",
                    "concurrent": scenario.endswith("concurrent"),
                }
            )
    if mode == "owned_ml":
        for model in ("aaa", "bbb"):
            rows.append(
                {
                    "token": f"owned_ml-equal_output-{model}",
                    "scenario": "equal_output",
                    "ordinal": int(model == "bbb"),
                    "requested_model": model,
                    "arg": "0",
                    "kind": "batched",
                    "concurrent": False,
                }
            )
        rows.append(
            {
                "token": "owned_ml-invalid-0",
                "scenario": "invalid",
                "ordinal": 0,
                "requested_model": "aaa",
                "arg": "invalid",
                "kind": "batched",
                "concurrent": False,
            }
        )
    return rows


def object_parameters(model: Any) -> dict[str, Any]:
    if isinstance(model, str):
        return {"value": model}
    return {"coef": model.coef_.tolist(), "intercept": float(model.intercept_)}


def object_observation(model: Any) -> dict[str, Any]:
    parameters = object_parameters(model)
    return {
        "parameters": parameters,
        "object_fingerprint": content_sha256(encode(parameters).encode()),
        "object_id": id(model),
        "pid": os.getpid(),
    }


def owned_artifacts(directory: Path) -> dict[str, dict[str, Any]]:
    numpy = importlib.import_module("numpy")
    joblib = importlib.import_module("joblib")
    estimator = importlib.import_module("sklearn.linear_model").LinearRegression
    directory.mkdir()
    artifacts = {}
    features = numpy.asarray([[-1.0], [0.0], [1.0]])
    for model_id, slope in (("aaa", 1.0), ("bbb", 2.0)):
        model = estimator().fit(features, features[:, 0] * slope)
        path = directory / f"{model_id}.joblib"
        buffer = BytesIO()
        joblib.dump(model, buffer)
        raw = buffer.getvalue()
        write_new_file(path, raw)
        artifacts[model_id] = {
            "path": str(path),
            "raw_hex": raw.hex(),
            "artifact_sha256": content_sha256(raw),
            "parameters": object_parameters(model),
            "object_fingerprint": object_observation(model)["object_fingerprint"],
        }
    return artifacts


async def apply_batch(
    owner: Any, serve: Any, mode: str, scenario: str, values: list[Any], kind: str
) -> list[Any]:
    native = serve.context._get_serve_request_context()
    selected = serve.get_multiplexed_model_id()
    batch_getter = getattr(serve.context, "_get_serve_batch_request_context", None)
    batch_contexts = batch_getter() if batch_getter is not None else []
    logging.getLogger("ray.serve").info("Request for model_id: %s\n%s", selected, native)
    members = [
        {
            "token": value["token"] if mode == "owned_ml" else None,
            "arg": value["arg"] if mode == "owned_ml" else value,
            "output": None,
        }
        for value in values
    ]
    observation: dict[str, Any] = {
        "batch": f"{scenario}-batch-{len(owner.batches)}",
        "scenario": scenario,
        "kind": kind,
        "native_context": {
            "model": selected,
            "generic_model": native.multiplexed_model_id,
            "request": native.request_id,
            "internal_request": native._internal_request_id,
            "route": native.route,
            "app": native.app_name,
        },
        "native_batch_contexts": [
            {"model": item.multiplexed_model_id, "request": item.request_id}
            for item in batch_contexts
        ],
        "actual_generation": None,
        "actual_object": None,
        "members": members,
        "completed": False,
        "error": None,
    }
    owner.batches.append(observation)
    try:
        model = await owner.get_model(selected)
        observation.update(
            actual_generation=owner.generations[id(model)],
            actual_object=object_observation(model),
        )
        if mode == "original":
            output = [f"Response from {model} {value}" for value in values]
        else:
            output = model.predict([[float(value["arg"])] for value in values]).tolist()
        for member, value in zip(members, output, strict=True):
            member["output"] = value
        observation["completed"] = True
        return list(output)
    except Exception as exc:
        observation["error"] = f"{type(exc).__name__}: {exc}"
        raise


def native_model(serve: Any, mode: str, scenario: str, artifacts: Any) -> Any:
    logger = logging.getLogger("ray.serve")

    @serve.deployment(num_replicas=1, ray_actor_options={"num_cpus": 0.5})
    class MultiplexedModel:
        def __init__(self) -> None:
            self.loads: dict[str, Any] = {}
            self.generations: dict[int, str] = {}
            self.batches: list[dict[str, Any]] = []

        @typed_decorator(serve.multiplexed(max_num_models_per_replica=2))
        async def get_model(self, model_id: str) -> Any:
            logger.info("Loading model: %s", model_id)
            if mode == "original":
                model: Any = f"model_obj_for_{model_id}"
                raw_hex = None
            else:
                entry = artifacts[model_id]
                raw = Path(entry["path"]).read_bytes()
                if content_sha256(raw) != entry["artifact_sha256"]:
                    raise ValueError("owned model bytes changed before load")
                model = importlib.import_module("joblib").load(BytesIO(raw))
                raw_hex = raw.hex()
            generation = f"{scenario}-load-{len(self.loads)}"
            self.generations[id(model)] = generation
            self.loads[generation] = {
                "model": model_id,
                "raw_hex": raw_hex,
                **object_observation(model),
            }
            return model

        async def apply(self, values: list[Any], kind: str) -> list[Any]:
            return await apply_batch(self, serve, mode, scenario, values, kind)

        @typed_decorator(
            serve.batch(max_batch_size=2, batch_wait_timeout_s=0.05, max_concurrent_batches=1)
        )
        async def batched(self, values: list[Any]) -> list[Any]:
            return await self.apply(values, "batched")

        async def non_batched(self, value: Any) -> Any:
            return (await self.apply([value], "non_batched"))[0]

        async def observations(self) -> dict[str, Any]:
            return {"loads": self.loads, "batches": self.batches}

    return cast(Any, MultiplexedModel).bind()


def native_application(serve: Any, mode: str, scenario: str, artifacts: Any) -> Any:
    app = importlib.import_module("fastapi").FastAPI()
    logger = logging.getLogger("ray.serve")

    @serve.deployment(num_replicas=1, ray_actor_options={"num_cpus": 0.5})
    @serve.ingress(app)
    class APIIngress:
        def __init__(self, model_handle: Any) -> None:
            self.model_handle = model_handle

        @typed_decorator(app.post("/predict"))
        async def predict(self, model_id: str, arg: str, kind: str, token: str = "") -> Any:
            logger.info("model_id %s", model_id)
            value: Any = {"token": token, "arg": arg} if mode == "owned_ml" else arg
            handle = self.model_handle.options(multiplexed_model_id=model_id)
            if kind == "batched":
                return await handle.batched.remote(value)
            return await handle.non_batched.remote(value)

        async def observations(self) -> dict[str, Any]:
            result: dict[str, Any] = await self.model_handle.observations.remote()
            return result

    return cast(Any, APIIngress).bind(native_model(serve, mode, scenario, artifacts))


def request_row(client: Any, url: str, planned: dict[str, Any], mode: str) -> dict[str, Any]:
    row = {
        **planned,
        "http_status": None,
        "output": None,
        "response_text": None,
        "response_request_id": None,
        "error": None,
    }
    parameters = {"model_id": row["requested_model"], "arg": row["arg"], "kind": row["kind"]}
    if mode == "owned_ml":
        parameters["token"] = row["token"]
    start = perf_counter_ns()
    try:
        response = client.post(
            url, params=parameters, headers={"Content-Type": "application/octet-stream"}
        )
        row.update(
            http_status=response.status_code,
            response_text=response.text,
            response_request_id=response.headers.get("x-request-id"),
        )
        try:
            row["output"] = response.json()
        except ValueError:
            row["error"] = "non_json_response"
        if response.status_code != 200:
            row["error"] = f"native_http_{response.status_code}"
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["elapsed_ns"] = perf_counter_ns() - start
    return row


def environment_identity() -> dict[str, Any]:
    identities = {}
    for name in ("ray.serve.batching", "ray.serve.api", "ray.serve.context", "ray.serve.multiplex"):
        module = importlib.import_module(name)
        if module.__file__ is None:
            raise ValueError("native source identity requires a module file")
        identities[name] = content_sha256(Path(module.__file__).read_bytes())
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {
            name: version(name)
            for name in (
                "ray",
                "fastapi",
                "httpx",
                "pydantic",
                "protobuf",
                "scikit-learn",
                "joblib",
                "numpy",
            )
        },
        "native_source_sha256": identities,
        "adapter_sha256": content_sha256(Path(__file__).read_bytes()),
        "telemetry": TELEMETRY,
    }


def _scenario(
    serve: Any, client: Any, output: dict[str, Any], mode: str, scenario: str, port: int
) -> None:
    plans = [row for row in output["planned"] if row["scenario"] == scenario]
    startup = {"scenario": scenario, "complete": False, "error": None}
    output["startups"].append(startup)
    name = f"audit_{mode}_{scenario}"
    handle = None
    try:
        handle = serve.run(
            native_application(serve, mode, scenario, output["artifacts"]),
            name=name,
            route_prefix=f"/{name}",
        )
        startup["complete"] = True
        url = f"http://127.0.0.1:{port}/{name}/predict"
        if plans[0]["concurrent"]:
            with ThreadPoolExecutor(max_workers=len(plans)) as executor:
                futures = [executor.submit(request_row, client, url, row, mode) for row in plans]
                output["rows"].extend(future.result() for future in futures)
        else:
            for planned in plans:
                output["rows"].append(request_row(client, url, planned, mode))
                sleep(0.1)  # Above upstream's 0.05s window; repeated warm singleton batches.
    except Exception as exc:
        startup["error"] = f"{type(exc).__name__}: {exc}"
        output["failures"].append({"stage": scenario, "error": startup["error"]})
    finally:
        if handle is not None:
            try:
                observed = handle.observations.remote().result(timeout_s=30)
                output["loads"].update(observed["loads"])
                output["batches"].extend(observed["batches"])
            except Exception as exc:
                output["failures"].append({"stage": "observations", "error": str(exc)})
        try:
            serve.delete(name)
        except Exception as exc:
            output["failures"].append({"stage": "delete_application", "error": str(exc)})


def complete_census(output: dict[str, Any]) -> None:
    recorded = {row["token"] for row in output["rows"]}
    for planned in output["planned"]:
        if planned["token"] not in recorded:
            output["rows"].append(
                {
                    **planned,
                    "http_status": None,
                    "output": None,
                    "response_text": None,
                    "response_request_id": None,
                    "error": "unexecuted_after_native_failure",
                    "elapsed_ns": None,
                }
            )
    order = {row["token"]: index for index, row in enumerate(output["planned"])}
    output["rows"].sort(key=lambda row: order[row["token"]])


def run_source(output_dir: Path, mode: str) -> dict[str, Any]:
    """Own one fresh native cluster; return the full census, preserving native logs."""
    planned = planned_rows(mode)
    if output_dir.exists() or output_dir.is_symlink():
        raise ValueError("fresh private native output directory required")
    output_dir.mkdir(parents=True)
    output: dict[str, Any] = {
        "schema": "request-model-native-source/v1",
        "source": SOURCE_URL,
        "mode": mode,
        "environment": None,
        "planned": planned,
        "rows": [],
        "loads": {},
        "batches": [],
        "artifacts": {},
        "startups": [],
        "failures": [],
        "shutdown": [],
        "reference_boundary": "explicit_payload_token"
        if mode == "owned_ml"
        else "sequential_http_ordinal",
    }
    ray: Any = None
    serve: Any = None
    owned_cluster = False
    with local_environment():
        try:
            ray = importlib.import_module("ray")
            serve = importlib.import_module("ray.serve")
            if ray.is_initialized():
                raise ValueError("refusing to reuse or shut down an existing Ray cluster")
            output["environment"] = environment_identity()
            if mode == "owned_ml":
                output["artifacts"] = owned_artifacts(output_dir / "artifacts")
                output["environment"]["packages"].update(
                    {name: version(name) for name in ("scikit-learn", "joblib", "numpy")}
                )
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            owned_cluster = True
            runtime = tempfile.mkdtemp(prefix="val01-ray-", dir="/private/tmp")
            output["runtime_directory"] = runtime
            ray.init(
                address="local",
                _node_ip_address="127.0.0.1",
                include_dashboard=False,
                num_cpus=2,
                object_store_memory=80 * 1024**2,
                _temp_dir=runtime,
                log_to_driver=True,
            )
            serve.start(proxy_location="HeadOnly", http_options={"host": "127.0.0.1", "port": port})
            httpx = importlib.import_module("httpx")
            with httpx.Client(timeout=30, trust_env=False) as client:
                for scenario in dict.fromkeys(row["scenario"] for row in planned):
                    _scenario(serve, client, output, mode, scenario, port)
        except Exception as exc:
            output["failures"].append({"stage": "source", "error": f"{type(exc).__name__}: {exc}"})
        finally:
            if owned_cluster:
                for name, cleanup in (("serve", serve.shutdown), ("ray", ray.shutdown)):
                    try:
                        cleanup()
                        output["shutdown"].append({"owner": name, "complete": True})
                    except Exception as exc:
                        output["shutdown"].append(
                            {"owner": name, "complete": False, "error": str(exc)}
                        )
            complete_census(output)
    return output
