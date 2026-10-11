"""Co-retain actual calibration evidence with the existing audit obligation.

Membership/arithmetic evidence is not encoded as a selected-model verdict. Its
context and call are dependency atoms, included in the same quota, transaction,
lease, eviction and recovery union as the state-use correspondence frame.
"""

from __future__ import annotations

import copy
from typing import Any, cast

from aletheia_lab.evaluation.calibration_audit_source import assess as linear_assess
from aletheia_lab.evaluation.incident_audit_archive import IncidentAuditArchive
from aletheia_lab.evaluation.incident_audit_incremental import IncrementalAuditArchive
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.request_model_audit import digest, materialize, resolve
from aletheia_lab.evaluation.request_model_retention import _atom, _decode
from aletheia_lab.project.identity import content_sha256


def assess(packet: dict[str, Any]) -> dict[str, str]:
    """Select an explicit capsule contract, never silently upgrade old evidence."""
    if packet.get("schema") == "calibration-native-evidence/v1":
        return linear_assess(packet)
    if packet.get("schema") == "pipeline-native-evidence/v1":
        from aletheia_lab.evaluation.pipeline_audit_source import assess as pipeline_assess

        return pipeline_assess(packet)
    raise ValueError("unsupported native evidence contract")


def frame(packet: dict[str, Any]) -> dict[str, Any]:
    """The old public resolver still means only authorized state correspondence."""
    call, token = packet["call"], packet["token"]
    approved = digest(packet["approved_state"])
    actual = digest(call["actual_state"]) if call.get("actual_state") else approved
    associated = call.get("actual_state") is not None
    if packet.get("schema") == "pipeline-native-evidence/v1":
        associated = associated and call["actual_state"].get("preprocessing") is not None
    call_hash = digest(call)
    output = digest(call["output"]) if call["output"] is not None else None
    return {
        "token": token,
        "requested": "aaa",
        "kind": "non_batched",
        "input": digest(call["input"]),
        "output": output,
        "closed": call["closed"],
        "failed": call["failed"],
        "loads": {
            "base": {
                "model": "aaa" if actual == approved else "bbb",
                "artifact": approved,
                "fingerprint": actual,
            },
            "call-proof": {"model": "aaa", "artifact": call_hash, "fingerprint": call_hash},
        },
        "uses": []
        if not associated or output is None
        else [
            {
                "token": call["token"],
                "batch": token,
                "index": 0,
                "generation": "base",
                "input": digest(call["input"]),
                "output": output,
                "fingerprint": actual,
            }
        ],
    }


class CalibrationEvidenceMixin(IncidentAuditArchive):
    """Additional evidence, with unchanged inherited admission/persistence rules."""

    def _validate(self: Any, state: dict[str, Any]) -> None:
        super()._validate(state)
        for token in state["entries"]:
            self._evidence(state, token)

    def _install_packet(self: Any, packet: dict[str, Any]) -> None:
        native_frame = frame(packet)
        resolve(native_frame)
        assess(packet)
        token = packet["token"]
        packed = materialize(native_frame)
        target, raw = _atom({k: v for k, v in packed.items() if k != "loads"})
        self.state["atoms"][target] = raw
        context = {k: v for k, v in packet.items() if k not in {"token", "call"}}
        proofs = {
            "base": {"part": "context", "value": context},
            "call-proof": {"part": "call", "value": packet["call"]},
        }
        loads = {}
        for generation, load in packed["loads"].items():
            key, raw = _atom({"load": load, "calibration_evidence": proofs[generation]})
            loads[generation], self.state["atoms"][key] = key, raw
        self.state["clock"] += 1
        self.state["entries"][token] = {
            "target": target,
            "loads": loads,
            "seal": content_sha256(encode([token, target, loads]).encode()),
            "created": self.state["clock"],
            "touch": self.state["clock"],
            "hits": 0,
        }

    def put_evidence(self: Any, packet: dict[str, Any], *, now: int) -> bool:
        """Commit capsule and frame together before acknowledging completion."""
        token = packet["token"]
        with self._transition(now):
            if token in self.state["entries"]:
                raise ValueError("duplicate completed scope")
            before = copy.deepcopy(self.state)
            reservation = self.state["pending"].pop(token, None)
            self._install_packet(packet)
            if reservation:
                self.state["leases"][f"pre:{token}"] = {
                    "scopes": [token],
                    "until": reservation["until"],
                }
                base = {
                    **before,
                    "pending": {k: v for k, v in before["pending"].items() if k != token},
                }
                growth = self._charge(self.state) - self._charge(base)
                if growth > reservation["bound"]:
                    self.state = before
                    del self.state["pending"][token]
                    self.state["overruns"] += 1
                    return False
            required = frozenset({token}) if reservation else frozenset()
            if not self._fit(required):
                self.state = before
                if reservation:
                    del self.state["pending"][token]
                    self.state["overruns"] += 1
                return False
            return token in self.state["entries"]

    def evidence(self: Any, token: str) -> dict[str, Any] | None:
        """Read only dependency-complete, retained committed evidence."""
        if self._read() != self.state:
            raise ValueError("evidence durable frontier differs")
        return cast(dict[str, Any] | None, self._evidence(self.state, token))

    def _evidence(self: Any, state: dict[str, Any], token: str) -> dict[str, Any] | None:
        entry = state["entries"].get(token)
        if entry is None:
            return None
        parts = {}
        for key in entry["loads"].values():
            decoded = _decode(key, state["atoms"][key])
            proof = decoded.get("calibration_evidence")
            if proof is None or proof["part"] in parts:
                raise ValueError("calibration evidence dependency missing/duplicated")
            parts[proof["part"]] = proof["value"]
        if set(parts) != {"context", "call"}:
            raise ValueError("calibration evidence closure differs")
        packet = {**parts["context"], "token": token, "call": parts["call"]}
        if frame(packet) != self._frame(token, entry, state["atoms"]):
            raise ValueError("capsule and correspondence frame differ")
        assess(packet)
        return packet

    def restore_evidence(self: Any, packets: list[dict[str, Any]], *, now: int) -> bool:
        """Atomically recover original scopes, not newly renamed receipts."""
        scopes = [packet["token"] for packet in packets]
        if not packets or len(packets) > 64 or len(scopes) != len(set(scopes)):
            raise ValueError("bounded unique recovery union required")
        with self._transition(now):
            before = copy.deepcopy(self.state)
            for packet in packets:
                token = packet["token"]
                if token in self.state["pending"]:
                    raise ValueError("recovery cannot replace uncompleted dispatch")
                if token in self.state["entries"]:
                    if self._evidence(self.state, token) != packet:
                        raise ValueError("recovery revision differs")
                else:
                    self._install_packet(packet)
            if not self._fit(frozenset(scopes)):
                self.state = before
                return False
        return True


class CompactCalibrationArchive(CalibrationEvidenceMixin, IncrementalAuditArchive):
    """Ordinary deduplicated keyed rows, with full query evidence."""


class FullCalibrationArchive(CalibrationEvidenceMixin, IncidentAuditArchive):
    """Whole-state retention with exactly the same evidence and obligations."""
