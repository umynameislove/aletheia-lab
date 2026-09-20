"""Create-only persistence for diagnosis main-study turns and terminals."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from aletheia_lab.diagnosis._main_runtime_contracts import (
    MainLogicalTerminal,
    MainRuntimeError,
)
from aletheia_lab.filesystem import publish_immutable_file
from aletheia_lab.model_gateway import GatewayExecutionResult, GatewayRequest


def _serialized(model: BaseModel) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


class MainRuntimeStore:
    """Create-only per-turn persistence; incomplete turns require explicit recovery."""

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            raise MainRuntimeError("main runtime store root must be a real directory")
        self.root = root.resolve()

    def _logical_root(self, logical_request_id: str) -> Path:
        if not logical_request_id.startswith("dmr-") or "/" in logical_request_id:
            raise MainRuntimeError("logical request ID is unsafe for persistence")
        path = self.root / logical_request_id
        path.mkdir(exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise MainRuntimeError("logical request store is not a real directory")
        return path

    def terminal(self, logical_request_id: str) -> MainLogicalTerminal | None:
        path = self._logical_root(logical_request_id) / "terminal.json"
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise MainRuntimeError("logical terminal is not a regular file")
        return MainLogicalTerminal.model_validate_json(path.read_bytes())

    def begin_or_resume_turn(
        self,
        logical_request_id: str,
        turn_ordinal: int,
        request: GatewayRequest,
    ) -> GatewayExecutionResult | None:
        turn_root = self._logical_root(logical_request_id) / f"turn-{turn_ordinal:02d}"
        turn_root.mkdir(exist_ok=True)
        request_path = turn_root / "request.json"
        result_path = turn_root / "result.json"
        expected = _serialized(request)
        if request_path.exists():
            if (
                request_path.is_symlink()
                or not request_path.is_file()
                or request_path.read_bytes() != expected
            ):
                raise MainRuntimeError("persisted turn request differs from reconstruction")
            if not result_path.exists():
                raise MainRuntimeError(
                    "incomplete persisted turn forbids automatic provider replay"
                )
            if result_path.is_symlink() or not result_path.is_file():
                raise MainRuntimeError("persisted turn result is not a regular file")
            return GatewayExecutionResult.model_validate_json(result_path.read_bytes())
        if result_path.exists():
            raise MainRuntimeError("turn result exists without its immutable request")
        publish_immutable_file(request_path, expected)
        return None

    def complete_turn(
        self,
        logical_request_id: str,
        turn_ordinal: int,
        result: GatewayExecutionResult,
    ) -> None:
        turn_root = self._logical_root(logical_request_id) / f"turn-{turn_ordinal:02d}"
        request_path = turn_root / "request.json"
        if not request_path.is_file() or request_path.is_symlink():
            raise MainRuntimeError("turn result cannot precede its immutable request")
        publish_immutable_file(turn_root / "result.json", _serialized(result))

    def publish_terminal(self, terminal: MainLogicalTerminal) -> None:
        publish_immutable_file(
            self._logical_root(terminal.logical_request_id) / "terminal.json",
            _serialized(terminal),
        )


__all__ = ["MainRuntimeStore"]
