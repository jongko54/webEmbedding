"""Tool definitions and dispatch for the source-first clone MCP server."""

from __future__ import annotations

from typing import Any, Callable

from .acquisition import detect_runtime_capabilities
from .acquisition import discover_embed_candidates as discover_embed_candidates_fn
from .acquisition import inspect_reference, trace_runtime_sources as trace_runtime_sources_fn
from .capture_bundle import capture_reference_bundle
from .orchestration import clone_reference_url
from .policy import classify_clone_mode
from .planning import plan_reproduction_path
from .reproduction import build_reproduction_bundle
from .verification import verify_fidelity_report


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


def inspect_url(arguments: dict[str, Any]) -> dict[str, Any]:
    timeout_seconds = int(arguments.get("timeout_seconds", 20))
    return inspect_reference(arguments["url"], timeout_seconds=timeout_seconds)


def detect_runtime_capabilities_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    del arguments
    return detect_runtime_capabilities()


def discover_embed_candidates(arguments: dict[str, Any]) -> dict[str, Any]:
    timeout_seconds = int(arguments.get("timeout_seconds", 20))
    return discover_embed_candidates_fn(arguments["url"], timeout_seconds=timeout_seconds)


def trace_runtime_sources(arguments: dict[str, Any]) -> dict[str, Any]:
    return trace_runtime_sources_fn(
        url=arguments["url"],
        wait_seconds=int(arguments.get("wait_seconds", 8)),
        pattern=arguments.get("pattern", "spline|preview|embed|viewer|scene|iframe"),
        user_data_dir=arguments.get("user_data_dir"),
        storage_state_path=arguments.get("storage_state_path"),
        storage_state_output_path=arguments.get("storage_state_output_path"),
        capture_html=bool(arguments.get("capture_html", False)),
        capture_screenshot=bool(arguments.get("capture_screenshot", False)),
        viewport_width=int(arguments.get("viewport_width", 1440)),
        viewport_height=int(arguments.get("viewport_height", 1200)),
    )


def capture_reference_bundle_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    return capture_reference_bundle(
        url=arguments["url"],
        timeout_seconds=int(arguments.get("timeout_seconds", 20)),
        wait_seconds=int(arguments.get("wait_seconds", 8)),
        include_runtime_trace=bool(arguments.get("include_runtime_trace", True)),
        user_data_dir=arguments.get("user_data_dir"),
        storage_state_path=arguments.get("storage_state_path"),
        storage_state_output_path=arguments.get("storage_state_output_path"),
        capture_html=bool(arguments.get("capture_html", False)),
        capture_screenshot=bool(arguments.get("capture_screenshot", False)),
        viewport_width=int(arguments.get("viewport_width", 1440)),
        viewport_height=int(arguments.get("viewport_height", 1200)),
        output_dir=arguments.get("output_dir"),
        exact_requested=bool(arguments.get("exact_requested", True)),
        license_text=arguments.get("license_text"),
        source_signals=arguments.get("source_signals"),
    )


def plan_reproduction_path_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    return plan_reproduction_path(
        exact_requested=bool(arguments.get("exact_requested", True)),
        license_text=arguments.get("license_text"),
        candidates=arguments.get("candidates"),
        source_signals=arguments.get("source_signals"),
        capture_bundle=arguments.get("capture_bundle"),
    )


def verify_fidelity_report_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    return verify_fidelity_report(
        reference_bundle=arguments.get("reference_bundle"),
        candidate_bundle=arguments.get("candidate_bundle"),
        reference_url=arguments.get("reference_url"),
        candidate_url=arguments.get("candidate_url"),
    )


def build_reproduction_bundle_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    return build_reproduction_bundle(
        capture_bundle=arguments.get("capture_bundle", {}),
        output_dir=arguments.get("output_dir"),
    )


def clone_reference_url_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    return clone_reference_url(
        url=arguments["url"],
        timeout_seconds=int(arguments.get("timeout_seconds", 20)),
        wait_seconds=int(arguments.get("wait_seconds", 8)),
        user_data_dir=arguments.get("user_data_dir"),
        storage_state_path=arguments.get("storage_state_path"),
        storage_state_output_path=arguments.get("storage_state_output_path"),
        capture_html=bool(arguments.get("capture_html", True)),
        capture_screenshot=bool(arguments.get("capture_screenshot", True)),
        viewport_width=int(arguments.get("viewport_width", 1440)),
        viewport_height=int(arguments.get("viewport_height", 1200)),
        output_dir=arguments.get("output_dir"),
        exact_requested=bool(arguments.get("exact_requested", True)),
        license_text=arguments.get("license_text"),
        source_signals=arguments.get("source_signals"),
        include_runtime_trace=bool(arguments.get("include_runtime_trace", True)),
    )


TOOLS = [
    {
        "name": "detect_runtime_capabilities",
        "description": "Report whether node, Playwright, and a usable Chrome/Chromium binary are available for session-aware capture.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "inspect_url",
        "description": "Fetch a URL, inspect HTML metadata, and summarize likely exact-clone paths.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 60},
            },
            "required": ["url"],
        },
    },
    {
        "name": "discover_embed_candidates",
        "description": "Extract likely embed, preview, viewer, remix, and export candidates from a page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 60},
            },
            "required": ["url"],
        },
    },
    {
        "name": "trace_runtime_sources",
        "description": "Use a browser runtime to trace preview, embed, and scene URLs that do not exist in static HTML, with optional session-aware capture when Playwright is available.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "wait_seconds": {"type": "integer", "minimum": 1, "maximum": 30},
                "pattern": {"type": "string"},
                "user_data_dir": {"type": "string"},
                "storage_state_path": {"type": "string"},
                "storage_state_output_path": {"type": "string"},
                "capture_html": {"type": "boolean"},
                "capture_screenshot": {"type": "boolean"},
                "viewport_width": {"type": "integer", "minimum": 320, "maximum": 3840},
                "viewport_height": {"type": "integer", "minimum": 240, "maximum": 3840},
            },
            "required": ["url"],
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
                "candidates": {"type": "array", "items": {"type": "object"}},
                "source_signals": {"type": "array", "items": {"type": "string"}},
            },
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
                "framework": {"type": "string", "enum": ["html", "nextjs"]},
            },
            "required": ["url"],
        },
    },
    {
        "name": "capture_reference_bundle",
        "description": "Build a structured capture bundle scaffold from static inspection and optional session-aware runtime capture without claiming full DOM/CSS fidelity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 60},
                "wait_seconds": {"type": "integer", "minimum": 1, "maximum": 30},
                "include_runtime_trace": {"type": "boolean"},
                "user_data_dir": {"type": "string"},
                "storage_state_path": {"type": "string"},
                "storage_state_output_path": {"type": "string"},
                "capture_html": {"type": "boolean"},
                "capture_screenshot": {"type": "boolean"},
                "viewport_width": {"type": "integer", "minimum": 320, "maximum": 3840},
                "viewport_height": {"type": "integer", "minimum": 240, "maximum": 3840},
                "output_dir": {"type": "string"},
                "exact_requested": {"type": "boolean"},
                "license_text": {"type": "string"},
                "source_signals": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["url"],
        },
    },
    {
        "name": "plan_reproduction_path",
        "description": "Produce a source-first execution plan using the current policy and capture bundle state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "exact_requested": {"type": "boolean"},
                "license_text": {"type": "string"},
                "candidates": {"type": "array", "items": {"type": "object"}},
                "source_signals": {"type": "array", "items": {"type": "string"}},
                "capture_bundle": {"type": "object"},
            },
        },
    },
    {
        "name": "verify_fidelity_report",
        "description": "Create an honest fidelity-verification scaffold and list the missing artifacts required for real visual comparison.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "reference_bundle": {"type": "object"},
                "candidate_bundle": {"type": "object"},
                "reference_url": {"type": "string"},
                "candidate_url": {"type": "string"},
            },
        },
    },
    {
        "name": "build_reproduction_bundle",
        "description": "Turn a capture bundle into an exact-reuse or reproduction bundle with persisted embed files when a trusted reuse path exists.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "capture_bundle": {"type": "object"},
                "output_dir": {"type": "string"},
            },
            "required": ["capture_bundle"],
        },
    },
    {
        "name": "clone_reference_url",
        "description": "Run the source-first exact-clone workflow end-to-end from a single URL, including session-aware capture and reproduction bundle output.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 60},
                "wait_seconds": {"type": "integer", "minimum": 1, "maximum": 30},
                "include_runtime_trace": {"type": "boolean"},
                "user_data_dir": {"type": "string"},
                "storage_state_path": {"type": "string"},
                "storage_state_output_path": {"type": "string"},
                "capture_html": {"type": "boolean"},
                "capture_screenshot": {"type": "boolean"},
                "viewport_width": {"type": "integer", "minimum": 320, "maximum": 3840},
                "viewport_height": {"type": "integer", "minimum": 240, "maximum": 3840},
                "output_dir": {"type": "string"},
                "exact_requested": {"type": "boolean"},
                "license_text": {"type": "string"},
                "source_signals": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["url"],
        },
    },
]


TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "detect_runtime_capabilities": detect_runtime_capabilities_tool,
    "inspect_url": inspect_url,
    "discover_embed_candidates": discover_embed_candidates,
    "trace_runtime_sources": trace_runtime_sources,
    "classify_clone_mode": classify_clone_mode,
    "generate_embed_snippet": generate_embed_snippet,
    "capture_reference_bundle": capture_reference_bundle_tool,
    "plan_reproduction_path": plan_reproduction_path_tool,
    "verify_fidelity_report": verify_fidelity_report_tool,
    "build_reproduction_bundle": build_reproduction_bundle_tool,
    "clone_reference_url": clone_reference_url_tool,
}


def handle_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name not in TOOL_HANDLERS:
        raise KeyError(f"Unknown tool: {name}")
    return TOOL_HANDLERS[name](arguments)
