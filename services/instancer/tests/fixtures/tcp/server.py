import asyncio
import os
import sys

PORT = int(os.environ.get("PORT", "8000"))
FLAG = os.environ.get("FLAG", "NO_FLAG_SET")


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        writer.write(b"L3m0nCTF TCP fixture ready\n")
        await writer.drain()

        while True:
            line = await reader.readline()
            if not line:
                break
            cmd = line.decode("utf-8", errors="replace").strip()
            if not cmd:
                continue
            if cmd == "flag" or cmd == "open":
                writer.write(f"{FLAG}\n".encode("utf-8"))
                await writer.drain()
            elif cmd.startswith("echo "):
                payload = cmd[5:] + "\n"
                writer.write(payload.encode("utf-8"))
                await writer.drain()
            elif cmd == "ping":
                writer.write(b"pong\n")
                await writer.drain()
            elif cmd == "quit" or cmd == "exit":
                break
            else:
                writer.write(b"incorrect\n")
                await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def main():
    server = await asyncio.start_server(handle_client, "0.0.0.0", PORT)
    print(f"TCP fixture listening on port {PORT}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
