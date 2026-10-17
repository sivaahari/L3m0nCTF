import asyncio
import logging
import os
import sys

from services.instancer.router.router import MAX_BODY_BYTES, InstanceRouter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
_log = logging.getLogger("l3mon.instancer.router")

# What the router will read from a stranger (it is reachable by anybody). Found by the integration audit: a declared Content-Length of four gigabytes
# was believed and read in full before the size check, no host name was needed to do it, and nothing ever timed a slow client out.
HEADER_TIMEOUT_SECONDS = 10  # the whole request line and header block
BODY_TIMEOUT_SECONDS = 30
MAX_HEADER_LINES = 100


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


async def _refuse(writer: asyncio.StreamWriter, status: int, text: str) -> None:
    body = (text + "\n").encode("utf-8")
    head = f"HTTP/1.1 {status} Error\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    writer.write(head.encode("ascii") + body)
    await writer.drain()


async def _read_head(reader: asyncio.StreamReader):
    """(request line, headers, declared body length), or None when the client sent more header lines than allowed (the caller answers 431)."""
    line = await reader.readline()
    if not line:
        return "", {}, 0
    req_line = line.decode("utf-8", errors="replace").strip()
    headers = {}
    content_length = 0
    for _ in range(MAX_HEADER_LINES + 1):
        header_line = await reader.readline()
        if not header_line or header_line == b"\r\n":
            return req_line, headers, content_length
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
    return None


async def _serve_one(reader, writer, router, header_timeout, body_timeout):
    head = await asyncio.wait_for(_read_head(reader), header_timeout)
    if head is None:
        await _refuse(writer, 431, "Request Header Fields Too Large")
        return
    req_line, headers, content_length = head
    parts = req_line.split()
    if len(parts) < 3:
        return
    method, path, _ = parts

    if content_length > MAX_BODY_BYTES:  # before a single byte of the body is read
        await _refuse(writer, 413, "Payload Too Large (Max 10MB)")
        return
    body = b""
    if content_length > 0:
        body = await asyncio.wait_for(reader.readexactly(content_length), body_timeout)

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


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, router: InstanceRouter,
                        header_timeout: float = HEADER_TIMEOUT_SECONDS, body_timeout: float = BODY_TIMEOUT_SECONDS):
    try:
        try:
            await _serve_one(reader, writer, router, header_timeout, body_timeout)
        except asyncio.TimeoutError:
            await _refuse(writer, 408, "Request Timeout")
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
