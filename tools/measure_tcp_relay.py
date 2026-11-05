#!/usr/bin/env python3
"""Measure TCP relay throughput and timing under default rate limits.

Default instancer router rate limiting:
  INSTANCER_TCP_RATE_LIMIT_BPS: 2,097,152 (2 MB/s)
  INSTANCER_TCP_RATE_LIMIT_BURST: 4,194,304 (4 MB)

For a 10 MB payload:
  Initial burst allowance: 4 MB (instantaneous)
  Remaining bytes: 6 MB at 2 MB/s -> ~3.00 seconds.
  Expected elapsed wall time: ~3.0 - 3.3 seconds.

Usage:
  python tools/measure_tcp_relay.py [--mb 10]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import socket
import sys
import time

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.instancer.router.router import (
    DEFAULT_TCP_RATE_LIMIT_BPS,
    DEFAULT_TCP_RATE_LIMIT_BURST,
    InstanceRouter,
)


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def run_measurement(total_bytes: int, rate_bps: int, burst_bytes: int) -> tuple[float, float, bool]:
    relay_port = get_free_port()
    backend_port = get_free_port()

    # Echo backend
    async def echo_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    backend_server = await asyncio.start_server(echo_handler, "127.0.0.1", backend_port)

    router = InstanceRouter(
        instance_domain="measure.local",
        instancer_url="http://127.0.0.1:8300",
        router_token="measure-token",
        tcp_port_start=relay_port,
        tcp_port_count=10,
        tcp_rate_limit_bps=rate_bps,
        tcp_rate_limit_burst=burst_bytes,
        instance_pool="127.0.0.0/8",
        allow_loopback_for_tests=True,
    )

    try:
        await router.sync_tcp_routes([
            {
                "id": "inst_measure",
                "protocol": "tcp",
                "port": relay_port,
                "target_ip": "127.0.0.1",
                "target_port": backend_port,
            }
        ])

        # Connect client through relay
        reader, writer = await asyncio.open_connection("127.0.0.1", relay_port)

        chunk_size = 65536
        chunk = b"L3M0N_BENCHMARK_" * 4096  # 64 KB pattern
        sent_hasher = hashlib.sha256()
        recv_hasher = hashlib.sha256()

        t_start = time.perf_counter()

        async def sender():
            bytes_sent = 0
            while bytes_sent < total_bytes:
                to_send = min(chunk_size, total_bytes - bytes_sent)
                data = chunk[:to_send]
                writer.write(data)
                sent_hasher.update(data)
                await writer.drain()
                bytes_sent += to_send
            writer.write_eof()
            await writer.drain()

        async def receiver() -> int:
            bytes_received = 0
            while True:
                data = await reader.read(chunk_size)
                if not data:
                    break
                recv_hasher.update(data)
                bytes_received += len(data)
            return bytes_received

        _, bytes_received = await asyncio.gather(sender(), receiver())
        t_end = time.perf_counter()

        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

        elapsed = t_end - t_start
        mb_transferred = bytes_received / (1024 * 1024)
        throughput_mb_s = mb_transferred / elapsed if elapsed > 0 else 0.0
        intact = (sent_hasher.digest() == recv_hasher.digest()) and (bytes_received == total_bytes)

        return elapsed, throughput_mb_s, intact

    finally:
        await router.close_all_tcp_listeners()
        backend_server.close()
        await backend_server.wait_closed()


def main():
    parser = argparse.ArgumentParser(description="Measure TCP relay rate limiting performance.")
    parser.add_argument("--mb", type=float, default=10.0, help="Megabytes to transfer (default: 10)")
    parser.add_argument("--rate-kb", type=int, default=2048, help="Rate limit in KB/s (default: 2048 KB/s = 2 MB/s)")
    parser.add_argument("--burst-kb", type=int, default=4096, help="Burst allowance in KB (default: 4096 KB = 4 MB)")
    args = parser.parse_args()

    total_bytes = int(args.mb * 1024 * 1024)
    rate_bps = args.rate_kb * 1024
    burst_bytes = args.burst_kb * 1024

    print("=" * 60)
    print(" L3m0nCTF TCP Relay Throughput & Timing Benchmark")
    print("=" * 60)
    print(f"Payload Size:     {args.mb:.2f} MB ({total_bytes:,} bytes)")
    print(f"Rate Limit:       {args.rate_kb:,} KB/s ({rate_bps:,} B/s)")
    print(f"Burst Allowance:  {args.burst_kb:,} KB ({burst_bytes:,} bytes)")
    theoretical_time = max(0.0, (total_bytes - burst_bytes) / rate_bps)
    print(f"Theoretical Min:  {theoretical_time:.3f} seconds")
    print("-" * 60)
    print("Executing benchmark through live InstanceRouter relay...")

    elapsed, throughput, intact = asyncio.run(run_measurement(total_bytes, rate_bps, burst_bytes))

    print(f"Measured Time:    {elapsed:.3f} seconds")
    print(f"Relay Throughput: {throughput:.2f} MB/s")
    print(f"Data Integrity:   {'PASSED (SHA-256 match)' if intact else 'FAILED'}")
    print("=" * 60)

    if not intact:
        print("ERROR: Transferred payload was corrupted or incomplete!", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
