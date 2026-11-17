"""Header deadlines around Uvicorn's unchanged, pinned h11 request parser."""

from __future__ import annotations

import asyncio
import math
from typing import cast

import h11
from uvicorn.protocols.http.h11_impl import H11Protocol


class _HeaderDeadlineProtocol(H11Protocol):
    _header_timeout_seconds: float
    _header_timer: asyncio.TimerHandle | None = None

    def _arm_header_timer(self) -> None:
        if self._header_timer is None:
            self._header_timer = self.loop.call_later(self._header_timeout_seconds, self._headers_expired)

    def _cancel_header_timer(self) -> None:
        if self._header_timer is not None:
            self._header_timer.cancel()
            self._header_timer = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        super().connection_made(cast(asyncio.Transport, transport))
        self._arm_header_timer()

    def handle_events(self) -> None:
        try:
            super().handle_events()
        finally:
            if self.transport.is_closing() or self.conn.their_state is not h11.IDLE:
                self._cancel_header_timer()
            elif self.conn.trailing_data[0]:
                # A keep-alive/pipelined head starts at its first buffered byte.
                self._arm_header_timer()

    def _headers_expired(self) -> None:
        self._header_timer = None
        if self.transport.is_closing() or self.conn.their_state is not h11.IDLE:
            return
        body = b"Request headers were not completed in time."
        try:
            events = (
                h11.Response(status_code=408, headers=[
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"), (b"connection", b"close"),
                ]),
                h11.Data(data=body), h11.EndOfMessage(),
            )
            for event in events:
                self.transport.write(self.conn.send(event))
        except h11.LocalProtocolError:
            pass
        finally:
            self._unset_keepalive_if_required()
            self.transport.close()

    def connection_lost(self, exc: Exception | None) -> None:
        self._cancel_header_timer()
        super().connection_lost(exc)

    def shutdown(self) -> None:
        self._cancel_header_timer()
        super().shutdown()


def header_deadline_protocol(timeout_seconds: float) -> type[H11Protocol]:
    """Bound each incomplete request head without extending the timer on data."""
    if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("A finite positive request-header deadline is required.")

    class HeaderDeadlineH11Protocol(_HeaderDeadlineProtocol):
        _header_timeout_seconds = timeout_seconds

    return HeaderDeadlineH11Protocol
