"""One worker at a time, through the cache: `claim(key, seconds)`.

Several jobs run "once every so often, by whichever request notices first" (the minute check of the dynamic values, the scheduler's reconcile). The flag
is a cache key that lasts `seconds`. cachelib's Redis `add` is two commands, SETNX and then EXPIRE: if the connection drops between them, or the worker
dies, the key stays with NO lifetime and the job would never run again (found by the audit of 3.7; a key without a lifetime is not even evicted under
Redis's volatile-lru policy). So the flag holds the time it was taken, and a flag older than its lifetime counts as free whatever the cache says.
"""
import time

from CTFd.cache import cache


def claim(key: str, seconds: float) -> bool:
    """True when the caller may do the job now. False when somebody holds the flag (taken less than `seconds` ago).

    A value that is not a time (an older flag, a test's) counts as held: it will expire by itself. A time from the future (the clocks of two machines
    disagree) or older than `seconds` is a flag whose lifetime was lost: it is taken over."""
    now = time.time()
    held = cache.get(key)
    if held is None:
        return bool(cache.add(key, now, timeout=seconds))
    if isinstance(held, float) and not 0 <= now - held < seconds:
        cache.set(key, now, timeout=seconds)
        return True
    return False
