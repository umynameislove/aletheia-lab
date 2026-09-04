"""Scalable immutable storage for the live claim-corpus execution."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from aletheia_lab.evaluation.attempt_store import (
    ImmutableAttemptStore,
    StoreClock,
    TerminalExecutionInventory,
)
from aletheia_lab.evaluation.claim_corpus_execution import ClaimCorpusExecutionError
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.filesystem import publish_immutable_file
from aletheia_lab.project.identity import canonical_project_json, content_sha256

if TYPE_CHECKING:
    from aletheia_lab.evaluation.claim_corpus_live import PreparedClaimCorpusRequest


class ClaimCorpusAttemptStore:
    """One independently verified immutable shard per frozen request.

    The underlying store verifies its complete membership on every transition.
    Sharding keeps that strong check bounded to one request instead of turning
    the 360-request run into a quadratic full-store rescan.
    """

    def __init__(self, root: Path, *, clock: StoreClock) -> None:
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            raise ClaimCorpusExecutionError("claim-corpus store root must be a real directory")
        self.root = root.resolve()
        self.clock = clock
        self.request_root = self.root / "requests"
        self.authority_root = self.root / "authorities"
        self.request_root.mkdir(exist_ok=True)
        self.authority_root.mkdir(exist_ok=True)
        if any(
            path.is_symlink() or not path.is_dir()
            for path in (self.request_root, self.authority_root)
        ):
            raise ClaimCorpusExecutionError("claim-corpus request store must be a real directory")
        if any(path.name not in {"requests", "authorities"} for path in self.root.iterdir()):
            raise ClaimCorpusExecutionError("claim-corpus store contains an unknown root entry")

    def shards(
        self,
        prepared: tuple[PreparedClaimCorpusRequest, ...],
    ) -> dict[str, ImmutableAttemptStore]:
        expected = {
            item.request.initial_attempt.request_identity_sha256 for item in prepared
        }
        existing = {path.name for path in self.request_root.iterdir()}
        if not existing <= expected or any(
            path.is_symlink() or not path.is_dir() for path in self.request_root.iterdir()
        ):
            raise ClaimCorpusExecutionError("claim-corpus store contains an unknown request shard")
        authority_names = {path.name for path in self.authority_root.iterdir()}
        expected_names = {f"{identity}.json" for identity in expected}
        if not authority_names <= expected_names:
            raise ClaimCorpusExecutionError("claim-corpus store contains an unknown authority")
        by_identity = {
            item.request.initial_attempt.request_identity_sha256: item for item in prepared
        }
        for identity in sorted(expected):
            payload = (
                canonical_project_json(by_identity[identity].authority.model_dump(mode="json"))
                + "\n"
            ).encode()
            publish_immutable_file(self.authority_root / f"{identity}.json", payload)
        return {
            identity: ImmutableAttemptStore(self.request_root / identity, clock=self.clock)
            for identity in sorted(expected)
        }

    @staticmethod
    def terminal_inventories(
        shards: dict[str, ImmutableAttemptStore],
    ) -> tuple[TerminalExecutionInventory, ...]:
        inventories = tuple(
            inventory
            for identity in sorted(shards)
            for inventory in shards[identity].terminal_inventories()
        )
        return tuple(sorted(inventories, key=lambda item: item.request_identity_sha256))

    def store_sha256(self, shards: dict[str, ImmutableAttemptStore]) -> str:
        return canonical_execution_sha256(
            {
                "schema_version": "claim-corpus-sharded-attempt-store/v1",
                "authorities": tuple(
                    (path.stem, content_sha256(path.read_bytes()))
                    for path in sorted(self.authority_root.glob("*.json"))
                ),
                "shards": tuple(
                    (identity, shards[identity].store_sha256())
                    for identity in sorted(shards)
                ),
            }
        )


__all__ = ["ClaimCorpusAttemptStore"]
