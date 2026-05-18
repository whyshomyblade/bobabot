import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return

        body = b"OK"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        logging.getLogger("HealthServer").debug(format, *args)


def start_health_server() -> ThreadingHTTPServer | None:
    logger = logging.getLogger("HealthServer")
    port = int(os.getenv("PORT", "10000"))

    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    except OSError as exc:
        logger.error("Could not start health server on port %s: %s", port, exc)
        return None

    thread = threading.Thread(
        target=server.serve_forever,
        name="health-server",
        daemon=True,
    )
    thread.start()
    logger.info("Health server started on port %s", port)
    return server
