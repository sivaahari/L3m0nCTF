import asyncio
import json
import logging
import os
import re
import urllib.parse
from typing import Optional

_log = logging.getLogger("l3mon.instancer.router")

_ID_PATTERN = re.compile(r"^[0-9a-f]{32}\Z")
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

MAX_BODY_BYTES = 10 * 1024 * 1024  # 10 MB
IDLE_TIMEOUT_SECONDS = 30
MAX_CONNECTIONS_PER_INSTANCE = 50


def extract_instance_id(host_header: str, instance_domain: str) -> Optional[str]:
    """Extract and validate the 32-hex instance ID from the Host header (<id>.<instance_domain>).

    Returns None if Host does not match the exact pattern or targets an outside host/IP.
    """
    if not host_header:
        return None

    # Strip port if present
    host = host_header.split(":")[0].strip().lower()
    suffix = "." + instance_domain.split(":")[0].strip().lower()

    if not host.endswith(suffix):
        return None

    prefix = host[: -len(suffix)]
    if not _ID_PATTERN.fullmatch(prefix):
        return None

    return prefix


def sanitize_headers(headers: dict, is_websocket: bool = False) -> dict:
    """Strip hop-by-hop headers to prevent proxy bypass, preserving WebSocket upgrade headers when applicable."""
    sanitized = {}
    for k, v in headers.items():
        k_lower = k.lower()
        if is_websocket and k_lower in ("connection", "upgrade"):
            sanitized[k] = v
        elif k_lower not in _HOP_BY_HOP:
            sanitized[k] = v
    return sanitized


class InstanceRouter:
    """Reverse proxy carrying HTTP and TCP traffic to per-team challenge instances without Docker daemon access."""

    def __init__(
        self,
        instance_domain: str = "localhost:8090",
        instancer_url: str = "http://instancer:8300",
        router_token: str = "",
        poll_interval: float = 2.0,
    ):
        self.instance_domain = instance_domain
        self.instancer_url = instancer_url
        self.router_token = router_token
        self.poll_interval = poll_interval
        self._routes: dict[str, dict] = {}  # instance_id -> route dict
        self._lock = asyncio.Lock()

    def update_routes(self, routes: list[dict]):
        new_routes = {}
        for r in routes:
            inst_id = r.get("id")
            if inst_id:
                new_routes[inst_id] = r
        self._routes = new_routes

    async def poll_routes_loop(self):
        """Poll the instancer service /v1/routes endpoint every 2 seconds."""
        parsed = urllib.parse.urlparse(self.instancer_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 8300

        while True:
            try:
                reader, writer = await asyncio.open_connection(host, port)
                req = (
                    f"GET /v1/routes HTTP/1.1\r\n"
                    f"Host: {host}:{port}\r\n"
                    f"Authorization: Bearer {self.router_token}\r\n"
                    f"Accept: application/json\r\n"
                    f"Connection: close\r\n\r\n"
                )
                writer.write(req.encode("utf-8"))
                await writer.drain()

                response_data = await reader.read()
                writer.close()
                await writer.wait_closed()

                parts = response_data.split(b"\r\n\r\n", 1)
                if len(parts) == 2:
                    body = json.loads(parts[1].decode("utf-8"))
                    routes = body.get("routes", [])
                    async with self._lock:
                        self.update_routes(routes)
            except Exception as e:
                _log.debug("Error polling routes from %s: %s", self.instancer_url, e)

            await asyncio.sleep(self.poll_interval)

    async def handle_http_request(
        self, method: str, path: str, headers: dict, body: bytes
    ) -> tuple[int, dict, bytes]:
        """Process an inbound HTTP request, match target instance, and forward traffic."""
        # Enforce max body size
        if len(body) > MAX_BODY_BYTES:
            return 413, {"Content-Type": "text/plain"}, b"Payload Too Large (Max 10MB)\n"

        host_header = headers.get("Host", "") or headers.get("host", "")
        inst_id = extract_instance_id(host_header, self.instance_domain)
        if not inst_id:
            # Strictly return static 404 for unknown hosts, IP targeting, or open-proxy attempts
            return 404, {"Content-Type": "text/plain"}, b"Instance not found or invalid host\n"

        route = self._routes.get(inst_id)
        if not route or route.get("protocol") != "http":
            return 404, {"Content-Type": "text/plain"}, b"Instance not found\n"

        target_ip = route.get("target_ip")
        target_port = route.get("target_port")
        if not target_ip or not target_port:
            return 503, {"Content-Type": "text/plain"}, b"Instance target unavailable\n"

        # Forward request to instance backend
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(target_ip, target_port),
                timeout=IDLE_TIMEOUT_SECONDS,
            )

            is_ws = headers.get("Upgrade", "").lower() == "websocket"
            fwd_headers = sanitize_headers(headers, is_websocket=is_ws)

            # Build HTTP request line and headers
            req_lines = [f"{method} {path} HTTP/1.1"]
            for k, v in fwd_headers.items():
                req_lines.append(f"{k}: {v}")
            req_lines.append(f"Content-Length: {len(body)}")
            req_lines.append("Connection: close")
            req_data = "\r\n".join(req_lines).encode("utf-8") + b"\r\n\r\n" + body

            writer.write(req_data)
            await writer.drain()

            raw_resp = await asyncio.wait_for(reader.read(), timeout=IDLE_TIMEOUT_SECONDS)
            writer.close()
            await writer.wait_closed()

            parts = raw_resp.split(b"\r\n\r\n", 1)
            if len(parts) == 2:
                header_part = parts[0].decode("utf-8", errors="replace")
                resp_body = parts[1]
                lines = header_part.split("\r\n")
                status = 200
                if lines and len(lines[0].split()) >= 2:
                    try:
                        status = int(lines[0].split()[1])
                    except ValueError:
                        status = 200

                resp_headers = {}
                for line in lines[1:]:
                    if ":" in line:
                        hk, hv = line.split(":", 1)
                        resp_headers[hk.strip()] = hv.strip()
                return status, resp_headers, resp_body
            else:
                return 502, {"Content-Type": "text/plain"}, b"Bad Gateway: Invalid backend response\n"

        except asyncio.TimeoutError:
            return 504, {"Content-Type": "text/plain"}, b"Gateway Timeout\n"
        except Exception as e:
            _log.warning("Error forwarding to %s:%s: %s", target_ip, target_port, e)
            return 502, {"Content-Type": "text/plain"}, b"Bad Gateway\n"
