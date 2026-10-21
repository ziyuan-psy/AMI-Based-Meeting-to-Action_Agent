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

STATIC = Path(__file__).resolve().parent / "static"
ASSETS = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
          "/styles.css": ("styles.css", "text/css"), "/favicon.svg": ("favicon.svg", "image/svg+xml")}


def make_server(host: str = "127.0.0.1", port: int = 8765,
                provider: MeetingProvider | None = None) -> ThreadingHTTPServer:
    source = provider or SavedDevelopmentProvider()

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
            url = urlsplit(self.path)
            path = unquote(url.path)
            if post:
                if path != "/api/runs":
                    raise DataError("Endpoint not found.", "not_found", 404)
                origin = self.headers.get("Origin")
                if origin and origin not in {f"http://{self.headers.get('Host')}"}:
                    raise DataError("Use the meeting-review app to open a run.", "origin_rejected", 403)
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 1 or length > 4096:
                        raise ValueError
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict) or set(data) != {"meeting_id"} or not isinstance(data["meeting_id"], str):
                        raise ValueError
                except (ValueError, UnicodeDecodeError):
                    raise DataError("Send a JSON object containing meeting_id.") from None
                self._json(source.start_run(data["meeting_id"]).payload, 201)
                return
            if path in ASSETS:
                name, content_type = ASSETS[path]
                self._send((STATIC / name).read_bytes(), content_type)
            elif path == "/api/catalog":
                self._json(source.catalog())
            elif match := re.fullmatch(r"/api/meetings/([^/]+)", path):
                self._json(source.meeting(match[1]))
            elif match := re.fullmatch(r"/api/runs/([a-f0-9]{32})(/export)?", path):
                bundle = source.get_run(match[1])
                if not match[2]:
                    self._json(bundle.payload)
                    return
                fmt = parse_qs(url.query).get("format", ["json"])[0]
                mid = bundle.payload["meeting_id"]
                if fmt == "json":
                    self._send(bundle.record_text.encode("utf-8"), "application/json", filename=f"{mid}.json")
                elif fmt == "markdown":
                    self._send(bundle.report_markdown.encode("utf-8"), "text/markdown", filename=f"{mid}_report.md")
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

    return ThreadingHTTPServer((host, port), Handler)


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
        server.server_close()


if __name__ == "__main__":
    main()
