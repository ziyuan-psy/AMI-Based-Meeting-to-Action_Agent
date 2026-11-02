"""Small loopback HTTP transport; replace the provider to connect the final agent."""
from __future__ import annotations

import argparse
import json
import logging
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .provider import DataError, MeetingProvider, SavedDevelopmentProvider
from .jobs import LiveJobs

STATIC = Path(__file__).resolve().parent / "static"
ASSETS = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
          "/styles.css": ("styles.css", "text/css"), "/favicon.svg": ("favicon.svg", "image/svg+xml")}


def make_server(host: str = "127.0.0.1", port: int = 8765,
                provider: MeetingProvider | None = None, jobs: LiveJobs | None = None) -> ThreadingHTTPServer:
    source = provider or SavedDevelopmentProvider()
    live = jobs or LiveJobs()

    def bundle(run_id):
        try:
            return source.get_run(run_id)
        except DataError as error:
            if error.code != "run_not_found":
                raise
            return live.bundle(run_id)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, content_type: str, status: int = 200, filename: str | None = None):
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(body)

        def _json(self, value: dict, status: int = 200):
            self._send(json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json", status)

        def _dispatch(self, post: bool = False):
            hostname = urlsplit("http://" + self.headers.get("Host", "")).hostname
            if hostname not in ("127.0.0.1", "localhost", "::1"):
                raise DataError("Open this app using its localhost address.", "host_rejected", 403)
            url = urlsplit(self.path)
            path = unquote(url.path)
            if post:
                cancel_match = re.fullmatch(r"/api/agent/runs/([a-f0-9]{32})/cancel", path)
                if path not in ("/api/runs", "/api/agent/runs") and not cancel_match:
                    raise DataError("Endpoint not found.", "not_found", 404)
                origin = self.headers.get("Origin")
                if origin and origin not in {f"http://{self.headers.get('Host')}"}:
                    raise DataError("Use the meeting-review app to open a run.", "origin_rejected", 403)
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 1 or length > 4096:
                        raise ValueError
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict):
                        raise ValueError
                    if not cancel_match and (set(data) != {"meeting_id"} or not isinstance(data["meeting_id"], str)):
                        raise ValueError
                except (ValueError, UnicodeDecodeError):
                    raise DataError("Send a JSON object containing meeting_id.") from None
                if cancel_match:
                    self._json(live.cancel(cancel_match[1]))
                elif path == "/api/agent/runs":
                    self._json(live.start(data["meeting_id"]), 202)
                else:
                    self._json(source.start_run(data["meeting_id"]).payload, 201)
                return
            if path in ASSETS:
                name, content_type = ASSETS[path]
                self._send((STATIC / name).read_bytes(), content_type)
            elif path == "/api/catalog":
                self._json(source.catalog())
            elif path == "/api/agent":
                self._json(live.capabilities())
            elif match := re.fullmatch(r"/api/agent/runs/([a-f0-9]{32})", path):
                self._json(live.get(match[1]))
            elif match := re.fullmatch(r"/api/meetings/([^/]+)", path):
                self._json(source.meeting(match[1]))
            elif match := re.fullmatch(r"/api/runs/([a-f0-9]{32})(/export)?", path):
                record = bundle(match[1])
                if not match[2]:
                    self._json(record.payload)
                    return
                fmt = parse_qs(url.query).get("format", ["json"])[0]
                mid = record.payload["meeting_id"]
                if fmt == "json":
                    self._send(record.record_text.encode("utf-8"), "application/json", filename=f"{mid}.json")
                elif fmt == "markdown":
                    self._send(record.report_markdown.encode("utf-8"), "text/markdown", filename=f"{mid}_report.md")
                else:
                    raise DataError("Export format must be json or markdown.")
            else:
                raise DataError("Endpoint not found.", "not_found", 404)

        def _handle(self, post: bool = False):
            try:
                self._dispatch(post)
            except DataError as error:
                self._json({"error": {"code": error.code, "message": str(error)}}, error.status)
            except (BrokenPipeError, ConnectionResetError):
                pass  # navigating to another meeting may abort the previous fetch
            except Exception:
                logging.exception("Could not load meeting data")
                self._json({"error": {"code": "data_unavailable", "message": "Meeting data could not be loaded. Check the local server log and try again."}}, 500)

        def do_GET(self):
            self._handle()

        def do_POST(self):
            self._handle(post=True)

        def log_message(self, fmt, *args):
            logging.info(fmt, *args)

    server = ThreadingHTTPServer((host, port), Handler)
    server.live_jobs = live
    return server


def main():
    parser = argparse.ArgumentParser(description="AMI meeting review, saved development runs")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    server = make_server(port=args.port)
    print(f"AMI meeting review (saved development outputs)\nLocal: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.live_jobs.close()
        server.server_close()


if __name__ == "__main__":
    main()
