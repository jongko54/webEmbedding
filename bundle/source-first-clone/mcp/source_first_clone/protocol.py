"""JSON-RPC protocol handling for the source-first clone MCP server.

MCP stdio transport is newline-delimited JSON. Content-Length (LSP-style) framing is also
accepted and answered in kind, so older local harnesses keep working.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from .constants import SERVER_NAME, SERVER_VERSION
from .tools import TOOLS, handle_call

SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

# Returned in `initialize`; MCP clients surface this to the model as server-level guidance,
# which is what decides whether an agent reaches for these tools at all.
SERVER_INSTRUCTIONS = """\
webEmbedding clones a web page from its URL: it captures the live page in a real browser, \
reuses the original (iframe/embed/source) when allowed, otherwise writes a self-contained \
offline HTML snapshot or rebuild, and verifies the result against screenshots at desktop, \
tablet, and mobile breakpoints.

Use these tools when the user gives a URL and wants the page cloned, copied, recreated, \
mirrored, archived as a faithful offline copy, embedded, or checked for visual fidelity \
(e.g. "clone this site", "make it look exactly like", "그대로 가져와줘", "완전 똑같이").

Tool choice:
- clone_reference_url: default for "clone/copy this page" — runs inspect, capture, rebuild, \
and self-verify in one call and returns the clone path plus scores.
- audit_reference_url: cheap preflight (no browser) to check whether reuse, local capture, \
login, or manual review is needed.
- inspect_url / discover_embed_candidates: frame policy and reusable embed/source URLs.
- generate_embed_snippet: iframe snippet once a reusable URL is known.
- capture_reference_bundle + verify_fidelity_report: capture evidence and compare a \
reference against your own implementation.

Do not use for scraping/summarizing content, original designs without a reference URL, or \
bypassing logins, paywalls, captchas, or licenses.\
"""


def _negotiate_protocol_version(params: dict[str, Any]) -> str:
    requested = params.get("protocolVersion") if isinstance(params, dict) else None
    if requested in SUPPORTED_PROTOCOL_VERSIONS:
        return str(requested)
    return SUPPORTED_PROTOCOL_VERSIONS[0]


class StdioTransport:
    def __init__(self) -> None:
        self.content_length_framing = False

    def read_message(self) -> dict[str, Any] | None:
        while True:
            line = sys.stdin.buffer.readline()
            if not line:
                return None
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(b"{"):
                self.content_length_framing = False
                return json.loads(stripped.decode("utf-8"))
            return self._read_content_length_message(line)

    def _read_content_length_message(self, first_line: bytes) -> dict[str, Any] | None:
        headers: dict[str, str] = {}
        line = first_line
        while line not in (b"\r\n", b"\n", b""):
            key, _, value = line.decode("utf-8").partition(":")
            headers[key.strip().lower()] = value.strip()
            line = sys.stdin.buffer.readline()
        content_length = int(headers.get("content-length", "0"))
        if content_length <= 0:
            return None
        self.content_length_framing = True
        return json.loads(sys.stdin.buffer.read(content_length).decode("utf-8"))

    def send(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        if self.content_length_framing:
            sys.stdout.buffer.write(f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii") + encoded)
        else:
            sys.stdout.buffer.write(encoded + b"\n")
        sys.stdout.buffer.flush()


def serve() -> int:
    transport = StdioTransport()
    while True:
        message = transport.read_message()
        if message is None:
            return 0

        message_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}

        try:
            if method == "initialize":
                result: dict[str, Any] = {
                    "protocolVersion": _negotiate_protocol_version(params),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "title": "webEmbedding", "version": SERVER_VERSION},
                    "instructions": SERVER_INSTRUCTIONS,
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                tool_result = handle_call(params.get("name"), params.get("arguments", {}))
                result = {"content": [{"type": "text", "text": json.dumps(tool_result, indent=2)}]}
            elif message_id is None:
                # Notifications (initialized, cancelled, ...) need no response.
                continue
            else:
                transport.send({"jsonrpc": "2.0", "id": message_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}})
                continue
            if message_id is not None:
                transport.send({"jsonrpc": "2.0", "id": message_id, "result": result})
        except Exception as exc:  # pragma: no cover - protocol safety net
            if message_id is not None:
                transport.send({"jsonrpc": "2.0", "id": message_id, "error": {"code": -32000, "message": str(exc)}})
