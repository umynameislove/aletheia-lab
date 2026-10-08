"""Matched durable full-frame and deduplicated capsule storage.

Both arms inherit the identical logical admission, lease, eviction and recovery
state machine and the same acknowledged row cache. Physical layouts differ.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from time import perf_counter_ns
from typing import Any, cast

from aletheia_lab.evaluation.calibration_audit_archive import CalibrationEvidenceMixin
from aletheia_lab.evaluation.incident_audit_archive import IncidentAuditArchive
from aletheia_lab.evaluation.incident_audit_incremental import IncrementalAuditArchive
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.evaluation.request_model_audit import materialize, resolve
from aletheia_lab.evaluation.request_model_retention import _atom, _decode
from aletheia_lab.project.identity import content_sha256

BOOKKEEPING = ("created", "touch", "hits", "seal")
METADATA = {
    "schema",
    "policy",
    "budget",
    "now",
    "clock",
    "accepted",
    "refused",
    "evicted",
    "overruns",
}
RAW_FAMILIES = ("frames", "entries", "leases", "pending")
TABLE_SQL = {
    "raw_fragments": (
        "SELECT family,key,payload FROM raw_fragments",
        "CREATE TABLE IF NOT EXISTS raw_fragments("
        "family TEXT NOT NULL,key TEXT NOT NULL,payload BLOB NOT NULL,"
        "PRIMARY KEY(family,key)) WITHOUT ROWID",
        "DELETE FROM raw_fragments WHERE family=? AND key=?",
        "INSERT OR REPLACE INTO raw_fragments VALUES(?,?,?)",
    ),
    "fragments": (
        "SELECT family,key,payload FROM fragments",
        "CREATE TABLE IF NOT EXISTS fragments("
        "family TEXT NOT NULL,key TEXT NOT NULL,payload BLOB NOT NULL,"
        "PRIMARY KEY(family,key)) WITHOUT ROWID",
        "DELETE FROM fragments WHERE family=? AND key=?",
        "INSERT OR REPLACE INTO fragments VALUES(?,?,?)",
    ),
}


def validate_load_wrapper(wrapper: dict[str, Any]) -> None:
    """Only the explicitly named capsule extension is part of this codec."""
    if type(wrapper) is not dict or set(wrapper) not in (
        {"load"},
        {"load", "calibration_evidence"},
    ):
        raise ValueError("unsupported supplemental load wrapper")
    if "calibration_evidence" in wrapper and type(wrapper["calibration_evidence"]) is not dict:
        raise ValueError("calibration evidence must be a full document")


class OwnerRowCache(IncidentAuditArchive):
    """Same volatile memoization and delta SQL for either physical codec."""

    table: str

    def __init__(self, path: Path, policy: str, budget: int, *, reopen: bool = False) -> None:
        self._ack_rows: dict[tuple[str, str], bytes] | None = None
        self._ack_state: dict[str, Any] | None = None
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(path) + suffix)
            if candidate.is_symlink() or (not reopen and candidate.exists()):
                raise ValueError("owned fresh archive files required")
        super().__init__(path, policy, budget, reopen=reopen)
        if self._ack_rows is None:
            self._ack_rows, self._ack_state = self._durable_rows(), copy.deepcopy(self.state)

    def _validate(self, state: dict[str, Any]) -> None:
        super()._validate(state)
        for entry in state["entries"].values():
            for key in entry["loads"].values():
                validate_load_wrapper(_decode(key, state["atoms"][key]))

    def _durable_rows(self) -> dict[tuple[str, str], bytes]:
        return {(family, key): bytes(raw) for family, key, raw in self.db.execute(self._sql()[0])}

    def _sql(self) -> tuple[str, str, str, str]:
        try:
            return TABLE_SQL[self.table]
        except KeyError:
            raise ValueError("unsupported archive table") from None

    def _read(self) -> dict[str, Any]:
        if self._ack_rows is None:
            return super()._read()
        started, before = perf_counter_ns(), self.metrics["read_ns"]
        if self._durable_rows() == self._ack_rows:
            assert self._ack_state is not None
            result = copy.deepcopy(self._ack_state)
        else:
            result = super()._read()
            if result != self._ack_state:
                raise ValueError("durable rows changed outside their acknowledged owner")
        self.metrics["read_ns"] = before + perf_counter_ns() - started
        return result

    def _commit(self: Any) -> None:
        self._validate(self.state)
        current, acknowledged = self._rows(self.state), copy.deepcopy(self.state)
        exists = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (self.table,)
        ).fetchone()
        previous = self._durable_rows() if exists else {}
        changed = [
            (family, key, raw)
            for (family, key), raw in current.items()
            if previous.get((family, key)) != raw
        ]
        removed = sorted(set(previous) - set(current))
        charge, started = self._charge(self.state), perf_counter_ns()
        _, create_sql, delete_sql, insert_sql = self._sql()
        with self.db:
            self.db.execute(create_sql)
            self.db.executemany(delete_sql, removed)
            self.db.executemany(insert_sql, changed)
        self._ack_rows, self._ack_state = current, acknowledged
        self.metrics["write_ns"] += perf_counter_ns() - started
        self.metrics["commits"] += 1
        self.metrics["written_bytes"] += sum(len(raw) for _, _, raw in changed)
        self.metrics["peak_charge"] = max(self.metrics["peak_charge"], charge)
        try:
            self.metrics["peak_db_wal_shm_bytes"] = max(
                self.metrics["peak_db_wal_shm_bytes"], self.storage_bytes()
            )
        except OSError:
            self.metrics["storage_sample_failures"] += 1


class RawCalibrationCodec(IncidentAuditArchive):
    """Full expanded frames, with exact uncompressed supplemental load fields.

    The strict load appears once in the full frame. Its supplemental fields are
    stored per generation and rejoined to that load on read, preserving every
    complete logical wrapper and its existing content-addressed manifest.
    """

    @staticmethod
    def _seal(rows: dict[tuple[str, str], bytes], metadata: dict[str, Any]) -> str:
        return content_sha256(
            encode(
                {
                    "metadata": metadata,
                    "rows": [
                        [family, key, content_sha256(raw)]
                        for (family, key), raw in sorted(rows.items())
                    ],
                }
            ).encode()
        )

    @staticmethod
    def _rows(state: dict[str, Any]) -> dict[tuple[str, str], bytes]:
        document = json.loads(IncidentAuditArchive._document(state))
        entries = document.pop("entries")
        document.pop("atoms")
        rows = {
            (family, key): encode(value).encode()
            for family in ("leases", "pending")
            for key, value in document.pop(family).items()
        }
        for token, entry in state["entries"].items():
            wrappers = {
                generation: _decode(key, state["atoms"][key])
                for generation, key in entry["loads"].items()
            }
            for wrapper in wrappers.values():
                validate_load_wrapper(wrapper)
            record = {
                "frame": IncidentAuditArchive._frame(token, entry, state["atoms"]),
                "load_extensions": {
                    generation: {key: value for key, value in wrapper.items() if key != "load"}
                    for generation, wrapper in wrappers.items()
                },
            }
            raw = encode(record).encode()
            rows[("frames", token)] = raw
            rows[("entries", token)] = encode(
                {
                    **{key: entries[token][key] for key in BOOKKEEPING},
                    "record_sha256": content_sha256(raw),
                }
            ).encode()
        rows[("metadata", "singleton")] = encode(
            {"values": document, "seal": RawCalibrationCodec._seal(rows, document)}
        ).encode()
        return rows

    def _read(self: Any) -> dict[str, Any]:
        started = perf_counter_ns()
        rows = self._durable_rows()
        raw_metadata = rows.pop(("metadata", "singleton"), None)
        if raw_metadata is None:
            raise ValueError("raw metadata missing")
        metadata = json.loads(raw_metadata)
        if type(metadata) is not dict or set(metadata) != {"values", "seal"}:
            raise ValueError("raw metadata fields differ")
        if encode(metadata).encode() != raw_metadata:
            raise ValueError("raw metadata is noncanonical")
        if type(metadata["values"]) is not dict or set(metadata["values"]) != METADATA:
            raise ValueError("raw logical metadata fields differ")
        if metadata["seal"] != self._seal(rows, metadata["values"]):
            raise ValueError("raw row-set seal differs")
        values = self._decode_rows(rows)
        document: dict[str, Any] = {
            **metadata["values"],
            "entries": {},
            "atoms": {},
            "leases": values["leases"],
            "pending": values["pending"],
        }
        for token, record in values["frames"].items():
            self._restore_record(document, token, record, values["entries"][token], rows)
        result = self._parse(document)
        self._validate(result)
        self.metrics["read_ns"] += perf_counter_ns() - started
        return cast(dict[str, Any], result)

    @staticmethod
    def _decode_rows(rows: dict[tuple[str, str], bytes]) -> dict[str, Any]:
        values: dict[str, Any] = {family: {} for family in RAW_FAMILIES}
        for (family, key), raw in rows.items():
            if family not in RAW_FAMILIES:
                raise ValueError("unexpected raw row")
            value = json.loads(raw)
            if type(value) is not dict or encode(value).encode() != raw:
                raise ValueError("raw row is noncanonical")
            values[family][key] = value
        if set(values["frames"]) != set(values["entries"]):
            raise ValueError("raw frame/entry closure differs")
        return values

    @staticmethod
    def _restore_record(
        document: dict[str, Any],
        token: str,
        record: dict[str, Any],
        entry: dict[str, Any],
        rows: dict[tuple[str, str], bytes],
    ) -> None:
        if (
            set(record) != {"frame", "load_extensions"}
            or type(record["load_extensions"]) is not dict
        ):
            raise ValueError("raw full-frame record differs")
        frame = record["frame"]
        if set(entry) != {*BOOKKEEPING, "record_sha256"}:
            raise ValueError("raw entry fields differ")
        if entry["record_sha256"] != content_sha256(rows[("frames", token)]):
            raise ValueError("raw full-frame seal differs")
        resolve(frame)
        if frame["token"] != token:
            raise ValueError("raw frame scope differs")
        packed = materialize(frame)
        if set(record["load_extensions"]) != set(packed["loads"]):
            raise ValueError("raw load-wrapper closure differs")
        target, raw = _atom({key: value for key, value in packed.items() if key != "loads"})
        document["atoms"][target] = raw
        loads = {}
        for generation, extension in record["load_extensions"].items():
            if type(extension) is not dict or set(extension) not in (
                set(),
                {"calibration_evidence"},
            ):
                raise ValueError("unsupported supplemental load fields")
            wrapper = {"load": packed["loads"][generation], **extension}
            validate_load_wrapper(wrapper)
            atom, raw = _atom(wrapper)
            loads[generation], document["atoms"][atom] = atom, raw
        document["entries"][token] = {
            **{key: entry[key] for key in BOOKKEEPING},
            "target": target,
            "loads": loads,
        }


class RawEvidenceArchive(CalibrationEvidenceMixin, OwnerRowCache, RawCalibrationCodec):
    table = "raw_fragments"


class CompactEvidenceArchive(CalibrationEvidenceMixin, OwnerRowCache, IncrementalAuditArchive):
    table = "fragments"
