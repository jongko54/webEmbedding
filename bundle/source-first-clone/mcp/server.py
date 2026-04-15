#!/usr/bin/env python3
"""MCP server for source-first clone workflows."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from html import unescape
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen


SERVER_NAME = "source-first-clone"
SERVER_VERSION = "0.1.2"

URL_PATTERNS = [
    ("spline-preview", re.compile(r"https://app\.spline\.design/file/[^\"'\s>]+\?view=preview", re.I)),
    ("spline-viewer", re.compile(r"https://viewer\.spline\.design/[^\"'\s>]+", re.I)),
    ("spline-code", re.compile(r"https://[^\"'\s>]+\.splinecode", re.I)),
    ("iframe-src", re.compile(r"<iframe[^>]+src=[\"']([^\"']+)", re.I)),
    ("generic-embed", re.compile(r"https://[^\"'\s>]*(?:embed|preview|viewer)[^\"'\s>]*", re.I)),
]
LICENSE_HINTS = [
    "cc0",
    "creative commons",
    "mit license",
    "apache license",
    "all rights reserved",
    "copyright",
    "licensed under",
    "remix",
    "fork",
    "export",
]


TOOLS = [
    {
        "name": "inspect_url",
        "description": "Fetch a URL, inspect HTML metadata, and summarize likely exact-clone paths.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 60}
            },
            "required": ["url"]
        },
    },
    {
        "name": "discover_embed_candidates",
        "description": "Extract likely embed, preview, viewer, remix, and export candidates from a page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 60}
            },
            "required": ["url"]
        },
    },
    {
        "name": "trace_runtime_sources",
        "description": "Use a browser runtime to trace preview, embed, and scene URLs that do not exist in static HTML.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "wait_seconds": {"type": "integer", "minimum": 1, "maximum": 30},
                "pattern": {"type": "string"}
            },
            "required": ["url"]
        },
    },
    {
        "name": "classify_clone_mode",
        "description": "Decide whether a reference should be embedded, sourced, rebuilt, or blocked.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "exact_requested": {"type": "boolean"},
                "license_text": {"type": "string"},
                "candidates": {
                    "type": "array",
                    "items": {
                        "type": "object"
                    }
                },
                "source_signals": {
                    "type": "array",
                    "items": {"type": "string"}
                }
            }
        },
    },
    {
        "name": "generate_embed_snippet",
        "description": "Generate a ready-to-paste iframe snippet for HTML or Next.js.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "title": {"type": "string"},
                "framework": {"type": "string", "enum": ["html", "nextjs"]}
            },
            "required": ["url"]
        },
    },
]


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


def fetch_url(url: str, timeout_seconds: int = 20) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "webEmbedding/0.1.2"})
    with urlopen(request, timeout=timeout_seconds) as response:
        html = response.read().decode("utf-8", "ignore")
        return {
            "status": getattr(response, "status", 200),
            "final_url": response.geturl(),
            "html": html,
        }


def extract_title(html: str) -> str | None:
    match = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    return unescape(match.group(1).strip()) if match else None


def extract_meta(html: str) -> dict[str, str]:
    results: dict[str, str] = {}
    for key in ["description", "og:title", "og:description", "og:image", "og:url", "twitter:image"]:
        match = re.search(
            rf"<meta[^>]+(?:property|name)=[\"']{re.escape(key)}[\"'][^>]+content=[\"']([^\"']+)",
            html,
            re.I,
        )
        if match:
            results[key] = unescape(match.group(1))
    return results


def extract_license_hints(html: str) -> list[str]:
    lowered = html.lower()
    return [hint for hint in LICENSE_HINTS if hint in lowered]


def build_candidates(base_url: str, html: str) -> list[dict[str, str]]:
    final_candidates: list[dict[str, str]] = []
    seen: set[str] = set()

    for kind, pattern in URL_PATTERNS:
        if kind == "iframe-src":
            matches = pattern.findall(html)
        else:
            matches = pattern.findall(html)

        for raw_match in matches:
            candidate = raw_match if isinstance(raw_match, str) else raw_match[0]
            normalized = urljoin(base_url, candidate.strip())
            if normalized in seen:
                continue
            seen.add(normalized)
            final_candidates.append({"kind": kind, "url": normalized})

    generic_urls = re.findall(r"https://[^\"'\s>]+", html)
    for raw in generic_urls:
        if not re.search(r"(spline|preview|embed|viewer|scene|iframe|remix|export)", raw, re.I):
            continue
        if raw in seen:
            continue
        seen.add(raw)
        final_candidates.append({"kind": "runtime-hint", "url": raw})

    return final_candidates


def inspect_url(arguments: dict[str, Any]) -> dict[str, Any]:
    timeout_seconds = int(arguments.get("timeout_seconds", 20))
    fetched = fetch_url(arguments["url"], timeout_seconds=timeout_seconds)
    html = fetched["html"]
    return {
        "url": arguments["url"],
        "final_url": fetched["final_url"],
        "status": fetched["status"],
        "title": extract_title(html),
        "meta": extract_meta(html),
        "license_hints": extract_license_hints(html),
        "candidate_urls": build_candidates(fetched["final_url"], html),
    }


def discover_embed_candidates(arguments: dict[str, Any]) -> dict[str, Any]:
    timeout_seconds = int(arguments.get("timeout_seconds", 20))
    fetched = fetch_url(arguments["url"], timeout_seconds=timeout_seconds)
    return {
        "url": arguments["url"],
        "final_url": fetched["final_url"],
        "candidates": build_candidates(fetched["final_url"], fetched["html"]),
    }


def trace_runtime_sources(arguments: dict[str, Any]) -> dict[str, Any]:
    if shutil.which("node") is None:
        return {"available": False, "error": "node is not installed"}

    wait_seconds = int(arguments.get("wait_seconds", 8))
    pattern = arguments.get("pattern", "spline|preview|embed|viewer|scene|iframe")

    script = r"""
const pageUrl = process.argv[1];
const waitSeconds = Number(process.argv[2] || "8");
const rawPattern = process.argv[3] || "spline|preview|embed|viewer|scene|iframe";
let playwright;
try {
  playwright = require("playwright");
} catch (firstError) {
  try {
    playwright = require("playwright-core");
  } catch (secondError) {
    console.log(JSON.stringify({
      available: false,
      error: "playwright or playwright-core is not installed"
    }));
    process.exit(0);
  }
}

const regex = new RegExp(rawPattern, "i");

(async () => {
  const launchOptions = { headless: true };
  if (process.env.WEB_EMBEDDING_CHROME_PATH) {
    launchOptions.executablePath = process.env.WEB_EMBEDDING_CHROME_PATH;
  }
  const browser = await playwright.chromium.launch(launchOptions);
  const page = await browser.newPage({ viewport: { width: 1440, height: 1200 } });
  const hits = [];

  page.on("response", (response) => {
    const target = response.url();
    if (!regex.test(target)) {
      return;
    }
    hits.push({ status: response.status(), url: target });
  });

  try {
    await page.goto(pageUrl, { waitUntil: "domcontentloaded", timeout: 90000 });
    await page.waitForTimeout(waitSeconds * 1000);
    const html = await page.content();
    const htmlMatches = Array.from(
      html.matchAll(/https?:\/\/[^"'\\s>]+/g),
      (match) => match[0]
    ).filter((value) => regex.test(value));
    console.log(JSON.stringify({
      available: true,
      finalUrl: page.url(),
      title: await page.title(),
      networkHits: hits.slice(0, 200),
      htmlMatches: [...new Set(htmlMatches)].slice(0, 200)
    }));
  } catch (error) {
    console.log(JSON.stringify({
      available: false,
      error: error.message
    }));
  } finally {
    await browser.close();
  }
})();
"""

    completed = subprocess.run(
        ["node", "-e", script, arguments["url"], str(wait_seconds), pattern],
        capture_output=True,
        text=True,
        check=False,
    )

    payload = completed.stdout.strip() or completed.stderr.strip()
    if not payload:
        return {"available": False, "error": "runtime trace produced no output"}
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {"available": False, "raw_output": payload}


def classify_clone_mode(arguments: dict[str, Any]) -> dict[str, Any]:
    exact_requested = bool(arguments.get("exact_requested", True))
    license_text = (arguments.get("license_text") or "").lower()
    source_signals = [str(item).lower() for item in arguments.get("source_signals", [])]
    candidates = arguments.get("candidates", [])

    candidate_kinds = {str(item.get("kind", "")).lower() for item in candidates if isinstance(item, dict)}
    reusable_license = any(token in license_text for token in ["cc0", "creative commons", "mit", "apache", "remix"])
    blocked_license = any(token in license_text for token in ["all rights reserved", "copyright"])
    source_signal = any(token in source_signals for token in ["remix", "export", "source", "fork"])

    if blocked_license and not reusable_license:
        return {
            "mode": "blocked",
            "reason": "The provided license text suggests the reference should not be cloned exactly without permission.",
        }

    if candidate_kinds & {"spline-preview", "spline-viewer", "iframe-src", "generic-embed"}:
        return {
            "mode": "embed",
            "reason": "An original preview, viewer, or iframe candidate exists.",
        }

    if reusable_license or source_signal or "spline-code" in candidate_kinds:
        return {
            "mode": "source",
            "reason": "The reference shows remix, export, or source-level reuse signals.",
        }

    if exact_requested:
        return {
            "mode": "rebuild",
            "reason": "No exact reuse path was found, so the request should be rebuilt with a clear accuracy disclaimer.",
        }

    return {"mode": "rebuild", "reason": "A rebuild is appropriate for this reference."}


def generate_embed_snippet(arguments: dict[str, Any]) -> dict[str, Any]:
    title = arguments.get("title") or "Embedded reference"
    framework = arguments.get("framework", "nextjs")
    url = arguments["url"]

    if framework == "html":
        snippet = (
            f'<iframe src="{url}" title="{title}" '
            'style="display:block;width:100%;height:100vh;border:0" allow="fullscreen"></iframe>'
        )
    else:
        snippet = "\n".join(
            [
                "<iframe",
                f'  src="{url}"',
                f'  title="{title}"',
                '  allow="fullscreen"',
                '  style={{ display: "block", width: "100%", height: "100vh", border: 0 }}',
                "/>",
            ]
        )

    return {"framework": framework, "snippet": snippet}


TOOL_HANDLERS = {
    "inspect_url": inspect_url,
    "discover_embed_candidates": discover_embed_candidates,
    "trace_runtime_sources": trace_runtime_sources,
    "classify_clone_mode": classify_clone_mode,
    "generate_embed_snippet": generate_embed_snippet,
}


def handle_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name not in TOOL_HANDLERS:
        raise KeyError(f"Unknown tool: {name}")
    return TOOL_HANDLERS[name](arguments)


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


if __name__ == "__main__":
    raise SystemExit(serve())
