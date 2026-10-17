import http.server
import os
import socketserver

PORT = 8080
FLAG = os.environ.get("FLAG", "NO_FLAG_SET")


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/flag":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(FLAG.encode("utf-8"))
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok\n")
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"L3m0nCTF fixture challenge running\n")

    def log_message(self, format, *args):
        # Plain minimal logging without sensitive info
        pass


if __name__ == "__main__":
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"Fixture serving on port {PORT}")
        httpd.serve_forever()
