"""Prepare source-bound editions in a new external directory; never open sockets."""

import argparse
import base64
import hashlib
import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
SPECS = {
    "A": dict(weight=2, helper=5, runtime_offset=1, runtime_revision="r1", helper_revision="h1"),
    "B": dict(weight=3, helper=5, runtime_offset=1, runtime_revision="r1", helper_revision="h1"),
    "C": dict(weight=3, helper=9, runtime_offset=7, runtime_revision="r2", helper_revision="h2"),
    "D": dict(weight=4, helper=11, runtime_offset=8, runtime_revision="r3", helper_revision="h3"),
}
IDS = [
    "A-x0",
    "A-x3",
    "B-published-x0",
    "B-held-old-x3",
    "B-active-x0",
    "B-active-x3",
    "C-load-x0",
    "C-load-x3",
    "C-repair-x0",
    "C-repair-x3",
    "C-noop-x3",
    "C-reload-x3",
    "D-load-x0",
    "D-load-x3",
    "D-repair-x0",
    "D-repair-x3",
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metadata():
    distribution = importlib.metadata.distribution("mlserver")
    sources = []
    for entry in distribution.files or []:
        if str(entry).startswith("mlserver/") and str(entry).endswith(".py"):
            path = Path(distribution.locate_file(entry))
            sha = hashlib.sha256(path.read_bytes()).digest()
            sources.append(
                {
                    "path": str(path),
                    "sha256": sha.hex(),
                    "wheel_record_matches": bool(
                        entry.hash
                        and entry.hash.mode == "sha256"
                        and entry.hash.value == base64.urlsafe_b64encode(sha).decode().rstrip("=")
                    ),
                }
            )
    sklearn = importlib.metadata.distribution("scikit-learn")
    deps = []
    for name in ("sklearn/linear_model/_base.py", "sklearn/base.py"):
        path = Path(sklearn.locate_file(name))
        deps.append({"path": str(path), "sha256": digest(path)})
    return {
        "python": sys.executable,
        "python_version": sys.version,
        "versions": {
            name: importlib.metadata.version(name)
            for name in (
                "mlserver",
                "scikit-learn",
                "numpy",
                "scipy",
                "pydantic",
                "uvicorn",
                "grpcio",
            )
        },
        "mlserver_sources": sources,
        "dependency_sources": deps,
    }


def prepare(output, service_python):
    if sys.platform != "darwin" or not Path("/usr/sbin/lsof").is_file():
        raise RuntimeError("Native listener guard currently qualified only on macOS with lsof")
    output, service_python = output.resolve(), service_python.absolute()
    if output == SOURCE.parents[1] or SOURCE.parents[1] in output.parents:
        raise ValueError("Keep generated preparation and outcomes outside the checkout")
    environment = {"PATH": "/usr/bin:/bin", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [str(service_python), "-B", str(Path(__file__).resolve()), "metadata"],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    info = json.loads(result.stdout)
    if (
        info["versions"]["mlserver"] != "1.7.1"
        or info["versions"]["scikit-learn"] != "1.9.0"
        or not info["mlserver_sources"]
        or not all(r["wheel_record_matches"] for r in info["mlserver_sources"])
    ):
        raise ValueError("Requires unmodified MLServer 1.7.1 and scikit-learn 1.9.0")
    cli = service_python.parent / "mlserver"
    if not cli.is_file():
        raise ValueError("Installed MLServer CLI missing beside service interpreter")
    info["dependency_sources"].append({"path": str(cli), "sha256": digest(cli)})
    output.mkdir(parents=False, exist_ok=False)
    editions = {}
    for name, spec in SPECS.items():
        folder = output / "editions" / name
        folder.mkdir(parents=True)
        runtime = (
            (SOURCE / "runtime.py.in")
            .read_text()
            .replace("__RUNTIME_REVISION__", spec["runtime_revision"])
            .replace("__RUNTIME_OFFSET__", str(spec["runtime_offset"]))
        )
        helper = (
            "from pathlib import Path\n"
            + f"REVISION = {spec['helper_revision']!r}\nVALUE = {spec['helper']}\n"
            + "SOURCE_BYTES = Path(__file__).read_bytes()\ndef component():\n    return VALUE\n"
        )
        training = {
            "revision": name,
            "x": [-1, 0, 1],
            "y": [spec["weight"] * x + 1 for x in (-1, 0, 1)],
        }
        for filename, body in {
            "continuation_runtime.py": runtime,
            "continuation_helper.py": helper,
            "training.json": json.dumps(training, sort_keys=True) + "\n",
        }.items():
            path = folder / filename
            with path.open("x", encoding="utf-8") as stream:
                stream.write(body)
            if path.suffix == ".py":
                compile(body, filename, "exec", dont_inherit=True)
        editions[name] = {
            **spec,
            "intercept": 1,
            "sourcehashes": {
                k: digest(folder / v)
                for k, v in {
                    "runtime": "continuation_runtime.py",
                    "helper": "continuation_helper.py",
                    "training": "training.json",
                }.items()
            },
        }
    prepared = {
        "schema": "serving-continuation-prepared/v1",
        "editions": editions,
        "metadata": info,
        "service_python": str(service_python),
        "sourcepins": {
            p.name: digest(p)
            for p in sorted(SOURCE.iterdir())
            if p.name
            in ("prepare_native.py", "native_sequence.py", "native_guard.py", "runtime.py.in")
        },
        "method_sha256": digest(SOURCE.parents[1] / "docs/serving-recovery-study.md"),
        "planned_main_ids": IDS,
    }
    with (output / "prepared.json").open("x", encoding="utf-8") as stream:
        json.dump(prepared, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return {"status": "prepared-no-sockets", "prepared_sha256": digest(output / "prepared.json")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "metadata"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--service-python", type=Path)
    args = parser.parse_args()
    if args.command == "metadata":
        print(json.dumps(metadata()))
    else:
        if args.output is None or args.service_python is None:
            parser.error("prepare requires --output and --service-python")
        print(json.dumps(prepare(args.output, args.service_python)))
