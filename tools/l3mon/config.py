"""The event settings file: load it, validate it, and turn it into the settings CTFd starts with (standard library only)."""
from __future__ import annotations

import json
import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path

REMOVE = object()  # for tests: "delete this key"

MODES = ("teams", "users")
VISIBILITY = ("public", "private", "admins")
HOST = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")
THEME = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
UTC_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SECTIONS = ("event", "teams", "visibility", "accounts", "admin")
EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
ADMIN_NAME = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


def load(path: Path) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def _utc(value) -> datetime | None:
    if isinstance(value, str) and UTC_TIME.match(value):
        try:
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate(data: dict) -> list[str]:
    """Every problem found, in plain English. An empty list means the file is valid."""
    errors: list[str] = []
    present = {name: isinstance(data.get(name), dict) for name in SECTIONS}
    for name in SECTIONS:
        if not present[name]:
            errors.append(f"the section [{name}] is missing")
    ev, teams, vis, acc, adm = (data.get(n) if present[n] else {} for n in SECTIONS)

    if present["event"]:
        for key in ("name", "description"):
            if not isinstance(ev.get(key), str) or not ev[key].strip():
                errors.append(f"event.{key} must be some text")
        if not isinstance(ev.get("theme"), str) or not THEME.match(ev["theme"]):
            errors.append("event.theme must be a short lowercase name (letters, digits, - and _)")
        for key in ("domain", "platform_host"):
            if not isinstance(ev.get(key), str) or not HOST.match(ev[key]):
                errors.append(f"event.{key} must be a host name such as example.org")
        if isinstance(ev.get("domain"), str) and isinstance(ev.get("platform_host"), str) and HOST.match(ev["domain"]) and HOST.match(ev["platform_host"]):
            if not (ev["platform_host"] == ev["domain"] or ev["platform_host"].endswith("." + ev["domain"])):
                errors.append("event.platform_host must be the domain or a name under the domain")
        start, end = _utc(ev.get("start")), _utc(ev.get("end"))
        if start is None:
            errors.append("event.start must be a UTC time like 2026-11-28T04:30:00Z (the Z means UTC)")
        if end is None:
            errors.append("event.end must be a UTC time like 2026-11-28T16:30:00Z (the Z means UTC)")
        if start and end and end <= start:
            errors.append("event.end must be after event.start")

    if present["teams"]:
        if teams.get("mode") not in MODES:
            errors.append(f"teams.mode must be one of: {', '.join(MODES)}")
        size = teams.get("size_max")
        if not _is_int(size) or not 1 <= size <= 8:
            errors.append("teams.size_max must be a whole number from 1 to 8")
    if present["visibility"]:
        for key in ("challenges", "accounts", "scores", "registration"):
            if vis.get(key) not in VISIBILITY:
                errors.append(f"visibility.{key} must be one of: {', '.join(VISIBILITY)}")
    if present["accounts"] and not isinstance(acc.get("verify_emails"), bool):
        errors.append("accounts.verify_emails must be true or false")
    if present["admin"]:
        if not isinstance(adm.get("name"), str) or not ADMIN_NAME.match(adm["name"]):
            errors.append("admin.name must be 3 to 32 letters, digits, dots, dashes or underscores")
        if not isinstance(adm.get("email"), str) or not EMAIL.match(adm["email"]):
            errors.append("admin.email must be an email address")
    # Login with CTFtime is optional: without [ctftime] client_id the sign-in route does not exist (the secret never goes in this file)
    ct = data.get("ctftime", {})
    if not isinstance(ct, dict):
        errors.append("the section [ctftime] must be a table")
    elif "client_id" in ct and not (isinstance(ct["client_id"], str) and (ct["client_id"] == "" or re.fullmatch(r"[0-9]{1,9}", ct["client_id"]))):
        errors.append("ctftime.client_id must be the CTFtime event number in quotes (digits only), or empty")
    return errors


def preset_configs(data: dict) -> dict:
    """The JSON object for CTFd's PRESET_CONFIGS setting. These values cannot be changed from the admin page."""
    errors = validate(data)
    if errors:
        raise ValueError("; ".join(errors))
    ev, teams, vis, acc = data["event"], data["teams"], data["visibility"], data["accounts"]
    return {
        "setup": True,
        "ctf_name": ev["name"],
        "ctf_description": ev["description"],
        "ctf_theme": ev["theme"],
        "user_mode": teams["mode"],
        "team_size": teams["size_max"],
        "start": int(_utc(ev["start"]).timestamp()),
        "end": int(_utc(ev["end"]).timestamp()),
        "challenge_visibility": vis["challenges"],
        "account_visibility": vis["accounts"],
        "score_visibility": vis["scores"],
        "registration_visibility": vis["registration"],
        "verify_emails": acc["verify_emails"],
        # After the end every programme on the release plan is on air again ("You can still read every programme", the approved
        # demo); CTFd only lets players open challenges after the end when this is on. CTFd still answers a flag sent after the
        # end ("correct" or "incorrect") but records nothing, and hints cost nothing then.
        "view_after_ctf": True,
        # Off, and fixed: with it on, a team can read the name, category and value of a programme it once tried and the crew has
        # since withheld, through /api/v1/users/me/submissions (found by the independent review of SP3 part 3.2).
        "view_self_submissions": False,
    }


def render(data: dict, out_dir: Path) -> dict[str, Path]:
    """Write the generated deployment files. Returns {file name: path}.

    preset_configs.json  one line of JSON for CTFd's PRESET_CONFIGS
    compose.env          the few values compose and nginx need (host name, the preset admin's name and address, the CTFtime event number)
    """
    errors = validate(data)
    if errors:
        raise ValueError("; ".join(errors))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    preset = out_dir / "preset_configs.json"
    preset_text = json.dumps(preset_configs(data), separators=(",", ":"), ensure_ascii=True) + chr(10)
    preset.write_bytes(preset_text.encode("utf-8"))
    try:
        preset.chmod(0o644)  # CTFd runs as another user inside its container; a strict umask must not lock it out
    except OSError:
        pass

    lines = [
        "# Generated by `python -m l3mon config render`. Do not edit; do not commit.",
        f"PLATFORM_HOST={data['event']['platform_host']}",
        f"PRESET_ADMIN_NAME={data['admin']['name']}",
        f"PRESET_ADMIN_EMAIL={data['admin']['email']}",
        f"CTFTIME_CLIENT_ID={data.get('ctftime', {}).get('client_id', '')}",
        "",
    ]
    env = out_dir / "compose.env"
    env.write_bytes(chr(10).join(lines).encode("utf-8"))
    return {"preset_configs.json": preset, "compose.env": env}
