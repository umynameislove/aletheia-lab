import hashlib
import io
import stat
import zipfile

import pytest

from aletheia_lab.hosted.source_bundle import SourceRejected, inspect_source_bundle


def bundle(items):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in items:
            if isinstance(name, str):
                info = zipfile.ZipInfo()
                info.filename = name
            else:
                info = name
            archive.writestr(info, data)
    return output.getvalue()


def inspect(data, **overrides):
    return inspect_source_bundle(data, **{"maximum_upload_bytes": 10000,
        "maximum_expanded_bytes": 10000, "maximum_entries": 10, **overrides})


def test_manifest_records_actual_content_hash_without_extracting():
    entries = inspect(bundle([("data/metrics.csv", b"metric,value\nloss,0.5\n")]))
    assert entries[0].path == "data/metrics.csv"
    assert entries[0].sha256 == hashlib.sha256(b"metric,value\nloss,0.5\n").hexdigest()


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/escape", "a\\b", "a/./b", "CON.txt", "a./b", "a//b"])
def test_unsafe_paths_are_rejected(name):
    with pytest.raises(SourceRejected):
        inspect(bundle([(name, b"data")]))


def test_expansion_count_duplicate_and_symlink_limits():
    for data, options in [
        (bundle([("a", b"x" * 1000)]), {"maximum_expanded_bytes": 100}),
        (bundle([("a", b"x"), ("b", b"x")]), {"maximum_entries": 1}),
        (bundle([("a", b"x"), ("A", b"x")]), {}),
    ]:
        with pytest.raises(SourceRejected):
            inspect(data, **options)
    link = zipfile.ZipInfo("link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(SourceRejected):
        inspect(bundle([(link, b"../target")]))


@pytest.mark.parametrize("items", [
    [(".git/config", b"untrusted")],
    [("data/.GIT/hooks/hook", b"untrusted")],
    [("data", b"file"), ("data/metrics.csv", b"content")],
    [("data/metrics.csv", b"content"), ("DATA", b"file")],
])
def test_git_metadata_and_file_directory_collisions_are_rejected(items):
    with pytest.raises(SourceRejected):
        inspect(bundle(items))
