"""Unit tests for reproducible dataset download and deterministic prep.

None of these touch the network: checksum, schema, and prep logic are exercised
against small local fixtures so the reproducibility contract is testable in CI.
The download failure-path tests use a monkeypatched ``urlopen`` (a fake response
or an injected error) and inspect the destination and any leftover ``.part``
files, so they prove fail-closed behavior rather than only asserting that an
exception was raised.
"""

from __future__ import annotations

import hashlib
import io
import json
import urllib.error
import urllib.request
from dataclasses import replace

import pandas as pd
import pytest

from aletheia_lab.data.download import (
    ChecksumError,
    download_dataset,
    sha256_file,
    verify_file,
    write_provenance,
)
from aletheia_lab.data.prep import (
    SchemaError,
    count_blank_total_charges,
    load_raw,
    prepare,
    prepare_dataset,
)
from aletheia_lab.data.sources import TELCO_CUSTOMER_CHURN, get_source

COLUMNS = TELCO_CUSTOMER_CHURN.columns


def _row(**overrides: str) -> dict[str, str]:
    base = {column: "x" for column in COLUMNS}
    base.update(
        {
            "SeniorCitizen": "0",
            "tenure": "1",
            "MonthlyCharges": "29.85",
            "TotalCharges": "29.85",
            "Contract": "Month-to-month",
            "Churn": "No",
        }
    )
    base.update(overrides)
    return base


def _write_raw(path, rows: list[dict[str, str]]) -> None:
    pd.DataFrame(rows, columns=list(COLUMNS)).to_csv(path, index=False, lineterminator="\n")


def _fixture_source(n_rows: int):
    return replace(TELCO_CUSTOMER_CHURN, n_rows=n_rows)


# --- fake network layer (no real sockets are ever opened) ---------------------


class _FakeResponse:
    """Minimal stand-in for the urlopen context manager used by download.py."""

    def __init__(self, payload: bytes) -> None:
        self._buf = io.BytesIO(payload)

    def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _fake_urlopen(payload: bytes):
    def _open(url, timeout=None):  # noqa: ANN001, ARG001
        return _FakeResponse(payload)

    return _open


def _boom_urlopen(url, timeout=None):  # noqa: ANN001, ARG001
    raise urllib.error.URLError("simulated network failure")


def _no_part_files(directory) -> bool:
    return list(directory.glob("*.part")) == []


def test_pinned_source_shape() -> None:
    assert len(TELCO_CUSTOMER_CHURN.sha256) == 64
    assert all(c in "0123456789abcdef" for c in TELCO_CUSTOMER_CHURN.sha256)
    assert len(TELCO_CUSTOMER_CHURN.columns) == 21
    assert TELCO_CUSTOMER_CHURN.target in TELCO_CUSTOMER_CHURN.columns
    assert TELCO_CUSTOMER_CHURN.drift_feature in TELCO_CUSTOMER_CHURN.columns


def test_get_source_unknown_id() -> None:
    with pytest.raises(KeyError):
        get_source("does_not_exist")


def test_sha256_file_matches_hashlib(tmp_path) -> None:
    payload = b"reproducible,bytes\n1,2\n"
    target = tmp_path / "sample.csv"
    target.write_bytes(payload)
    assert sha256_file(target) == hashlib.sha256(payload).hexdigest()


def test_verify_file_pass_and_mismatch(tmp_path) -> None:
    target = tmp_path / "sample.bin"
    target.write_bytes(b"payload")
    good = replace(TELCO_CUSTOMER_CHURN, sha256=sha256_file(target))
    assert verify_file(target, good) == good.sha256

    bad = replace(TELCO_CUSTOMER_CHURN, sha256="0" * 64)
    with pytest.raises(ChecksumError):
        verify_file(target, bad)


def test_download_offline_missing_file(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        download_dataset(TELCO_CUSTOMER_CHURN, tmp_path / "absent.csv", offline=True)


def test_download_reuses_valid_existing_file(tmp_path) -> None:
    target = tmp_path / "raw.csv"
    target.write_bytes(b"already here")
    source = replace(TELCO_CUSTOMER_CHURN, sha256=sha256_file(target))
    # offline + present + matching checksum -> returned without any network access.
    assert download_dataset(source, target, offline=True) == target


# --- download failure-path regression tests (the P1-C-01 atomic-verify fix) ---


def test_download_success_replaces_only_after_verification(tmp_path, monkeypatch) -> None:
    dest = tmp_path / "raw.csv"
    payload = b"customerID,Churn\nA,No\n"
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(payload))
    source = replace(TELCO_CUSTOMER_CHURN, sha256=hashlib.sha256(payload).hexdigest())

    out = download_dataset(source, dest)

    assert out == dest
    assert dest.read_bytes() == payload  # only the verified bytes land at dest
    assert _no_part_files(tmp_path)


def test_download_wrong_checksum_raises_and_leaves_no_destination(tmp_path, monkeypatch) -> None:
    dest = tmp_path / "raw.csv"
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(b"corrupt payload"))
    # Pinned checksum cannot match the corrupt payload.
    source = replace(TELCO_CUSTOMER_CHURN, sha256="0" * 64)

    with pytest.raises(ChecksumError):
        download_dataset(source, dest)

    assert not dest.exists()  # fail closed: no corrupt file at the destination
    assert _no_part_files(tmp_path)  # temp .part removed


def test_download_force_wrong_checksum_preserves_existing_valid(tmp_path, monkeypatch) -> None:
    dest = tmp_path / "raw.csv"
    good_bytes = b"good,data\n1,2\n"
    dest.write_bytes(good_bytes)
    source = replace(TELCO_CUSTOMER_CHURN, sha256=sha256_file(dest))  # dest is valid under source
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(b"corrupt payload"))

    with pytest.raises(ChecksumError):
        download_dataset(source, dest, force=True)

    assert dest.read_bytes() == good_bytes  # old valid file untouched
    assert _no_part_files(tmp_path)


def test_download_network_failure_leaves_no_destination(tmp_path, monkeypatch) -> None:
    dest = tmp_path / "raw.csv"
    monkeypatch.setattr(urllib.request, "urlopen", _boom_urlopen)

    with pytest.raises(urllib.error.URLError):
        download_dataset(TELCO_CUSTOMER_CHURN, dest)

    assert not dest.exists()
    assert _no_part_files(tmp_path)  # interrupted download cleans its temp file


def test_download_force_network_failure_preserves_existing_valid(tmp_path, monkeypatch) -> None:
    dest = tmp_path / "raw.csv"
    good_bytes = b"good,data\n1,2\n"
    dest.write_bytes(good_bytes)
    source = replace(TELCO_CUSTOMER_CHURN, sha256=sha256_file(dest))
    monkeypatch.setattr(urllib.request, "urlopen", _boom_urlopen)

    with pytest.raises(urllib.error.URLError):
        download_dataset(source, dest, force=True)

    assert dest.read_bytes() == good_bytes  # failed forced refresh must not destroy valid file
    assert _no_part_files(tmp_path)


def test_download_replace_failure_leaves_no_destination(tmp_path, monkeypatch) -> None:
    dest = tmp_path / "raw.csv"
    payload = b"verified replacement payload"
    source = replace(TELCO_CUSTOMER_CHURN, sha256=hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(payload))

    def fail_replace(source_path, destination_path):  # noqa: ANN001, ARG001
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr("aletheia_lab.data.download.os.replace", fail_replace)

    with pytest.raises(OSError, match="simulated atomic replace failure"):
        download_dataset(source, dest)

    assert not dest.exists()
    assert _no_part_files(tmp_path)


def test_download_force_replace_failure_preserves_existing_valid(tmp_path, monkeypatch) -> None:
    dest = tmp_path / "raw.csv"
    old_bytes = b"existing verified dataset"
    new_bytes = b"new verified dataset"
    dest.write_bytes(old_bytes)
    source = replace(TELCO_CUSTOMER_CHURN, sha256=hashlib.sha256(new_bytes).hexdigest())
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(new_bytes))

    def fail_replace(source_path, destination_path):  # noqa: ANN001, ARG001
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr("aletheia_lab.data.download.os.replace", fail_replace)

    with pytest.raises(OSError, match="simulated atomic replace failure"):
        download_dataset(source, dest, force=True)

    assert dest.read_bytes() == old_bytes
    assert _no_part_files(tmp_path)


def test_download_force_success_replaces_existing_valid(tmp_path, monkeypatch) -> None:
    dest = tmp_path / "raw.csv"
    old_bytes = b"existing verified dataset"
    new_bytes = b"new verified dataset"
    dest.write_bytes(old_bytes)
    source = replace(TELCO_CUSTOMER_CHURN, sha256=hashlib.sha256(new_bytes).hexdigest())
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(new_bytes))

    out = download_dataset(source, dest, force=True)

    assert out == dest
    assert dest.read_bytes() == new_bytes
    assert _no_part_files(tmp_path)


# --- provenance path contract (contract B: exactly the caller-provided path) ---


def test_write_provenance_records_caller_path_and_fields(tmp_path) -> None:
    rel = "data/raw/telco_customer_churn_raw.csv"
    out = write_provenance(TELCO_CUSTOMER_CHURN, rel, tmp_path / "prov.json")
    record = json.loads(out.read_text(encoding="utf-8"))

    assert record["file"] == rel  # stored exactly as passed (script passes a relative path)
    assert set(record) >= {
        "dataset_id",
        "source_url",
        "sha256",
        "n_bytes",
        "n_rows",
        "file",
        "retrieved_at",
    }


def test_load_raw_accepts_matching_schema(tmp_path) -> None:
    raw = tmp_path / "raw.csv"
    _write_raw(raw, [_row(), _row(Churn="Yes")])
    frame = load_raw(raw, _fixture_source(n_rows=2))
    assert list(frame.columns) == list(COLUMNS)
    assert len(frame) == 2


def test_load_raw_rejects_wrong_row_count(tmp_path) -> None:
    raw = tmp_path / "raw.csv"
    _write_raw(raw, [_row()])
    with pytest.raises(SchemaError):
        load_raw(raw, _fixture_source(n_rows=99))


def test_load_raw_rejects_missing_column(tmp_path) -> None:
    raw = tmp_path / "raw.csv"
    frame = pd.DataFrame([_row()], columns=list(COLUMNS)).drop(columns=["Churn"])
    frame.to_csv(raw, index=False, lineterminator="\n")
    with pytest.raises(SchemaError):
        load_raw(raw, _fixture_source(n_rows=1))


def test_prepare_zeroes_blank_total_charges(tmp_path) -> None:
    raw = tmp_path / "raw.csv"
    _write_raw(raw, [_row(), _row(tenure="0", TotalCharges="")])
    frame = load_raw(raw, _fixture_source(n_rows=2))
    assert count_blank_total_charges(frame) == 1

    prepared = prepare(frame)
    assert prepared["TotalCharges"].dtype == float
    assert prepared["SeniorCitizen"].dtype == int
    assert prepared.loc[1, "TotalCharges"] == 0.0


def test_prepare_dataset_is_deterministic(tmp_path) -> None:
    raw = tmp_path / "raw.csv"
    _write_raw(raw, [_row(), _row(Churn="Yes", TotalCharges=""), _row(tenure="12")])
    source = _fixture_source(n_rows=3)

    first = prepare_dataset(raw, tmp_path / "out_a.csv", source)
    second = prepare_dataset(raw, tmp_path / "out_b.csv", source)

    assert first["sha256"] == second["sha256"]
    assert first["n_rows"] == 3
    assert first["n_cols"] == 21
    assert first["total_charges_blanks_zeroed"] == 1
