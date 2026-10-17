#!/usr/bin/env python3
"""Lightweight test SMTP catcher for operations integration tests."""
import asyncio
from pathlib import Path

LOG_FILE = Path("/tmp/received.log")
LOG_FILE.touch()

async def handle(reader, writer):
    writer.write(b"220 test-catcher ESMTP ready\r\n")
    await writer.drain()
    while True:
        line = await reader.readline()
        if not line:
            break
        cmd = line.strip().upper()
        if cmd.startswith(b"QUIT"):
            writer.write(b"221 2.0.0 Bye\r\n")
            await writer.drain()
            break
        elif cmd.startswith(b"DATA"):
            writer.write(b"354 Start mail input\r\n")
            await writer.drain()
            while True:
                d = await reader.readline()
                if not d or d == b".\r\n" or d == b".\n":
                    break
            with open(LOG_FILE, "a") as f:
                f.write("MSG_RECEIVED\n")
            writer.write(b"250 2.0.0 Ok: queued\r\n")
            await writer.drain()
        else:
            writer.write(b"250 Ok\r\n")
            await writer.drain()
    writer.close()

async def main():
    server = await asyncio.start_server(handle, "0.0.0.0", 1025)
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
