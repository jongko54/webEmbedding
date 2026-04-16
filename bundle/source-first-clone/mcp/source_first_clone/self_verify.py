"""Rendered scaffold self-verification helpers."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

from .capture_bundle import capture_reference_bundle
from .verification import verify_fidelity_report


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        del format, args


@contextmanager
def _serve_directory(directory: Path) -> Iterator[str]:
    handler = partial(_QuietHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _load_json_file(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    candidate = Path(path)
    if not candidate.exists():
        return None
    payload = json.loads(candidate.read_text())
    return payload if isinstance(payload, dict) else None


def _breakpoint_variant_map(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    breakpoints = bundle.get("breakpoints", {}) if isinstance(bundle, dict) else {}
    variants = breakpoints.get("variants", []) if isinstance(breakpoints, dict) else []
    mapped: dict[str, dict[str, Any]] = {}
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        name = str(variant.get("name") or "").strip().lower()
        if name:
            mapped[name] = variant
    return mapped


def _persist_report(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return str(path)


def run_rebuild_self_verify(
    reference_bundle: dict[str, Any],
    rebuild_artifacts: dict[str, str],
    output_dir: Path,
) -> dict[str, Any]:
    if not isinstance(rebuild_artifacts, dict):
        return {
            "available": False,
            "status": "skipped",
            "reason": "No persisted rebuild scaffold artifacts were available.",
        }

    starter_html_path = rebuild_artifacts.get("starter.html")
    if not starter_html_path:
        return {
            "available": False,
            "status": "skipped",
            "reason": "starter.html was not present in the rebuild scaffold.",
        }

    rebuild_root = Path(starter_html_path).expanduser().resolve().parent
    primary_request = reference_bundle.get("session_request", {}) if isinstance(reference_bundle, dict) else {}
    breakpoint_summary = reference_bundle.get("breakpoints", {}) if isinstance(reference_bundle, dict) else {}
    breakpoint_profiles = breakpoint_summary.get("requested_profiles") if isinstance(breakpoint_summary, dict) else []
    self_verify_dir = output_dir / "reproduction" / "self-verify"
    rendered_capture_dir = self_verify_dir / "rendered-capture"

    with _serve_directory(rebuild_root) as base_url:
        preview_url = f"{base_url}/starter.html"
        rendered_bundle = capture_reference_bundle(
            url=preview_url,
            timeout_seconds=10,
            wait_seconds=4,
            include_runtime_trace=True,
            capture_html=True,
            capture_screenshot=True,
            viewport_width=int(primary_request.get("viewport_width") or 1440),
            viewport_height=int(primary_request.get("viewport_height") or 1200),
            breakpoint_profiles=breakpoint_profiles if isinstance(breakpoint_profiles, list) else [],
            output_dir=str(rendered_capture_dir),
            exact_requested=False,
        )

    root_report = verify_fidelity_report(
        reference_bundle=reference_bundle,
        candidate_bundle=rendered_bundle,
        reference_url=reference_bundle.get("url"),
        candidate_url=rendered_bundle.get("url"),
    )
    persisted: dict[str, Any] = {
        "root_report": _persist_report(self_verify_dir / "verification.json", root_report),
    }

    reference_variants = _breakpoint_variant_map(reference_bundle)
    rendered_variants = _breakpoint_variant_map(rendered_bundle)
    breakpoint_reports: list[dict[str, Any]] = []
    if reference_variants and rendered_variants:
        for name in sorted(set(reference_variants) & set(rendered_variants)):
            reference_variant_bundle = _load_json_file(reference_variants[name].get("capture_manifest"))
            rendered_variant_bundle = _load_json_file(rendered_variants[name].get("capture_manifest"))
            if not reference_variant_bundle or not rendered_variant_bundle:
                breakpoint_reports.append(
                    {
                        "name": name,
                        "available": False,
                        "reason": "Variant capture bundle was missing.",
                    }
                )
                continue
            report = verify_fidelity_report(
                reference_bundle=reference_variant_bundle,
                candidate_bundle=rendered_variant_bundle,
                reference_url=reference_variant_bundle.get("url"),
                candidate_url=rendered_variant_bundle.get("url"),
            )
            report_path = _persist_report(self_verify_dir / "breakpoints" / f"{name}-verification.json", report)
            breakpoint_reports.append(
                {
                    "name": name,
                    "available": True,
                    "verdict": report.get("verdict"),
                    "score": (report.get("comparison_summary") or {}).get("score"),
                    "ready_for_exact_clone": (report.get("downstream_guidance") or {}).get("ready_for_exact_clone"),
                    "report_path": report_path,
                }
            )
        persisted["breakpoint_reports"] = {
            report["name"]: report["report_path"]
            for report in breakpoint_reports
            if report.get("available") and report.get("report_path")
        }

    overall_ready = bool((root_report.get("downstream_guidance") or {}).get("ready_for_exact_clone"))
    if breakpoint_reports:
        overall_ready = overall_ready and all(bool(report.get("ready_for_exact_clone")) for report in breakpoint_reports if report.get("available"))

    result = {
        "available": True,
        "status": "completed",
        "preview_entrypoint": str(Path(starter_html_path).expanduser().resolve()),
        "rendered_capture_manifest": ((rendered_bundle.get("bundle") or {}).get("persisted") or {}).get("files", {}).get("capture_manifest"),
        "root_report": {
            "verdict": root_report.get("verdict"),
            "score": (root_report.get("comparison_summary") or {}).get("score"),
            "ready_for_exact_clone": (root_report.get("downstream_guidance") or {}).get("ready_for_exact_clone"),
            "report_path": persisted["root_report"],
        },
        "breakpoints": {
            "compared": len([report for report in breakpoint_reports if report.get("available")]),
            "reports": breakpoint_reports,
        },
        "overall_ready_for_exact_clone": overall_ready,
        "persisted": persisted,
        "note": "This self-verify loop renders the bounded starter scaffold and compares it back to the reference bundle. It does not boot a full Next.js runtime.",
    }
    _persist_report(self_verify_dir / "summary.json", result)
    persisted["summary"] = str(self_verify_dir / "summary.json")
    result["persisted"] = persisted
    return result
