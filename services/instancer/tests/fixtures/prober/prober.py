import http.server
import json
import os
import socket
import sys
import urllib.parse
import urllib.request

PORT = int(os.environ.get("PORT", "8080"))


def try_tcp_connect(host: str, port: int, timeout: float = 1.0) -> tuple[bool, str]:
    """Attempts a TCP connection. Returns (reachable, error_message)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        s.close()
        return True, "connected"
    except socket.timeout:
        return False, "timeout"
    except ConnectionRefusedError:
        return False, "connection_refused"
    except socket.gaierror as e:
        return False, f"dns_error: {e}"
    except OSError as e:
        return False, f"os_error: {e}"
    finally:
        try:
            s.close()
        except Exception:
            pass


def check_metadata(timeout: float = 1.0) -> tuple[bool, str]:
    url = "http://169.254.169.254/computeMetadata/v1/"
    req = urllib.request.Request(url, headers={"Metadata-Flavor": "Google"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, f"status_{resp.status}"
    except Exception as e:
        return False, str(e)


def run_isolation_probes(params: dict[str, str]) -> dict:
    results = {}
    timeout = float(params.get("timeout", "0.2"))

    # 1. Other instance by IP
    other_ip = params.get("other_ip") or os.environ.get("PROBE_OTHER_IP")
    other_port = int(params.get("other_port") or os.environ.get("PROBE_OTHER_PORT", "8080"))
    if other_ip:
        reachable, err = try_tcp_connect(other_ip, other_port, timeout=timeout)
        results["other_instance_by_ip"] = {
            "isolated": not reachable,
            "reachable": reachable,
            "target": f"{other_ip}:{other_port}",
            "detail": err,
        }
    else:
        results["other_instance_by_ip"] = {
            "isolated": True,
            "reachable": False,
            "target": "none",
            "detail": "target_ip_not_configured",
        }

    # 2. Other instance by hostname/container name
    other_name = params.get("other_name") or os.environ.get("PROBE_OTHER_NAME")
    if other_name:
        reachable, err = try_tcp_connect(other_name, other_port, timeout=timeout)
        results["other_instance_by_name"] = {
            "isolated": not reachable,
            "reachable": reachable,
            "target": f"{other_name}:{other_port}",
            "detail": err,
        }
    else:
        results["other_instance_by_name"] = {
            "isolated": True,
            "reachable": False,
            "target": "none",
            "detail": "target_name_not_configured",
        }

    # 3. Host gateway probe
    gateway_ip = params.get("gateway_ip") or os.environ.get("PROBE_GATEWAY_IP", "10.200.0.1")
    gateway_breached = False
    gw_details = {}
    for p in [22, 2375, 8080]:
        r, err = try_tcp_connect(gateway_ip, p, timeout=timeout)
        gw_details[str(p)] = {"reachable": r, "detail": err}
        if r:
            gateway_breached = True
    results["host_gateway"] = {
        "isolated": not gateway_breached,
        "reachable": gateway_breached,
        "ports": gw_details,
    }

    # 4. Internet outbound probe: public TCP connect AND DNS lookup
    pub_ip = params.get("public_ip") or os.environ.get("PROBE_PUBLIC_IP", "1.1.1.1")
    pub_host = params.get("public_host") or os.environ.get("PROBE_PUBLIC_HOST", "example.com")
    tcp_pub_reachable, tcp_pub_err = try_tcp_connect(pub_ip, 53, timeout=timeout)
    dns_resolved = False
    dns_err = "none"
    try:
        # Public domain DNS resolution attempt
        socket.getaddrinfo(pub_host, 80)
        dns_resolved = True
        dns_err = "resolved"
    except Exception as e:
        dns_err = str(e)
    internet_breached = tcp_pub_reachable or dns_resolved
    results["internet_outbound"] = {
        "isolated": not internet_breached,
        "reachable": internet_breached,
        "tcp_public_53": {"reachable": tcp_pub_reachable, "detail": tcp_pub_err},
        "dns_lookup": {"resolved": dns_resolved, "detail": dns_err},
    }

    # 5. Cloud metadata 169.254.169.254
    meta_reachable, meta_err = check_metadata(timeout=timeout)
    results["cloud_metadata"] = {
        "isolated": not meta_reachable,
        "reachable": meta_reachable,
        "detail": meta_err,
    }

    # 6. Docker API: no filesystem socket AND port 2375 closed
    sock_path = "/var/run/docker.sock"
    sock_exists = os.path.exists(sock_path)
    d2375_local, d2375_local_err = try_tcp_connect("127.0.0.1", 2375, timeout=timeout)
    d2375_gw, d2375_gw_err = try_tcp_connect(gateway_ip, 2375, timeout=timeout)
    docker_api_breached = sock_exists or d2375_local or d2375_gw
    results["docker_api"] = {
        "isolated": not docker_api_breached,
        "reachable": docker_api_breached,
        "socket_file_exists": sock_exists,
        "port_2375_local": {"reachable": d2375_local, "detail": d2375_local_err},
        "port_2375_gateway": {"reachable": d2375_gw, "detail": d2375_gw_err},
    }

    # 7. Instancer API probe (ports 8300, 8301)
    instancer_host = params.get("instancer_host") or os.environ.get("PROBE_INSTANCER_HOST", "instancer")
    inst_r8300, inst_err8300 = try_tcp_connect(instancer_host, 8300, timeout=timeout)
    inst_r8301, inst_err8301 = try_tcp_connect(instancer_host, 8301, timeout=timeout)
    results["instancer_api"] = {
        "isolated": not (inst_r8300 or inst_r8301),
        "port_8300": {"reachable": inst_r8300, "detail": inst_err8300},
        "port_8301": {"reachable": inst_r8301, "detail": inst_err8301},
    }

    # 8. Router control ports probe
    router_host = params.get("router_host") or os.environ.get("PROBE_ROUTER_HOST", "router")
    r_ctl, r_ctl_err = try_tcp_connect(router_host, 8090, timeout=timeout)
    results["router_control"] = {
        "isolated": not r_ctl,
        "reachable": r_ctl,
        "detail": r_ctl_err,
    }

    # 9. CTFd / Database / Redis / Compose project containers by name
    core_breached = False
    core_details = {}
    core_targets = [
        ("ctfd", 8000),
        ("db", 3306),
        ("mariadb", 3306),
        ("cache", 6379),
        ("redis", 6379),
        ("nginx", 80),
        ("instancer-socket-proxy", 2375),
    ]
    for host, prt in core_targets:
        r, err = try_tcp_connect(host, prt, timeout=timeout)
        core_details[f"{host}:{prt}"] = {"reachable": r, "detail": err}
        if r:
            core_breached = True
    results["platform_core"] = {
        "isolated": not core_breached,
        "reachable": core_breached,
        "services": core_details,
    }

    all_isolated = all(
        check.get("isolated", False) for check in results.values()
    )
    return {"fully_isolated": all_isolated, "checks": results}


class ProberHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}\n')
            return

        if parsed.path in ["/probe", "/report"]:
            query_params = dict(urllib.parse.parse_qsl(parsed.query))
            report = run_isolation_probes(query_params)
            status_code = 200 if report["fully_isolated"] else 500
            data = json.dumps(report, indent=2).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    if "--run-once" in sys.argv:
        report = run_isolation_probes({})
        print(json.dumps(report, indent=2))
        sys.exit(0 if report["fully_isolated"] else 1)

    server = http.server.ThreadingHTTPServer(("", PORT), ProberHandler)
    print(f"Prober listening on {PORT}", flush=True)
    server.serve_forever()
