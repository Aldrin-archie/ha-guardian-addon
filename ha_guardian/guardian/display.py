"""Show the camera snapshot on Google Cast displays (Nest Hub) next to the phone alert."""
import logging
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("guardian.display")


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # no packet is sent; this just picks the LAN interface
        return s.getsockname()[0]
    finally:
        s.close()


class CastDisplay:
    def __init__(self, ha, players, port=8765, seconds=30, host=None):
        self.ha, self.players, self.port, self.seconds = ha, list(players), int(port), int(seconds)
        self.host = host
        self.images: dict[str, bytes] = {}
        self.lock = threading.Lock()
        self.server = None

    def _start(self) -> None:
        if self.server:
            return
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                with outer.lock:
                    data = outer.images.get(self.path.strip("/").removesuffix(".jpg"))
                if not data:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("0.0.0.0", self.port), H)
        threading.Thread(target=self.server.serve_forever, daemon=True, name="display-http").start()

    def show(self, jpeg: bytes) -> None:
        """Fire and forget: never delays or breaks the phone alert."""
        threading.Thread(target=self._show, args=(jpeg,), daemon=True).start()

    def _show(self, jpeg: bytes) -> None:
        try:
            self._start()
            token = secrets.token_urlsafe(16)  # unguessable path: only the display we tell can fetch the picture
            with self.lock:
                self.images = {token: jpeg}
            url = f"http://{self.host or lan_ip()}:{self.port}/{token}.jpg"
            for p in self.players:
                try:
                    self.ha.call_service("media_player", "play_media", {
                        "entity_id": p, "media_content_id": url, "media_content_type": "image/jpeg"})
                except Exception as e:
                    log.warning("could not show the snapshot on %s: %s", p, e)
            time.sleep(self.seconds)
            for p in self.players:
                try:
                    self.ha.call_service("media_player", "media_stop", {"entity_id": p})
                except Exception:
                    pass
            with self.lock:
                self.images.pop(token, None)
        except Exception as e:
            log.warning("display failed: %s", e)
