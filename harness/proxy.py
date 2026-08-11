"""Byte-counting TCP proxy with optional per-direction forwarding delay.

Used for (a) exact wire-byte accounting per run and (b) emulating network RTT
on localhost: a delay of d seconds applied to each direction adds ~2d to every
application-layer request/response exchange, approximating a 2d RTT link.
TCP connection establishment against the proxy itself completes at localhost
speed; we therefore report connection counts separately so transport-level
setup cost can be accounted for analytically (see paper, Methodology).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class ProxyStats:
    bytes_c2s: int = 0
    bytes_s2c: int = 0
    connections: int = 0
    chunks_c2s: int = 0
    chunks_s2c: int = 0


class CountingProxy:
    def __init__(
        self,
        target_host: str,
        target_port: int,
        delay_c2s: float = 0.0,
        delay_s2c: float = 0.0,
    ):
        self.target_host = target_host
        self.target_port = target_port
        self.delay_c2s = delay_c2s
        self.delay_s2c = delay_s2c
        self.stats = ProxyStats()
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task] = set()
        self.port: int | None = None

    async def start(self) -> int:
        self._server = await asyncio.start_server(
            self._handle, host="127.0.0.1", port=0
        )
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def reset(self) -> None:
        self.stats = ProxyStats()

    def snapshot(self) -> ProxyStats:
        return ProxyStats(**self.stats.__dict__)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.stats.connections += 1
        try:
            up_reader, up_writer = await asyncio.open_connection(
                self.target_host, self.target_port
            )
        except OSError:
            writer.close()
            return

        async def pump(src, dst, delay, direction):
            try:
                while True:
                    chunk = await src.read(65536)
                    if not chunk:
                        break
                    if delay > 0:
                        await asyncio.sleep(delay)
                    if direction == "c2s":
                        self.stats.bytes_c2s += len(chunk)
                        self.stats.chunks_c2s += 1
                    else:
                        self.stats.bytes_s2c += len(chunk)
                        self.stats.chunks_s2c += 1
                    dst.write(chunk)
                    await dst.drain()
            except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                pass
            finally:
                try:
                    dst.write_eof()
                except OSError:
                    pass

        t1 = asyncio.create_task(pump(reader, up_writer, self.delay_c2s, "c2s"))
        t2 = asyncio.create_task(pump(up_reader, writer, self.delay_s2c, "s2c"))
        self._tasks.update({t1, t2})
        t1.add_done_callback(self._tasks.discard)
        t2.add_done_callback(self._tasks.discard)
        await asyncio.gather(t1, t2, return_exceptions=True)
        for w in (writer, up_writer):
            try:
                w.close()
            except Exception:
                pass
