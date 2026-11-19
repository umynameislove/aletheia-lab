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
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", check],
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
