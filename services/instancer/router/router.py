import asyncio
import collections
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

MAX_BODY_BYTES = 10 * 1024 * 1024  # 10 MB client request cap
MAX_RESPONSE_BYTES = 20 * 1024 * 1024  # 20 MB backend response streaming cap
IDLE_TIMEOUT_SECONDS = 30
MAX_CONNECTIONS_PER_INSTANCE = 50
MAX_TOTAL_CONNECTIONS = 1000
CHUNK_SIZE = 64 * 1024  # 64 KB


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


def sanitize_headers(headers: dict | list[tuple[str, str]], is_websocket: bool = False) -> dict | list[tuple[str, str]]:
    """Strip hop-by-hop headers to prevent proxy bypass, preserving multi-valued headers."""
    if isinstance(headers, dict):
        sanitized_dict = {}
        for k, v in headers.items():
            if k.lower().strip() not in _HOP_BY_HOP:
                sanitized_dict[k] = v
        return sanitized_dict

    sanitized_list = []
    for k, v in headers:
        k_lower = k.lower().strip()
        if k_lower not in _HOP_BY_HOP:
            sanitized_list.append((k.strip(), v.strip()))
    return sanitized_list


class InstanceRouter:
    """Reverse proxy carrying HTTP traffic to per-team challenge instances without Docker daemon access."""

    def __init__(
        self,
        instance_domain: str = "localhost:8090",
        instancer_url: str = "http://instancer:8300",
        router_token: str = "",
        poll_interval: float = 2.0,
        max_connections_per_instance: int = MAX_CONNECTIONS_PER_INSTANCE,
        max_total_connections: int = MAX_TOTAL_CONNECTIONS,
    ):
        self.instance_domain = instance_domain
        self.instancer_url = instancer_url
        self.router_token = router_token
        self.poll_interval = poll_interval
        self.max_connections_per_instance = max_connections_per_instance
        self.max_total_connections = max_total_connections

        self._routes: dict[str, dict] = {}  # instance_id -> route dict
        self._lock = asyncio.Lock()
        self._active_connections_per_inst: dict[str, int] = collections.defaultdict(int)
        self._total_active_connections: int = 0
        self._conn_lock = asyncio.Lock()

    def update_routes(self, routes: list[dict]):
        new_routes = {}
        for r in routes:
            inst_id = r.get("id")
            if inst_id:
                new_routes[inst_id] = r
        self._routes = new_routes

    async def acquire_connection(self, inst_id: str) -> tuple[bool, int, str]:
        """Atomically check and acquire a connection slot."""
        async with self._conn_lock:
            if self._total_active_connections >= self.max_total_connections:
                return False, 503, "Service Busy (Global Limit)"
            current = self._active_connections_per_inst.get(inst_id, 0)
            if current >= self.max_connections_per_instance:
                return False, 503, "Service Busy (Instance Limit)"
            self._total_active_connections += 1
            self._active_connections_per_inst[inst_id] = current + 1
            return True, 200, ""

    async def release_connection(self, inst_id: str):
        """Atomically release an acquired connection slot."""
        async with self._conn_lock:
            self._total_active_connections = max(0, self._total_active_connections - 1)
            current = self._active_connections_per_inst.get(inst_id, 0)
            if current <= 1:
                self._active_connections_per_inst.pop(inst_id, None)
            else:
                self._active_connections_per_inst[inst_id] = current - 1

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

    async def forward_stream(
        self,
        method: str,
        path: str,
        client_headers: list[tuple[str, str]],
        body: bytes,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        """Stream client request to backend instance and stream backend response back to client."""
        if len(body) > MAX_BODY_BYTES:
            await self._refuse_stream(client_writer, 413, "Payload Too Large (Max 10MB)\n")
            return

        host_header = ""
        for hk, hv in client_headers:
            if hk.lower() == "host":
                host_header = hv
                break

        inst_id = extract_instance_id(host_header, self.instance_domain)
        if not inst_id:
            await self._refuse_stream(client_writer, 404, "Instance not found or invalid host\n")
            return

        route = self._routes.get(inst_id)
        if not route or route.get("protocol") != "http":
            await self._refuse_stream(client_writer, 404, "Instance not found\n")
            return

        target_ip = route.get("target_ip")
        target_port = route.get("target_port")
        if not target_ip or not target_port:
            await self._refuse_stream(client_writer, 503, "Instance target unavailable\n")
            return

        acquired, err_status, err_msg = await self.acquire_connection(inst_id)
        if not acquired:
            await self._refuse_stream(client_writer, err_status, f"{err_msg}\n")
            return

        writer = None  # the connection to the instance: closed in the finally below whatever happens (integration audit: a timeout or an error left it open)
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(target_ip, target_port),
                timeout=IDLE_TIMEOUT_SECONDS,
            )

            # Strip hop-by-hop and Content-Length from client headers to send exactly one Content-Length
            fwd_headers = []
            for k, v in client_headers:
                kl = k.lower().strip()
                if kl not in _HOP_BY_HOP and kl != "content-length":
                    fwd_headers.append((k.strip(), v.strip()))

            req_lines = [f"{method} {path} HTTP/1.1"]
            for k, v in fwd_headers:
                req_lines.append(f"{k}: {v}")
            req_lines.append(f"Content-Length: {len(body)}")
            req_lines.append("Connection: close")
            req_data = "\r\n".join(req_lines).encode("utf-8") + b"\r\n\r\n" + body

            writer.write(req_data)
            await writer.drain()

            # Read backend status line
            status_line_bytes = await asyncio.wait_for(reader.readline(), timeout=IDLE_TIMEOUT_SECONDS)
            if not status_line_bytes:
                await self._refuse_stream(client_writer, 502, "Bad Gateway: Empty backend response\n")
                writer.close()
                await writer.wait_closed()
                return

            status_line = status_line_bytes.decode("utf-8", errors="replace").strip()

            # Read backend response headers (up to 100 header lines)
            backend_resp_headers: list[tuple[str, str]] = []
            content_length_val: Optional[int] = None
            is_chunked: bool = False

            for _ in range(101):
                header_line = await asyncio.wait_for(reader.readline(), timeout=IDLE_TIMEOUT_SECONDS)
                if not header_line or header_line == b"\r\n":
                    break
                line_str = header_line.decode("utf-8", errors="replace").strip()
                if ":" in line_str:
                    hk, hv = line_str.split(":", 1)
                    hk_clean = hk.strip()
                    hv_clean = hv.strip()
                    hkl = hk_clean.lower()
                    if hkl == "transfer-encoding" and "chunked" in hv_clean.lower():
                        is_chunked = True
                    elif hkl not in _HOP_BY_HOP:
                        if hkl == "content-length":
                            try:
                                content_length_val = int(hv_clean)
                            except ValueError:
                                pass
                        else:
                            backend_resp_headers.append((hk_clean, hv_clean))

            # Send headers to client
            out_lines = [status_line]
            for hk, hv in backend_resp_headers:
                out_lines.append(f"{hk}: {hv}")
            if is_chunked:
                out_lines.append("Transfer-Encoding: chunked")
            elif content_length_val is not None:
                out_lines.append(f"Content-Length: {content_length_val}")
            out_lines.append("Connection: close")
            head_bytes = "\r\n".join(out_lines).encode("utf-8") + b"\r\n\r\n"

            client_writer.write(head_bytes)
            await client_writer.drain()

            # Stream backend response body with 20 MB cap
            total_streamed = 0
            while True:
                chunk = await asyncio.wait_for(reader.read(CHUNK_SIZE), timeout=IDLE_TIMEOUT_SECONDS)
                if not chunk:
                    break
                total_streamed += len(chunk)
                if total_streamed > MAX_RESPONSE_BYTES:
                    _log.warning("Backend response exceeded 20MB cap (%d bytes); terminating connection", total_streamed)
                    break
                client_writer.write(chunk)
                await client_writer.drain()

            writer.close()
            await writer.wait_closed()

        except asyncio.TimeoutError:
            await self._refuse_stream(client_writer, 504, "Gateway Timeout\n")
        except Exception as e:
            _log.warning("Error forwarding to %s:%s: %s", target_ip, target_port, e)
            await self._refuse_stream(client_writer, 502, "Bad Gateway\n")
        finally:
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), timeout=2)
                except Exception:
                    pass
            await self.release_connection(inst_id)

    async def _refuse_stream(self, writer: asyncio.StreamWriter, status: int, text: str) -> None:
        body = text.encode("utf-8")
        status_text = (
            "OK" if status == 200
            else "Not Found" if status == 404
            else "Bad Request" if status == 400
            else "Request Timeout" if status == 408
            else "Payload Too Large" if status == 413
            else "Request Header Fields Too Large" if status == 431
            else "Service Unavailable" if status == 503
            else "Gateway Timeout" if status == 504
            else "Bad Gateway" if status == 502
            else "Error"
        )
        head = f"HTTP/1.1 {status} {status_text}\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        writer.write(head.encode("ascii") + body)
        await writer.drain()

    async def handle_http_request(
        self, method: str, path: str, headers: dict | list[tuple[str, str]], body: bytes
    ) -> tuple[int, dict, bytes]:
        """Adapter for backward compatibility with test callers."""
        class _BufferWriter:
            def __init__(self):
                self.data = b""

            def write(self, b):
                self.data += b

            async def drain(self):
                pass

        if isinstance(headers, dict):
            client_headers = list(headers.items())
        else:
            client_headers = headers

        bw = _BufferWriter()
        await self.forward_stream(method, path, client_headers, body, bw)

        parts = bw.data.split(b"\r\n\r\n", 1)
        if len(parts) < 2:
            return 502, {"Content-Type": "text/plain"}, b"Bad Gateway\n"

        head_part = parts[0].decode("utf-8", errors="replace")
        resp_body = parts[1]
        lines = head_part.split("\r\n")
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
