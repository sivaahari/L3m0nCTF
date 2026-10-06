"""Random secrets for one machine, written to files and never printed (standard library only).

Who can read them: the folder is private to its owner (0700), so nobody else on the machine can open the files. The files
themselves are readable (0644), and that is deliberate: on Linux, Docker Compose mounts each file into a container with
the owner and mode it has on the host, and the services run as their own unprivileged users (CTFd 1001, MariaDB 999, Redis
999), who are not the host owner. With 0600 they could not read their own secrets (found by the CI run on Linux). Each
container is given only the secret files it needs (see deploy/compose/compose.base.yml).
"""
from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

PRIVATE_DIR = stat.S_IRWXU  # 0700
READABLE_FILE = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH  # 0644

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
    try:
        directory.chmod(PRIVATE_DIR)  # no effect on Windows, which does not use these bits
    except OSError:
        pass
    existing = [name for name in MAKERS if (directory / name).exists()]
    if existing and not force:
        raise FileExistsError(f"{directory} already holds secrets ({', '.join(existing)}); use --force to replace them")
    made: dict[str, Path] = {}
    for name, make in MAKERS.items():
        path = directory / name
        # the folder is already private (0700), so a readable file inside it is never reachable by other users of the machine
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, READABLE_FILE)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(make() + "\n")
        try:
            path.chmod(READABLE_FILE)
        except OSError:
            pass
        made[name] = path
    return made
