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
SECTIONS = ("event", "teams", "visibility", "accounts")


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
    ev, teams, vis, acc = (data.get(n) if present[n] else {} for n in SECTIONS)

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
            errors.append("event.start must be a UTC time like 2026-11-28T03:30:00Z (the Z means UTC)")
        if end is None:
            errors.append("event.end must be a UTC time like 2026-11-29T03:30:00Z (the Z means UTC)")
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
    }


def render(data: dict, out_dir: Path) -> dict[str, Path]:
    """Write the generated deployment files. Returns {file name: path}."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    preset = out_dir / "preset_configs.json"
    preset.write_text(json.dumps(preset_configs(data), separators=(",", ":"), ensure_ascii=True) + "\n", encoding="utf-8")
    return {"preset_configs.json": preset}
