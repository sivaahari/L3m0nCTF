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


# Secrets that do not exist yet when the machine is set up: CTFtime gives its secret only after it approves the event, and the mail service's password
# only once the sending account is made. Production mounts both as Docker secrets, and compose refuses to start when a secret's file is missing, so an
# EMPTY file is made for each. The platform treats an empty one as "off" (no sign-in with CTFtime, no mail). Never replaced, not even with --force:
# a filled one is a real secret.
PLACEHOLDERS = ("CTFTIME_CLIENT_SECRET", "MAIL_PASSWORD")


def generate(directory: Path, force: bool = False) -> dict[str, Path]:
    """Write one file per secret. Refuses to replace existing files unless force is true. Returns {name: path}."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(PRIVATE_DIR)  # no effect on Windows, which does not use these bits
    except OSError as exc:
        if os.name == "posix":
            # a folder we cannot make private (someone else's, say) must not receive world-readable files
            raise PermissionError(f"cannot make {directory} private (0700): {exc}") from exc
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
    for name in PLACEHOLDERS:
        path = directory / name
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT, READABLE_FILE)
            os.close(fd)
            try:
                path.chmod(READABLE_FILE)
            except OSError:
                pass
    return made
