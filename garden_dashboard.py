#!/usr/bin/env python3
"""Read-only garden dashboard for a trusted home LAN. No directory serving."""

import argparse
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import threading
import time
from urllib.parse import parse_qs, quote, unquote, urlsplit


class Dashboard:
    def __init__(self, root):
        self.root = root.resolve()
        self.image_roots = [self.root / name for name in ("captures", "captures-tc41", "cat-results")]
        self.lock = threading.Lock()
        self.cached = None
        self.cached_at = 0

    def image_path(self, relative):
        path = (self.root / relative).resolve()
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            raise ValueError("Image unavailable")
        # resolve() prevents symlinks and .. escaping an explicitly allowed directory.
        if not any(path.is_relative_to(root) and root.resolve() == root
                   for root in self.image_roots):
            raise ValueError("Image unavailable")
        if not path.is_file():
            raise ValueError("Image unavailable")
        return path

    def image_url(self, path):
        try:
            path = self.image_path(str(path.relative_to(self.root)))
            return "/image?path=" + quote(str(path.relative_to(self.root)), safe="")
        except (ValueError, OSError):
            return None

    def captures(self):
        # Cache the scan so multiple phones/polling do not repeatedly walk storage.
        with self.lock:
            if self.cached is not None and time.monotonic() - self.cached_at < 15:
                return self.cached
            items = []
            for root in self.image_roots[:2]:
                if root.is_symlink():
                    continue
                for path in root.rglob("*.jpg"):
                    if path.is_symlink():
                        continue
                    try:
                        url = self.image_url(path)
                        if url:
                            items.append({"name": path.name, "folder": str(path.parent.relative_to(self.root)),
                                          "kind": "raw" if "raw" in path.relative_to(root).parts else "preview",
                                          "url": url, "modified": path.stat().st_mtime})
                    except OSError:
                        continue
            items.sort(key=lambda item: item["name"], reverse=True)
            self.cached, self.cached_at = items, time.monotonic()
            return items

    def service_status(self):
        try:
            result = subprocess.run([
                "systemctl", "show", "garden-detector", "--no-pager",
                "-p", "ActiveState", "-p", "SubState", "-p", "NRestarts",
                "-p", "ActiveEnterTimestamp"], capture_output=True, text=True, timeout=3)
            if result.returncode:
                return {"ActiveState": "unknown"}
            return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        except (OSError, subprocess.TimeoutExpired):
            return {"ActiveState": "unknown"}

    def decisions(self):
        root = self.root / "spray-review"
        runs = sorted((p for p in root.glob("run_*") if p.is_dir() and not p.is_symlink()), reverse=True)
        for run in runs:
            log = run / "decisions.jsonl"
            if not log.is_file() or log.is_symlink():
                continue
            records = []
            with log.open() as handle:
                lines = deque(handle, maxlen=500)
            for line in lines:
                try:
                    item = json.loads(line)
                    evidence = []
                    for frame in item.get("evidence", []):
                        report = frame["report"]
                        original = self.image_url(Path(report["input_image"]))
                        annotated = self.image_url(Path(frame["report_file"]).parent / report["output_image"])
                        evidence.append({"index": frame["burst_index"], "original": original,
                                         "annotated": annotated,
                                         "detections": [{"label": d["label"], "confidence": d["confidence"]}
                                                        for d in report.get("detections", [])]})
                    records.append({"event_id": item["event_id"], "would_spray": item["would_spray"],
                                    "reason": item["reason"], "evidence": evidence,
                                    "evaluated_at": item.get("evaluated_at"), "policy": item.get("policy")})
                except (ValueError, KeyError, TypeError):
                    continue
            return {"run": run.name, "items": sorted(records, key=lambda r: r["event_id"], reverse=True)}
        return {"run": None, "items": []}


def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Avoid logging query strings and filesystem details.
            pass

        def send_content(self, body, content_type):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            try:
                if parsed.path in {"/", "/app.js", "/style.css"}:
                    name = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[parsed.path]
                    mime = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "style.css": "text/css; charset=utf-8"}[name]
                    self.send_content((Path(__file__).parent / "dashboard" / name).read_bytes(), mime)
                elif parsed.path == "/api/status":
                    items = app.captures()
                    data = {"service": app.service_status(), "capture_count": len(items),
                            "latest_capture": items[0] if items else None,
                            "server_time": datetime.now().astimezone().isoformat()}
                    self.send_content(json.dumps(data).encode(), "application/json")
                elif parsed.path == "/api/captures":
                    kind = query.get("kind", ["preview"])[0]
                    offset = max(0, int(query.get("offset", ["0"])[0]))
                    items = [item for item in app.captures() if item["kind"] == kind]
                    data = {"items": items[offset:offset+24], "total": len(items)}
                    self.send_content(json.dumps(data).encode(), "application/json")
                elif parsed.path == "/api/decisions":
                    self.send_content(json.dumps(app.decisions()).encode(), "application/json")
                elif parsed.path == "/image":
                    path = app.image_path(query.get("path", [""])[0])
                    self.send_content(path.read_bytes(), "image/png" if path.suffix.lower() == ".png" else "image/jpeg")
                else:
                    self.send_error(404)
            except (OSError, ValueError, KeyError):
                self.send_error(404, "Not available")

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--root", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), handler_for(Dashboard(args.root)))
    print(f"Garden dashboard listening on {args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
