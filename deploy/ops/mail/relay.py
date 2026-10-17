#!/usr/bin/env python3
"""Internal Queueing SMTP Relay for L3m0nCTF 2026.

Accepts unauthenticated, plain SMTP traffic from CTFd on the internal network.
Persists outgoing messages in a durable SQLite spool.
Enforces per-minute and daily rate limits, size limits, and access controls.
Relays upstream via STARTTLS/TLS using Docker secret MAIL_PASSWORD.
Exports Prometheus metrics and health status on /metrics.
"""
from __future__ import annotations

import asyncio
import http.server
import ipaddress
import json
import logging
import os
import re
import smtplib
import socket
import sqlite3
import ssl
import sys
import threading
import time
from email.parser import BytesParser
from email.policy import default
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mail-relay")

SMTP_HOST = os.environ.get("SMTP_HOST", "0.0.0.0")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "25"))
METRICS_PORT = int(os.environ.get("METRICS_PORT", "9025"))
SPOOL_DIR = Path(os.environ.get("SPOOL_DIR", "/var/spool/l3mon-mail"))
try:
    SPOOL_DIR.mkdir(parents=True, exist_ok=True)
    test_f = SPOOL_DIR / ".probe"
    test_f.touch()
    test_f.unlink()
except (PermissionError, OSError):
    SPOOL_DIR = Path("/tmp/l3mon-mail")
    SPOOL_DIR.mkdir(parents=True, exist_ok=True)

MAX_MESSAGE_BYTES = int(os.environ.get("MAX_MESSAGE_BYTES", "10485760"))  # 10 MB
MAX_QUEUE_AGE_SECONDS = int(os.environ.get("MAX_QUEUE_AGE_SECONDS", "86400"))  # 24 hours
RATE_LIMIT_PER_MINUTE = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "30"))
RATE_LIMIT_PER_DAY = int(os.environ.get("RATE_LIMIT_PER_DAY", "500"))

UPSTREAM_HOST = os.environ.get("UPSTREAM_SMTP_HOST", "").strip()
UPSTREAM_PORT = int(os.environ.get("UPSTREAM_SMTP_PORT", "587"))
UPSTREAM_USER = os.environ.get("UPSTREAM_SMTP_USER", "").strip()
UPSTREAM_TLS = os.environ.get("UPSTREAM_SMTP_TLS", "true").lower() in ("true", "1", "yes")
UPSTREAM_SSL = os.environ.get("UPSTREAM_SMTP_SSL", "false").lower() in ("true", "1", "yes")
PASSWORD_FILE = Path(os.environ.get("MAIL_PASSWORD_FILE", "/run/secrets/MAIL_PASSWORD"))

# Allowed client CIDRs (Docker internal networks and localhost)
ALLOWED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
]

# Metrics counters
METRICS = {
    "sent_total": 0,
    "failed_total": 0,
    "rate_limited_total": 0,
    "received_total": 0,
    "rejected_unauthorized": 0,
    "holding": 0,
}


class SpoolDB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.Lock()
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender TEXT,
                    recipients TEXT,
                    data BLOB,
                    created_at REAL,
                    next_attempt REAL,
                    attempts INTEGER DEFAULT 0,
                    last_error TEXT DEFAULT ''
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS delivery_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sent_at REAL
                )
            """)
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_next_attempt ON queue(next_attempt)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_sent_at ON delivery_log(sent_at)")

    def enqueue(self, sender: str, recipients: list[str], data: bytes) -> int:
        now = time.time()
        with self.lock, self.conn:
            cur = self.conn.execute(
                "INSERT INTO queue (sender, recipients, data, created_at, next_attempt, attempts) VALUES (?, ?, ?, ?, ?, 0)",
                (sender, json.dumps(recipients), data, now, now),
            )
            return cur.lastrowid

    def get_pending(self, limit: int = 10) -> list[tuple]:
        now = time.time()
        with self.lock:
            cur = self.conn.execute(
                "SELECT id, sender, recipients, data, created_at, attempts FROM queue WHERE next_attempt <= ? ORDER BY id ASC LIMIT ?",
                (now, limit),
            )
            return cur.fetchall()

    def mark_success(self, msg_id: int):
        now = time.time()
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM queue WHERE id = ?", (msg_id,))
            self.conn.execute("INSERT INTO delivery_log (sent_at) VALUES (?)", (now,))

    def mark_failure(self, msg_id: int, attempts: int, error: str):
        now = time.time()
        backoff = min(3600, 2 * (2 ** min(attempts, 6)))
        next_attempt = now + backoff
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE queue SET attempts = ?, next_attempt = ?, last_error = ? WHERE id = ?",
                (attempts + 1, next_attempt, error, msg_id),
            )

    def purge_expired(self, max_age: float) -> int:
        cutoff = time.time() - max_age
        with self.lock, self.conn:
            cur = self.conn.execute("DELETE FROM queue WHERE created_at < ?", (cutoff,))
            # also trim delivery_log older than 48 hours
            self.conn.execute("DELETE FROM delivery_log WHERE sent_at < ?", (time.time() - 172800,))
            return cur.rowcount

    def queue_length(self) -> int:
        with self.lock:
            cur = self.conn.execute("SELECT COUNT(*) FROM queue")
            return cur.fetchone()[0]

    def deliveries_in_window(self, seconds: float) -> int:
        cutoff = time.time() - seconds
        with self.lock:
            cur = self.conn.execute("SELECT COUNT(*) FROM delivery_log WHERE sent_at >= ?", (cutoff,))
            return cur.fetchone()[0]


spool = SpoolDB(SPOOL_DIR / "spool.db")


def is_ip_allowed(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
        return any(ip in net for net in ALLOWED_NETWORKS)
    except ValueError:
        return False


def get_mail_password() -> str:
    if PASSWORD_FILE.is_file():
        return PASSWORD_FILE.read_text(encoding="utf-8").strip()
    return ""


# ------------------------------------------------------------- SMTP Server Implementation
class SmtpSession:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.peer = writer.get_extra_info("peername")
        self.peer_ip = self.peer[0] if self.peer else "0.0.0.0"
        self.helo = ""
        self.sender = ""
        self.recipients: list[str] = []

    async def send_line(self, line: str):
        self.writer.write((line + "\r\n").encode("utf-8"))
        await self.writer.drain()

    async def handle(self):
        # Access control check
        if not is_ip_allowed(self.peer_ip):
            logger.warning("Rejected unauthorized client connection from %s", self.peer_ip)
            METRICS["rejected_unauthorized"] += 1
            await self.send_line("550 5.7.1 Relaying denied: untrusted network")
            self.writer.close()
            await self.writer.wait_closed()
            return

        await self.send_line("220 l3mon-mail-relay ESMTP ready")

        while True:
            line_bytes = await self.reader.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", "replace").strip()
            if not line:
                continue

            cmd = line.split(None, 1)[0].upper()
            arg = line[len(cmd):].strip()

            if cmd in ("HELO", "EHLO"):
                self.helo = arg
                await self.send_line("250-l3mon-mail-relay")
                await self.send_line("250-SIZE 10485760")
                await self.send_line("250 8BITMIME")

            elif cmd == "MAIL":
                m = re.search(r"FROM:\s*<([^>]*)>", arg, re.IGNORECASE)
                if not m:
                    await self.send_line("501 5.5.4 Syntax error in parameters")
                else:
                    self.sender = m.group(1).strip()
                    self.recipients = []
                    await self.send_line("250 2.1.0 Sender ok")

            elif cmd == "RCPT":
                if not self.sender:
                    await self.send_line("503 5.5.1 Need MAIL before RCPT")
                    continue
                m = re.search(r"TO:\s*<([^>]*)>", arg, re.IGNORECASE)
                if not m:
                    await self.send_line("501 5.5.4 Syntax error in parameters")
                else:
                    rcpt = m.group(1).strip()
                    self.recipients.append(rcpt)
                    await self.send_line("250 2.1.5 Recipient ok")

            elif cmd == "DATA":
                if not self.sender or not self.recipients:
                    await self.send_line("503 5.5.1 Need MAIL and RCPT before DATA")
                    continue
                await self.send_line("354 Start mail input; end with <CRLF>.<CRLF>")

                data_chunks = []
                total_bytes = 0
                overflow = False
                buf = b""

                while True:
                    chunk = await self.reader.read(65536)
                    if not chunk:
                        break
                    buf += chunk
                    total_bytes += len(chunk)
                    if total_bytes > MAX_MESSAGE_BYTES:
                        overflow = True

                    if not overflow:
                        data_chunks.append(chunk)

                    if b"\r\n.\r\n" in buf or b"\n.\n" in buf or buf.endswith(b"\r\n.") or buf == b".\r\n":
                        break
                    # Keep only last 10 bytes in buf to check for boundary
                    if len(buf) > 10:
                        buf = buf[-10:]

                if overflow:
                    await self.send_line("552 5.3.4 Message size exceeds maximum limit")
                    self.sender = ""
                    self.recipients = []
                    continue

                full_data = b"".join(data_chunks)
                # Strip trailing terminator
                if full_data.endswith(b"\r\n.\r\n"):
                    full_data = full_data[:-3]
                elif full_data.endswith(b"\n.\n"):
                    full_data = full_data[:-2]

                msg_id = spool.enqueue(self.sender, self.recipients, full_data)
                METRICS["received_total"] += 1
                logger.info("Queued message #%d from <%s> to %d recipients (%d bytes)", msg_id, self.sender, len(self.recipients), len(full_data))
                await self.send_line("250 2.0.0 Queued as %d" % msg_id)
                self.sender = ""
                self.recipients = []

            elif cmd == "RSET":
                self.sender = ""
                self.recipients = []
                await self.send_line("250 2.0.0 Reset ok")

            elif cmd == "NOOP":
                await self.send_line("250 2.0.0 OK")

            elif cmd == "QUIT":
                await self.send_line("221 2.0.0 Bye")
                break
            else:
                await self.send_line("500 5.5.2 Command unrecognized")

        self.writer.close()
        try:
            await self.writer.wait_closed()
        except Exception:
            pass


async def start_smtp_server():
    server = await asyncio.start_server(
        lambda r, w: SmtpSession(r, w).handle(),
        SMTP_HOST,
        SMTP_PORT,
    )
    logger.info("SMTP relay listening on %s:%d", SMTP_HOST, SMTP_PORT)
    async with server:
        await server.serve_forever()


# -------------------------------------------------------- Outgoing Delivery Worker
def delivery_worker():
    logger.info("Delivery worker thread started.")
    while True:
        try:
            # Purge expired items older than 24h
            purged = spool.purge_expired(MAX_QUEUE_AGE_SECONDS)
            if purged > 0:
                logger.warning("Purged %d expired messages from spool", purged)

            password = get_mail_password()
            if not UPSTREAM_HOST or not password:
                # Holding mode: no upstream or empty password secret
                METRICS["holding"] = 1
                time.sleep(3)
                continue
            else:
                METRICS["holding"] = 0

            # Check rate limits
            if spool.deliveries_in_window(60) >= RATE_LIMIT_PER_MINUTE:
                METRICS["rate_limited_total"] += 1
                time.sleep(2)
                continue

            if spool.deliveries_in_window(86400) >= RATE_LIMIT_PER_DAY:
                METRICS["rate_limited_total"] += 1
                time.sleep(10)
                continue

            pending = spool.get_pending(limit=5)
            if not pending:
                time.sleep(1)
                continue

            for msg_id, sender, rcpts_json, data, created_at, attempts in pending:
                recipients = json.loads(rcpts_json)
                success, err = send_upstream(sender, recipients, data, password)
                if success:
                    spool.mark_success(msg_id)
                    METRICS["sent_total"] += 1
                    logger.info("Delivered message #%d to %s", msg_id, recipients)
                else:
                    spool.mark_failure(msg_id, attempts, err)
                    METRICS["failed_total"] += 1
                    logger.warning("Delivery failed for message #%d: %s", msg_id, err)
                time.sleep(0.5)

        except Exception as e:
            logger.error("Error in delivery worker loop: %s", e)
            time.sleep(2)


def send_upstream(sender: str, recipients: list[str], data: bytes, password: str) -> tuple[bool, str]:
    try:
        if UPSTREAM_SSL:
            context = ssl.create_default_context()
            server = smtplib.SMTP_SSL(UPSTREAM_HOST, UPSTREAM_PORT, context=context, timeout=15)
        else:
            server = smtplib.SMTP(UPSTREAM_HOST, UPSTREAM_PORT, timeout=15)
            if UPSTREAM_TLS:
                context = ssl.create_default_context()
                server.starttls(context=context)

        if UPSTREAM_USER and password:
            server.login(UPSTREAM_USER, password)

        server.sendmail(sender, recipients, data)
        try:
            server.quit()
        except Exception:
            try:
                server.close()
            except Exception:
                pass
        return True, ""
    except Exception as e:
        return False, str(e)


# ------------------------------------------------------------- Metrics HTTP Server
class MetricsHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/metrics", "/metrics/"):
            q_len = spool.queue_length()
            body = (
                "# HELP l3mon_mail_queue_length Current number of queued emails.\n"
                "# TYPE l3mon_mail_queue_length gauge\n"
                f"l3mon_mail_queue_length {q_len}\n"
                "# HELP l3mon_mail_sent_total Total delivered emails.\n"
                "# TYPE l3mon_mail_sent_total counter\n"
                f"l3mon_mail_sent_total {METRICS['sent_total']}\n"
                "# HELP l3mon_mail_failed_total Total failed email delivery attempts.\n"
                "# TYPE l3mon_mail_failed_total counter\n"
                f"l3mon_mail_failed_total {METRICS['failed_total']}\n"
                "# HELP l3mon_mail_rate_limited_total Count of times rate limit throttled delivery.\n"
                "# TYPE l3mon_mail_rate_limited_total counter\n"
                f"l3mon_mail_rate_limited_total {METRICS['rate_limited_total']}\n"
                "# HELP l3mon_mail_holding Whether relay is in holding mode (missing password or upstream).\n"
                "# TYPE l3mon_mail_holding gauge\n"
                f"l3mon_mail_holding {METRICS['holding']}\n"
                "# HELP l3mon_mail_rejected_unauthorized_total Connections rejected from non-allowed networks.\n"
                "# TYPE l3mon_mail_rejected_unauthorized_total counter\n"
                f"l3mon_mail_rejected_unauthorized_total {METRICS['rejected_unauthorized']}\n"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path in ("/healthz", "/readyz"):
            body = b'{"status": "ok", "service": "mail-relay"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # suppress default access logs
        return


def start_metrics_server():
    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", METRICS_PORT), MetricsHandler)
    logger.info("Metrics HTTP server listening on :%d", METRICS_PORT)
    httpd.serve_forever()


def main():
    threading.Thread(target=start_metrics_server, daemon=True).start()
    threading.Thread(target=delivery_worker, daemon=True).start()
    asyncio.run(start_smtp_server())


if __name__ == "__main__":
    main()
