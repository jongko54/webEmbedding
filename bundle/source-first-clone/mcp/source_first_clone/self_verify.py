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


def _renderer_candidates(rebuild_artifacts: dict[str, str]) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    starter_html_path = rebuild_artifacts.get("starter.html")
    if starter_html_path:
        candidates.append(
            {
                "name": "starter",
                "entrypoint": starter_html_path,
                "note": "Low-level starter scaffold derived directly from captured block summaries.",
            }
        )
    app_preview_path = rebuild_artifacts.get("app-preview.html")
    if app_preview_path:
        candidates.append(
            {
                "name": "role-inferred-app",
                "entrypoint": app_preview_path,
                "note": "Role-inferred app-model preview that mirrors the bounded Next renderer more closely.",
            }
        )
    return candidates


def _comparison_score(report: dict[str, Any]) -> int:
    summary = report.get("comparison_summary", {}) if isinstance(report, dict) else {}
    try:
        return int(summary.get("score") or 0)
    except (TypeError, ValueError):
        return 0


def _renderer_ready(report: dict[str, Any]) -> bool:
    return bool(((report.get("downstream_guidance") or {}).get("ready_for_exact_clone")))


def _build_repair_plan(
    preferred_renderer: dict[str, Any] | None,
    breakpoint_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    renderer = preferred_renderer or {}
    root_report = renderer.get("report", {}) if isinstance(renderer, dict) else {}
    guidance = root_report.get("downstream_guidance", {}) if isinstance(root_report, dict) else {}
    comparison = root_report.get("comparison_summary", {}) if isinstance(root_report, dict) else {}
    renderer_name = renderer.get("name") or "starter"
    renderer_score = renderer.get("score")
    breakpoint_focus = []
    for report in breakpoint_reports:
        if not isinstance(report, dict) or not report.get("available"):
            continue
        if report.get("ready_for_exact_clone"):
            continue
        breakpoint_focus.append(
            {
                "name": report.get("name"),
                "score": report.get("score"),
                "focus": report.get("focus"),
            }
        )
    focus_checks = [item.get("name") for item in (comparison.get("weakest_checks") or []) if isinstance(item, dict)]
    return {
        "available": True,
        "status": "generated",
        "target_renderer": renderer_name,
        "score": renderer_score,
        "focus_checks": focus_checks[:4],
        "priority_findings": guidance.get("priority_findings") or [],
        "recommended_actions": guidance.get("recommended_actions") or [],
        "breakpoint_focus": breakpoint_focus,
        "prompt": "\n".join(
            [
                f"Repair the bounded renderer `{renderer_name}` before claiming exact parity.",
                f"Current bounded score: {renderer_score}.",
                "Focus checks: " + (", ".join(focus_checks[:4]) if focus_checks else "screenshot, structure, and interaction parity"),
                "Priority findings:",
                *[f"- {item}" for item in (guidance.get("priority_findings") or [])[:6]],
                "Recommended actions:",
                *[f"- {item}" for item in (guidance.get("recommended_actions") or [])[:6]],
                "Breakpoint focus:",
                *[
                    f"- {item.get('name')}: {item.get('focus')} (score {item.get('score')})"
                    for item in breakpoint_focus[:4]
                ],
            ]
        ).rstrip(),
    }


def run_rebuild_self_verify(
    reference_bundle: dict[str, Any],
    rebuild_artifacts: dict[str, str],
    output_dir: Path,
    stage_path: str = "self-verify",
) -> dict[str, Any]:
    if not isinstance(rebuild_artifacts, dict):
        return {
            "available": False,
            "status": "skipped",
            "reason": "No persisted rebuild scaffold artifacts were available.",
        }

    renderer_candidates = _renderer_candidates(rebuild_artifacts)
    if not renderer_candidates:
        return {
            "available": False,
            "status": "skipped",
            "reason": "No renderable preview entrypoint was present in the rebuild scaffold.",
        }

    rebuild_root = Path(renderer_candidates[0]["entrypoint"]).expanduser().resolve().parent
    primary_request = reference_bundle.get("session_request", {}) if isinstance(reference_bundle, dict) else {}
    breakpoint_summary = reference_bundle.get("breakpoints", {}) if isinstance(reference_bundle, dict) else {}
    breakpoint_profiles = breakpoint_summary.get("requested_profiles") if isinstance(breakpoint_summary, dict) else []
    self_verify_dir = output_dir / "reproduction" / Path(stage_path)
    persisted: dict[str, Any] = {"renderers": {}}
    renderer_results: list[dict[str, Any]] = []

    with _serve_directory(rebuild_root) as base_url:
        for renderer in renderer_candidates:
            name = renderer["name"]
            entrypoint = Path(renderer["entrypoint"]).expanduser().resolve()
            renderer_dir = self_verify_dir / "renderers" / name
            rendered_capture_dir = renderer_dir / "rendered-capture"
            preview_url = f"{base_url}/{entrypoint.relative_to(rebuild_root).as_posix()}"
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
            root_report_path = _persist_report(renderer_dir / "verification.json", root_report)

            reference_variants = _breakpoint_variant_map(reference_bundle)
            rendered_variants = _breakpoint_variant_map(rendered_bundle)
            breakpoint_reports: list[dict[str, Any]] = []
            if reference_variants and rendered_variants:
                for variant_name in sorted(set(reference_variants) & set(rendered_variants)):
                    reference_variant_bundle = _load_json_file(reference_variants[variant_name].get("capture_manifest"))
                    rendered_variant_bundle = _load_json_file(rendered_variants[variant_name].get("capture_manifest"))
                    if not reference_variant_bundle or not rendered_variant_bundle:
                        breakpoint_reports.append(
                            {
                                "name": variant_name,
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
                    report_path = _persist_report(renderer_dir / "breakpoints" / f"{variant_name}-verification.json", report)
                    breakpoint_reports.append(
                        {
                            "name": variant_name,
                            "available": True,
                            "verdict": report.get("verdict"),
                            "score": (report.get("comparison_summary") or {}).get("score"),
                            "ready_for_exact_clone": (report.get("downstream_guidance") or {}).get("ready_for_exact_clone"),
                            "focus": ((report.get("downstream_guidance") or {}).get("priority_findings") or [None])[0],
                            "report_path": report_path,
                        }
                    )
            overall_ready = _renderer_ready(root_report)
            if breakpoint_reports:
                overall_ready = overall_ready and all(bool(report.get("ready_for_exact_clone")) for report in breakpoint_reports if report.get("available"))
            score = _comparison_score(root_report)
            renderer_result = {
                "name": name,
                "entrypoint": str(entrypoint),
                "preview_url": preview_url,
                "note": renderer.get("note"),
                "report": root_report,
                "score": score,
                "ready_for_exact_clone": overall_ready,
                "rendered_capture_manifest": ((rendered_bundle.get("bundle") or {}).get("persisted") or {}).get("files", {}).get("capture_manifest"),
                "root_report": {
                    "verdict": root_report.get("verdict"),
                    "score": score,
                    "ready_for_exact_clone": _renderer_ready(root_report),
                    "report_path": root_report_path,
                },
                "breakpoints": {
                    "compared": len([report for report in breakpoint_reports if report.get("available")]),
                    "reports": breakpoint_reports,
                },
            }
            renderer_results.append(renderer_result)
            persisted["renderers"][name] = {
                "root_report": root_report_path,
                "breakpoint_reports": {
                    report["name"]: report["report_path"]
                    for report in breakpoint_reports
                    if report.get("available") and report.get("report_path")
                },
            }

    preferred_renderer = max(
        renderer_results,
        key=lambda item: (
            1 if item.get("ready_for_exact_clone") else 0,
            int(item.get("score") or 0),
        ),
    ) if renderer_results else None
    preferred_breakpoints = ((preferred_renderer or {}).get("breakpoints") or {}).get("reports") or []
    overall_ready = any(bool(item.get("ready_for_exact_clone")) for item in renderer_results)
    repair_plan = _build_repair_plan(preferred_renderer, preferred_breakpoints)
    persisted["repair_plan"] = _persist_report(self_verify_dir / "repair-plan.json", repair_plan)
    repair_prompt_path = self_verify_dir / "repair-prompt.txt"
    repair_prompt_path.write_text(str(repair_plan.get("prompt") or "").rstrip() + "\n")
    persisted["repair_prompt"] = str(repair_prompt_path)

    result = {
        "available": True,
        "status": "completed",
        "renderer_count": len(renderer_results),
        "preferred_renderer": {
            "name": (preferred_renderer or {}).get("name"),
            "entrypoint": (preferred_renderer or {}).get("entrypoint"),
            "score": (preferred_renderer or {}).get("score"),
            "ready_for_exact_clone": (preferred_renderer or {}).get("ready_for_exact_clone"),
            "report_path": (((preferred_renderer or {}).get("root_report") or {}).get("report_path")),
        },
        "renderers": [
            {
                "name": item.get("name"),
                "score": item.get("score"),
                "ready_for_exact_clone": item.get("ready_for_exact_clone"),
                "entrypoint": item.get("entrypoint"),
                "report_path": ((item.get("root_report") or {}).get("report_path")),
            }
            for item in renderer_results
        ],
        "rendered_capture_manifest": (preferred_renderer or {}).get("rendered_capture_manifest"),
        "root_report": (preferred_renderer or {}).get("root_report"),
        "breakpoints": (preferred_renderer or {}).get("breakpoints") or {"compared": 0, "reports": []},
        "overall_ready_for_exact_clone": overall_ready,
        "repair_plan": {
            "target_renderer": repair_plan.get("target_renderer"),
            "score": repair_plan.get("score"),
            "priority_findings": repair_plan.get("priority_findings"),
            "recommended_actions": repair_plan.get("recommended_actions"),
            "path": persisted["repair_plan"],
            "prompt_path": persisted["repair_prompt"],
        },
        "persisted": persisted,
        "note": "This self-verify loop renders bounded scaffold previews, compares them back to the reference bundle, and emits a repair plan. It still does not boot a full Next.js runtime.",
    }
    _persist_report(self_verify_dir / "summary.json", result)
    persisted["summary"] = str(self_verify_dir / "summary.json")
    result["persisted"] = persisted
    return result
