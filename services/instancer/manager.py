import logging
import secrets
import threading
import time
from typing import Callable, Optional

from services.instancer.flag import is_valid_flag_shape
from services.instancer.manifest import ChallengeSpec, Manifest
from services.instancer.safety import assert_safe

_log = logging.getLogger("l3mon.instancer")

MAX_INSTANCES_PER_TEAM = 3
COOLDOWN_SECONDS = 60
MIN_RENEW_INTERVAL_SECONDS = 60
ABSOLUTE_LIFETIME_SECONDS = 3 * 3600  # 3 hours


class InstancerError(Exception):
    """Base exception for instancer operations."""
    pass


class SlugNotFoundError(InstancerError):
    """Raised when the requested slug is not in the manifest."""
    pass


class QuotaError(InstancerError):
    """Raised when team active instance quota is reached."""
    pass


class CooldownError(InstancerError):
    """Raised when start is requested during challenge cooldown."""
    def __init__(self, retry_after: int, message: str = "Challenge is in cooldown"):
        super().__init__(message)
        self.retry_after = retry_after


class CapacityError(InstancerError):
    """Raised when the host has reached maximum capacity."""
    def __init__(self, retry_after: int = 30, message: str = "No capacity available on this host"):
        super().__init__(message)
        self.retry_after = retry_after


class InstanceState:
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    EXPIRED = "expired"
    ERROR = "error"


class InstanceRecord:
    def __init__(
        self,
        instance_id: str,
        team_id: int,
        challenge_id: int,
        slug: str,
        protocol: str,
        internal_port: int,
        created_at: int,
        expires_at: int,
        allocated_tcp_port: Optional[int] = None,
        state: str = InstanceState.STARTING,
        renewals: int = 0,
        last_renewed_at: Optional[int] = None,
        target_ip: Optional[str] = None,
        container_id: Optional[str] = None,
    ):
        self.id = instance_id
        self.team_id = team_id
        self.challenge_id = challenge_id
        self.slug = slug
        self.protocol = protocol
        self.internal_port = internal_port
        self.created_at = created_at
        self.expires_at = expires_at
        self.allocated_tcp_port = allocated_tcp_port
        self.state = state
        self.renewals = renewals
        self.last_renewed_at = last_renewed_at
        self.target_ip = target_ip
        self.container_id = container_id

    def to_dict(self, instance_domain: str, tcp_host: str) -> dict:
        url = f"http://{self.id}.{instance_domain}/" if self.protocol == "http" else None
        host = tcp_host if self.protocol == "tcp" else None
        return {
            "id": self.id,
            "team_id": self.team_id,
            "challenge_id": self.challenge_id,
            "slug": self.slug,
            "state": self.state,
            "protocol": self.protocol,
            "url": url,
            "host": host,
            "port": self.allocated_tcp_port if self.protocol == "tcp" else None,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "renewals": self.renewals,
        }


class InstanceManager:
    """Manages ephemeral per-team challenge instances, lifecycle, quotas, and label reconciliation."""

    def __init__(
        self,
        manifest: Manifest,
        docker_client,
        instance_domain: str = "localhost:8090",
        tcp_host: str = "localhost",
        tcp_port_start: int = 30000,
        tcp_port_count: int = 200,
        max_slots: int = 200,
        max_mem_mb: int = 16384,
        router_container_name: str = "l3mon-router",
        dev_mode: bool = False,
        time_fn: Optional[Callable[[], float]] = None,
    ):
        self.manifest = manifest
        self.docker = docker_client
        self.instance_domain = instance_domain
        self.tcp_host = tcp_host
        self.tcp_port_start = tcp_port_start
        self.tcp_port_count = tcp_port_count
        self.max_slots = max_slots
        self.max_mem_mb = max_mem_mb
        self.router_container_name = router_container_name
        self.dev_mode = dev_mode
        self._time = time_fn or time.time
        self._lock = threading.Lock()

        # State storage
        self._instances: dict[str, InstanceRecord] = {}  # instance_id -> record
        self._team_index: dict[int, set[str]] = {}       # team_id -> set of instance_ids
        self._challenge_index: dict[tuple[int, int], str] = {}  # (team_id, challenge_id) -> instance_id
        self._cooldowns: dict[tuple[int, int], int] = {}  # (team_id, challenge_id) -> expiry_ts
        self._allocated_tcp_ports: set[int] = set()

    def _now(self) -> int:
        return int(self._time())

    def reconcile_from_docker(self) -> None:
        """Scan Docker containers with label 'l3mon.instance' and rebuild in-memory state."""
        with self._lock:
            containers = self.docker.list_containers(label_filter={"l3mon.instance": None})
            now = self._now()
            for c in containers:
                labels = c.get("labels", {})
                inst_id = labels.get("l3mon.instance")
                if not inst_id:
                    continue
                try:
                    team_id = int(labels.get("l3mon.team_id", 0))
                    challenge_id = int(labels.get("l3mon.challenge_id", 0))
                    slug = labels.get("l3mon.slug", "")
                    created_at = int(labels.get("l3mon.created_at", now))
                    expires_at = int(labels.get("l3mon.expires_at", now))
                    protocol = labels.get("l3mon.protocol", "http")
                    port = int(labels.get("l3mon.port", 8080))
                    renewals = int(labels.get("l3mon.renewals", 0))
                    tcp_port = int(labels.get("l3mon.tcp_port")) if labels.get("l3mon.tcp_port") else None
                except (ValueError, TypeError):
                    continue

                state = InstanceState.RUNNING if c.get("state") in ("running", "created") else InstanceState.ERROR
                record = InstanceRecord(
                    instance_id=inst_id,
                    team_id=team_id,
                    challenge_id=challenge_id,
                    slug=slug,
                    protocol=protocol,
                    internal_port=port,
                    created_at=created_at,
                    expires_at=expires_at,
                    allocated_tcp_port=tcp_port,
                    state=state,
                    renewals=renewals,
                    target_ip=c.get("ip"),
                    container_id=c.get("id"),
                )
                self._instances[inst_id] = record
                self._team_index.setdefault(team_id, set()).add(inst_id)
                self._challenge_index[(team_id, challenge_id)] = inst_id
                if tcp_port is not None:
                    self._allocated_tcp_ports.add(tcp_port)

            # Clean orphan networks (created before a crash without a running container)
            try:
                networks = self.docker.list_networks(label_filter={"l3mon.instance": None})
                for net in networks:
                    labels = net.get("Labels", {}) or {}
                    net_inst_id = labels.get("l3mon.instance")
                    if net_inst_id and net_inst_id not in self._instances:
                        net_id = net.get("Id") or net.get("Name")
                        _log.info("Pruning orphan network %s (instance %s)", net_id, net_inst_id)
                        try:
                            self.docker.disconnect_network(net_id, self.router_container_name)
                        except Exception:
                            pass
                        try:
                            self.docker.remove_network(net_id)
                        except Exception:
                            pass
            except Exception as e:
                _log.warning("Error reconciling orphan networks: %s", e)

    def start_instance(
        self, team_id: int, challenge_id: int, slug: str, flag: str, ttl_seconds: Optional[int] = None
    ) -> tuple[dict, bool]:
        """Start a per-team challenge instance or return existing active one. Returns (instance_dict, created_boolean)."""
        if not is_valid_flag_shape(flag):
            raise ValueError("Flag does not match the required format")

        spec = self.manifest.get(slug)
        if spec is None:
            raise SlugNotFoundError(f"Unknown challenge slug '{slug}'")

        with self._lock:
            now = self._now()
            key = (team_id, challenge_id)

            # 1. Idempotency: return existing active instance if one exists
            existing_id = self._challenge_index.get(key)
            if existing_id and existing_id in self._instances:
                inst = self._instances[existing_id]
                if inst.state in (InstanceState.RUNNING, InstanceState.STARTING):
                    return inst.to_dict(self.instance_domain, self.tcp_host), False

            # 2. Cooldown check
            cooldown_until = self._cooldowns.get(key, 0)
            if now < cooldown_until:
                retry_after = cooldown_until - now
                raise CooldownError(retry_after=retry_after, message=f"Cooldown active. Retry in {retry_after}s")

            # 3. Quota check: at most 3 live instances per team
            active_team_instances = [
                self._instances[iid]
                for iid in self._team_index.get(team_id, set())
                if iid in self._instances and self._instances[iid].state in (InstanceState.RUNNING, InstanceState.STARTING)
            ]
            if len(active_team_instances) >= MAX_INSTANCES_PER_TEAM:
                raise QuotaError(f"Team active instance quota ({MAX_INSTANCES_PER_TEAM}) reached")

            # 4. Capacity check
            total_active = len([inst for inst in self._instances.values() if inst.state in (InstanceState.RUNNING, InstanceState.STARTING)])
            if total_active >= self.max_slots:
                raise CapacityError(retry_after=30, message="Host slot capacity reached")

            # 5. Allocate resources
            inst_id = secrets.token_hex(16).lower()  # 32 lowercase hex characters
            allocated_tcp_port = None
            if spec.protocol == "tcp":
                for p in range(self.tcp_port_start, self.tcp_port_start + self.tcp_port_count):
                    if p not in self._allocated_tcp_ports:
                        allocated_tcp_port = p
                        self._allocated_tcp_ports.add(p)
                        break
                if allocated_tcp_port is None:
                    raise CapacityError(retry_after=30, message="No available TCP relay ports")

            # Calculate TTL
            if self.dev_mode and ttl_seconds is not None:
                duration = int(ttl_seconds)
            else:
                duration = spec.ttl_minutes * 60
            expires_at = now + duration

            record = InstanceRecord(
                instance_id=inst_id,
                team_id=team_id,
                challenge_id=challenge_id,
                slug=slug,
                protocol=spec.protocol,
                internal_port=spec.port,
                created_at=now,
                expires_at=expires_at,
                allocated_tcp_port=allocated_tcp_port,
                state=InstanceState.STARTING,
                renewals=0,
            )
            self._instances[inst_id] = record
            self._team_index.setdefault(team_id, set()).add(inst_id)
            self._challenge_index[key] = inst_id

        # Network creation and Docker calls OUTSIDE self._lock
        net_name = f"l3mon_inst_{inst_id}"
        try:
            self.docker.create_network(net_name, internal=True, labels={"l3mon.instance": inst_id})
        except Exception as e:
            with self._lock:
                self._instances.pop(inst_id, None)
                if team_id in self._team_index:
                    self._team_index[team_id].discard(inst_id)
                if self._challenge_index.get(key) == inst_id:
                    self._challenge_index.pop(key, None)
                if allocated_tcp_port is not None:
                    self._allocated_tcp_ports.discard(allocated_tcp_port)
            raise InstancerError(f"Failed to create network: {e}")

        # Attach router to network
        try:
            self.docker.connect_network(net_name, self.router_container_name)
        except Exception as e:
            _log.warning("Could not connect router container '%s' to network '%s': %s", self.router_container_name, net_name, e)

        # Build Docker container spec
        container_labels = {
            "l3mon.instance": inst_id,
            "l3mon.team_id": str(team_id),
            "l3mon.challenge_id": str(challenge_id),
            "l3mon.slug": slug,
            "l3mon.created_at": str(now),
            "l3mon.expires_at": str(expires_at),
            "l3mon.protocol": spec.protocol,
            "l3mon.port": str(spec.port),
            "l3mon.renewals": "0",
        }
        if allocated_tcp_port is not None:
            container_labels["l3mon.tcp_port"] = str(allocated_tcp_port)

        container_spec = {
            "Image": spec.image,
            "User": "10001:10001",
            "Env": [f"FLAG={flag}"],
            "Labels": container_labels,
            "HostConfig": {
                "NetworkMode": net_name,
                "Privileged": False,
                "CapDrop": ["ALL"],
                "CapAdd": [],
                "SecurityOpt": ["no-new-privileges:true"],
                "ReadonlyRootfs": spec.read_only,
                "Init": True,
                "RestartPolicy": {"Name": "no"},
                "Binds": [],
                "VolumesFrom": [],
                "Devices": [],
                "NanoCpus": int(spec.cpus * 1_000_000_000),
                "Memory": spec.mem_mb * 1024 * 1024,
                "PidsLimit": spec.pids,
                "PortBindings": {},
                "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=64m"},
                "LogConfig": {"Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"}},
                "Ulimits": [{"Name": "nofile", "Soft": 1024, "Hard": 2048}],
            },
        }

        # Security verification
        try:
            assert_safe(container_spec, dev_mode=self.dev_mode)
        except Exception:
            try:
                self.docker.disconnect_network(net_name, self.router_container_name)
            except Exception:
                pass
            try:
                self.docker.remove_network(net_name)
            except Exception:
                pass
            with self._lock:
                self._instances.pop(inst_id, None)
                if team_id in self._team_index:
                    self._team_index[team_id].discard(inst_id)
                if self._challenge_index.get(key) == inst_id:
                    self._challenge_index.pop(key, None)
                if allocated_tcp_port is not None:
                    self._allocated_tcp_ports.discard(allocated_tcp_port)
            raise

        # Create and start container
        name = inst_id
        try:
            real_cid = self.docker.create_container(container_spec, name=name)
            self.docker.start_container(real_cid)
        except Exception as e:
            # Cleanup on failure
            try:
                self.docker.remove_container(name)
            except Exception:
                pass
            try:
                self.docker.disconnect_network(net_name, self.router_container_name)
                self.docker.remove_network(net_name)
            except Exception:
                pass
            with self._lock:
                self._instances.pop(inst_id, None)
                if team_id in self._team_index:
                    self._team_index[team_id].discard(inst_id)
                if self._challenge_index.get(key) == inst_id:
                    self._challenge_index.pop(key, None)
                if allocated_tcp_port is not None:
                    self._allocated_tcp_ports.discard(allocated_tcp_port)
            raise InstancerError(f"Failed to create/start container: {e}")

        # Retrieve target IP
        target_ip = None
        try:
            cinfo = self.docker.list_containers(label_filter={"l3mon.instance": inst_id})
            if cinfo:
                target_ip = cinfo[0].get("ip")
        except Exception:
            pass

        with self._lock:
            record.state = InstanceState.RUNNING
            record.target_ip = target_ip
            record.container_id = real_cid
            return record.to_dict(self.instance_domain, self.tcp_host), True

    def get_instance(self, instance_id: str) -> Optional[dict]:
        with self._lock:
            record = self._instances.get(instance_id)
            if not record:
                return None
            return record.to_dict(self.instance_domain, self.tcp_host)

    def list_instances(self, team_id: Optional[int] = None) -> list[dict]:
        with self._lock:
            res = []
            for inst in self._instances.values():
                if team_id is None or inst.team_id == team_id:
                    res.append(inst.to_dict(self.instance_domain, self.tcp_host))
            return res

    def renew_instance(self, instance_id: str) -> dict:
        """Renew an instance TTL. Refused if renewed < 60s ago or past 3h absolute lifetime cap."""
        with self._lock:
            record = self._instances.get(instance_id)
            if not record:
                raise SlugNotFoundError(f"Instance '{instance_id}' not found")

            now = self._now()
            # 1. Refuse if renewed less than 60s ago
            if record.last_renewed_at is not None and (now - record.last_renewed_at < MIN_RENEW_INTERVAL_SECONDS):
                raise ValueError("Instance cannot be renewed less than 60 seconds after previous renewal")

            # 2. Refuse if past absolute lifetime cap (3 hours from creation)
            if now - record.created_at >= ABSOLUTE_LIFETIME_SECONDS:
                raise ValueError("Instance has reached its absolute 3-hour lifetime cap")

            spec = self.manifest.get(record.slug)
            ttl_minutes = spec.ttl_minutes if spec else 45
            new_expiry = min(now + ttl_minutes * 60, record.created_at + ABSOLUTE_LIFETIME_SECONDS)

            record.expires_at = new_expiry
            record.last_renewed_at = now
            record.renewals += 1

            return record.to_dict(self.instance_domain, self.tcp_host)

    def stop_instance(self, instance_id: str) -> bool:
        """Stop and tear down an instance. Idempotent."""
        with self._lock:
            record = self._instances.get(instance_id)
            if not record:
                return False
            if record.state == InstanceState.STOPPING:
                return False

            now = self._now()
            record.state = InstanceState.STOPPING

            # Set cooldown for (team_id, challenge_id)
            key = (record.team_id, record.challenge_id)
            self._cooldowns[key] = now + COOLDOWN_SECONDS

            if record.allocated_tcp_port is not None:
                self._allocated_tcp_ports.discard(record.allocated_tcp_port)

            if record.team_id in self._team_index:
                self._team_index[record.team_id].discard(instance_id)
            if self._challenge_index.get(key) == instance_id:
                self._challenge_index.pop(key, None)

            target_container = getattr(record, "container_id", None) or instance_id

        # Cleanup docker container and network OUTSIDE self._lock
        net_name = f"l3mon_inst_{instance_id}"
        try:
            self.docker.stop_container(target_container)
            self.docker.remove_container(target_container)
        except Exception as e:
            _log.warning("Error stopping container %s: %s", target_container, e)

        try:
            self.docker.disconnect_network(net_name, self.router_container_name)
            self.docker.remove_network(net_name)
        except Exception as e:
            _log.warning("Error removing network %s: %s", net_name, e)

        with self._lock:
            self._instances.pop(instance_id, None)

        return True

    def kill_instances(self, team_id: Optional[int] = None, challenge_id: Optional[int] = None) -> int:
        """Admin kill by team and/or challenge, or all if none given."""
        to_kill = []
        with self._lock:
            for inst in list(self._instances.values()):
                match_team = team_id is None or inst.team_id == team_id
                match_chal = challenge_id is None or inst.challenge_id == challenge_id
                if match_team and match_chal:
                    to_kill.append(inst.id)

        count = 0
        for iid in to_kill:
            if self.stop_instance(iid):
                count += 1
        return count

    def reap_expired(self) -> list[str]:
        """Scan active instances and reap those past their expiry time."""
        expired_ids = []
        with self._lock:
            now = self._now()
            for inst in list(self._instances.values()):
                if inst.state in (InstanceState.RUNNING, InstanceState.STARTING) and now >= inst.expires_at:
                    expired_ids.append(inst.id)

        reaped = []
        for iid in expired_ids:
            if self.stop_instance(iid):
                reaped.append(iid)
        return reaped

    def get_capacity(self) -> dict:
        with self._lock:
            active = [inst for inst in self._instances.values() if inst.state in (InstanceState.RUNNING, InstanceState.STARTING)]
            slots_used = len(active)
            mem_mb_used = 0
            for inst in active:
                spec = self.manifest.get(inst.slug)
                if spec:
                    mem_mb_used += spec.mem_mb

            return {
                "slots_total": self.max_slots,
                "slots_used": slots_used,
                "mem_mb_total": self.max_mem_mb,
                "mem_mb_used": mem_mb_used,
            }

    def get_routes(self) -> list[dict]:
        """Returns the route list for the instance router."""
        with self._lock:
            routes = []
            for inst in self._instances.values():
                if inst.state == InstanceState.RUNNING and inst.target_ip:
                    routes.append({
                        "id": inst.id,
                        "protocol": inst.protocol,
                        "target_ip": inst.target_ip,
                        "target_port": inst.internal_port,
                        "port": inst.allocated_tcp_port,
                    })
            return routes
