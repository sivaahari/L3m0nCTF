"""Regenerate deploy/nginx/cloudflare-realip.conf from Cloudflare's published address ranges (standard library only).

    python tools/update_cloudflare_ips.py

nginx trusts the CF-Connecting-IP header only from these ranges, so a visitor who reaches the server directly can never
pretend to be someone else. Run it before each deployment and review the diff.
"""
from __future__ import annotations

import ipaddress
import sys
import urllib.request
from datetime import date
from pathlib import Path

SOURCES = ("https://www.cloudflare.com/ips-v4/", "https://www.cloudflare.com/ips-v6/")
TARGET = Path(__file__).resolve().parents[1] / "deploy" / "nginx" / "snippets" / "cloudflare-realip.conf"


def fetch(url: str) -> list[str]:
    # Cloudflare refuses the default Python user agent, so say who is asking
    request = urllib.request.Request(url, headers={"User-Agent": "l3mon-platform-tools/1.0 (+https://github.com/sivaahari/L3m0nCTF)"})
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - fixed https URLs
        text = response.read().decode("ascii")
    ranges = [line.strip() for line in text.splitlines() if line.strip()]
    for item in ranges:
        ipaddress.ip_network(item)  # raises on anything that is not an address range
    return ranges


def render(ranges: list[str]) -> str:
    lines = [
        "# Cloudflare's address ranges, from " + " and ".join(SOURCES),
        f"# Generated {date.today().isoformat()} by tools/update_cloudflare_ips.py. Do not edit by hand.",
        "",
    ]
    lines += [f"set_real_ip_from {r};" for r in ranges]
    lines += ["", "real_ip_header CF-Connecting-IP;", "real_ip_recursive off;", ""]
    return "\n".join(lines)


def main() -> int:
    ranges: list[str] = []
    for url in SOURCES:
        ranges += fetch(url)
    if len(ranges) < 10:
        print("too few ranges came back; refusing to write", file=sys.stderr)
        return 1
    TARGET.write_text(render(ranges), encoding="utf-8", newline="\n")
    print(f"wrote {len(ranges)} ranges to {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
