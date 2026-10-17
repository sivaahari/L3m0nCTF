import re

_PINNED_DIGEST_PATTERN = re.compile(r"^.+@sha256:[0-9a-f]{64}\Z")

# Global maximum caps
MAX_CPUS = 2.0
MAX_NANO_CPUS = int(MAX_CPUS * 1_000_000_000)
MAX_MEM_BYTES = 1024 * 1024 * 1024  # 1024 MB
MAX_PIDS = 256


class ContainerSafetyError(ValueError):
    """Raised when a container creation specification violates safety requirements."""
    pass


def assert_safe(spec: dict, dev_mode: bool = False) -> None:
    """Validate a container creation request body against strict security constraints.

    Refuses:
      - privileged mode
      - added capabilities (all must be dropped)
      - host or container: network, pid, ipc, or uts modes
      - bind mounts, volumes-from, devices
      - the Docker socket in any form
      - any security option except no-new-privileges
      - unpinned image digest (outside dev mode)
      - root or empty user
      - resource limits exceeding global maximums or missing
      - published ports
      - non-zero restart policy
    """
    if not isinstance(spec, dict):
        raise ContainerSafetyError("Container spec must be a dictionary")

    # 1. Image validation
    image = spec.get("Image")
    if not image or not isinstance(image, str):
        raise ContainerSafetyError("Container image must be a non-empty string")
    if not dev_mode and not _PINNED_DIGEST_PATTERN.fullmatch(image):
        raise ContainerSafetyError(f"Container image '{image}' must be pinned by digest (@sha256:...)")

    # 2. User validation (strictly non-root)
    user = spec.get("User")
    if user is None or not isinstance(user, str) or not user.strip():
        raise ContainerSafetyError("Container user must be specified and non-empty")
    user_str = user.strip().lower()
    if user_str in ("root", "0", "0:0") or user_str.startswith("0:") or user_str.endswith(":0"):
        raise ContainerSafetyError(f"Container user '{user}' cannot be root (UID 0)")

    # 3. HostConfig validation
    host_config = spec.get("HostConfig")
    if not isinstance(host_config, dict):
        raise ContainerSafetyError("HostConfig must be a dictionary")

    # Privileged
    if host_config.get("Privileged", False):
        raise ContainerSafetyError("Privileged containers are strictly forbidden")

    # Capabilities: ALL must be dropped, none added
    cap_add = host_config.get("CapAdd") or []
    if cap_add:
        raise ContainerSafetyError(f"Added capabilities ({cap_add}) are forbidden")
    cap_drop = [c.upper() for c in (host_config.get("CapDrop") or [])]
    if "ALL" not in cap_drop:
        raise ContainerSafetyError("ALL capabilities must be dropped (CapDrop must include 'ALL')")

    # Isolation modes: network, pid, ipc, uts
    net_mode = str(host_config.get("NetworkMode", ""))
    if not net_mode or net_mode.lower() == "host" or net_mode.lower().startswith("container:"):
        raise ContainerSafetyError(f"Forbidden network mode '{net_mode}'")

    pid_mode = str(host_config.get("PidMode", "")).lower()
    if pid_mode == "host" or pid_mode.startswith("container:"):
        raise ContainerSafetyError(f"Forbidden pid mode '{pid_mode}'")

    ipc_mode = str(host_config.get("IpcMode", "")).lower()
    if ipc_mode == "host" or ipc_mode.startswith("container:"):
        raise ContainerSafetyError(f"Forbidden ipc mode '{ipc_mode}'")

    uts_mode = str(host_config.get("UTSMode", "")).lower()
    if uts_mode == "host":
        raise ContainerSafetyError(f"Forbidden uts mode '{uts_mode}'")

    # Mounts and devices: no host binds, no volumes from, no host devices
    binds = host_config.get("Binds") or []
    if binds:
        raise ContainerSafetyError(f"Host bind mounts are forbidden: {binds}")

    volumes_from = host_config.get("VolumesFrom") or []
    if volumes_from:
        raise ContainerSafetyError(f"volumes_from is forbidden: {volumes_from}")

    devices = host_config.get("Devices") or []
    if devices:
        raise ContainerSafetyError(f"Host devices mapping is forbidden: {devices}")

    # Inspect all spec strings for docker socket reference
    spec_repr = str(spec).lower()
    if "docker.sock" in spec_repr or "dockershim" in spec_repr:
        raise ContainerSafetyError("References to the docker socket are strictly forbidden")

    # Security options
    security_opts = host_config.get("SecurityOpt") or []
    normalized_opts = [opt.strip().lower() for opt in security_opts]
    if "no-new-privileges:true" not in normalized_opts:
        raise ContainerSafetyError("SecurityOpt must include 'no-new-privileges:true'")
    for opt in normalized_opts:
        if opt != "no-new-privileges:true":
            raise ContainerSafetyError(f"Forbidden security option '{opt}': only 'no-new-privileges:true' allowed")

    # Resource limits: Memory, NanoCpus, PidsLimit
    memory = host_config.get("Memory", 0)
    if not isinstance(memory, int) or memory <= 0:
        raise ContainerSafetyError("Container must have an explicit memory limit greater than 0")
    if memory > MAX_MEM_BYTES:
        raise ContainerSafetyError(f"Memory limit ({memory} bytes) exceeds maximum ceiling ({MAX_MEM_BYTES} bytes)")

    nano_cpus = host_config.get("NanoCpus", 0)
    if not isinstance(nano_cpus, int) or nano_cpus <= 0:
        raise ContainerSafetyError("Container must have an explicit NanoCpus limit greater than 0")
    if nano_cpus > MAX_NANO_CPUS:
        raise ContainerSafetyError(f"NanoCpus ({nano_cpus}) exceeds maximum ceiling ({MAX_NANO_CPUS})")

    pids_limit = host_config.get("PidsLimit", 0)
    if not isinstance(pids_limit, int) or pids_limit <= 0:
        raise ContainerSafetyError("Container must have an explicit PidsLimit greater than 0")
    if pids_limit > MAX_PIDS:
        raise ContainerSafetyError(f"PidsLimit ({pids_limit}) exceeds maximum ceiling ({MAX_PIDS})")

    # Port bindings: must not bind directly to host ports
    port_bindings = host_config.get("PortBindings") or {}
    if port_bindings:
        raise ContainerSafetyError(f"Direct host published ports are forbidden: {port_bindings}")

    # Restart policy: must be "no"
    restart_policy = host_config.get("RestartPolicy") or {}
    if restart_policy.get("Name", "no") != "no":
        raise ContainerSafetyError(f"Restart policy must be 'no', got '{restart_policy.get('Name')}'")
