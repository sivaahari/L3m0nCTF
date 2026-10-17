"""Disposable email domains policy (SP5 B4.2).

Checks email domain and any subdomains against a local blocklist loaded once at startup.
"""
import os
import re

_DOMAINS: set[str] | None = None
_DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "disposable_domains.txt")


def _load_domains() -> set[str]:
    global _DOMAINS
    if _DOMAINS is not None:
        return _DOMAINS

    domains = set()
    if os.path.isfile(_DATA_PATH):
        with open(_DATA_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip().lower()
                if line and not line.startswith("#"):
                    domains.add(line)
    _DOMAINS = domains
    return _DOMAINS


def is_disposable_domain(email_or_domain: str) -> bool:
    """Return True if the domain or any parent domain is on the disposable blocklist."""
    if not email_or_domain:
        return False

    raw = email_or_domain.strip().lower()
    if "@" in raw:
        domain = raw.split("@", 1)[1]
    else:
        domain = raw

    domain = domain.strip().strip(".")
    if not domain:
        return False

    blocked = _load_domains()
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in blocked:
            return True

    return False
