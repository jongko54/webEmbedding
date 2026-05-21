"""Tool definitions and dispatch for the source-first clone MCP server."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .acquisition import assert_http_url, detect_runtime_capabilities
from .acquisition import discover_embed_candidates as discover_embed_candidates_fn
from .acquisition import inspect_reference, trace_runtime_sources as trace_runtime_sources_fn
from .capture_bundle import capture_reference_bundle
from .har_replay import build_replay_report
from .job_queue import JobQueue
from .orchestration import clone_reference_url
from .policy import classify_clone_mode
from .planning import plan_reproduction_path
from .rebuild_scaffold import build_rebuild_scaffold, persist_rebuild_scaffold
from .reproduction import build_reproduction_bundle
from .verification import verify_fidelity_report


def generate_embed_snippet(arguments: dict[str, Any]) -> dict[str, Any]:
    title = arguments.get("title") or "Embedded reference"
    framework = arguments.get("framework", "nextjs")
    url = assert_http_url(arguments["url"])

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
        breakpoint_profiles=arguments.get("breakpoint_profiles"),
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
        site_profile=arguments.get("site_profile"),
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


def build_rebuild_scaffold_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    scaffold = build_rebuild_scaffold(arguments.get("capture_bundle", {}))
    if arguments.get("output_dir"):
        persisted = persist_rebuild_scaffold(Path(arguments["output_dir"]).expanduser().resolve(), scaffold)
        scaffold["persisted"] = persisted
    return scaffold


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
        breakpoint_profiles=arguments.get("breakpoint_profiles"),
        output_dir=arguments.get("output_dir"),
        exact_requested=bool(arguments.get("exact_requested", True)),
        license_text=arguments.get("license_text"),
        source_signals=arguments.get("source_signals"),
        include_runtime_trace=bool(arguments.get("include_runtime_trace", True)),
    )


def _queue_clone_args(arguments: dict[str, Any]) -> dict[str, Any]:
    clone_args = dict(arguments.get("clone_args") or {})
    for key in (
        "timeout_seconds",
        "wait_seconds",
        "user_data_dir",
        "storage_state_path",
        "storage_state_output_path",
        "capture_html",
        "capture_screenshot",
        "viewport_width",
        "viewport_height",
        "breakpoint_profiles",
        "exact_requested",
        "license_text",
        "source_signals",
        "include_runtime_trace",
    ):
        if key in arguments and arguments.get(key) is not None:
            clone_args[key] = arguments[key]
    return clone_args


def enqueue_clone_job_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    queue = JobQueue(
        arguments["queue_root"],
        max_attempts=int(arguments.get("max_attempts", 2)),
        retry_delay_seconds=int(arguments.get("retry_delay_seconds", 30)),
    )
    return queue.enqueue(
        arguments["url"],
        output_dir=arguments.get("output_dir"),
        clone_args=_queue_clone_args(arguments),
        metadata=arguments.get("metadata") if isinstance(arguments.get("metadata"), dict) else None,
        max_attempts=int(arguments["max_attempts"]) if arguments.get("max_attempts") is not None else None,
        retry_delay_seconds=int(arguments["retry_delay_seconds"]) if arguments.get("retry_delay_seconds") is not None else None,
    )


def list_clone_jobs_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    queue = JobQueue(arguments["queue_root"])
    states = arguments.get("states")
    jobs = queue.list(states=states if isinstance(states, list) else None)
    return {
        "queue_root": str(queue.queue_root),
        "count": len(jobs),
        "jobs": jobs,
    }


def load_clone_job_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    return JobQueue(arguments["queue_root"]).load(arguments["job_id"])


def cancel_clone_job_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    return JobQueue(arguments["queue_root"]).cancel(arguments["job_id"], reason=arguments.get("reason"))


def run_clone_job_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    queue = JobQueue(arguments["queue_root"])
    worker_id = arguments.get("worker_id")
    if arguments.get("job_id"):
        return queue.run_job(arguments["job_id"], worker_id=worker_id)
    job = queue.run_next(worker_id=worker_id)
    return {"processed": False, "job": None} if job is None else {"processed": True, "job": job}


def replay_har_requests_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    requests = arguments.get("requests")
    if requests is not None and not isinstance(requests, list):
        raise ValueError("requests must be an array when provided")
    return build_replay_report(
        arguments["har_path"],
        requests=requests,
        output_path=arguments.get("output_path"),
        consume=bool(arguments.get("consume", True)),
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
        "description": "Use a browser runtime to trace preview, embed, and scene URLs for a single viewport, with optional session-aware capture when Playwright is available.",
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
                "site_profile": {"type": "object"},
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
                "breakpoint_profiles": {"type": "array", "items": {"type": "string", "enum": ["desktop", "tablet", "mobile"]}},
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
                "site_profile": {"type": "object"},
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
        "name": "build_rebuild_scaffold",
        "description": "Generate a bounded rebuild scaffold from an existing capture bundle when exact reuse is unavailable.",
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
                "breakpoint_profiles": {"type": "array", "items": {"type": "string", "enum": ["desktop", "tablet", "mobile"]}},
                "output_dir": {"type": "string"},
                "exact_requested": {"type": "boolean"},
                "license_text": {"type": "string"},
                "source_signals": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["url"],
        },
    },
    {
        "name": "enqueue_clone_job",
        "description": "Persist a source-first clone job into a filesystem-backed async queue for later worker execution.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "queue_root": {"type": "string"},
                "url": {"type": "string"},
                "output_dir": {"type": "string"},
                "clone_args": {"type": "object"},
                "metadata": {"type": "object"},
                "max_attempts": {"type": "integer", "minimum": 1, "maximum": 10},
                "retry_delay_seconds": {"type": "integer", "minimum": 0, "maximum": 3600},
            },
            "required": ["queue_root", "url"],
        },
    },
    {
        "name": "list_clone_jobs",
        "description": "List persisted clone jobs from the filesystem-backed async queue.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "queue_root": {"type": "string"},
                "states": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["queue_root"],
        },
    },
    {
        "name": "load_clone_job",
        "description": "Load one persisted clone job by id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "queue_root": {"type": "string"},
                "job_id": {"type": "string"},
            },
            "required": ["queue_root", "job_id"],
        },
    },
    {
        "name": "cancel_clone_job",
        "description": "Move a queued or retry-wait clone job to cancelled.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "queue_root": {"type": "string"},
                "job_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["queue_root", "job_id"],
        },
    },
    {
        "name": "run_clone_job",
        "description": "Run one clone queue job, or the next due queued/retry-wait job when job_id is omitted.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "queue_root": {"type": "string"},
                "job_id": {"type": "string"},
                "worker_id": {"type": "string"},
            },
            "required": ["queue_root"],
        },
    },
    {
        "name": "replay_har_requests",
        "description": "Replay request specs against a standard HAR, near-HAR, or webEmbedding network manifest and return deterministic response matches.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "har_path": {"type": "string"},
                "requests": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "method": {"type": "string"},
                            "url": {"type": "string"},
                            "postData": {},
                        },
                        "required": ["url"],
                    },
                },
                "output_path": {"type": "string"},
                "consume": {"type": "boolean"},
            },
            "required": ["har_path"],
        },
    },
]


TOOL_ANNOTATIONS: dict[str, dict[str, bool]] = {
    "detect_runtime_capabilities": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False, "idempotentHint": True},
    "inspect_url": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True, "idempotentHint": True},
    "discover_embed_candidates": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True, "idempotentHint": True},
    "trace_runtime_sources": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True, "idempotentHint": False},
    "classify_clone_mode": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False, "idempotentHint": True},
    "generate_embed_snippet": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False, "idempotentHint": True},
    "capture_reference_bundle": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True, "idempotentHint": False},
    "plan_reproduction_path": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False, "idempotentHint": True},
    "verify_fidelity_report": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False, "idempotentHint": True},
    "build_reproduction_bundle": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False, "idempotentHint": False},
    "build_rebuild_scaffold": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False, "idempotentHint": False},
    "clone_reference_url": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True, "idempotentHint": False},
    "enqueue_clone_job": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False, "idempotentHint": False},
    "list_clone_jobs": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False, "idempotentHint": True},
    "load_clone_job": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False, "idempotentHint": True},
    "cancel_clone_job": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False, "idempotentHint": False},
    "run_clone_job": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True, "idempotentHint": False},
    "replay_har_requests": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False, "idempotentHint": False},
}


for tool in TOOLS:
    annotations = TOOL_ANNOTATIONS.get(str(tool.get("name") or ""))
    if annotations:
        tool["annotations"] = annotations


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
    "build_rebuild_scaffold": build_rebuild_scaffold_tool,
    "clone_reference_url": clone_reference_url_tool,
    "enqueue_clone_job": enqueue_clone_job_tool,
    "list_clone_jobs": list_clone_jobs_tool,
    "load_clone_job": load_clone_job_tool,
    "cancel_clone_job": cancel_clone_job_tool,
    "run_clone_job": run_clone_job_tool,
    "replay_har_requests": replay_har_requests_tool,
}


def handle_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name not in TOOL_HANDLERS:
        raise KeyError(f"Unknown tool: {name}")
    return TOOL_HANDLERS[name](arguments)
