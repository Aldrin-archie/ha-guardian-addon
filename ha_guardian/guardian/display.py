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
    def __init__(self, ha, players, port=8765, seconds=10, host=None, live=False):
        self.ha, self.players, self.port, self.seconds = ha, list(players), int(port), int(seconds)
        self.live, self.gen, self.last_show = live, 0, 0.0
        self.host = host
        self.images: dict[str, bytes] = {}
        self.lock = threading.Lock()
        self.server = None

    def _start(self) -> None:
        if self.server:
            return
        outer = self

        class H(BaseHTTPRequestHandler):
            def _serve(self, body: bool):
                with outer.lock:
                    data = outer.images.get(self.path.strip("/").removesuffix(".jpg"))
                if not data:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Access-Control-Allow-Origin", "*")  # Cast receivers fetch media like a web page
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if body:
                    self.wfile.write(data)

            def do_GET(self):
                self._serve(True)

            def do_HEAD(self):
                self._serve(False)

            def log_message(self, *a):
                pass

            def handle_one_request(self):
                super().handle_one_request()
                log.info("display: %s asked for the picture (%s)", self.client_address[0], self.path[:8] + "...")

        self.server = ThreadingHTTPServer(("0.0.0.0", self.port), H)
        threading.Thread(target=self.server.serve_forever, daemon=True, name="display-http").start()

    def show(self, jpeg: bytes, camera: str | None = None) -> int:
        """Fire and forget: never delays or breaks the phone alert. Returns a ticket for clear()."""
        with self.lock:
            self.gen += 1
            mine = self.gen
        threading.Thread(target=self._show, args=(jpeg, camera, mine), daemon=True).start()
        return mine

    def clear(self, ticket: int) -> None:
        """Take the picture down again, unless a newer one has replaced it."""
        with self.lock:
            if self.gen != ticket:
                return
            self.gen += 1
        for p in self.players:
            try:
                self.ha.call_service("media_player", "turn_off", {"entity_id": p})
            except Exception:
                pass

    def _show(self, jpeg: bytes, camera: str | None = None, mine: int | None = None) -> None:
        if mine is None:
            with self.lock:
                self.gen += 1
                mine = self.gen
        try:
            self._start()
            token = secrets.token_urlsafe(16)  # unguessable path: only the display we tell can fetch the picture
            with self.lock:
                self.images = {token: jpeg}
            url = f"http://{self.host or lan_ip()}:{self.port}/{token}.jpg"
            log.info("display: showing a snapshot at %s", url.rsplit("/", 1)[0] + "/...")
            for p in self.players:
                try:
                    before = self.ha.state(p).get("state")
                    log.info("display: %s is '%s' before the picture", p, before)
                    if before not in ("idle", "off", "standby", "unavailable") and time.time() - self.last_show > self.seconds + 5:
                        # a leftover session (e.g. a paused video) can keep the picture from showing: close it first
                        self.ha.call_service("media_player", "turn_off", {"entity_id": p})
                        time.sleep(1.5)
                except Exception:
                    pass
                try:
                    self.ha.call_service("media_player", "play_media", {
                        "entity_id": p, "media_content_id": url, "media_content_type": "image/jpeg"})
                except Exception as e:
                    log.warning("could not show the snapshot on %s: %s", p, e)
            if self.live and camera:  # the live picture takes over from the snapshot as soon as the stream is ready
                for p in self.players:
                    try:
                        self.ha.call_service("camera", "play_stream", {"entity_id": camera, "media_player": p, "format": "hls"})
                    except Exception as e:
                        log.warning("live view on %s failed, keeping the snapshot: %s", p, e)
            time.sleep(min(2, self.seconds))
            for p in self.players:
                try:
                    st = self.ha.state(p)
                    a = st.get("attributes") or {}
                    log.info("display: 2 s in, %s is '%s', app '%s', type '%s'", p, st.get("state"), a.get("app_name"),
                             a.get("media_content_type"))
                except Exception:
                    pass
            time.sleep(max(0, self.seconds - 2))
            with self.lock:
                if self.gen != mine:  # a newer alert took over the display: leave it alone
                    self.images.pop(token, None)
                    return
            for p in self.players:
                try:
                    self.ha.call_service("media_player", "turn_off", {"entity_id": p})
                except Exception:
                    pass
            with self.lock:
                self.images.pop(token, None)
        except Exception as e:
            log.warning("display failed: %s", e)


def test_picture() -> bytes:
    import io

    from PIL import Image, ImageDraw

    im = Image.new("RGB", (1024, 600), (16, 24, 40))
    d = ImageDraw.Draw(im)
    d.text((60, 260), "HA Guardian: display test", fill=(255, 255, 255))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=85)
    return out.getvalue()
