"""Finger_Light app server -- the touchscreen UI and its API, stdlib only.

    python3 app/server.py                      # on the Pi: LEDs on SPI if present
    python3 app/server.py --preview            # on a PC: open http://127.0.0.1:8080/preview

Stdlib on purpose (http.server + server-sent events): nothing to pip-install on the Pi,
nothing to install on a PC to preview, and no framework between a key press and the bar.

Routes
------
GET  /                  the app (the 800x480 panel loads this in kiosk Chromium)
GET  /preview           the app + a simulated LED bar and keyboard, for a PC
GET  /static/<file>     app assets
GET  /api/state         one JSON snapshot of everything the screens draw
GET  /api/events        the same snapshot, pushed on every change (text/event-stream)
GET  /api/geometry      key and LED positions in mm, for the preview's bar
POST /api/open          {"id", "hands"?}             start an attempt
POST /api/transport     {"cmd", "arg"?}              play/pause/toggle/prev/next/loop/loop_spot/tempo/stop
POST /api/retry         {"tempo_delta"?, "loop"?}    try again
POST /api/note          {"pitch", "on", "velocity"?} on-screen keys
POST /api/pedal         {"down"}                     sustain pedal (preview)
POST /api/freeplay      {}                           end the open attempt; the bar plays free
POST /api/calibrate     {"cmd", "arg"?}              start/select/step/nudge/shift_all/
                                                     reset_key/reset_all/save/close
POST /api/autoplay      {}                           play the current event correctly
POST /api/settings      {key: value, ...}
POST /api/rescan        {}                           re-read the scores folders
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "firmware", "src"))
sys.path.insert(0, HERE)

from fingerlight.lightscore import LightScoreError  # noqa: E402

from engine import Engine  # noqa: E402
from leds import make_sink  # noqa: E402

STATIC = os.path.join(HERE, "static")
GEOMETRY = os.path.join(REPO, "data", "led_map.json")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    stopping = False
    verbose = False

    def handle_error(self, request, client_address):
        # A browser dropping a keep-alive or event-stream socket is routine (every page
        # reload does it); a traceback per drop would bury real errors in the journal.
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    engine: Engine = None           # set by main()
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):     # quiet: the Pi's journal is not a request log
        if self.server.verbose:
            super().log_message(fmt, *args)

    # ------------------------------------------------------------------ helpers

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def _file(self, path: str) -> None:
        real = os.path.realpath(path)
        if not real.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(real):
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(real)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        with open(real, "rb") as fh:
            self._send(200, fh.read(), ctype)

    # --------------------------------------------------------------------- GET

    def do_GET(self):
        path = urlparse(self.path).path
        eng = self.engine
        if path in ("/", "/index.html"):
            return self._file(os.path.join(STATIC, "index.html"))
        if path in ("/preview", "/preview.html"):
            return self._file(os.path.join(STATIC, "preview.html"))
        if path.startswith("/static/"):
            return self._file(os.path.join(STATIC, path[len("/static/"):]))
        if path == "/api/state":
            return self._json(eng.state())
        if path == "/api/geometry":
            with open(GEOMETRY, encoding="utf-8") as fh:
                return self._send(200, fh.read().encode("utf-8"),
                                  "application/json; charset=utf-8")
        if path == "/api/events":
            return self._stream()
        return self._send(404, b"not found", "text/plain")

    def _stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        eng = self.engine
        seen = -1
        try:
            while not self.server.stopping:
                v = eng.wait_for_change(seen, timeout=15.0)
                if v == seen:
                    self.wfile.write(b": keep-alive\n\n")
                else:
                    seen = v
                    data = json.dumps(eng.state())
                    self.wfile.write(("data: %s\n\n" % data).encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        self.close_connection = True

    # -------------------------------------------------------------------- POST

    def do_POST(self):
        path = urlparse(self.path).path
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json({"ok": False, "error": "body is not JSON"}, 400)
        if not isinstance(body, dict):
            return self._json({"ok": False, "error": "body must be a JSON object"}, 400)
        eng = self.engine
        try:
            if path == "/api/open":
                eng.open(body["id"], hands=body.get("hands"))
            elif path == "/api/transport":
                eng.transport(body["cmd"], body.get("arg"))
            elif path == "/api/retry":
                loop = body.get("loop")
                eng.retry(int(body.get("tempo_delta") or 0),
                          tuple(loop) if loop else None)
            elif path == "/api/note":
                pitch = int(body["pitch"])
                if not 21 <= pitch <= 108:
                    raise ValueError("pitch %d is outside the 88 keys (21..108)" % pitch)
                vel = int(body.get("velocity") or 100)
                if not 1 <= vel <= 127:
                    raise ValueError("velocity %d is outside 1..127" % vel)
                eng.note(pitch, bool(body.get("on", True)), "keys", vel)
            elif path == "/api/pedal":
                eng.pedal(bool(body["down"]))
            elif path == "/api/freeplay":
                eng.end_attempt()
            elif path == "/api/calibrate":
                eng.calibrate(str(body["cmd"]), body.get("arg"))
            elif path == "/api/autoplay":
                eng.autoplay()
            elif path == "/api/settings":
                for k, v in body.items():
                    eng.set_setting(k, v)
            elif path == "/api/rescan":
                eng.rescan()
            else:
                return self._json({"ok": False, "error": "no such endpoint"}, 404)
        except (KeyError, ValueError, TypeError, LightScoreError) as exc:
            return self._json({"ok": False, "error": str(exc).strip("'\"")}, 400)
        return self._json({"ok": True, "version": eng.version})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="127.0.0.1",
                    help="0.0.0.0 to let a phone on the same network in (default: local only)")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--home", default=os.path.join(os.path.expanduser("~"), ".fingerlight"),
                    help="settings, history and the scores/ folder (default ~/.fingerlight)")
    ap.add_argument("--leds", choices=("auto", "spi", "dry", "none"), default="auto",
                    help="auto = SPI when /dev/spidev0.0 and spidev exist")
    ap.add_argument("--preview", action="store_true",
                    help="PC mode: no LEDs, no MIDI/mic threads; use /preview")
    ap.add_argument("--no-demo", action="store_true", help="hide the bundled demo scores")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    scores = os.path.join(args.home, "scores")
    os.makedirs(scores, exist_ok=True)
    dirs = [("scores", scores)]
    if not args.no_demo:
        dirs += [("demo", os.path.join(HERE, "demo_scores")),
                 ("examples", os.path.join(REPO, "lightscore", "examples"))]

    eng = Engine(args.home, dirs)
    kind = "none" if args.preview else args.leds
    try:
        eng.led_sink = make_sink(kind, eng.strip.led_count, eng.settings["led_order"],
                                 eng.settings["led_one_pattern"])
    except Exception as exc:
        print("LED output unavailable (%s) -- continuing without it" % exc, file=sys.stderr)
        eng.led_sink = make_sink("none", 0, "GRB", "1110")
        eng.error = "LED output unavailable: %s" % exc

    threads = []
    if not args.preview:
        from inputs import MicThread, MidiThread
        threads = [MidiThread(eng), MicThread(eng)]
        for t in threads:
            t.start()
    else:
        eng.set_input_status("midi", "unavailable", "preview mode")
        eng.set_input_status("mic", "unavailable", "preview mode")

    srv = Server((args.host, args.port), Handler)
    srv.verbose = args.verbose
    Handler.engine = eng

    def ticker():
        while not srv.stopping:
            eng.tick()
            time.sleep(0.02)

    threading.Thread(target=ticker, name="tick", daemon=True).start()
    where = "http://%s:%d/" % ("127.0.0.1" if args.host == "0.0.0.0" else args.host, args.port)
    print("Finger_Light app on %s%s  (LEDs: %s, home: %s)"
          % (where, "preview" if args.preview else "", eng.led_sink.describe(), args.home),
          flush=True)
    try:
        srv.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        srv.stopping = True
        for t in threads:
            t.stop.set()
        eng.led_sink.close()
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
