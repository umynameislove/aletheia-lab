"""Run immutable capacity profiles against explicitly authorized real HTTP targets."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import os
import platform
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

from aletheia_lab.hosted.capacity import CapacityHarness
from aletheia_lab.hosted.capacity_models import Profile, load_actors
from aletheia_lab.hosted.capacity_profiles import calibration_profile, expanded_profile


def _allowed(target: str, explicit: str | None) -> bool:
    host = urlsplit(target).hostname
    if host == "localhost":
        return True
    try:
        if host and ipaddress.ip_address(host).is_loopback:
            return True
    except ValueError:
        pass
    return explicit == target


def _write(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=True, allow_nan=False, indent=2)
        stream.write("\n")


def _tool_identity() -> dict[str, object]:
    root = Path(__file__).resolve().parents[1]
    files = [
        *sorted((root / "src/aletheia_lab/hosted").glob("capacity*.py")),
        Path(__file__).resolve(),
    ]
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return {
        "python": platform.python_version(),
        "platform": platform.system(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "dependencies": {n: version(n) for n in ("httpx2", "pydantic")},
        "harness_source_sha256": digest.hexdigest(),
        "scope": "Generator process on this host; machine idle/NIC/clock skew and SUT resources require external witnesses.",
    }


def _primary_dataset(manifest: dict[str, object]) -> bool:
    if (
        manifest.get("primary_dataset_qualified") is not True
        or manifest.get("primary_gaps")
        or manifest.get("unsupported_dimensions")
    ):
        return False
    counts, strata, physical = (manifest.get(k) for k in ("logical_counts", "strata", "physical"))
    if (
        not isinstance(counts, dict)
        or not isinstance(strata, dict)
        or not isinstance(physical, dict)
    ):
        return False
    for name, minimum in (
        ("principals", 50000),
        ("workspaces", 2000),
        ("projects", 10000),
        ("snapshots", 200000),
        ("results", 2000000),
    ):
        n = counts.get(name)
        if type(n) is not int or n < minimum:
            return False
    for size, rows, claims in (("S", 1000, 100), ("M", 10000, 1000), ("L", 100000, 10000)):
        dimensions = strata.get(size)
        if not isinstance(dimensions, dict):
            return False
        for name, minimum in (
            ("source_rows_per_generation", rows),
            ("actual_claims_per_result", claims),
        ):
            n = dimensions.get(name)
            if type(n) is not int or n < minimum:
                return False
    nodes = strata["L"].get("actual_graph_nodes_per_result")
    return (
        type(nodes) is int
        and nodes >= 10000
        and physical.get("all_source_payload_hashes_verified") is True
        and physical.get("all_object_file_hashes_verified") is True
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    template = sub.add_parser(
        "template", help="Write one calibration profile before observing outcomes"
    )
    for name in ("target", "origin", "host", "release-sha256", "dataset"):
        template.add_argument("--" + name, required=True)
    template.add_argument("--tls-ca-file")
    template.add_argument("--mode", choices=("closed", "open"), default="closed")
    template.add_argument("--workload", choices=("calibration", "expanded"), default="calibration")
    template.add_argument("--run-label", default="calibration")
    template.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--profile", type=Path, required=True)
    run.add_argument("--actors", type=Path, required=True)
    run.add_argument("--dataset", type=Path, required=True)
    run.add_argument(
        "--allow-target",
        help="Exact authorized non-loopback origin; does not provision infrastructure",
    )
    run.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    harness = None
    created = False
    try:
        if args.command == "template":
            dataset = Path(args.dataset)
            profile = calibration_profile(
                target=args.target,
                origin=args.origin,
                host=args.host,
                release_sha256=args.release_sha256,
                dataset_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(),
                tls_ca_file=args.tls_ca_file,
                mode=args.mode,
                run_label=args.run_label,
            )
            if args.workload == "expanded":
                profile = expanded_profile(profile)
            _write(args.output, profile.model_dump(mode="json"))
            print(
                json.dumps(
                    {
                        "status": "profile_created",
                        "profile_sha256": profile.fingerprint(),
                        "scope": "calibration_only",
                    }
                )
            )
            return 0
        if (
            args.profile.stat().st_size > 2 * 1024 * 1024
            or args.dataset.stat().st_size > 8 * 1024 * 1024
        ):
            raise ValueError("Bounded profile and public dataset manifest required.")
        profile = Profile.model_validate_json(args.profile.read_bytes())
        if not _allowed(profile.target, args.allow_target):
            raise ValueError("An exact authorized target allowlist is required.")
        if hashlib.sha256(args.dataset.read_bytes()).hexdigest() != profile.dataset_sha256:
            raise ValueError("Dataset identity changed after profile freeze.")
        actors, actor_hash = load_actors(args.actors, profile)
        manifest = json.loads(args.dataset.read_bytes())
        if not isinstance(manifest, dict) or manifest.get("actors_file_sha256") != actor_hash:
            raise ValueError(
                "Private actor file does not match the pinned public dataset manifest."
            )
        if profile.scope == "primary" and not _primary_dataset(manifest):
            raise ValueError(
                "Primary execution requires the verified full logical/physical guide dataset, not debug dimensions."
            )
        args.output.mkdir(parents=True, exist_ok=False)
        created = True
        _write(
            args.output / "attempt.json",
            {
                "status": "started",
                "started_at": datetime.now(UTC).isoformat(),
                "profile_sha256": profile.fingerprint(),
                "actor_file_sha256": actor_hash,
                "dataset_sha256": profile.dataset_sha256,
                "generator": _tool_identity(),
            },
        )
        harness = CapacityHarness(profile, actors)
        with asyncio.Runner(
            loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
        ) as runner:
            result = runner.run(harness.run())
        result["actor_file_sha256"] = actor_hash
        result["generator"] = _tool_identity()
        _write(args.output / "result.json", result)
        print(
            json.dumps(
                {
                    "status": "finished",
                    "counts": result["counts"],
                    "primary_campaign_passed": False,
                    "profile_sha256": profile.fingerprint(),
                }
            )
        )
        counts = result["counts"]
        return (
            0
            if counts.get("completed", 0) > 0
            and not any(
                counts.get(k, 0)
                for k in (
                    "failed",
                    "timeout",
                    "interrupted",
                    "dropped",
                    "rejected",
                    "pending",
                    "outcome_unknown",
                    "conflict",
                    "unknown_mutations",
                )
            )
            else 1
        )
    except BaseException as error:
        if isinstance(error, (SystemExit, GeneratorExit)):
            raise
        # Never print validation inputs, HTTP/driver exceptions, cookies or a traceback.
        if created:
            if harness is not None:
                partial = harness.report(harness._queue.qsize())
                partial["attempt_status"] = (
                    "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
                )
                if not (args.output / "result.json").exists():
                    _write(args.output / "result.json", partial)
            _write(
                args.output / "failure.json",
                {
                    "status": "interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                    "code": "capacity_run_unavailable",
                    "private_diagnostics": "suppressed",
                },
            )
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": "capacity_run_unavailable",
                    "private_diagnostics": "suppressed",
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
