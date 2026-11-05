#!/usr/bin/env python3
"""L3m0nCTF Challenge Instancer Packing and Density Benchmark Tool (SP4 / F5).

Standard-library only tool to measure:
1. Container startup latency (p50, p95).
2. Per-instance memory and CPU overhead via docker stats.
3. Host packing density capacity.

Safety guarantees:
- Refuses to run if free memory is below margin (default: 512 MB).
- Interactively prompts for confirmation unless --yes is specified.
- Only stops and touches instances created during this test run.
- Reliably cleans up all created instances on completion or interrupt (SIGINT/SIGTERM).
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Optional


def get_host_free_memory_mb() -> float:
    """Read available memory from /proc/meminfo or Windows systeminfo / fallback."""
    if os.path.exists("/proc/meminfo"):
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        parts = line.split()
                        return float(parts[1]) / 1024.0  # kB to MB
        except Exception:
            pass

    # Fallback for Windows or systems without MemAvailable
    try:
        import ctypes
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return float(stat.ullAvailPhys) / (1024.0 * 1024.0)
    except Exception:
        pass

    # Conservative default if probe fails
    return 2048.0


def calculate_percentile(values: list[float], p: float) -> float:
    """Calculate the p-th percentile (0.0 to 100.0) of a list of numeric values."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    d = k - f
    return s[f] + d * (s[c] - s[f])


class PackBenchmark:
    def __init__(
        self,
        api_url: str = "http://127.0.0.1:8300",
        token: Optional[str] = None,
        slug: str = "demo-web",
        count: int = 5,
        min_free_mem_mb: int = 512,
        interactive: bool = True,
    ):
        self.api_url = api_url.rstrip("/")
        self.token = token or os.getenv("INSTANCER_TOKEN", "").strip()
        self.slug = slug
        self.count = count
        self.min_free_mem_mb = min_free_mem_mb
        self.interactive = interactive

        self.created_instance_ids: list[str] = []
        self._interrupted = False

    def check_safety(self):
        free_mb = get_host_free_memory_mb()
        if free_mb < self.min_free_mem_mb:
            raise RuntimeError(
                f"Safety check failed: Host free memory ({free_mb:.1f} MB) is below safe margin ({self.min_free_mem_mb} MB). Refusing to run."
            )

        if self.interactive:
            prompt = (
                f"\n=== Pack Test Safety Confirmation ===\n"
                f"Target URL:        {self.api_url}\n"
                f"Challenge Slug:    {self.slug}\n"
                f"Number of instances: {self.count}\n"
                f"Available Memory:  {free_mb:.1f} MB\n"
                f"Are you sure you want to proceed? [y/N]: "
            )
            resp = input(prompt).strip().lower()
            if resp not in ("y", "yes"):
                print("Benchmark cancelled by user.")
                sys.exit(0)

    def _api_request(self, method: str, path: str, body: Optional[dict] = None) -> tuple[int, dict]:
        url = f"{self.api_url}{path}"
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                content = resp.read()
                return resp.status, json.loads(content.decode("utf-8")) if content else {}
        except urllib.error.HTTPError as e:
            content = e.read()
            parsed = {}
            if content:
                try:
                    parsed = json.loads(content.decode("utf-8"))
                except Exception:
                    pass
            raise RuntimeError(f"API request {method} {path} failed with HTTP {e.code}: {parsed}")

    def query_container_stats(self, container_id: str) -> tuple[float, float]:
        """Query memory (MB) and CPU (%) from docker stats --no-stream."""
        try:
            cmd = ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}\t{{.CPUPerc}}", container_id]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode == 0 and res.stdout.strip():
                parts = res.stdout.strip().split("\t")
                # Parse memory usage: e.g. "12.5MiB / 256MiB"
                mem_part = parts[0].split("/")[0].strip()
                mem_mb = 0.0
                if "gib" in mem_part.lower():
                    mem_mb = float(mem_part.lower().replace("gib", "").replace("gb", "")) * 1024.0
                elif "mib" in mem_part.lower():
                    mem_mb = float(mem_part.lower().replace("mib", "").replace("mb", ""))
                elif "kib" in mem_part.lower():
                    mem_mb = float(mem_part.lower().replace("kib", "").replace("kb", "")) / 1024.0

                cpu_str = parts[1].replace("%", "").strip() if len(parts) > 1 else "0"
                cpu_pct = float(cpu_str)
                return mem_mb, cpu_pct
        except Exception:
            pass
        return 0.0, 0.0

    def cleanup(self):
        """Stop only instances created during this test."""
        if not self.created_instance_ids:
            return
        print(f"\nCleaning up {len(self.created_instance_ids)} test instance(s)...")
        for iid in self.created_instance_ids:
            try:
                self._api_request("DELETE", f"/v1/instances/{iid}")
            except Exception as e:
                print(f"Warning: Failed to stop instance {iid}: {e}")
        self.created_instance_ids.clear()
        print("Cleanup complete.")

    def run(self) -> dict:
        self.check_safety()

        def _handle_signal(signum, frame):
            print("\nBenchmark interrupted by signal! Initiating cleanup...")
            self._interrupted = True
            self.cleanup()
            sys.exit(1)

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

        results = []
        print(f"\nStarting packing benchmark: N={self.count}, slug='{self.slug}'")
        print("-" * 72)
        print(f"{'#':<4} | {'Instance ID':<16} | {'Start (s)':<10} | {'Mem (MB)':<10} | {'CPU (%)':<8}")
        print("-" * 72)

        start_latencies: list[float] = []
        mem_usages: list[float] = []
        cpu_usages: list[float] = []

        try:
            for i in range(1, self.count + 1):
                if self._interrupted:
                    break

                team_id = 90000 + i
                challenge_id = 9999
                t0 = time.time()
                status, data = self._api_request(
                    "POST",
                    "/v1/instances",
                    {
                        "team_id": team_id,
                        "challenge_id": challenge_id,
                        "slug": self.slug,
                        "flag": "FLAG_PACK_TEST",
                        "ttl_seconds": 600,
                    },
                )
                t_latency = time.time() - t0
                inst = data.get("instance", {})
                iid = inst.get("id", f"unknown-{i}")
                self.created_instance_ids.append(iid)

                container_id = inst.get("container_id", iid)
                time.sleep(0.5)  # Allow container to settle before querying stats
                mem_mb, cpu_pct = self.query_container_stats(container_id)

                start_latencies.append(t_latency)
                mem_usages.append(mem_mb)
                cpu_usages.append(cpu_pct)

                print(f"{i:<4} | {iid[:16]:<16} | {t_latency:<10.3f} | {mem_mb:<10.2f} | {cpu_pct:<8.1f}")
                results.append({"i": i, "id": iid, "latency": t_latency, "mem_mb": mem_mb, "cpu_pct": cpu_pct})

        finally:
            self.cleanup()

        p50_latency = calculate_percentile(start_latencies, 50.0)
        p95_latency = calculate_percentile(start_latencies, 95.0)
        avg_mem = sum(mem_usages) / len(mem_usages) if mem_usages else 0.0
        avg_cpu = sum(cpu_usages) / len(cpu_usages) if cpu_usages else 0.0

        summary = {
            "count": len(start_latencies),
            "p50_start_seconds": round(p50_latency, 3),
            "p95_start_seconds": round(p95_latency, 3),
            "avg_mem_mb": round(avg_mem, 2),
            "avg_cpu_percent": round(avg_cpu, 2),
            "total_mem_mb": round(sum(mem_usages), 2),
        }

        print("=" * 72)
        print("Benchmark Summary:")
        print(f"Total instances started: {summary['count']}")
        print(f"p50 startup latency:     {summary['p50_start_seconds']} s")
        print(f"p95 startup latency:     {summary['p95_start_seconds']} s")
        print(f"Average memory / inst:   {summary['avg_mem_mb']} MB")
        print(f"Average CPU / inst:      {summary['avg_cpu_percent']} %")
        print(f"Total container memory:  {summary['total_mem_mb']} MB")
        print("=" * 72)

        return summary


def main():
    parser = argparse.ArgumentParser(description="L3m0nCTF Challenge Instancer Packing Benchmark")
    parser.add_argument("--url", default="http://127.0.0.1:8300", help="Instancer API endpoint")
    parser.add_argument("--token", default=None, help="Instancer bearer token")
    parser.add_argument("--slug", default="demo-web", help="Challenge slug to start")
    parser.add_argument("--count", type=int, default=5, help="Number of instances to start sequentially")
    parser.add_argument("--min-free-mem-mb", type=int, default=512, help="Safety check minimum free memory margin in MB")
    parser.add_argument("--yes", action="store_true", help="Skip interactive confirmation prompt")
    args = parser.parse_args()

    bench = PackBenchmark(
        api_url=args.url,
        token=args.token,
        slug=args.slug,
        count=args.count,
        min_free_mem_mb=args.min_free_mem_mb,
        interactive=not args.yes,
    )
    bench.run()


if __name__ == "__main__":
    main()
