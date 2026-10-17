import hmac
import json
import logging
import re
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Optional

from services.instancer.manager import (
    InstanceManager,
    QuotaError,
    CooldownError,
    CapacityError,
    SlugNotFoundError,
)

_log = logging.getLogger("l3mon.instancer.server")


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class InstancerAPIHandler(BaseHTTPRequestHandler):
    """Internal API handler for port 8300 (authenticated via Bearer token)."""

    def log_message(self, format, *args):
        # Silence default stderr logging to keep logs structured and clean
        _log.info("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    @property
    def manager(self) -> InstanceManager:
        return self.server.manager

    @property
    def instancer_token(self) -> str:
        return self.server.instancer_token

    @property
    def router_token(self) -> str:
        return self.server.router_token

    def _send_json(self, status: int, data: Optional[dict] = None, headers: Optional[dict] = None):
        body = b""
        if data is not None:
            body = json.dumps(data, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if headers:
            for k, v in headers.items():
                self.send_header(k, str(v))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_no_content(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _check_auth(self, allow_router: bool = False) -> bool:
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            self._send_json(401, {"error": "unauthorized", "message": "Missing or malformed Authorization header"})
            return False

        token = auth_header[7:].strip()
        # Constant-time comparison
        valid_instancer = hmac.compare_digest(token, self.instancer_token)
        valid_router = allow_router and hmac.compare_digest(token, self.router_token)

        if not (valid_instancer or valid_router):
            self._send_json(401, {"error": "unauthorized", "message": "Invalid token"})
            return False
        return True

    def _read_json(self) -> Optional[dict]:
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/v1/routes":
            if not self._check_auth(allow_router=True):
                return
            routes = self.manager.get_routes()
            self._send_json(200, {"routes": routes})
            return

        if not self._check_auth(allow_router=False):
            return

        if path == "/v1/capacity":
            cap = self.manager.get_capacity()
            self._send_json(200, cap)
            return

        if path == "/v1/instances":
            team_id = None
            if "team_id" in query:
                try:
                    team_id = int(query["team_id"][0])
                except ValueError:
                    self._send_json(400, {"error": "invalid_request", "message": "team_id must be integer"})
                    return
            instances = self.manager.list_instances(team_id=team_id)
            self._send_json(200, {"instances": instances})
            return

        match = re.fullmatch(r"/v1/instances/([a-z0-9]{32})", path)
        if match:
            inst_id = match.group(1)
            inst = self.manager.get_instance(inst_id)
            if not inst:
                self._send_json(404, {"error": "not_found", "message": "Instance not found"})
                return
            self._send_json(200, {"instance": inst})
            return

        self._send_json(404, {"error": "not_found", "message": "Endpoint not found"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if not self._check_auth(allow_router=False):
            return

        if path == "/v1/instances":
            body = self._read_json()
            if body is None or not isinstance(body, dict):
                self._send_json(400, {"error": "invalid_request", "message": "Body must be valid JSON object"})
                return

            team_id = body.get("team_id")
            challenge_id = body.get("challenge_id")
            slug = body.get("slug")
            flag = body.get("flag")
            ttl_seconds = body.get("ttl_seconds")

            if not isinstance(team_id, int) or not isinstance(challenge_id, int) or not slug or not flag:
                self._send_json(400, {"error": "invalid_request", "message": "team_id, challenge_id, slug, and flag are required"})
                return

            try:
                inst, created = self.manager.start_instance(
                    team_id=team_id,
                    challenge_id=challenge_id,
                    slug=slug,
                    flag=flag,
                    ttl_seconds=ttl_seconds,
                )
                status = 201 if created else 200
                self._send_json(status, {"instance": inst})
                return
            except SlugNotFoundError as e:
                self._send_json(404, {"error": "not_found", "message": str(e)})
                return
            except QuotaError as e:
                self._send_json(409, {"error": "quota", "message": str(e)})
                return
            except CooldownError as e:
                self._send_json(429, {"error": "cooldown", "message": str(e), "retry_after": e.retry_after}, headers={"Retry-After": str(e.retry_after)})
                return
            except CapacityError as e:
                self._send_json(503, {"error": "no_capacity", "message": str(e), "retry_after": e.retry_after}, headers={"Retry-After": str(e.retry_after)})
                return
            except ValueError as e:
                self._send_json(400, {"error": "invalid_request", "message": str(e)})
                return
            except Exception as e:
                _log.exception("Error starting instance: %s", e)
                self._send_json(500, {"error": "internal_error", "message": str(e)})
                return

        match_renew = re.fullmatch(r"/v1/instances/([a-z0-9]{32})/renew", path)
        if match_renew:
            inst_id = match_renew.group(1)
            try:
                inst = self.manager.renew_instance(inst_id)
                self._send_json(200, {"instance": inst})
                return
            except SlugNotFoundError as e:
                self._send_json(404, {"error": "not_found", "message": str(e)})
                return
            except ValueError as e:
                self._send_json(429, {"error": "cooldown", "message": str(e), "retry_after": 60}, headers={"Retry-After": "60"})
                return
            except Exception as e:
                self._send_json(500, {"error": "internal_error", "message": str(e)})
                return

        if path == "/v1/admin/kill":
            body = self._read_json() or {}
            team_id = body.get("team_id")
            challenge_id = body.get("challenge_id")
            stopped = self.manager.kill_instances(team_id=team_id, challenge_id=challenge_id)
            self._send_json(200, {"stopped": stopped})
            return

        self._send_json(404, {"error": "not_found", "message": "Endpoint not found"})

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if not self._check_auth(allow_router=False):
            return

        match = re.fullmatch(r"/v1/instances/([a-z0-9]{32})", path)
        if match:
            inst_id = match.group(1)
            self.manager.stop_instance(inst_id)
            self._send_no_content()
            return

        self._send_json(404, {"error": "not_found", "message": "Endpoint not found"})


class MetricsAPIHandler(BaseHTTPRequestHandler):
    """Unauthenticated handler for port 8301 (/healthz, /metrics)."""

    def log_message(self, format, *args):
        pass

    @property
    def manager(self) -> InstanceManager:
        return self.server.manager

    def do_GET(self):
        if self.path == "/healthz":
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/metrics":
            cap = self.manager.get_capacity()
            instances = self.manager.list_instances()
            lines = [
                "# HELP l3mon_instances_slots_total Total container slots capacity on this host.",
                "# TYPE l3mon_instances_slots_total gauge",
                f"l3mon_instances_slots_total {cap['slots_total']}",
                "# HELP l3mon_instances_slots_used Currently used container slots.",
                "# TYPE l3mon_instances_slots_used gauge",
                f"l3mon_instances_slots_used {cap['slots_used']}",
                "# HELP l3mon_instances_mem_mb_used Currently allocated memory in MB.",
                "# TYPE l3mon_instances_mem_mb_used gauge",
                f"l3mon_instances_mem_mb_used {cap['mem_mb_used']}",
                "# HELP l3mon_instances_active Active instances by protocol.",
                "# TYPE l3mon_instances_active gauge",
                f'l3mon_instances_active{{protocol="http"}} {sum(1 for i in instances if i.get("protocol") == "http")}',
                f'l3mon_instances_active{{protocol="tcp"}} {sum(1 for i in instances if i.get("protocol") == "tcp")}',
            ]
            content = "\n".join(lines).encode("utf-8") + b"\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        self.send_response(404)
        self.end_headers()


def create_instancer_server(
    host: str,
    port: int,
    manager: InstanceManager,
    instancer_token: str,
    router_token: str,
) -> ThreadedHTTPServer:
    server = ThreadedHTTPServer((host, port), InstancerAPIHandler)
    server.manager = manager
    server.instancer_token = instancer_token
    server.router_token = router_token
    return server


def create_metrics_server(
    host: str,
    port: int,
    manager: InstanceManager,
) -> ThreadedHTTPServer:
    server = ThreadedHTTPServer((host, port), MetricsAPIHandler)
    server.manager = manager
    return server
