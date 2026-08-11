"""Transport-layer baselines: TCP connect and TLS 1.3 handshake on loopback.

These give the reference point requested in the paper: what does the layer
below the agent protocols charge for connection setup? Reported separately
from the application-layer handshake measurements. Under an emulated RTT of
r, TCP adds one round trip and TLS 1.3 one more; we measure the loopback
compute cost here and report the RTT arithmetic analytically.

  python -m harness.baseline --n 50 --out results/raw/baseline.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import subprocess
import tempfile
import threading
import time


def tcp_connect_times(port: int, n: int) -> list[float]:
    times = []
    for _ in range(n):
        t0 = time.perf_counter_ns()
        with socket.create_connection(("127.0.0.1", port), timeout=5):
            t1 = time.perf_counter_ns()
        times.append((t1 - t0) / 1e6)
    return times


def make_cert(tmp: str) -> tuple[str, str]:
    cert, key = os.path.join(tmp, "cert.pem"), os.path.join(tmp, "key.pem")
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", key, "-out", cert, "-days", "1", "-subj", "/CN=localhost",
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


def tls_server(port: int, cert: str, key: str, stop: threading.Event) -> None:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_cert_chain(cert, key)
    with socket.create_server(("127.0.0.1", port)) as srv:
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except TimeoutError:
                continue
            try:
                with ctx.wrap_socket(conn, server_side=True):
                    pass
            except Exception:
                pass


def tls_handshake_times(port: int, n: int, cert: str) -> list[float]:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_verify_locations(cert)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    times = []
    for _ in range(n):
        raw = socket.create_connection(("127.0.0.1", port), timeout=5)
        t0 = time.perf_counter_ns()
        with ctx.wrap_socket(raw, server_hostname="localhost"):
            t1 = time.perf_counter_ns()
        times.append((t1 - t0) / 1e6)
        time.sleep(0.005)
    return times


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--out", default="results/raw/baseline.jsonl")
    args = ap.parse_args()

    port = 9450
    rows = []

    # TCP: measure against a plain listener
    stop = threading.Event()
    listener = socket.create_server(("127.0.0.1", port))
    accepts = threading.Thread(
        target=lambda: [
            c[0].close()
            for c in iter(
                lambda: listener.accept() if not stop.is_set() else None, None
            )
        ],
        daemon=True,
    )
    accepts.start()
    for ms in tcp_connect_times(port, args.n):
        rows.append({"baseline": "tcp-connect", "network": "local", "ms": ms})
    stop.set()
    listener.close()

    # TLS 1.3
    with tempfile.TemporaryDirectory() as tmp:
        cert, key = make_cert(tmp)
        stop2 = threading.Event()
        port2 = port + 1
        th = threading.Thread(
            target=tls_server, args=(port2, cert, key, stop2), daemon=True
        )
        th.start()
        time.sleep(0.5)
        for ms in tls_handshake_times(port2, args.n, cert):
            rows.append({"baseline": "tls13-handshake", "network": "local", "ms": ms})
        stop2.set()
        th.join(timeout=2)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    for name in ("tcp-connect", "tls13-handshake"):
        vals = sorted(r["ms"] for r in rows if r["baseline"] == name)
        print(
            f"{name}: n={len(vals)} median={vals[len(vals) // 2]:.3f}ms "
            f"p95={vals[int(len(vals) * 0.95)]:.3f}ms"
        )


if __name__ == "__main__":
    main()
