"""JSON-RPC protocol handling for the source-first clone MCP server."""

from __future__ import annotations

import json
import sys
from typing import Any

from .constants import SERVER_NAME, SERVER_VERSION
from .tools import TOOLS, handle_call


def send_message(payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def read_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        key, value = line.decode("utf-8").split(":", 1)
        headers[key.strip().lower()] = value.strip()

    content_length = int(headers.get("content-length", "0"))
    if content_length <= 0:
        return None
    body = sys.stdin.buffer.read(content_length)
    return json.loads(body.decode("utf-8"))


def send_result(message_id: Any, result: dict[str, Any]) -> None:
    send_message({"jsonrpc": "2.0", "id": message_id, "result": result})


def send_error(message_id: Any, code: int, message: str) -> None:
    send_message({"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}})


def serve() -> int:
    while True:
        message = read_message()
        if message is None:
            return 0

        message_id = message.get("id")
        method = message.get("method")
        params = message.get("params", {})

        try:
            if method == "initialize":
                send_result(
                    message_id,
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                    },
                )
                continue

            if method == "notifications/initialized":
                continue

            if method == "tools/list":
                send_result(message_id, {"tools": TOOLS})
                continue

            if method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments", {})
                result = handle_call(name, arguments)
                send_result(
                    message_id,
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(result, indent=2),
                            }
                        ]
                    },
                )
                continue

            if message_id is not None:
                send_error(message_id, -32601, f"Unknown method: {method}")
        except Exception as exc:  # pragma: no cover - protocol safety net
            if message_id is not None:
                send_error(message_id, -32000, str(exc))
