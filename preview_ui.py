"""One-command local preview: prepare dependencies, start the UI, open a browser."""
from __future__ import annotations

import argparse
import hashlib
import json
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent


def is_ui_running(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/catalog", timeout=1) as response:
            catalog = json.load(response)
        return isinstance(catalog, dict) and catalog.get("source") == "saved_development" and catalog.get("ui_version", 0) >= 2
    except (OSError, ValueError):
        return False


def port_in_use(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def ensure_environment(root: Path = ROOT) -> Path:
    environment = root / ".venv-ui"
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    requirements = root / "ui" / "requirements.txt"
    marker = environment / ".preview-requirements.sha256"
    fingerprint = hashlib.sha256(requirements.read_bytes()).hexdigest()
    if not python.is_file():
        print("First launch: creating the local UI environment...", flush=True)
        subprocess.run([sys.executable, "-m", "venv", str(environment)], cwd=root, check=True)
    if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != fingerprint:
        print("Preparing UI packages (first launch needs an internet connection)...", flush=True)
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                        "-r", str(requirements)], cwd=root, check=True)
        marker.write_text(fingerprint + "\n", encoding="utf-8")
    return python


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Start the AMI UI preview; no API key required.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="start without opening a browser tab")
    args = parser.parse_args(argv)
    if sys.version_info < (3, 10):
        print("Install Python 3.10 or newer, then run this shortcut again.", file=sys.stderr)
        return 1
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    url = f"http://127.0.0.1:{args.port}/"
    if is_ui_running(args.port):
        print(f"AMI UI is already running: {url}", flush=True)
        if not args.no_browser:
            webbrowser.open(url, new=2)
        return 0
    if port_in_use(args.port):
        print(f"Port {args.port} is used by another application. Run this shortcut with --port 8766.", file=sys.stderr)
        return 1

    process = None
    try:
        python = ensure_environment()
        process = subprocess.Popen([str(python), "-m", "ui.server", "--port", str(args.port)], cwd=ROOT)
        deadline = time.monotonic() + 30
        while not is_ui_running(args.port):
            if process.poll() is not None:
                raise RuntimeError("The UI server stopped before it became ready. Check the message above.")
            if time.monotonic() >= deadline:
                raise RuntimeError("The UI server did not become ready within 30 seconds.")
            time.sleep(0.15)
        print(f"\nPreview ready: {url}\nKeep this window open. Press Ctrl+C to stop the preview.\n", flush=True)
        if not args.no_browser:
            if not webbrowser.open(url, new=2):
                print(f"Open this address in your browser: {url}", flush=True)
        return process.wait()
    except KeyboardInterrupt:
        print("\nStopping the UI preview.", flush=True)
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Preview could not start: {error}", file=sys.stderr)
        return 1
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
