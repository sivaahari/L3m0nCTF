import logging
import os
import re
import sys
import threading
import time

from services.instancer.docker_client import DockerClient
from services.instancer.manager import InstanceManager
from services.instancer.manifest import load_manifest
from services.instancer.server import create_instancer_server, create_metrics_server

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
_log = logging.getLogger("l3mon.instancer")


def _read_secret(name: str, default: str = "") -> str:
    path = f"/run/secrets/{name}"
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                val = f.read().strip()
                if val:
                    return val
        except Exception as e:
            _log.warning("Could not read secret file %s: %s", path, e)
    return os.getenv(name, default).strip()


def run_reaper_loop(manager: InstanceManager, interval: int = 5):
    """Periodically check and reap expired containers."""
    while True:
        try:
            time.sleep(interval)
            reaped = manager.reap_expired()
            if reaped:
                _log.info("Reaped %d expired container(s): %s", len(reaped), reaped)
        except Exception as e:
            _log.error("Error in reaper loop: %s", e)


def main():
    _log.info("Starting L3m0nCTF Challenge Instancer Service")
    dev_mode = os.getenv("INSTANCER_DEV", "0") in ("1", "true", "yes")
    agent_mode = os.getenv("INSTANCER_AGENT_MODE", "0") in ("1", "true", "yes")
    host_name = os.getenv("INSTANCER_HOST_NAME", "default").strip()

    clean_name = re.sub(r"[^A-Za-z0-9_]", "_", host_name.upper())
    instancer_token = _read_secret(f"INSTANCER_TOKEN_{clean_name}") or _read_secret("INSTANCER_TOKEN")
    router_token = _read_secret(f"ROUTER_TOKEN_{clean_name}") or _read_secret("ROUTER_TOKEN")
    if not instancer_token:
        _log.error("INSTANCER_TOKEN is not set or empty. Exiting.")
        sys.exit(1)

    manifest_path = os.getenv("MANIFEST_PATH", "/config/challenges.toml")
    if not os.path.exists(manifest_path):
        manifest_path = "config/instancer/challenges.example.toml"

    _log.info("Loading manifest from %s (dev_mode=%s, agent_mode=%s, host_name=%s)", manifest_path, dev_mode, agent_mode, host_name)
    manifest = load_manifest(manifest_path, dev_mode=dev_mode)

    docker = DockerClient()
    instance_domain = os.getenv("INSTANCE_DOMAIN", "localhost:8090")
    tcp_host = os.getenv("INSTANCER_TCP_HOST", "localhost")
    tcp_port_start = int(os.getenv("INSTANCER_TCP_PORT_START", "30000"))
    tcp_port_count = int(os.getenv("INSTANCER_TCP_PORT_COUNT", "200"))
    router_container_name = os.getenv("ROUTER_CONTAINER_NAME", "l3mon-router-1")

    manager = InstanceManager(
        manifest=manifest,
        docker_client=docker,
        instance_domain=instance_domain,
        tcp_host=tcp_host,
        tcp_port_start=tcp_port_start,
        tcp_port_count=tcp_port_count,
        router_container_name=router_container_name,
        dev_mode=dev_mode,
        host_name=host_name,
        agent_mode=agent_mode,
    )

    # Reconcile existing containers from Docker labels
    _log.info("Reconciling active containers from Docker labels...")
    try:
        manager.reconcile_from_docker()
        active = manager.list_instances()
        _log.info("Discovered %d active instance(s) from Docker labels", len(active))
    except Exception as e:
        _log.warning("Failed to reconcile from Docker: %s", e)

    # Start TTL reaper thread
    reaper_thread = threading.Thread(target=run_reaper_loop, args=(manager,), daemon=True)
    reaper_thread.start()

    # Start metrics server on 8301
    m_server = create_metrics_server("0.0.0.0", 8301, manager)
    m_thread = threading.Thread(target=m_server.serve_forever, daemon=True)
    m_thread.start()
    _log.info("Metrics listener active on 0.0.0.0:8301")

    # Start main API server on 8300
    server = create_instancer_server("0.0.0.0", 8300, manager, instancer_token, router_token)
    _log.info("Instancer API active on 0.0.0.0:8300")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log.info("Shutting down instancer service...")
        server.shutdown()
        m_server.shutdown()


if __name__ == "__main__":
    main()
