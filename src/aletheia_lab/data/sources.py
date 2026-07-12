"""Pinned dataset sources for reproducible benchmarks.

Every dataset the benchmark depends on is pinned here by URL and SHA-256 so a
download reproduces the file byte-for-byte. ``configs/project.yaml`` selects a
dataset by ``id``; the exact source and checksum live here, next to the code,
so the pin is version-controlled and reviewed like any other change rather than
buried in config.

To move to a different dataset, add a ``DatasetSource`` and point
``dataset.id`` in the project config at it.
"""

from __future__ import annotations

from dataclasses import dataclass

# Column order of the raw Telco CSV, used to validate a download before prep.
_TELCO_COLUMNS: tuple[str, ...] = (
    "customerID",
    "gender",
    "SeniorCitizen",
    "Partner",
    "Dependents",
    "tenure",
    "PhoneService",
    "MultipleLines",
    "InternetService",
    "OnlineSecurity",
    "OnlineBackup",
    "DeviceProtection",
    "TechSupport",
    "StreamingTV",
    "StreamingMovies",
    "Contract",
    "PaperlessBilling",
    "PaymentMethod",
    "MonthlyCharges",
    "TotalCharges",
    "Churn",
)


@dataclass(frozen=True)
class DatasetSource:
    """A single, checksum-pinned dataset the benchmark can reproduce.

    ``sha256`` is the acceptance contract: a download is accepted only if its
    digest matches, so the dataset is byte-for-byte reproducible.
    """

    dataset_id: str
    url: str
    sha256: str
    n_bytes: int
    n_rows: int
    columns: tuple[str, ...]
    target: str
    drift_feature: str
    license: str
    filename: str


TELCO_CUSTOMER_CHURN = DatasetSource(
    dataset_id="telco_customer_churn",
    url=(
        "https://raw.githubusercontent.com/IBM/telco-customer-churn-on-icp4d/"
        "master/data/Telco-Customer-Churn.csv"
    ),
    sha256="16320c9c1ec72448db59aa0a26a0b95401046bef5d02fd3aeb906448e3055e91",
    n_bytes=970457,
    n_rows=7043,
    columns=_TELCO_COLUMNS,
    target="Churn",
    drift_feature="Contract",
    license="IBM sample data, academic/educational use (confirm with advisor)",
    filename="telco_customer_churn_raw.csv",
)


DATASETS: dict[str, DatasetSource] = {
    TELCO_CUSTOMER_CHURN.dataset_id: TELCO_CUSTOMER_CHURN,
}


def get_source(dataset_id: str) -> DatasetSource:
    """Return the pinned source for ``dataset_id`` or fail with a clear message."""

    try:
        return DATASETS[dataset_id]
    except KeyError:
        known = ", ".join(sorted(DATASETS)) or "(none)"
        msg = f"no pinned source for dataset_id {dataset_id!r}; known: {known}"
        raise KeyError(msg) from None
