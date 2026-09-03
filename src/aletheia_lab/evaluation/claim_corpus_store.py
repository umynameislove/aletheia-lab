"""Create-only, content-addressed claim-corpus artifact store."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ValidationError

from aletheia_lab.evaluation.claim_corpus_contracts import (
    ClaimCorpusContractError,
    ClaimCorpusManifest,
    ClaimCorpusObjectPointer,
    ClaimCorpusStoreReceipt,
    ClaimSupportCorpusEntry,
)
from aletheia_lab.evaluation.claim_corpus_materializer import reconcile_materialized_entries
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.filesystem import (
    fsync_directory_tree,
    publish_staged_directory,
    write_new_file,
)
from aletheia_lab.project.identity import canonical_project_json, content_sha256

_RUN_ID: Final = re.compile(r"^ccrun-[0-9a-f]{64}$")
_OBJECT_BUCKET: Final = re.compile(r"^[0-9a-f]{2}$")
_OBJECT_NAME: Final = re.compile(r"^[0-9a-f]{62}\.json$")


def canonical_model_bytes(model: BaseModel) -> bytes:
    return (canonical_project_json(model.model_dump(mode="json")) + "\n").encode("utf-8")


class ClaimCorpusArtifactStore:
    """Write complete runs atomically and reject every non-identical replay."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def runs_root(self) -> Path:
        return self.root / "runs"

    def publish(
        self,
        *,
        protocol_sha256: str,
        census_sha256: str,
        entries: tuple[ClaimSupportCorpusEntry, ...],
        provider_calls_recorded: int,
    ) -> ClaimCorpusStoreReceipt:
        checked = reconcile_materialized_entries(entries)
        objects = tuple(
            (content_sha256(canonical_model_bytes(entry)), entry)
            for entry in checked
        )
        objects = tuple(sorted(objects, key=lambda item: item[0]))
        pointers = tuple(
            ClaimCorpusObjectPointer(
                entry_sha256=entry.entry_sha256,
                object_sha256=digest,
            )
            for digest, entry in objects
        )
        manifest_payload = {
            "schema_version": "claim-support-corpus-manifest/v1",
            "protocol_sha256": protocol_sha256,
            "census_sha256": census_sha256,
            "entries": tuple(item.model_dump(mode="json") for item in pointers),
            "object_sha256s": tuple(digest for digest, _ in objects),
            "provider_calls_recorded": provider_calls_recorded,
        }
        manifest = ClaimCorpusManifest.model_validate(
            {
                **manifest_payload,
                "manifest_sha256": canonical_execution_sha256(manifest_payload),
            }
        )
        receipt_payload = {
            "schema_version": "claim-support-corpus-store-receipt/v1",
            "run_id": f"ccrun-{manifest.manifest_sha256}",
            "manifest_sha256": manifest.manifest_sha256,
            "entry_count": len(checked),
            "terminal": True,
        }
        receipt = ClaimCorpusStoreReceipt.model_validate(
            {
                **receipt_payload,
                "receipt_sha256": canonical_execution_sha256(receipt_payload),
            }
        )

        self._ensure_store_root()
        destination = self.runs_root / receipt.run_id
        if destination.exists() or destination.is_symlink():
            existing = self.verify_run(receipt.run_id)
            if existing != receipt:
                raise ClaimCorpusContractError("non-identical corpus run replay rejected")
            return existing

        stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=self.runs_root))
        try:
            for digest, entry in objects:
                write_new_file(
                    stage / "objects" / "sha256" / digest[:2] / f"{digest[2:]}.json",
                    canonical_model_bytes(entry),
                )
            write_new_file(stage / "manifest.json", canonical_model_bytes(manifest))
            write_new_file(stage / "receipt.json", canonical_model_bytes(receipt))
            fsync_directory_tree(stage)
            publish_staged_directory(stage, destination)
        except Exception:
            _remove_empty_stage(stage)
            raise
        return self.verify_run(receipt.run_id)

    def _ensure_store_root(self) -> None:
        self.runs_root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or self.runs_root.is_symlink():
            raise ClaimCorpusContractError("corpus store roots must not be symlinks")
        if not self.root.is_dir() or not self.runs_root.is_dir():
            raise ClaimCorpusContractError("corpus store roots must be directories")

    def verify_run(self, run_id: str) -> ClaimCorpusStoreReceipt:
        if _RUN_ID.fullmatch(run_id) is None:
            raise ClaimCorpusContractError("corpus run ID is invalid")
        run_root = self.runs_root / run_id
        manifest, receipt = _load_run_metadata(run_root)
        if receipt.run_id != run_id or receipt.manifest_sha256 != manifest.manifest_sha256:
            raise ClaimCorpusContractError("corpus run metadata identities differ")
        if receipt.entry_count != len(manifest.entries):
            raise ClaimCorpusContractError("corpus receipt count differs from manifest")
        object_files = _verify_objects(run_root, manifest)
        _verify_run_membership(run_root, object_files)
        return receipt


def _load_run_metadata(
    run_root: Path,
) -> tuple[ClaimCorpusManifest, ClaimCorpusStoreReceipt]:
    try:
        if run_root.is_symlink() or not run_root.is_dir():
            raise ClaimCorpusContractError("corpus run root is invalid")
        manifest = ClaimCorpusManifest.model_validate_json(
            _read_regular(run_root / "manifest.json")
        )
        receipt = ClaimCorpusStoreReceipt.model_validate_json(
            _read_regular(run_root / "receipt.json")
        )
    except (OSError, UnicodeError, ValidationError) as exc:
        raise ClaimCorpusContractError("corpus run metadata is unavailable or invalid") from exc
    return manifest, receipt


def _verify_objects(
    run_root: Path,
    manifest: ClaimCorpusManifest,
) -> tuple[Path, ...]:
    object_root = run_root / "objects" / "sha256"
    if object_root.is_symlink() or not object_root.is_dir():
        raise ClaimCorpusContractError("corpus object root is invalid")
    actual: set[str] = set()
    object_files: list[Path] = []
    pointers = {item.object_sha256: item.entry_sha256 for item in manifest.entries}
    for bucket in object_root.iterdir():
        if bucket.is_symlink() or not bucket.is_dir() or not _OBJECT_BUCKET.fullmatch(bucket.name):
            raise ClaimCorpusContractError("corpus object bucket is invalid")
        for path in bucket.iterdir():
            _verify_object_file(path, bucket.name, pointers, actual)
            object_files.append(path)
    if actual != set(manifest.object_sha256s):
        raise ClaimCorpusContractError("corpus object membership differs from manifest")
    return tuple(object_files)


def _verify_object_file(
    path: Path,
    bucket: str,
    pointers: dict[str, str],
    actual: set[str],
) -> None:
    if path.is_symlink() or not path.is_file() or not _OBJECT_NAME.fullmatch(path.name):
        raise ClaimCorpusContractError("corpus object path is invalid")
    digest = bucket + path.stem
    if content_sha256(path.read_bytes()) != digest:
        raise ClaimCorpusContractError("corpus object content hash differs")
    entry = ClaimSupportCorpusEntry.model_validate_json(path.read_bytes())
    if pointers.get(digest) != entry.entry_sha256:
        raise ClaimCorpusContractError("corpus object is absent from manifest")
    actual.add(digest)


def _verify_run_membership(run_root: Path, object_files: tuple[Path, ...]) -> None:
    expected_files = {
        "manifest.json",
        "receipt.json",
        *(str(path.relative_to(run_root)) for path in object_files),
    }
    actual_files = {
        str(path.relative_to(run_root)) for path in run_root.rglob("*") if path.is_file()
    }
    if actual_files != expected_files:
        raise ClaimCorpusContractError("corpus run contains partial or untracked files")


def _read_regular(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ClaimCorpusContractError("corpus artifact must be a regular file")
    return path.read_bytes()


def _remove_empty_stage(stage: Path) -> None:
    """Best-effort cleanup without ever deleting a published destination."""

    if not stage.exists() or stage.is_symlink() or not stage.is_dir():
        return
    for path in sorted(stage.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_file() and not path.is_symlink():
            path.unlink(missing_ok=True)
        elif path.is_dir() and not path.is_symlink():
            path.rmdir()
    stage.rmdir()


__all__ = ["ClaimCorpusArtifactStore", "canonical_model_bytes"]
