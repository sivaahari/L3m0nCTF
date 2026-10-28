#!/usr/bin/env bash
# Save everything that lives ONLY on this machine, in one file, so the platform can be brought up again on another machine exactly as it is now.
#   tools/export-state.sh [--out DIR] [--encrypt] [--skip-backup]
#
# Run it on the machine that runs the platform, from the repository's folder (the platform must be up: the database is dumped through it).
# It collects:
#   - a backup of the database and the uploaded files (tools/backup.sh, with its checksums)
#   - the story volume (the cold-open comics are not in the image or the database; they are put there at deployment)
#   - the secrets in .secrets/ (SECRET_KEY and FLAG_HMAC_SECRET above all: they keep every sign-in link and every team's flag valid)
#   - the settings that are not in git: config/event.toml, deploy/compose/generated/, the Cloudflare address list nginx trusts
#   - the folder outside the repository (default /etc/l3mon): production.env, admin-nets.conf and the TLS files, the origin key included
#   - which release is running (commit, tag, image id), whether anyone changed the checked-out files, and what Docker holds
# and writes ONE file, backups/l3mon-state-<host>-<time>.tar, owner-readable only, with a checksum next to it. Nothing is printed that is secret.
#
# The file holds personal data and every secret: treat it like a password file. --encrypt asks for a passphrase (twice, not shown) and writes
# the same file encrypted (AES-256, key derived with PBKDF2) and removes the plain one. Copy it off the machine, check the checksum, keep it
# somewhere only you can open, and bring it to the new machine with tools/import-state.sh.
#
# Settings for tests and unusual machines: L3MON_ETC (the folder outside the repository, default /etc/l3mon); L3MON_STATE_PASSPHRASE_FILE (a file that holds
# the passphrase, for a run with nobody at the keyboard).
set -euo pipefail
umask 077
root="$(cd "$(dirname "$0")/.." && pwd)"
# a path such as C:/folder (a Windows shell) would be read by GNU tar as "host:path" without this
tar_help="$(tar --help 2>&1 || true)"; tar_local=(); if [[ "$tar_help" == *--force-local* ]]; then tar_local=(--force-local); fi
tarf() { tar "${tar_local[@]}" "$@"; }
etc="${L3MON_ETC:-/etc/l3mon}"
out="$root/backups"
encrypt=0
skip_backup=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) out="${2:?--out needs a folder}"; shift 2 ;;
    --encrypt) encrypt=1; shift ;;
    --skip-backup) skip_backup=1; shift ;;   # only for tests: the real run always makes the backup
    -h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

# tools/compose.sh reads the environment's name and the overlays from the shell only
if [[ -f "$etc/production.env" ]]; then set -a; . "$etc/production.env"; set +a; fi
compose() { "$root/tools/compose.sh" "$@"; }

[[ -f "$root/.secrets/SECRET_KEY" ]] || { echo "No .secrets/SECRET_KEY in $root: this is not the folder of a running platform." >&2; exit 1; }
[[ -f "$root/deploy/compose/generated/compose.env" ]] || { echo "No deploy/compose/generated/compose.env in $root." >&2; exit 1; }
if [[ -z "$(compose ps -q ctfd 2>/dev/null)" ]]; then
  echo "The platform is not running here (no ctfd container). Start it first: the database is saved through it." >&2
  exit 1
fi

mkdir -p "$out"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
host="$(hostname -s 2>/dev/null || echo host)"
name="l3mon-state-$host-$stamp"
work="$(mktemp -d "$out/.work-$stamp.XXXXXX")"
cleanup() { rm -rf "$work"; }
trap cleanup EXIT
state="$work/state"
mkdir -p "$state/backup" "$state/repo" "$state/etc" "$state/git" "$state/docker"

echo "1/6 The database and the uploaded files ..."
if [[ "$skip_backup" -eq 1 ]]; then
  echo "    (skipped: --skip-backup)"
  : > "$state/backup/SKIPPED"
else
  "$root/tools/backup.sh" "$state/backup" > "$work/backup.log" 2>&1 || { cat "$work/backup.log" >&2; echo "The backup failed; nothing was saved." >&2; exit 1; }
fi

echo "2/6 The story volume ..."
story_files="$(compose exec -T ctfd sh -c 'ls -A /var/story 2>/dev/null | wc -l' | tr -d '[:space:]' || true)"
story_files="${story_files:-0}"
if [[ "$story_files" -gt 0 ]]; then
  compose exec -T ctfd sh -c 'cd /var/story && exec tar -cf - .' | gzip -9 > "$state/story.tar.gz"
fi

echo "3/6 The secrets and the settings that are not in git ..."
cp -a "$root/.secrets" "$state/repo/secrets"
[[ -f "$root/config/event.toml" ]] && cp -a "$root/config/event.toml" "$state/repo/event.toml"
cp -a "$root/deploy/compose/generated" "$state/repo/generated"
[[ -f "$root/deploy/nginx/snippets/cloudflare-realip.conf" ]] && cp -a "$root/deploy/nginx/snippets/cloudflare-realip.conf" "$state/repo/cloudflare-realip.conf"
if [[ -d "$etc" ]]; then cp -a "$etc/." "$state/etc/"; fi
[[ -f /etc/docker/daemon.json ]] && cp -a /etc/docker/daemon.json "$state/docker/daemon.json" || true

echo "4/6 Which release is running ..."
commit="$(git -C "$root" rev-parse HEAD 2>/dev/null || echo unknown)"
tag="$(git -C "$root" describe --tags --exact-match 2>/dev/null || true)"
# these two scripts are copied onto a machine whose checked-out release does not have them yet; they are not "changes"
git -C "$root" status --porcelain 2>/dev/null | grep -vF -e ' tools/export-state.sh' -e ' tools/import-state.sh' > "$state/git/status.txt" || true
if [[ -s "$state/git/status.txt" ]]; then git -C "$root" diff HEAD --binary > "$state/git/local-changes.patch" 2>/dev/null || true; fi
ctfd_container="$(compose ps -q ctfd | sed -n 1p)"
ctfd_image_id="$(docker inspect --format '{{.Image}}' "$ctfd_container" 2>/dev/null || echo unknown)"
ctfd_image_name="$(docker inspect --format '{{.Config.Image}}' "$ctfd_container" 2>/dev/null || echo unknown)"
docker image ls --digests --format '{{.Repository}}:{{.Tag}} {{.Digest}} {{.ID}} {{.Size}}' > "$state/docker/images.txt" 2>/dev/null || true
docker ps --format '{{.Names}} {{.Image}} {{.Status}}' > "$state/docker/ps.txt" 2>/dev/null || true
docker volume ls --format '{{.Name}}' > "$state/docker/volumes.txt" 2>/dev/null || true
{ uname -a; docker --version 2>/dev/null || true; docker compose version 2>/dev/null || true; grep -E '^(PRETTY_NAME|VERSION_ID)=' /etc/os-release 2>/dev/null || true; } > "$state/docker/versions.txt"

echo "5/6 The summary and the checksums ..."
{
  echo "STATE_FORMAT=1"
  echo "CREATED_UTC=$stamp"
  echo "HOST=$host"
  echo "COMMIT=$commit"
  echo "TAG=$tag"
  echo "L3MON_ENV=${L3MON_ENV:-dev}"
  echo "L3MON_IMAGE_TAG=${L3MON_IMAGE_TAG:-}"
  echo "CTFD_IMAGE_ID=$ctfd_image_id"
  echo "CTFD_IMAGE_NAME=$ctfd_image_name"
  echo "STORY_FILES=$story_files"
  echo "ETC_DIR=$etc"
  echo "LOCAL_CHANGES=$([[ -s "$state/git/status.txt" ]] && echo yes || echo no)"
} > "$state/STATE.env"
( cd "$state" && find . -type f ! -name MANIFEST.sha256 -print0 | sort -z | xargs -0 sha256sum > MANIFEST.sha256 )

echo "6/6 Writing the file ..."
plain="$out/$name.tar"
tarf -cf "$plain" -C "$work" state
chmod 600 "$plain"
final="$plain"
if [[ "$encrypt" -eq 1 ]]; then
  if [[ -n "${L3MON_STATE_PASSPHRASE_FILE:-}" ]]; then
    pass1="$(cat "$L3MON_STATE_PASSPHRASE_FILE")"; pass2="$pass1"   # for a run with nobody at the keyboard: a file only you can read, never the passphrase on the command line
  else
    read -rsp "Passphrase for the file (not shown): " pass1 || { rm -f "$plain"; echo "Could not read a passphrase: run this from a terminal, or set L3MON_STATE_PASSPHRASE_FILE. Nothing was written." >&2; exit 1; }; echo
    read -rsp "Again: " pass2 || { rm -f "$plain"; echo "Could not read the passphrase again. Nothing was written." >&2; exit 1; }; echo
  fi
  [[ -n "$pass1" && "$pass1" == "$pass2" ]] || { rm -f "$plain"; echo "The two passphrases differ (or are empty). Nothing was written." >&2; exit 1; }
  final="$plain.enc"
  printf '%s' "$pass1" | openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -salt -pass stdin -in "$plain" -out "$final"
  # prove that it opens before the plain copy goes
  back="$(printf '%s' "$pass1" | openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -pass stdin -in "$final" | sha256sum | cut -d' ' -f1)"
  want="$(sha256sum "$plain" | cut -d' ' -f1)"
  unset pass1 pass2
  [[ "$back" == "$want" ]] || { rm -f "$final"; echo "The encrypted copy does not open back into the original. The plain file is kept: $plain" >&2; exit 1; }
  chmod 600 "$final"
  if command -v shred >/dev/null 2>&1; then shred -u "$plain"; else rm -f "$plain"; fi
fi
( cd "$out" && sha256sum "$(basename "$final")" > "$(basename "$final").sha256" )
chmod 600 "$final.sha256"

echo
echo "Saved: $final"
ls -l "$final"
cat "$final.sha256"
echo "Release: ${tag:-no tag} ($commit); files in the story volume: $story_files; local changes to the checked-out files: $([[ -s "$state/git/status.txt" ]] && echo YES, see the patch inside || echo none)"
echo "Next: copy it off this machine, compare the checksum there, and keep it where only you can open it. On the new machine: tools/import-state.sh place <file>"
