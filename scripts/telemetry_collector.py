#!/usr/bin/env python3
"""Minimal self-hosted JSONL collector for opt-in webEmbedding telemetry."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


MAX_BODY_BYTES = 256 * 1024


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def json_response(handler: BaseHTTPRequestHandler, status: HTTPStatus, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def build_handler(output_path: Path, accepted_path: str) -> type[BaseHTTPRequestHandler]:
    class TelemetryHandler(BaseHTTPRequestHandler):
        server_version = "webEmbeddingTelemetryCollector/1"

        def log_message(self, format: str, *args: Any) -> None:
            print(f"{self.address_string()} - {format % args}", file=sys.stderr)

        def do_GET(self) -> None:
            if self.path == "/health":
                json_response(self, HTTPStatus.OK, {"ok": True})
                return
            json_response(self, HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:
            if accepted_path and self.path.split("?", 1)[0] != accepted_path:
                json_response(self, HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return

            content_type = self.headers.get("Content-Type", "")
            if "application/json" not in content_type.lower():
                json_response(self, HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "expected_json"})
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                json_response(self, HTTPStatus.LENGTH_REQUIRED, {"error": "invalid_content_length"})
                return

            if length <= 0:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": "empty_body"})
                return
            if length > MAX_BODY_BYTES:
                json_response(self, HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body_too_large"})
                return

            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
                return

            if not isinstance(payload, dict):
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": "expected_json_object"})
                return

            record = dict(payload)
            record.setdefault("received_at", utc_now_iso())
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")

            json_response(self, HTTPStatus.ACCEPTED, {"ok": True})

    return TelemetryHandler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect opt-in webEmbedding telemetry POST events into a JSONL file."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Defaults to 127.0.0.1.")
    parser.add_argument("--port", type=int, default=8765, help="Bind port. Defaults to 8765.")
    parser.add_argument(
        "--path",
        default="/events",
        help="Accepted POST path. Defaults to /events. Use an empty string to accept any path.",
    )
    parser.add_argument(
        "--out",
        default="./telemetry.jsonl",
        help="JSONL output path. Defaults to ./telemetry.jsonl.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_path = Path(args.out).expanduser().resolve()
    handler = build_handler(output_path, args.path)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(
        f"Collecting telemetry at http://{args.host}:{args.port}{args.path} -> {output_path}",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping telemetry collector.", file=sys.stderr)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
