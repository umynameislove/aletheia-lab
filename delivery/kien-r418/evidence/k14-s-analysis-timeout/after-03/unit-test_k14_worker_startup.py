"""One-shot hosted workers must not initialize the unused offline facade."""

import os
import subprocess
import sys
from pathlib import Path


def test_upload_worker_avoids_offline_service_startup_but_keeps_public_exports():
    root = Path(__file__).resolve().parents[2]
    check = """
import sys
import aletheia_lab.hosted.upload_worker
import aletheia_lab.product as product
assert 'aletheia_lab.product.service' not in sys.modules
assert 'aletheia_lab.project.closeout' not in sys.modules
assert 'ProductService' in dir(product)
from aletheia_lab.product import ProductError, ProductService
from aletheia_lab.product.boundary import ProductError as Error
from aletheia_lab.product.service import ProductService as Service
assert ProductError is Error and ProductService is Service
assert product.ProductService is Service
exports = {}
exec('from aletheia_lab.product import *', exports)
assert exports['ProductService'] is Service and exports['ProductError'] is Error
try:
    product.unknown_product_export
except AttributeError:
    pass
else:
    raise AssertionError('unknown public attribute accepted')
import aletheia_lab.project as project
from aletheia_lab.project import ProjectStore, canonical_project_sha256
from aletheia_lab.project.persistence import ProjectStore as Store
from aletheia_lab.project.identity import canonical_project_sha256 as digest
assert ProjectStore is Store and canonical_project_sha256 is digest
assert set(project.__all__) <= set(dir(project))
exec('from aletheia_lab.project import *', exports)
assert all(exports[name] is getattr(project, name) for name in project.__all__)
try:
    project.unknown_project_export
except AttributeError:
    pass
else:
    raise AssertionError('unknown project attribute accepted')
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", check],
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
