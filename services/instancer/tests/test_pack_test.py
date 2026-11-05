"""Unit tests for services/instancer/tools/pack_test.py."""
from __future__ import annotations

from unittest import mock
import pytest

from services.instancer.tools.pack_test import (
    PackBenchmark,
    calculate_percentile,
    get_host_free_memory_mb,
)


def test_calculate_percentile():
    data = [10.0, 20.0, 30.0, 40.0, 50.0]
    # Median / 50th percentile
    assert calculate_percentile(data, 50.0) == 30.0
    # 0th and 100th
    assert calculate_percentile(data, 0.0) == 10.0
    assert calculate_percentile(data, 100.0) == 50.0
    # Empty list
    assert calculate_percentile([], 50.0) == 0.0


def test_host_free_memory_probe():
    mem_mb = get_host_free_memory_mb()
    assert isinstance(mem_mb, float)
    assert mem_mb > 0.0


def test_safety_check_memory_threshold():
    bench = PackBenchmark(min_free_mem_mb=1024, interactive=False)
    with mock.patch("services.instancer.tools.pack_test.get_host_free_memory_mb", return_value=256.0):
        with pytest.raises(RuntimeError, match="Host free memory .* is below safe margin"):
            bench.check_safety()


def test_interactive_prompt_cancellation():
    bench = PackBenchmark(min_free_mem_mb=128, interactive=True)
    with mock.patch("services.instancer.tools.pack_test.get_host_free_memory_mb", return_value=4096.0):
        with mock.patch("builtins.input", return_value="n"):
            with pytest.raises(SystemExit):
                bench.check_safety()


def test_benchmark_run_lifecycle_and_cleanup():
    bench = PackBenchmark(
        api_url="http://mock-agent:8300",
        token="test_token",
        slug="demo-web",
        count=3,
        min_free_mem_mb=128,
        interactive=False,
    )

    created_ids = []
    deleted_ids = []

    def fake_api_request(method, path, body=None):
        if method == "POST" and path == "/v1/instances":
            iid = f"testinst{len(created_ids) + 1:04d}"
            created_ids.append(iid)
            return 201, {"instance": {"id": iid, "container_id": f"cont_{iid}"}}
        elif method == "DELETE" and path.startswith("/v1/instances/"):
            iid = path.split("/")[-1]
            deleted_ids.append(iid)
            return 204, {}
        return 200, {}

    with mock.patch("services.instancer.tools.pack_test.get_host_free_memory_mb", return_value=4096.0):
        with mock.patch.object(bench, "_api_request", side_effect=fake_api_request):
            with mock.patch.object(bench, "query_container_stats", return_value=(15.5, 0.5)):
                with mock.patch("time.sleep"):  # Speed up test
                    summary = bench.run()

    assert summary["count"] == 3
    assert summary["avg_mem_mb"] == 15.5
    assert summary["avg_cpu_percent"] == 0.5
    assert summary["total_mem_mb"] == 46.5
    # Strict cleanup verification: every created instance must be deleted
    assert len(created_ids) == 3
    assert deleted_ids == created_ids
    assert len(bench.created_instance_ids) == 0  # Cleared after cleanup
