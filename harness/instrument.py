"""httpx-level instrumentation: records every application-layer request/response
with timing, sizes, and body captures, tagged with the current handshake phase.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

BODY_CAPTURE_CAP = 256 * 1024


@dataclass
class RequestRecord:
    seq: int
    phase: str
    method: str
    path: str
    t_start_ns: int = 0
    t_headers_ns: int | None = None
    t_end_ns: int | None = None
    status: int | None = None
    req_body_bytes: int = 0
    resp_body_bytes: int = 0
    req_wire_bytes: int = 0
    resp_header_bytes: int = 0
    content_type: str = ""
    req_body: bytes = b""
    resp_body: bytes = b""
    error: str | None = None

    def to_dict(self, t0_ns: int) -> dict:
        return {
            "seq": self.seq,
            "phase": self.phase,
            "method": self.method,
            "path": self.path,
            "status": self.status,
            "t_offset_ms": (self.t_start_ns - t0_ns) / 1e6,
            "ttfb_ms": (self.t_headers_ns - self.t_start_ns) / 1e6
            if self.t_headers_ns
            else None,
            "dur_ms": (self.t_end_ns - self.t_start_ns) / 1e6 if self.t_end_ns else None,
            "req_body_bytes": self.req_body_bytes,
            "resp_body_bytes": self.resp_body_bytes,
            "req_wire_bytes": self.req_wire_bytes,
            "resp_header_bytes": self.resp_header_bytes,
            "content_type": self.content_type,
            "error": self.error,
        }


class Recorder:
    """Collects RequestRecords; the runner sets the current phase label."""

    def __init__(self) -> None:
        self.records: list[RequestRecord] = []
        self.phase = "unlabeled"
        self._seq = 0

    def set_phase(self, phase: str) -> None:
        self.phase = phase

    def new_record(self, method: str, path: str) -> RequestRecord:
        self._seq += 1
        rec = RequestRecord(seq=self._seq, phase=self.phase, method=method, path=path)
        self.records.append(rec)
        return rec

    def handshake_records(self) -> list[RequestRecord]:
        """Records belonging to the handshake proper (not validation/teardown)."""
        return [
            r
            for r in self.records
            if r.phase not in ("task-execution", "teardown", "unlabeled")
        ]


class _CountingStream(httpx.AsyncByteStream):
    def __init__(self, inner, record: RequestRecord):
        self._inner = inner
        self._record = record

    async def __aiter__(self):
        try:
            async for chunk in self._inner:
                self._record.resp_body_bytes += len(chunk)
                if len(self._record.resp_body) < BODY_CAPTURE_CAP:
                    self._record.resp_body += chunk
                self._record.t_end_ns = time.perf_counter_ns()
                yield chunk
        except Exception as exc:
            self._record.error = f"{type(exc).__name__}: {exc}"
            self._record.t_end_ns = time.perf_counter_ns()
            raise

    async def aclose(self) -> None:
        if self._record.t_end_ns is None:
            self._record.t_end_ns = time.perf_counter_ns()
        await self._inner.aclose()


def _estimate_request_wire_bytes(request: httpx.Request, body_len: int) -> int:
    line = len(request.method) + len(request.url.raw_path) + len(b" HTTP/1.1\r\n") + 1
    headers = sum(len(k) + len(v) + 4 for k, v in request.headers.raw) + 2
    return line + headers + body_len


def _estimate_response_header_bytes(response: httpx.Response) -> int:
    line = len(b"HTTP/1.1 200 OK\r\n")
    headers = sum(len(k) + len(v) + 4 for k, v in response.headers.raw) + 2
    return line + headers


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, recorder: Recorder, inner: httpx.AsyncBaseTransport | None = None):
        self.recorder = recorder
        self.inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        rec = self.recorder.new_record(request.method, request.url.raw_path.decode())
        try:
            body = request.content
        except Exception:
            body = b""
        rec.req_body_bytes = len(body)
        rec.req_body = body[:BODY_CAPTURE_CAP]
        rec.req_wire_bytes = _estimate_request_wire_bytes(request, len(body))
        rec.t_start_ns = time.perf_counter_ns()
        try:
            response = await self.inner.handle_async_request(request)
        except Exception as exc:
            rec.error = f"{type(exc).__name__}: {exc}"
            rec.t_end_ns = time.perf_counter_ns()
            raise
        rec.t_headers_ns = time.perf_counter_ns()
        rec.status = response.status_code
        rec.content_type = response.headers.get("content-type", "")
        rec.resp_header_bytes = _estimate_response_header_bytes(response)
        response.stream = _CountingStream(response.stream, rec)
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()


def make_client(
    recorder: Recorder, timeout: float = 30.0, base_url: str = ""
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=RecordingTransport(recorder),
        timeout=timeout,
        follow_redirects=True,
        base_url=base_url,
    )
