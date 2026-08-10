"""Webviewer voor actieve Eufy-dashboardcamera's.

Deze module start een eenvoudige HTTP-server die dezelfde actieve
RTSP-camera's in een browser toont als de app. De camera's moeten eerst
in het Eufy-dashboard zijn gestart; de server gebruikt de bestaande
RTSP-decoderende stream-threads van tasks/eufy.py.
"""
import io
import math
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

try:
    from PIL import Image
except ImportError:
    Image = None

from tasks import eufy

POORT = 8777
_URLS = {"lan": None}
_SERVER = None


def _lokaal_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        s.close()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._root()
        elif parsed.path.startswith("/stream/"):
            self._stream(parsed.path.split("/", 2)[-1])
        else:
            self.send_error(404, "Niet gevonden")

    def _root(self):
        if not eufy.actief():
            html = self._html_page(None, "Start eerst het Eufy-dashboard")
        else:
            html = self._html_page(eufy._dash._streams)
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _stream(self, idx_text):
        if Image is None:
            self.send_error(500, "Pillow ontbreekt")
            return
        try:
            idx = int(idx_text)
        except ValueError:
            self.send_error(404, "Ongeldige stream")
            return
        if not eufy.actief() or idx < 0 or idx >= len(eufy._dash._streams):
            self.send_error(404, "Stream niet gevonden")
            return

        stream = eufy._dash._streams[idx]
        self.send_response(200)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()

        boundary = b"--frame\r\n"
        while eufy.actief():
            frame = stream.frame
            if frame is None:
                time.sleep(0.2)
                continue
            try:
                image = Image.fromarray(frame[:, :, ::-1])
            except Exception:
                time.sleep(0.2)
                continue
            buf = io.BytesIO()
            image.save(buf, format="JPEG", quality=60)
            jpg = buf.getvalue()
            try:
                self.wfile.write(boundary)
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(jpg)}\r\n\r\n".encode("ascii"))
                self.wfile.write(jpg)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                break
            time.sleep(0.08)

    def _html_page(self, streams, waarschuwing=None):
        title = "Eufy camera's"
        if streams is None:
            inhoud = (
                "<p>De Eufy-dashboardstreams draaien nog niet. "
                "Start eerst het dashboard in de app.</p>"
            )
        else:
            visible = [s for s in streams if s is not None]
            count = len(visible)
            if count == 0:
                inhoud = "<p>Geen actieve camera's beschikbaar.</p>"
            else:
                lay = eufy.raster_cellen()
                if lay is None:
                    rijen = math.ceil(math.sqrt(count))
                    kolommen = math.ceil(count / rijen)
                    cellen = [(r, c, 1, 1)
                              for r in range(rijen)
                              for c in range(kolommen)][:count]
                else:
                    rijen, kolommen, cellen = lay
                items = []
                for idx, stream in enumerate(visible):
                    r, c, rs, cs = cellen[idx] if idx < len(cellen) else (0, idx, 1, 1)
                    naam = stream.naam or f"Camera {idx + 1}"
                    items.append(
                        f"<div class=\"cell\" style=\"grid-row: {r + 1} / span {rs}; "
                        f"grid-column: {c + 1} / span {cs};\">"
                        f"<div class=\"label\">{naam}</div>"
                        f"<img src=\"/stream/{idx}\" alt=\"{naam}\">"
                        f"</div>"
                    )
                inhoud = (
                    f"<div class=\"grid\" style=\"grid-template-columns: repeat({kolommen}, minmax(0, 1fr)); "
                    f"grid-template-rows: repeat({rijen}, minmax(0, 1fr));\">"
                    + "".join(items) + "</div>"
                )
        waarschuwing_html = f"<div class=\"warning\">{waarschuwing}</div>" if waarschuwing else ""
        return (
            "<!doctype html><html lang=\"nl\">"
            "<head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no\">"
            f"<title>{title}</title>"
            "<style>html,body{height:100%;margin:0;padding:0;background:#000;overflow:hidden;font-family:Segoe UI,Arial,sans-serif;color:#e2e8f0;}"
            "a{color:#60a5fa;text-decoration:none;}"
            "body>*{box-sizing:border-box;}"
            " .page{display:grid;grid-template-rows:auto 1fr;min-height:100vh;width:100vw;padding:0;gap:0;}"
            " .warning{padding:10px 14px;margin:0;background:#fde68a;color:#92400e;font-size:0.95rem;line-height:1.4;}"
            " .grid{display:grid;gap:6px;height:100%;width:100%;grid-auto-rows:1fr;min-height:0;}"
            " .cell{position:relative;overflow:hidden;border-radius:8px;background:#111827;border:1px solid #1f2937;min-height:0;}"
            " .label{padding:8px 10px;font-size:0.84rem;font-weight:700;background:rgba(15,23,42,0.75);color:#f8fafc;position:absolute;top:0;left:0;z-index:2;backdrop-filter:blur(4px);}"
            " img{width:100%;height:100%;object-fit:contain;display:block;background:#000;}"
            " @media (max-width: 900px){.grid{gap:4px;}}"
            " @media (max-width: 640px){.label{font-size:0.78rem;padding:6px 8px;}}"
            "</style></head><body><div class=\"page\">"
            + waarschuwing_html + inhoud + "</div></body></html>"
        )


class _StilleServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        return


def start(log, port: int = POORT) -> bool:
    global _SERVER, _URLS
    if _SERVER is not None:
        log("Eufy-webviewer draait al.", "WARNING")
        return True
    if not eufy.actief():
        log("Start eerst het Eufy-dashboard; de webviewer kan de camera's alleen tonen als de streams actief zijn.", "WARNING")
        return False
    try:
        httpd = _StilleServer(("0.0.0.0", port), _Handler)
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        _SERVER = httpd
        _URLS["lan"] = f"http://{_lokaal_ip()}:{port}"
        log(f"Eufy-webviewer gestart op {_URLS['lan']}", "SUCCESS")
        return True
    except OSError as exc:
        log(f"Eufy-webviewer kon niet starten: {exc}", "ERROR")
        return False


def stop(log) -> bool:
    global _SERVER, _URLS
    if _SERVER is None:
        log("Eufy-webviewer draaide niet.", "WARNING")
        return False
    _SERVER.shutdown()
    _SERVER.server_close()
    _SERVER = None
    _URLS = {"lan": None}
    log("Eufy-webviewer gestopt.", "SUCCESS")
    return True


def actief() -> bool:
    return _SERVER is not None


def urls() -> dict:
    return dict(_URLS)
