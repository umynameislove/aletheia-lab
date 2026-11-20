"""Report workers load only the parsing dependencies their formats require."""

import os
import subprocess
import sys
from pathlib import Path


def test_report_import_keeps_parquet_dependencies_unloaded():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import sys; import aletheia_lab.hosted.worker; "
            "assert 'pyarrow' not in sys.modules; "
            "assert 'numpy' not in sys.modules",
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
