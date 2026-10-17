import asyncio
import logging
import os
import sys

from services.instancer.router.router import InstanceRouter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
_log = logging.getLogger("l3mon.instancer.router")


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


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, router: InstanceRouter):
    try:
        # Read HTTP request header
        line = await reader.readline()
        if not line:
            writer.close()
            return
        req_line = line.decode("utf-8", errors="replace").strip()
        parts = req_line.split()
        if len(parts) < 3:
            writer.close()
            return
        method, path, _ = parts

        headers = {}
        content_length = 0
        while True:
            header_line = await reader.readline()
            if not header_line or header_line == b"\r\n":
                break
            line_str = header_line.decode("utf-8", errors="replace").strip()
            if ":" in line_str:
                hk, hv = line_str.split(":", 1)
                hk_clean = hk.strip()
                headers[hk_clean] = hv.strip()
                if hk_clean.lower() == "content-length":
                    try:
                        content_length = int(hv.strip())
                    except ValueError:
                        pass

        body = b""
        if content_length > 0:
            body = await reader.readexactly(content_length)

        status, resp_headers, resp_body = await router.handle_http_request(method, path, headers, body)

        status_text = "OK" if status == 200 else ("Not Found" if status == 404 else "Error")
        lines = [f"HTTP/1.1 {status} {status_text}"]
        for hk, hv in resp_headers.items():
            lines.append(f"{hk}: {hv}")
        lines.append(f"Content-Length: {len(resp_body)}")
        lines.append("Connection: close")
        response_bytes = "\r\n".join(lines).encode("utf-8") + b"\r\n\r\n" + resp_body

        writer.write(response_bytes)
        await writer.drain()
    except Exception as e:
        _log.debug("Client handler error: %s", e)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def main():
    _log.info("Starting L3m0nCTF Instance Router")
    router_token = _read_secret("ROUTER_TOKEN")
    if not router_token:
        _log.warning("ROUTER_TOKEN is not set or empty. Route polling will operate without token.")

    instance_domain = os.getenv("INSTANCE_DOMAIN", "localhost:8090")
    instancer_url = os.getenv("INSTANCER_URL", "http://instancer:8300")
    port = int(os.getenv("ROUTER_HTTP_PORT", "8090"))

    router = InstanceRouter(
        instance_domain=instance_domain,
        instancer_url=instancer_url,
        router_token=router_token,
    )

    # Start route poller task in background
    asyncio.create_task(router.poll_routes_loop())

    server = await asyncio.start_server(
        lambda r, w: handle_client(r, w, router),
        "0.0.0.0",
        port,
    )
    _log.info("Instance Router listening for HTTP on 0.0.0.0:%d (domain: %s)", port, instance_domain)

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        _log.info("Router stopped.")
