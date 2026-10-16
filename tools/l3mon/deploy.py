"""`python -m l3mon deploy check`: what is missing before the production stack can start, in plain words (standard library only).

Each line is one of three things:
  ok       in place
  todo     not in place yet, and nothing breaks without it (Login with CTFtime waits for CTFtime to approve the event; the platform runs without it)
  BLOCKED  the stack must not start (or must not be opened to the public) like this

The exit code is 1 when anything is BLOCKED. The check reads files only: it does not start anything and never prints a secret.
"""
from __future__ import annotations

import ipaddress
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from . import config, secrets_gen

OK, TODO, BLOCKED = "ok", "todo", "BLOCKED"
RANDOM_SECRETS = tuple(secrets_gen.MAKERS)  # every one of them must exist and hold a value
TLS_FILES = ("origin.pem", "origin.key", "cloudflare-origin-pull-ca.pem")
CLOUDFLARE_IPS_MAX_AGE_DAYS = 45
DOCUMENTATION_NETS = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32")]


@dataclass
class Item:
    status: str
    name: str
    detail: str = ""

    def human(self) -> str:
        return f"{self.status:8} {self.name}" + (f": {self.detail}" if self.detail else "")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").strip()


def _world_readable(path: Path) -> bool:
    return os.name == "posix" and bool(path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO))


def check_event(event_file: Path, generated: Path, items: list[Item]) -> dict | None:
    if not event_file.is_file():
        items.append(Item(BLOCKED, "event settings", f"{event_file} does not exist: copy config/event.example.toml to it and edit it"))
        return None
    data = config.load(event_file)
    problems = config.validate(data)
    if problems:
        items.append(Item(BLOCKED, "event settings", "; ".join(problems)))
        return None
    items.append(Item(OK, "event settings", f"{event_file.name} is valid; the platform answers at {data['event']['platform_host']}"))
    with tempfile.TemporaryDirectory() as scratch:
        fresh = config.render(data, Path(scratch))
        for name, path in fresh.items():
            made = generated / name
            if not made.is_file() or made.read_bytes() != path.read_bytes():
                items.append(Item(BLOCKED, "generated settings", f"{made} is missing or older than {event_file.name}: run `python -m l3mon config render {event_file} --out {generated}`"))
                return data
    items.append(Item(OK, "generated settings", "match the event settings"))
    return data


def check_secrets(directory: Path, data: dict | None, items: list[Item]) -> None:
    if not directory.is_dir():
        items.append(Item(BLOCKED, "secrets", f"{directory} does not exist: run `python -m l3mon secrets generate --dir {directory}` on this machine"))
        return
    if os.name == "posix" and directory.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        items.append(Item(BLOCKED, "secrets folder", f"{directory} can be opened by other users of this machine: chmod 700 {directory}"))
    absent = [name for name in RANDOM_SECRETS if name != "PRESET_ADMIN_TOKEN" and not (directory / name).is_file()]
    empty = [name for name in RANDOM_SECRETS if name != "PRESET_ADMIN_TOKEN" and (directory / name).is_file() and not _read(directory / name)]
    if absent or empty:
        items.append(Item(BLOCKED, "secrets", "missing: " + ", ".join(absent) if absent else "empty: " + ", ".join(empty)))
    else:
        items.append(Item(OK, "secrets", "all generated and filled"))
    if (directory / "PRESET_ADMIN_TOKEN").exists():
        items.append(Item(BLOCKED, "development administrator token", f"delete {directory / 'PRESET_ADMIN_TOKEN'}: production never has a fixed token that works from any address and never expires"))
    else:
        items.append(Item(OK, "development administrator token", "absent, as it must be"))

    client_id = (data or {}).get("ctftime", {}).get("client_id", "")
    ctftime_secret = _read(directory / "CTFTIME_CLIENT_SECRET") if (directory / "CTFTIME_CLIENT_SECRET").is_file() else ""
    if not (directory / "CTFTIME_CLIENT_SECRET").is_file():
        items.append(Item(BLOCKED, "Login with CTFtime", f"{directory / 'CTFTIME_CLIENT_SECRET'} does not exist: compose needs the file, and an empty one is fine (`secrets generate` makes it on a new machine; on one that has secrets already: `: > {directory / 'CTFTIME_CLIENT_SECRET'}`)"))
    elif client_id and ctftime_secret:
        items.append(Item(OK, "Login with CTFtime", f"on: event number {client_id} and the secret are both set"))
    elif client_id and not ctftime_secret:
        items.append(Item(BLOCKED, "Login with CTFtime", "the event number is set in event.toml but .secrets/CTFTIME_CLIENT_SECRET is empty: the route stays off"))
    elif ctftime_secret and not client_id:
        items.append(Item(TODO, "Login with CTFtime", "the secret is there but [ctftime] client_id in event.toml is empty: the route stays off"))
    else:
        items.append(Item(TODO, "Login with CTFtime", "off until CTFtime approves the event: then put the event number in event.toml and the secret in .secrets/CTFTIME_CLIENT_SECRET (docs/ctftime-endpoints.md, section 6)"))

    mail = (data or {}).get("mail")
    mail_password = _read(directory / "MAIL_PASSWORD") if (directory / "MAIL_PASSWORD").is_file() else None
    if mail_password is None:
        items.append(Item(BLOCKED, "mail", f"{directory / 'MAIL_PASSWORD'} does not exist: compose needs the file, and an empty one is fine (`: > {directory / 'MAIL_PASSWORD'}`)"))
    elif not mail:
        verify = (data or {}).get("accounts", {}).get("verify_emails")
        items.append(Item(TODO, "mail", "no [mail] section: no verification or password-reset message can be sent, so nobody can finish registering" + (" (accounts.verify_emails is true)" if verify else "")))
    elif mail.get("username") and not mail_password:
        items.append(Item(BLOCKED, "mail", "the mail account has a login name but .secrets/MAIL_PASSWORD is empty"))
    else:
        items.append(Item(OK, "mail", f"sends through {mail['host']}:{mail['port']} as {mail['from_addr']}"))


def check_tls(directory: Path, items: list[Item]) -> None:
    missing = [name for name in TLS_FILES if not (directory / name).is_file()]
    if missing:
        items.append(Item(BLOCKED, "TLS files", f"missing in {directory}: {', '.join(missing)} (docs/deploy/platform-deployment.md, C1 and C3)"))
        return
    bad = [name for name in ("origin.pem", "cloudflare-origin-pull-ca.pem") if "-----BEGIN CERTIFICATE-----" not in _read(directory / name)]
    if bad:
        items.append(Item(BLOCKED, "TLS files", f"not a certificate in PEM form: {', '.join(bad)}"))
        return
    if not re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", _read(directory / "origin.key")):
        items.append(Item(BLOCKED, "TLS files", "origin.key is not a private key in PEM form"))
        return
    if _world_readable(directory / "origin.key") and _read(directory / "origin.key"):
        group_only = not (directory / "origin.key").stat().st_mode & stat.S_IRWXO
        if not group_only:
            items.append(Item(BLOCKED, "TLS key", f"{directory / 'origin.key'} can be read by every user of this machine: chmod 640 and give it to the nginx group"))
            return
    items.append(Item(OK, "TLS files", f"origin certificate, key and the Cloudflare client-certificate authority are in {directory}"))


def _networks_of(path: Path) -> list[tuple[str, ipaddress._BaseNetwork]]:
    found = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split("#", 1)[0].strip().rstrip(";").strip()
        if not line:
            continue
        match = re.fullmatch(r"([0-9a-fA-F:.]+(?:/\d{1,3})?)\s+1", line)
        if match:
            try:
                found.append((match.group(1), ipaddress.ip_network(match.group(1), strict=False)))
            except ValueError:
                found.append((match.group(1), None))
    return found


def check_admin_nets(path: Path | None, repo_default: Path, items: list[Item]) -> None:
    if path is None:
        items.append(Item(BLOCKED, "organisers' addresses", "ADMIN_NETS_FILE is not set: production needs a file outside the repository that lists the addresses the crew reaches /admin from (start from deploy/nginx/snippets/admin-nets.production.example.conf)"))
        return
    if not path.is_file():
        items.append(Item(BLOCKED, "organisers' addresses", f"{path} does not exist"))
        return
    if path.resolve() == repo_default.resolve():
        items.append(Item(BLOCKED, "organisers' addresses", "this is the development list from the repository: it opens /admin to the addresses a laptop uses"))
        return
    nets = _networks_of(path)
    if not nets:
        items.append(Item(BLOCKED, "organisers' addresses", f"{path} lists no address: nobody could reach /admin"))
        return
    unreadable = [text for text, net in nets if net is None]
    private = [text for text, net in nets if net is not None and (net.is_private and not any(net.subnet_of(d) for d in DOCUMENTATION_NETS if net.version == d.version) or net.is_loopback or net.is_link_local)]
    placeholders = [text for text, net in nets if net is not None and any(net.version == d.version and net.subnet_of(d) for d in DOCUMENTATION_NETS)]
    wide = [text for text, net in nets if net is not None and net.prefixlen < (8 if net.version == 4 else 32)]
    if unreadable:
        items.append(Item(BLOCKED, "organisers' addresses", f"not an address: {', '.join(unreadable)}"))
    elif private:
        items.append(Item(BLOCKED, "organisers' addresses", f"private or local ranges ({', '.join(private)}): behind Docker or a tunnel every visitor can arrive from one of these, which would open /admin to everyone"))
    elif wide:
        items.append(Item(BLOCKED, "organisers' addresses", f"too wide a range ({', '.join(wide)})"))
    elif placeholders and len(placeholders) == len(nets):
        items.append(Item(BLOCKED, "organisers' addresses", "still the example list from the repository (documentation addresses that match nobody)"))
    else:
        items.append(Item(OK, "organisers' addresses", f"{len(nets)} explicit address(es)" + (f" (and {len(placeholders)} example line(s) that match nobody: remove them)" if placeholders else "")))


def check_cloudflare_list(path: Path, today: date, items: list[Item]) -> None:
    if not path.is_file():
        items.append(Item(BLOCKED, "Cloudflare address list", f"{path} does not exist"))
        return
    found = re.search(r"Generated (\d{4}-\d{2}-\d{2})", path.read_text(encoding="utf-8"))
    if not found:
        items.append(Item(TODO, "Cloudflare address list", "no generation date in the file: run `python3 tools/update_cloudflare_ips.py`"))
        return
    age = (today - datetime.strptime(found.group(1), "%Y-%m-%d").date()).days
    if age > CLOUDFLARE_IPS_MAX_AGE_DAYS:
        items.append(Item(TODO, "Cloudflare address list", f"{age} days old: run `python3 tools/update_cloudflare_ips.py` and read the diff before deploying"))
    else:
        items.append(Item(OK, "Cloudflare address list", f"generated {found.group(1)} ({age} days ago)"))


def check_environment(env: dict, items: list[Item]) -> None:
    if env.get("L3MON_ENV", "dev") != "production":
        items.append(Item(TODO, "environment", "L3MON_ENV is not `production`: start with `L3MON_ENV=production tools/compose.sh up -d --wait`"))
    tag = env.get("L3MON_IMAGE_TAG", "dev")
    if tag in ("dev", "", "latest"):
        items.append(Item(TODO, "image tag", f"L3MON_IMAGE_TAG is {tag!r}: deploy a release tag, so you can always say exactly what is running"))
    else:
        items.append(Item(OK, "image tag", tag))


def run_checks(root: Path, event_file: Path | None = None, secrets_dir: Path | None = None, tls_dir: Path | None = None, admin_nets: Path | None = None,
               env: dict | None = None, today: date | None = None) -> list[Item]:
    env = dict(os.environ) if env is None else env
    root = Path(root)
    event_file = event_file or root / "config" / "event.toml"
    secrets_dir = secrets_dir or root / ".secrets"
    tls_dir = tls_dir or Path(env.get("TLS_DIR") or "/etc/l3mon/tls")
    if admin_nets is None and env.get("ADMIN_NETS_FILE"):
        admin_nets = Path(env["ADMIN_NETS_FILE"])
    items: list[Item] = []
    data = check_event(event_file, root / "deploy" / "compose" / "generated", items)
    check_secrets(secrets_dir, data, items)
    check_tls(tls_dir, items)
    check_admin_nets(admin_nets, root / "deploy" / "nginx" / "snippets" / "admin-nets.conf", items)
    check_cloudflare_list(root / "deploy" / "nginx" / "snippets" / "cloudflare-realip.conf", today or date.today(), items)
    check_environment(env, items)
    return items
