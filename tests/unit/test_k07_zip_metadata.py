import io
import struct
import zipfile

import pytest

from aletheia_lab.hosted.source_bundle import SourceRejected, inspect_source_bundle


def bundle():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("dataset.csv", "id,target\n1,0\n")
        archive.writestr("metrics.csv", "run,name,value\nr,loss,0\n")
    return output.getvalue()


@pytest.mark.parametrize("false_count", [False, True])
def test_entry_budget_is_checked_before_zipinfo_allocation(monkeypatch, false_count):
    payload = bundle()
    if false_count:
        payload = bytearray(payload)
        footer = payload.rfind(zipfile.stringEndArchive)
        struct.pack_into("<HH", payload, footer + 8, 1, 1)
        payload = bytes(payload)
    def forbidden_constructor(*args, **kwargs):
        raise AssertionError("Unbounded ZIP metadata reached ZipFile allocation")
    monkeypatch.setattr(zipfile, "ZipFile", forbidden_constructor)
    with pytest.raises(SourceRejected):
        inspect_source_bundle(payload, maximum_upload_bytes=10000, maximum_expanded_bytes=10000, maximum_entries=1)


def test_zip64_count_metadata_is_supported(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(zipfile, "ZIP_FILECOUNT_LIMIT", 1)
        payload = bundle()
    assert zipfile.stringEndArchive64 in payload
    entries = inspect_source_bundle(payload, maximum_upload_bytes=10000, maximum_expanded_bytes=10000, maximum_entries=10)
    assert [entry.path for entry in entries] == ["dataset.csv", "metrics.csv"]


def test_trailing_bytes_and_truncated_comment_are_rejected():
    payload = bundle()
    with pytest.raises(SourceRejected):
        inspect_source_bundle(payload + b"trailing", maximum_upload_bytes=10000, maximum_expanded_bytes=10000, maximum_entries=10)
    changed = bytearray(payload)
    struct.pack_into("<H", changed, len(changed) - 2, 10)
    with pytest.raises(SourceRejected):
        inspect_source_bundle(bytes(changed), maximum_upload_bytes=10000, maximum_expanded_bytes=10000, maximum_entries=10)
