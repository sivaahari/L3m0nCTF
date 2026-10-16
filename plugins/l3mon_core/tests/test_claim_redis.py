"""`claim` on a real Redis, through the same Flask-Caching backend class CTFd uses (found by the audit of 3.7, which could only use a stand-in client).

cachelib's Redis `add` is two commands: SETNX, then EXPIRE. When the connection drops between them the key is left with NO lifetime, and Redis's
volatile-lru policy never evicts such a key, so a job guarded by a plain `add` would never run again. `claim` stores the time it was taken and treats a
flag older than its period as free.

    tools/run-migration-test.sh -- /l3mon_tests/l3mon_core/test_claim_redis.py
"""
import os
import threading
import time
from types import SimpleNamespace
from unittest import mock

import pytest

from CTFd.plugins.l3mon_core import claim as claim_module

pytestmark = pytest.mark.skipif(not os.getenv("L3MON_TEST_REDIS_URL"), reason="needs a real Redis (tools/run-migration-test.sh)")

PREFIX = "flask_cache_"
KEY = "l3mon:test:claim-redis"


@pytest.fixture()
def real():
    from flask_caching.backends.rediscache import RedisCache
    from redis import Redis

    client = Redis.from_url(os.environ["L3MON_TEST_REDIS_URL"])
    client.flushdb()
    backend = RedisCache(host=client, key_prefix=PREFIX, default_timeout=300)
    shim = SimpleNamespace(get=backend.get, add=backend.add, set=backend.set)
    with mock.patch.object(claim_module, "cache", shim):
        yield client, backend
    client.flushdb()


def test_eight_workers_claiming_at_once_get_it_once_and_it_has_a_lifetime(real):
    client, _ = real
    barrier, results = threading.Barrier(8), []

    def worker():
        barrier.wait()
        results.append(claim_module.claim(KEY, 30))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1 and results.count(False) == 7
    assert 0 < client.ttl(PREFIX + KEY) <= 30


def test_a_flag_left_without_a_lifetime_by_a_lost_expire_is_taken_over_once_it_is_old_enough(real):
    client, backend = real
    calls = []

    def lost(*args, **kwargs):
        calls.append(args)
        raise ConnectionError("the connection dropped between SETNX and EXPIRE")

    with mock.patch.object(backend._write_client, "expire", side_effect=lost):
        with pytest.raises(ConnectionError):
            claim_module.claim(KEY, 30)
    assert calls and client.exists(PREFIX + KEY) == 1
    assert client.ttl(PREFIX + KEY) == -1, "this is the state the audit reproduced: a key that never expires and, under volatile-lru, is never evicted"
    assert claim_module.claim(KEY, 30) is False, "just taken: still held"
    later = time.time() + 31
    with mock.patch.object(claim_module.time, "time", return_value=later):
        assert claim_module.claim(KEY, 30) is True, "older than its period: taken over"
    assert 0 < client.ttl(PREFIX + KEY) <= 30, "and it has a lifetime again"


def test_a_flag_that_expires_by_itself_is_free_again(real):
    client, _ = real
    assert claim_module.claim(KEY, 1) is True
    time.sleep(1.2)
    assert client.exists(PREFIX + KEY) == 0
    assert claim_module.claim(KEY, 1) is True
