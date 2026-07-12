#!/usr/bin/env python3
"""Download and deterministically prepare the benchmark dataset (task P1-C-01).

The source URL and SHA-256 are pinned in ``aletheia_lab.data.sources``. A
download is accepted only when its checksum matches the pin, so the raw dataset
reproduces byte-for-byte; prep then turns it into a stable, modelling-ready CSV.

The dataset id and paths are read from ``configs/project.yaml`` (``dataset.id``,
``paths.raw_data``, ``paths.processed_data``).

Usage:
    python scripts/download_dataset.py all         # download + verify, then prep
    python scripts/download_dataset.py download     # fetch raw and verify checksum
    python scripts/download_dataset.py download --offline   # verify an existing file
    python scripts/download_dataset.py prep         # raw -> processed (deterministic)
    python scripts/download_dataset.py verify       # re-check the raw checksum only
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aletheia_lab.config import load_yaml
from aletheia_lab.data.download import download_dataset, verify_file, write_provenance
from aletheia_lab.data.prep import prepare_dataset
from aletheia_lab.data.sources import DatasetSource, get_source


def _resolve(config_path: Path) -> tuple[DatasetSource, Path, Path, Path]:
    config = load_yaml(config_path)
    dataset_id = config["dataset"]["id"]
    paths = config["paths"]
    source = get_source(dataset_id)
    raw_path = Path(paths["raw_data"]) / source.filename
    processed_path = Path(paths["processed_data"]) / f"{dataset_id}.csv"
    provenance_path = Path(paths["raw_data"]) / f"{dataset_id}.provenance.json"
    return source, raw_path, processed_path, provenance_path


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def cmd_download(source: DatasetSource, raw_path: Path, provenance: Path, offline: bool, force: bool) -> None:
    download_dataset(source, raw_path, offline=offline, force=force)
    write_provenance(source, raw_path, provenance)
    _emit({"step": "download", "dataset_id": source.dataset_id, "raw": str(raw_path), "sha256": source.sha256, "verified": True})


def cmd_prep(source: DatasetSource, raw_path: Path, processed_path: Path) -> None:
    if not raw_path.exists():
        msg = f"{raw_path} is missing; run 'download' first"
        raise FileNotFoundError(msg)
    stats = prepare_dataset(raw_path, processed_path, source)
    _emit({"step": "prep", "dataset_id": source.dataset_id, **stats})


def cmd_verify(source: DatasetSource, raw_path: Path) -> None:
    if not raw_path.exists():
        msg = f"{raw_path} is missing; run 'download' first"
        raise FileNotFoundError(msg)
    verify_file(raw_path, source)
    _emit({"step": "verify", "dataset_id": source.dataset_id, "raw": str(raw_path), "sha256": source.sha256, "verified": True})


def main() -> int:
    parser = argparse.ArgumentParser(description="Reproducible benchmark dataset acquisition")
    parser.add_argument("command", choices=["download", "prep", "all", "verify"])
    parser.add_argument("--config", type=Path, default=Path("configs/project.yaml"))
    parser.add_argument("--offline", action="store_true", help="verify an existing file, never download")
    parser.add_argument("--force", action="store_true", help="re-download even if a valid file exists")
    args = parser.parse_args()

    source, raw_path, processed_path, provenance = _resolve(args.config)

    if args.command in ("download", "all"):
        cmd_download(source, raw_path, provenance, args.offline, args.force)
    if args.command == "verify":
        cmd_verify(source, raw_path)
    if args.command in ("prep", "all"):
        cmd_prep(source, raw_path, processed_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
