from dataclasses import dataclass
import re
import tomllib

_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}\Z")
_PINNED_DIGEST_PATTERN = re.compile(r"^.+@sha256:[0-9a-f]{64}\Z")

# Global maximum caps to protect the host against excessive resource allocations
GLOBAL_MAX_CPUS = 2.0
GLOBAL_MAX_MEM_MB = 1024
GLOBAL_MAX_PIDS = 256
GLOBAL_MAX_TTL_MINUTES = 180

DEFAULT_CPUS = 0.5
DEFAULT_MEM_MB = 256
DEFAULT_PIDS = 128
DEFAULT_TTL_MINUTES = 45
DEFAULT_READ_ONLY = True
DEFAULT_MAX_PER_TEAM = 1
DEFAULT_MAX_BODY_BYTES = 1048576  # 1 MB
GLOBAL_MAX_BODY_BYTES = 10485760  # 10 MB
DEFAULT_RESPONSE_TIMEOUT_SECONDS = 120
GLOBAL_MAX_RESPONSE_TIMEOUT_SECONDS = 600


@dataclass(frozen=True)
class ChallengeSpec:
    slug: str
    image: str
    protocol: str
    port: int
    cpus: float
    mem_mb: int
    pids: int
    ttl_minutes: int
    read_only: bool
    max_per_team: int
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    response_timeout_seconds: int = DEFAULT_RESPONSE_TIMEOUT_SECONDS



class Manifest:
    def __init__(self, challenges: dict[str, ChallengeSpec]):
        self._challenges = dict(challenges)

    @property
    def challenges(self) -> dict[str, ChallengeSpec]:
        return dict(self._challenges)

    def get(self, slug: str) -> ChallengeSpec | None:
        return self._challenges.get(slug)

    def __contains__(self, slug: str) -> bool:
        return slug in self._challenges


def _validate_and_build_spec(data: dict, dev_mode: bool) -> ChallengeSpec:
    if not isinstance(data, dict):
        raise ValueError("Each [[challenge]] entry must be a dictionary")

    raw_slug = data.get("slug")
    if not raw_slug or not isinstance(raw_slug, str) or not _SLUG_PATTERN.fullmatch(raw_slug):
        raise ValueError(f"Invalid slug '{raw_slug}': must match ^[a-z0-9][a-z0-9_-]{{0,47}}\\Z")

    image = data.get("image")
    if not image or not isinstance(image, str):
        raise ValueError(f"Challenge '{raw_slug}' missing required 'image'")
    if not dev_mode and not _PINNED_DIGEST_PATTERN.fullmatch(image):
        raise ValueError(f"Challenge '{raw_slug}' image must be pinned by digest (@sha256:<64 hex>)")

    protocol = data.get("protocol", "http")
    if protocol not in ("http", "tcp"):
        raise ValueError(f"Challenge '{raw_slug}' protocol must be 'http' or 'tcp', got '{protocol}'")

    raw_port = data.get("port")
    if raw_port is None or not isinstance(raw_port, int) or raw_port < 1 or raw_port > 65535:
        raise ValueError(f"Challenge '{raw_slug}' port must be an integer between 1 and 65535")

    cpus = float(data.get("cpus", DEFAULT_CPUS))
    if cpus <= 0 or cpus > GLOBAL_MAX_CPUS:
        raise ValueError(f"Challenge '{raw_slug}' cpus ({cpus}) exceeds allowed limit (0 < cpus <= {GLOBAL_MAX_CPUS})")

    mem_mb = int(data.get("mem_mb", DEFAULT_MEM_MB))
    if mem_mb <= 0 or mem_mb > GLOBAL_MAX_MEM_MB:
        raise ValueError(f"Challenge '{raw_slug}' mem_mb ({mem_mb}) exceeds allowed limit (0 < mem_mb <= {GLOBAL_MAX_MEM_MB})")

    pids = int(data.get("pids", DEFAULT_PIDS))
    if pids <= 0 or pids > GLOBAL_MAX_PIDS:
        raise ValueError(f"Challenge '{raw_slug}' pids ({pids}) exceeds allowed limit (0 < pids <= {GLOBAL_MAX_PIDS})")

    ttl_minutes = int(data.get("ttl_minutes", DEFAULT_TTL_MINUTES))
    if ttl_minutes <= 0 or ttl_minutes > GLOBAL_MAX_TTL_MINUTES:
        raise ValueError(f"Challenge '{raw_slug}' ttl_minutes ({ttl_minutes}) exceeds allowed limit")

    read_only = bool(data.get("read_only", DEFAULT_READ_ONLY))
    max_per_team = int(data.get("max_per_team", DEFAULT_MAX_PER_TEAM))
    if max_per_team < 1:
        raise ValueError(f"Challenge '{raw_slug}' max_per_team must be at least 1")

    # Request body cap: default 1 MB, at most 10 MB
    if "max_body_bytes" in data:
        max_body_bytes = int(data["max_body_bytes"])
    elif "max_body_mb" in data:
        max_body_bytes = int(data["max_body_mb"]) * 1024 * 1024
    else:
        max_body_bytes = DEFAULT_MAX_BODY_BYTES

    if max_body_bytes <= 0 or max_body_bytes > GLOBAL_MAX_BODY_BYTES:
        raise ValueError(
            f"Challenge '{raw_slug}' max_body_bytes ({max_body_bytes}) exceeds allowed limit (0 < max_body_bytes <= {GLOBAL_MAX_BODY_BYTES})"
        )

    # Response total timeout: default 120s, max 600s
    if "response_timeout_seconds" in data:
        response_timeout_seconds = int(data["response_timeout_seconds"])
    elif "total_timeout_seconds" in data:
        response_timeout_seconds = int(data["total_timeout_seconds"])
    else:
        response_timeout_seconds = DEFAULT_RESPONSE_TIMEOUT_SECONDS

    if response_timeout_seconds <= 0 or response_timeout_seconds > GLOBAL_MAX_RESPONSE_TIMEOUT_SECONDS:
        raise ValueError(
            f"Challenge '{raw_slug}' response_timeout_seconds ({response_timeout_seconds}) exceeds allowed limit (0 < timeout <= {GLOBAL_MAX_RESPONSE_TIMEOUT_SECONDS})"
        )

    return ChallengeSpec(
        slug=raw_slug,
        image=image,
        protocol=protocol,
        port=raw_port,
        cpus=cpus,
        mem_mb=mem_mb,
        pids=pids,
        ttl_minutes=ttl_minutes,
        read_only=read_only,
        max_per_team=max_per_team,
        max_body_bytes=max_body_bytes,
        response_timeout_seconds=response_timeout_seconds,
    )



def load_manifest_from_string(content: str, dev_mode: bool = False) -> Manifest:
    """Parse manifest TOML content into a validated Manifest object."""
    parsed = tomllib.loads(content)
    raw_challenges = parsed.get("challenge", [])
    if not isinstance(raw_challenges, list):
        raise ValueError("'challenge' key in manifest must be a list of challenge tables")

    challenges: dict[str, ChallengeSpec] = {}
    for entry in raw_challenges:
        spec = _validate_and_build_spec(entry, dev_mode=dev_mode)
        if spec.slug in challenges:
            raise ValueError(f"Duplicate challenge slug '{spec.slug}' found in manifest")
        challenges[spec.slug] = spec

    return Manifest(challenges)


def load_manifest(path: str, dev_mode: bool = False) -> Manifest:
    """Read and parse a manifest TOML file."""
    with open(path, "rb") as f:
        parsed = tomllib.load(f)
    raw_challenges = parsed.get("challenge", [])
    challenges: dict[str, ChallengeSpec] = {}
    for entry in raw_challenges:
        spec = _validate_and_build_spec(entry, dev_mode=dev_mode)
        if spec.slug in challenges:
            raise ValueError(f"Duplicate challenge slug '{spec.slug}' found in manifest")
        challenges[spec.slug] = spec
    return Manifest(challenges)
