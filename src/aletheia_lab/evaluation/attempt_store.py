"""Public compatibility facade for the isolated immutable attempt store."""

from aletheia_lab.evaluation._attempt_store.contracts import (
    FAILURE_RECEIPT_SCHEMA_VERSION,
    STORE_ENTRY_SCHEMA_VERSION,
    STORE_RECEIPT_SCHEMA_VERSION,
    TERMINAL_INVENTORY_SCHEMA_VERSION,
    AttemptStoreConflictError,
    AttemptStoreError,
    AttemptStoreIntegrityError,
    AttemptStoreTransitionError,
    StoreClock,
    StoreLedgerEntry,
    StoreWriteReceipt,
    TechnicalFailureReceipt,
    TerminalExecutionInventory,
)
from aletheia_lab.evaluation._attempt_store.store import ImmutableAttemptStore

__all__ = [
    "FAILURE_RECEIPT_SCHEMA_VERSION",
    "STORE_ENTRY_SCHEMA_VERSION",
    "STORE_RECEIPT_SCHEMA_VERSION",
    "TERMINAL_INVENTORY_SCHEMA_VERSION",
    "AttemptStoreConflictError",
    "AttemptStoreError",
    "AttemptStoreIntegrityError",
    "AttemptStoreTransitionError",
    "ImmutableAttemptStore",
    "StoreClock",
    "StoreLedgerEntry",
    "StoreWriteReceipt",
    "TechnicalFailureReceipt",
    "TerminalExecutionInventory",
]
