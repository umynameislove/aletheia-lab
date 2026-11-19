"""Keep large canonical snapshots out of subprocess pipes without widening report limits."""
from __future__ import annotations

from uuid import uuid4

from .async_work import run_blocking
from .worker_limits import MAX_INPUT_BYTES, MAX_UPLOAD_OUTPUT_BYTES
from .worker_payloads import cleanup_exchange, create_exchange, read_output
from .worker_supervisor import UploadWorkerSupervisor, WorkerRejected


class PayloadUploadWorkerSupervisor(UploadWorkerSupervisor):
    maximum_payload_bytes = MAX_UPLOAD_OUTPUT_BYTES

    async def run_upload(self, payload: bytes) -> bytes:
        if not isinstance(payload, bytes) or len(payload) > self.maximum_payload_bytes:
            raise WorkerRejected("worker_input_too_large")
        if len(payload) <= MAX_INPUT_BYTES:
            return await super().run_upload(payload)
        exchange = uuid4().hex
        try:
            reference = await run_blocking(create_exchange, self._private_root, exchange, payload)
            envelope = await super().run_upload(reference)
            return await run_blocking(read_output, self._private_root, exchange, envelope)
        except (ValueError, OSError):
            raise WorkerRejected("worker_payload_invalid") from None
        finally:
            await run_blocking(cleanup_exchange, self._private_root, exchange)
