#!/usr/bin/env python3
"""Garden monitoring and decision feedback for a trusted home LAN."""

import argparse
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import csv
import hashlib
import io
from pathlib import Path
import secrets
import sqlite3
import sys
import subprocess
import threading
import time
from urllib.parse import parse_qs, quote, unquote, urlsplit


class Dashboard:
    def __init__(self, root, data_dir=None):
        self.root = root.resolve()
        self.image_roots = [self.root / name for name in ("captures", "captures-tc41", "cat-results")]
        self.lock = threading.Lock()
        self.cached = None
        self.cached_at = 0
        self.data_dir = Path(data_dir) if data_dir else self.root / "dashboard-data"
        self.feedback_token = secrets.token_urlsafe(32)

    def database(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.data_dir / "feedback.sqlite3", timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("""CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY, decision_id TEXT NOT NULL, event_id TEXT NOT NULL,
            run TEXT NOT NULL, would_spray INTEGER NOT NULL, verdict TEXT NOT NULL,
            note TEXT NOT NULL, saved_at TEXT NOT NULL)""")
        connection.execute("CREATE INDEX IF NOT EXISTS feedback_decision ON feedback(decision_id, id)")
        return connection

    def check_feedback_storage(self):
        """Check real write access, including systemd filesystem restrictions."""
        connection = self.database()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO feedback (decision_id,event_id,run,would_spray,verdict,note,saved_at) VALUES (?,?,?,?,?,?,?)",
                               ("storage-check", "", "", 0, "agree", "", ""))
            connection.rollback()
        finally:
            connection.close()

    def feedback_latest(self):
        if not (self.data_dir / "feedback.sqlite3").exists():
            return {}
        connection = self.database()
        try:
            rows = connection.execute("SELECT * FROM feedback WHERE id IN (SELECT MAX(id) FROM feedback GROUP BY decision_id)")
            return {row["decision_id"]: dict(row) for row in rows}
        finally:
            connection.close()

    def save_feedback(self, payload):
        verdict, note = payload.get("verdict"), payload.get("note", "")
        if verdict not in {"agree", "disagree"} or not isinstance(note, str) or len(note) > 2000:
            raise ValueError("Choose agree or disagree; notes are limited to 2000 characters")
        batch = self.decisions()
        decision = next((d for d in batch["items"] if d["decision_id"] == payload.get("decision_id")), None)
        if decision is None:
            raise ValueError("Decision is no longer in the current batch; refresh first")
        saved_at = datetime.now().astimezone().isoformat()
        connection = self.database()
        try:
            with connection:
                connection.execute("INSERT INTO feedback (decision_id,event_id,run,would_spray,verdict,note,saved_at) VALUES (?,?,?,?,?,?,?)",
                    (decision["decision_id"], decision["event_id"], decision.get("review_run", batch["run"]),
                     int(decision["would_spray"]), verdict, note, saved_at))
        finally:
            connection.close()
        return {"verdict": verdict, "note": note, "saved_at": saved_at}

    def feedback_csv(self):
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        keys = ["decision_id", "event_id", "run", "would_spray", "verdict", "note", "saved_at"]
        writer.writerow(keys)
        for feedback in self.feedback_latest().values():
            values = [feedback[key] for key in keys]
            # Keep notes as text when opened in spreadsheet software.
            writer.writerow(["'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")) else v for v in values])
        return output.getvalue().encode("utf-8")

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

    def service_status(self, unit="garden-detector"):
        try:
            result = subprocess.run([
                "systemctl", "show", unit, "--no-pager",
                "-p", "ActiveState", "-p", "SubState", "-p", "NRestarts",
                "-p", "ActiveEnterTimestamp"], capture_output=True, text=True, timeout=3)
            if result.returncode:
                return {"ActiveState": "unknown"}
            return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        except (OSError, subprocess.TimeoutExpired):
            return {"ActiveState": "unknown"}

    def decisions(self):
        feedback = self.feedback_latest()
        root = self.root / "spray-review"
        runs = sorted((p for p in root.glob("run_*") if p.is_dir() and not p.is_symlink()), reverse=True)
        records = []
        sources = []
        for run in [root / "live"] + runs[:1]:
            log = run / "decisions.jsonl"
            if run.name == "live":
                paths = sorted(run.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:500]
                lines = []
                for path in paths:
                    if path.is_symlink():
                        continue
                    try:
                        lines.append(path.read_text())
                    except OSError:
                        continue
                if not lines:
                    continue
            else:
                if not log.is_file() or log.is_symlink():
                    continue
                with log.open() as handle:
                    lines = deque(handle, maxlen=500)
            sources.append(run.name)
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
                    identity = json.dumps([run.name, item["event_id"], item.get("source_directory", "")])
                    decision_id = hashlib.sha256(identity.encode()).hexdigest()
                    records.append({"event_id": item["event_id"], "would_spray": item["would_spray"],
                                    "decision_id": decision_id, "feedback": feedback.get(decision_id), "review_run": run.name,
                                    "reason": item["reason"], "evidence": evidence,
                                    "evaluated_at": item.get("evaluated_at"), "policy": item.get("policy")})
                except (ValueError, KeyError, TypeError):
                    continue
        return {"run": " + ".join(sources) or None,
                "items": sorted(records, key=lambda r: r["event_id"], reverse=True)}


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
                    data = {"service": app.service_status(), "review_service": app.service_status("garden-review"), "capture_count": len(items),
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
                    self.send_content(json.dumps({**app.decisions(), "feedback_token": app.feedback_token}).encode(), "application/json")
                elif parsed.path == "/api/feedback.csv":
                    self.send_content(app.feedback_csv(), "text/csv; charset=utf-8")
                elif parsed.path == "/image":
                    path = app.image_path(query.get("path", [""])[0])
                    self.send_content(path.read_bytes(), "image/png" if path.suffix.lower() == ".png" else "image/jpeg")
                else:
                    self.send_error(404)
            except (OSError, ValueError, KeyError):
                self.send_error(404, "Not available")
            except sqlite3.Error:
                self.send_error(503, "Feedback storage unavailable")

        def do_POST(self):
            if self.path != "/api/feedback":
                self.send_error(404)
                return
            if not secrets.compare_digest(self.headers.get("X-Feedback-Token", ""), app.feedback_token):
                self.feedback_error(403, "The dashboard restarted. Refresh the page before submitting feedback.")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16384 or self.headers.get_content_type() != "application/json":
                    raise ValueError("Invalid feedback request")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("Invalid feedback request")
                result = app.save_feedback(payload)
                self.send_content(json.dumps(result).encode(), "application/json")
            except (ValueError, TypeError, KeyError):
                self.feedback_error(400, "Invalid feedback or an outdated decision. Refresh and try again.")
            except (OSError, sqlite3.Error):
                print("Feedback save failed: storage is unavailable or not writable. Check the installed dashboard unit's StateDirectory and --data-dir.", file=sys.stderr, flush=True)
                self.feedback_error(503, "Feedback was not saved: storage is unavailable or read-only. Reinstall the updated garden-dashboard.service and restart it.")

        def feedback_error(self, status, message):
            body = json.dumps({"error": message}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--root", type=Path, default=Path(__file__).parent)
    parser.add_argument("--data-dir", type=Path, help="persistent feedback storage directory")
    args = parser.parse_args()
    app = Dashboard(args.root, args.data_dir)
    try:
        app.check_feedback_storage()
    except (OSError, sqlite3.Error):
        parser.exit(1, "Dashboard feedback storage is not writable. Reinstall the updated garden-dashboard.service (StateDirectory=garden-dashboard and --data-dir /var/lib/garden-dashboard), or select a writable --data-dir for manual use.\n")
    server = ThreadingHTTPServer((args.host, args.port), handler_for(app))
    print(f"Garden dashboard listening on {args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
