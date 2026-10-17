import json
import logging
import os
import socket
import urllib.parse
from http.client import HTTPConnection
from typing import Any, Optional

_log = logging.getLogger("l3mon.instancer.docker")


class UnixHTTPConnection(HTTPConnection):
    def __init__(self, unix_socket_path: str, timeout: int = 30):
        super().__init__("localhost", timeout=timeout)
        self.unix_socket_path = unix_socket_path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self.unix_socket_path)
        self.sock = sock


class DockerClient:
    """Minimal Docker Engine API client using standard library http.client.

    Supports both HTTP/TCP (socket proxy at e.g. http://socket-proxy:2375)
    and UNIX domain sockets (/var/run/docker.sock).
    """

    def __init__(self, base_url: Optional[str] = None, timeout: int = 30):
        url = base_url or os.getenv("DOCKER_HOST", "http://socket-proxy:2375")
        self.base_url = url
        self.timeout = timeout
        self.is_unix = url.startswith("unix://")
        if self.is_unix:
            self.socket_path = url[len("unix://"):]
        else:
            if url.startswith("tcp://"):
                url = "http://" + url[len("tcp://"):]
            parsed = urllib.parse.urlparse(url)
            self.host = parsed.hostname or "127.0.0.1"
            self.port = parsed.port or 2375

    def _get_connection(self) -> HTTPConnection:
        if self.is_unix:
            return UnixHTTPConnection(self.socket_path, timeout=self.timeout)
        return HTTPConnection(self.host, self.port, timeout=self.timeout)

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> tuple[int, Any]:
        conn = self._get_connection()
        try:
            headers = {"Accept": "application/json"}
            payload = None
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
                headers["Content-Length"] = str(len(payload))

            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            status = resp.status
            raw_data = resp.read()
            data = None
            if raw_data:
                try:
                    data = json.loads(raw_data.decode("utf-8"))
                except Exception:
                    data = raw_data.decode("utf-8", errors="replace")
            return status, data
        finally:
            conn.close()

    def create_network(self, name: str, internal: bool = True, labels: Optional[dict] = None) -> str:
        body = {
            "Name": name,
            "CheckDuplicate": True,
            "Driver": "bridge",
            "Internal": internal,
            "Labels": labels or {},
        }
        status, data = self._request("POST", "/networks/create", body)
        if status not in (200, 201):
            raise RuntimeError(f"Docker create network '{name}' failed ({status}): {data}")
        return data.get("Id", name)

    def remove_network(self, net_id: str) -> None:
        status, data = self._request("DELETE", f"/networks/{net_id}")
        if status not in (200, 204, 404):
            _log.warning("Docker remove network '%s' returned status %s: %s", net_id, status, data)

    def connect_network(self, net_id: str, container_id: str) -> None:
        body = {"Container": container_id}
        status, data = self._request("POST", f"/networks/{net_id}/connect", body)
        if status not in (200, 201):
            raise RuntimeError(f"Docker connect container '{container_id}' to network '{net_id}' failed ({status}): {data}")

    def disconnect_network(self, net_id: str, container_id: str) -> None:
        body = {"Container": container_id, "Force": True}
        status, data = self._request("POST", f"/networks/{net_id}/disconnect", body)
        if status not in (200, 204, 404):
            _log.warning("Docker disconnect container '%s' from network '%s' returned %s: %s", container_id, net_id, status, data)

    def create_container(self, spec: dict, name: Optional[str] = None) -> str:
        path = f"/containers/create?name={name}" if name else "/containers/create"
        status, data = self._request("POST", path, spec)
        if status not in (200, 201):
            raise RuntimeError(f"Docker create container failed ({status}): {data}")
        return data["Id"]

    def start_container(self, container_id: str) -> None:
        status, data = self._request("POST", f"/containers/{container_id}/start")
        if status not in (200, 204, 304):
            raise RuntimeError(f"Docker start container '{container_id}' failed ({status}): {data}")

    def stop_container(self, container_id: str, timeout: int = 5) -> None:
        status, data = self._request("POST", f"/containers/{container_id}/stop?t={timeout}")
        if status not in (200, 204, 304, 404):
            _log.warning("Docker stop container '%s' returned %s: %s", container_id, status, data)

    def remove_container(self, container_id: str, force: bool = True) -> None:
        status, data = self._request("DELETE", f"/containers/{container_id}?force={'true' if force else 'false'}&v=true")
        if status not in (200, 204, 404):
            _log.warning("Docker remove container '%s' returned %s: %s", container_id, status, data)

    def list_containers(self, label_filter: Optional[dict] = None) -> list[dict]:
        filters: dict[str, list[str]] = {}
        if label_filter:
            label_list = []
            for k, v in label_filter.items():
                if v is None:
                    label_list.append(k)
                else:
                    label_list.append(f"{k}={v}")
            filters["label"] = label_list

        query = urllib.parse.urlencode({"all": "true", "filters": json.dumps(filters)})
        status, data = self._request("GET", f"/containers/json?{query}")
        if status != 200 or not isinstance(data, list):
            _log.warning("Docker list_containers returned status %s: %s", status, data)
            return []

        result = []
        for c in data:
            cid = c.get("Id", "")
            labels = c.get("Labels", {})
            state = c.get("State", "")
            # Extract first IP address in networks
            ip = None
            networks = c.get("NetworkSettings", {}).get("Networks", {})
            for net_info in networks.values():
                if net_info.get("IPAddress"):
                    ip = net_info["IPAddress"]
                    break

            result.append({
                "id": cid,
                "labels": labels,
                "state": state,
                "ip": ip,
            })
        return result
