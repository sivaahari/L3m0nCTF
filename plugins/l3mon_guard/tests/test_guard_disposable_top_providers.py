"""Tests for disposable domain blocklist safety and top provider preservation (SP5 F3).

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_guard/test_guard_disposable_top_providers.py
"""
from CTFd.plugins.l3mon_guard.policies.disposable import _load_domains, is_disposable_domain

TOP_20_FREE_PROVIDERS = (
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "msn.com",
    "yahoo.com",
    "ymail.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "proton.me",
    "protonmail.com",
    "aol.com",
    "zoho.com",
    "mail.com",
    "gmx.com",
    "gmx.net",
    "fastmail.com",
    "yandex.com",
)


def test_top_20_providers_never_blocked():
    """Verify that none of the top 20 major email providers are in the disposable domains blocklist."""
    loaded_domains = _load_domains()
    for provider in TOP_20_FREE_PROVIDERS:
        assert provider not in loaded_domains, f"Critical error: major provider {provider} is in disposable blocklist!"
        assert is_disposable_domain(f"player@{provider}") is False
        assert is_disposable_domain(provider) is False


def test_disposable_domain_and_subdomain_blocking():
    """Verify that listed disposable domains and their arbitrary subdomains are blocked."""
    # Direct domains
    assert is_disposable_domain("mailinator.com") is True
    assert is_disposable_domain("guerrillamail.com") is True
    assert is_disposable_domain("temp-mail.org") is True

    # Subdomains
    assert is_disposable_domain("sub.mailinator.com") is True
    assert is_disposable_domain("deep.nested.sub.guerrillamail.com") is True

    # Legit educational and corporate domains
    assert is_disposable_domain("amrita.edu") is False
    assert is_disposable_domain("cb.students.amrita.edu") is False
    assert is_disposable_domain("mit.edu") is False
    assert is_disposable_domain("google.com") is False


def test_the_domains_of_the_first_curated_list_stay_blocked():
    """Verify that well-known domains from the original curated list remain blocked."""
    first_curated_domains = (
        "tempmail.com",
        "tempmail.net",
        "tempmailaddress.com",
        "yopmail.org",
        "10minutemail.org",
        "spamgourmet.com",
        "disposable.com",
    )
    for domain in first_curated_domains:
        assert is_disposable_domain(f"user@{domain}") is True, f"{domain} must stay blocked"
        assert is_disposable_domain(f"sub.{domain}") is True, f"sub.{domain} must stay blocked"

