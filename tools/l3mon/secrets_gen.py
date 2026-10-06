"""Random secrets for one machine, written to files and never printed (standard library only)."""
from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

# name -> how to make the value
MAKERS = {
    "SECRET_KEY": lambda: secrets.token_hex(32),
    "FLAG_HMAC_SECRET": lambda: secrets.token_hex(32),
    "PRESET_ADMIN_TOKEN": lambda: "ctfd_" + secrets.token_hex(32),
    "DATABASE_PASSWORD": lambda: secrets.token_urlsafe(32),
    "DATABASE_ROOT_PASSWORD": lambda: secrets.token_urlsafe(32),
    "REDIS_PASSWORD": lambda: secrets.token_urlsafe(32),
    "PRESET_ADMIN_PASSWORD": lambda: secrets.token_urlsafe(32),
}


def generate(directory: Path, force: bool = False) -> dict[str, Path]:
    """Write one file per secret. Refuses to replace existing files unless force is true. Returns {name: path}."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    existing = [name for name in MAKERS if (directory / name).exists()]
    if existing and not force:
        raise FileExistsError(f"{directory} already holds secrets ({', '.join(existing)}); use --force to replace them")
    made: dict[str, Path] = {}
    for name, make in MAKERS.items():
        path = directory / name
        # create with owner-only permissions from the start, so there is never a moment when others can read it
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(make() + "\n")
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        made[name] = path
    return made
