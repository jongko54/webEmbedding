"""Rendered scaffold self-verification helpers."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
import urllib.request
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


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _runtime_package_json() -> dict[str, Any]:
    return {
        "name": "web-embedding-next-runtime",
        "private": True,
        "version": "0.0.0",
        "scripts": {
            "build": "next build",
            "start": "next start",
        },
        "dependencies": {
            "next": "16.2.4",
            "react": "19.2.5",
            "react-dom": "19.2.5",
        },
    }


def _runtime_tsconfig() -> dict[str, Any]:
    return {
        "compilerOptions": {
            "target": "ES2022",
            "lib": ["dom", "dom.iterable", "es2022"],
            "allowJs": False,
            "skipLibCheck": True,
            "strict": False,
            "noEmit": True,
            "esModuleInterop": True,
            "module": "esnext",
            "moduleResolution": "bundler",
            "resolveJsonModule": True,
            "isolatedModules": True,
            "jsx": "preserve",
            "incremental": True,
            "plugins": [{"name": "next"}],
        },
        "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx"],
        "exclude": ["node_modules"],
    }


def _runtime_next_config() -> str:
    return "\n".join(
        [
            "/** @type {import('next').NextConfig} */",
            "const nextConfig = {",
            "  typescript: { ignoreBuildErrors: true },",
            "};",
            "",
            "export default nextConfig;",
        ]
    )


def _ensure_runtime_base(runtime_root: Path) -> None:
    runtime_root.mkdir(parents=True, exist_ok=True)
    (runtime_root / "package.json").write_text(json.dumps(_runtime_package_json(), indent=2) + "\n")
    (runtime_root / "tsconfig.json").write_text(json.dumps(_runtime_tsconfig(), indent=2) + "\n")
    (runtime_root / "next.config.mjs").write_text(_runtime_next_config().rstrip() + "\n")
    (runtime_root / "next-env.d.ts").write_text(
        "\n".join(
            [
                '/// <reference types="next" />',
                '/// <reference types="next/image-types/global" />',
                "",
            ]
        )
    )


def _copy_runtime_artifact(source: str | None, target: Path) -> None:
    if not source:
        return
    candidate = Path(source)
    if not candidate.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(candidate.read_text())


def _materialize_next_runtime_project(rebuild_artifacts: dict[str, str], runtime_root: Path) -> None:
    _ensure_runtime_base(runtime_root)
    layout_target = runtime_root / "app" / "layout.tsx"
    _copy_runtime_artifact(rebuild_artifacts.get("next-app/app/layout.tsx"), layout_target)
    _copy_runtime_artifact(rebuild_artifacts.get("next-app/app/page.tsx"), runtime_root / "app" / "page.tsx")
    _copy_runtime_artifact(rebuild_artifacts.get("next-app/app/fonts.css"), runtime_root / "app" / "fonts.css")
    _copy_runtime_artifact(rebuild_artifacts.get("next-app/app/globals.css"), runtime_root / "app" / "globals.css")
    _copy_runtime_artifact(
        rebuild_artifacts.get("next-app/components/BoundedReferencePage.tsx"),
        runtime_root / "components" / "BoundedReferencePage.tsx",
    )
    _copy_runtime_artifact(
        rebuild_artifacts.get("next-app/components/reference-data.ts"),
        runtime_root / "components" / "reference-data.ts",
    )
    if layout_target.exists() and 'import "./fonts.css"' in layout_target.read_text() and not (runtime_root / "app" / "fonts.css").exists():
        (runtime_root / "app" / "fonts.css").write_text("/* runtime stub for generated font imports */\n")


def _run_checked(command: list[str], cwd: Path, log_path: Path, timeout: int = 300) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["NPM_CONFIG_CACHE"] = str(cwd / ".npm-cache")
    with log_path.open("w") as handle:
        process = subprocess.run(
            command,
            cwd=str(cwd),
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    if process.returncode != 0:
        raise RuntimeError(f"Command failed ({process.returncode}): {' '.join(command)}")


def _reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_http(url: str, timeout_seconds: int = 45) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if int(response.status) < 500:
                    return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(1)
    if last_error is not None:
        raise RuntimeError(f"Timed out waiting for {url}: {last_error}")
    raise RuntimeError(f"Timed out waiting for {url}")


@contextmanager
def _serve_next_runtime(runtime_root: Path, renderer_dir: Path) -> Iterator[str]:
    install_log = renderer_dir / "runtime-install.log"
    build_log = renderer_dir / "runtime-build.log"
    start_log = renderer_dir / "runtime-start.log"
    env = os.environ.copy()
    env["NPM_CONFIG_CACHE"] = str(runtime_root / ".npm-cache")
    if not (runtime_root / "node_modules").exists():
        _run_checked(["npm", "install", "--no-fund", "--no-audit"], runtime_root, install_log, timeout=600)
    _run_checked(["npm", "run", "build"], runtime_root, build_log, timeout=600)
    port = _reserve_port()
    start_log.parent.mkdir(parents=True, exist_ok=True)
    with start_log.open("w") as handle:
        process = subprocess.Popen(
            ["npm", "run", "start", "--", "--hostname", "127.0.0.1", "--port", str(port)],
            cwd=str(runtime_root),
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
    url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_http(url)
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


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
                "kind": "static",
                "note": "Low-level starter scaffold derived directly from captured block summaries.",
            }
        )
    app_preview_path = rebuild_artifacts.get("app-preview.html")
    if app_preview_path:
        candidates.append(
            {
                "name": "role-inferred-app",
                "entrypoint": app_preview_path,
                "kind": "static",
                "note": "Role-inferred app-model preview that mirrors the bounded Next renderer more closely.",
            }
        )
    if all(
        rebuild_artifacts.get(key)
        for key in (
            "next-app/app/layout.tsx",
            "next-app/app/page.tsx",
            "next-app/app/globals.css",
            "next-app/components/BoundedReferencePage.tsx",
            "next-app/components/reference-data.ts",
        )
    ):
        candidates.append(
            {
                "name": "next-runtime-app",
                "entrypoint": rebuild_artifacts["next-app/app/page.tsx"],
                "kind": "next-runtime",
                "note": "Booted Next runtime using the generated next-app scaffold for higher-fidelity verification.",
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
    normalized_focus_checks = [str(item) for item in focus_checks if item]
    score = 0
    try:
        score = int(renderer_score or 0)
    except (TypeError, ValueError):
        score = 0
    priority_findings = [str(item) for item in (guidance.get("priority_findings") or []) if item]
    recommended_actions = [str(item) for item in (guidance.get("recommended_actions") or []) if item]
    breakpoint_needs_layout_attention = any(
        isinstance(report, dict)
        and report.get("available")
        and (
            "screenshot" in str(report.get("focus") or "").lower()
            or "layout" in str(report.get("focus") or "").lower()
            or "spacing" in str(report.get("focus") or "").lower()
        )
        for report in breakpoint_focus
    )
    if score < 70 or breakpoint_needs_layout_attention:
        for name in ("screenshot", "dom snapshot", "computed styles"):
            if name not in normalized_focus_checks:
                normalized_focus_checks.append(name)
    if any("interaction" in item.lower() for item in priority_findings + recommended_actions):
        for name in ("interaction states", "interaction trace"):
            if name not in normalized_focus_checks:
                normalized_focus_checks.append(name)
    return {
        "available": True,
        "status": "generated",
        "target_renderer": renderer_name,
        "score": renderer_score,
        "focus_checks": normalized_focus_checks[:6],
        "priority_findings": priority_findings,
        "recommended_actions": recommended_actions,
        "breakpoint_focus": breakpoint_focus,
        "prompt": "\n".join(
            [
                f"Repair the bounded renderer `{renderer_name}` before claiming exact parity.",
                f"Current bounded score: {renderer_score}.",
                "Focus checks: "
                + (
                    ", ".join(normalized_focus_checks[:6])
                    if normalized_focus_checks
                    else "screenshot, structure, and interaction parity"
                ),
                "Priority findings:",
                *[f"- {item}" for item in priority_findings[:6]],
                "Recommended actions:",
                *[f"- {item}" for item in recommended_actions[:6]],
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
    runtime_cache_root = output_dir / "reproduction" / "_next-runtime-cache"

    with _serve_directory(rebuild_root) as base_url:
        for renderer in renderer_candidates:
            name = renderer["name"]
            entrypoint = Path(renderer["entrypoint"]).expanduser().resolve()
            renderer_dir = self_verify_dir / "renderers" / name
            rendered_capture_dir = renderer_dir / "rendered-capture"
            preview_url: str | None = None
            rendered_bundle: dict[str, Any] | None = None
            runtime_error: str | None = None
            try:
                if renderer.get("kind") == "next-runtime":
                    _materialize_next_runtime_project(rebuild_artifacts, runtime_cache_root)
                    with _serve_next_runtime(runtime_cache_root, renderer_dir) as runtime_url:
                        preview_url = runtime_url
                        rendered_bundle = capture_reference_bundle(
                            url=runtime_url,
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
                else:
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
            except Exception as exc:  # noqa: BLE001
                runtime_error = str(exc)

            if not rendered_bundle or not preview_url:
                renderer_result = {
                    "name": name,
                    "entrypoint": str(entrypoint),
                    "preview_url": preview_url,
                    "note": renderer.get("note"),
                    "score": 0,
                    "ready_for_exact_clone": False,
                    "root_report": {
                        "verdict": "skipped",
                        "score": 0,
                        "ready_for_exact_clone": False,
                        "report_path": None,
                        "error": runtime_error,
                    },
                    "breakpoints": {"compared": 0, "reports": []},
                    "runtime_error": runtime_error,
                }
                renderer_results.append(renderer_result)
                persisted["renderers"][name] = {
                    "root_report": None,
                    "breakpoint_reports": {},
                    "runtime_error": runtime_error,
                }
                continue

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
                "kind": renderer.get("kind"),
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
                "kind": item.get("kind"),
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
        "note": "This self-verify loop renders bounded scaffold previews, and when a generated next-app scaffold is present it also attempts a booted Next runtime candidate before emitting a repair plan.",
    }
    _persist_report(self_verify_dir / "summary.json", result)
    persisted["summary"] = str(self_verify_dir / "summary.json")
    result["persisted"] = persisted
    return result
