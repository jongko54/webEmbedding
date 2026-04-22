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


def _load_text_file(path: str | None) -> str:
    if not path:
        return ""
    candidate = Path(path)
    if not candidate.exists():
        return ""
    return candidate.read_text()


def _artifact_quality_signals(rebuild_artifacts: dict[str, str]) -> dict[str, Any]:
    app_model = _load_json_file(rebuild_artifacts.get("app-model.json")) or {}
    layout_summary = _load_json_file(rebuild_artifacts.get("layout-summary.json")) or {}
    tsx = _load_text_file(rebuild_artifacts.get("next-app/components/BoundedReferencePage.tsx"))
    preview = _load_text_file(rebuild_artifacts.get("app-preview.html"))
    renderer = layout_summary.get("renderer", {}) if isinstance(layout_summary.get("renderer"), dict) else {}
    renderer_kind = str(renderer.get("kind") or app_model.get("rendererKind") or "").strip()
    visual_stage = app_model.get("visualStage")
    if not isinstance(visual_stage, dict):
        visual_stage = layout_summary.get("visualStage") if isinstance(layout_summary.get("visualStage"), dict) else {}
    reference_image = visual_stage.get("referenceImage", {}) if isinstance(visual_stage, dict) else {}
    if not isinstance(reference_image, dict):
        reference_image = {}
    reference_src = str(reference_image.get("src") or "")
    visual_layers = app_model.get("visualLayers") if isinstance(app_model.get("visualLayers"), list) else []
    shell_regions = app_model.get("shellRegions") if isinstance(app_model.get("shellRegions"), list) else []
    expects_visual_stage = (
        renderer_kind == "visual-fallback-next-app"
        or bool((visual_stage or {}).get("available"))
        or bool(reference_src)
        or bool(visual_layers)
    )
    expects_shell_regions = renderer_kind == "app-shell-dashboard-next-app" or bool(shell_regions)
    checks = [
        {
            "name": "visual-stage-reference",
            "required": expects_visual_stage,
            "passed": (not expects_visual_stage) or bool(reference_src),
            "detail": "captured screenshot reference image is present in app-model/layout-summary",
        },
        {
            "name": "visual-layers-model",
            "required": expects_visual_stage,
            "passed": (not expects_visual_stage) or bool(visual_layers),
            "detail": "captured visual geometry is present in app-model.visualLayers",
        },
        {
            "name": "visual-stage-tsx",
            "required": expects_visual_stage,
            "passed": (not expects_visual_stage) or "bounded-stage-reference" in tsx,
            "detail": "Next renderer renders the captured screenshot stage",
        },
        {
            "name": "visual-layers-tsx",
            "required": expects_visual_stage,
            "passed": (not expects_visual_stage) or ("visualLayers.slice" in tsx and "bounded-visual-layer" in tsx),
            "detail": "Next renderer overlays captured visual layer geometry",
        },
        {
            "name": "visual-stage-preview",
            "required": expects_visual_stage,
            "passed": (not expects_visual_stage) or "bounded-stage-reference" in preview,
            "detail": "static app preview renders the captured screenshot stage",
        },
        {
            "name": "visual-layers-preview",
            "required": expects_visual_stage,
            "passed": (not expects_visual_stage) or "bounded-visual-layer" in preview,
            "detail": "static app preview overlays captured visual layer geometry",
        },
        {
            "name": "shell-regions-model",
            "required": expects_shell_regions,
            "passed": (not expects_shell_regions) or bool(shell_regions),
            "detail": "captured shell panel geometry is present in app-model.shellRegions",
        },
        {
            "name": "shell-regions-tsx",
            "required": expects_shell_regions,
            "passed": (not expects_shell_regions) or "bounded-shell-region" in tsx,
            "detail": "Next renderer renders captured shell panel regions",
        },
        {
            "name": "shell-regions-preview",
            "required": expects_shell_regions,
            "passed": (not expects_shell_regions)
            or ("bounded-shell-region" in preview and "bounded-shell-panel-grid" in preview),
            "detail": "static app preview renders captured shell panel regions and panel grid",
        },
    ]
    missing_required = [
        str(check.get("name"))
        for check in checks
        if check.get("required") and not check.get("passed")
    ]
    return {
        "available": bool(app_model or layout_summary or tsx or preview),
        "ready": not missing_required,
        "required": expects_visual_stage or expects_shell_regions,
        "renderer_kind": renderer_kind,
        "expects_visual_stage": expects_visual_stage,
        "expects_shell_regions": expects_shell_regions,
        "visual_layer_count": len(visual_layers),
        "shell_region_count": len(shell_regions),
        "reference_image_present": bool(reference_src),
        "checks": checks,
        "missing_required": missing_required,
    }


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _find_report_check(report: dict[str, Any] | None, name: str) -> dict[str, Any] | None:
    if not isinstance(report, dict):
        return None
    if report.get("name") == name:
        return report
    for key in ("check_details", "checks"):
        checks = report.get(key)
        if not isinstance(checks, list):
            continue
        for item in checks:
            if isinstance(item, dict) and item.get("name") == name:
                return item
    return None


def _visual_qa_metric_summary(metrics: dict[str, Any]) -> dict[str, float]:
    selected = {}
    for key in (
        "dimension_similarity",
        "ahash_similarity",
        "mean_luma_similarity",
        "contrast_similarity",
        "rgb_similarity",
        "histogram_similarity",
        "grid_similarity",
        "quadrant_similarity",
        "band_similarity",
        "edge_similarity",
        "pixel_luma_similarity",
        "pixel_rgb_similarity",
        "pixel_mismatch_similarity",
        "pixel_mismatch_ratio",
        "pixel_mean_abs_luma_delta",
        "pixel_mean_abs_rgb_delta",
    ):
        value = _coerce_float(metrics.get(key))
        if value is not None:
            selected[key] = round(value, 4)
    return selected


def _visual_qa_focus_for_flags(flags: list[str]) -> list[str]:
    focus = ["screenshot"]
    flag_focus = {
        "viewport-or-breakpoint drift": ["breakpoint layout", "viewport geometry"],
        "composition drift": ["stage geometry", "section placement"],
        "vertical-flow drift": ["vertical flow", "section placement"],
        "layout-or-large-visual drift": ["stage geometry", "media placement"],
        "palette-or-background drift": ["palette"],
        "color-balance drift": ["palette"],
        "shape-and-contrast drift": ["typography", "component shape"],
        "contrast-or-depth drift": ["surface contrast"],
        "pixel-structure drift": ["media placement", "micro layout"],
        "luma-or-contrast drift": ["surface contrast"],
        "channel-color drift": ["accent colors"],
    }
    for flag in flags:
        for item in flag_focus.get(flag, []):
            if item not in focus:
                focus.append(item)
    return focus[:8]


def _visual_qa_actions_for_flags(flags: list[str]) -> list[str]:
    actions = []
    if "viewport-or-breakpoint drift" in flags:
        actions.append("Re-render and compare against the same viewport and breakpoint dimensions before tuning layout.")
    if "composition drift" in flags or "layout-or-large-visual drift" in flags:
        actions.append("Align stage geometry, section y-positions, and large media placement against the reference screenshot.")
    if "vertical-flow drift" in flags:
        actions.append("Rebuild vertical flow using captured section heights and top offsets before changing fine styling.")
    if "pixel-structure drift" in flags:
        actions.append("Compare local spacing, overlay positions, and image/media bounds against the reference pixel grid.")
    if "palette-or-background drift" in flags or "color-balance drift" in flags or "channel-color drift" in flags:
        actions.append("Promote captured body, surface, text, and accent colors into the renderer tokens.")
    if "shape-and-contrast drift" in flags or "contrast-or-depth drift" in flags or "luma-or-contrast drift" in flags:
        actions.append("Audit typography weight, contrast, surface depth, and component shape against the reference capture.")
    if not actions:
        actions.append("Recheck screenshot, stage geometry, media placement, and palette before claiming visual parity.")
    deduped = []
    for action in actions:
        if action not in deduped:
            deduped.append(action)
    return deduped[:6]


def _visual_qa_score_from_metrics(similarity: float | None, metrics: dict[str, Any], flags: list[str]) -> int:
    score = int(round(max(0.0, min(1.0, similarity if similarity is not None else 0.0)) * 100))
    if similarity is None:
        score = 50 if metrics else 0
    flag_penalties = {
        "viewport-or-breakpoint drift": 20,
        "composition drift": 16,
        "vertical-flow drift": 14,
        "layout-or-large-visual drift": 16,
        "pixel-structure drift": 14,
        "palette-or-background drift": 10,
        "color-balance drift": 10,
        "channel-color drift": 10,
        "shape-and-contrast drift": 8,
        "contrast-or-depth drift": 8,
        "luma-or-contrast drift": 8,
    }
    flag_penalty = min(48, sum(flag_penalties.get(flag, 6) for flag in flags))
    score = min(score, max(0, 100 - flag_penalty))
    metric_penalty = 0
    pixel_mismatch = _coerce_float(metrics.get("pixel_mismatch_ratio"))
    if pixel_mismatch is not None:
        if pixel_mismatch >= 0.28:
            metric_penalty += 12
        elif pixel_mismatch >= 0.16:
            metric_penalty += 6
    for key in ("grid_similarity", "band_similarity", "histogram_similarity"):
        value = _coerce_float(metrics.get(key))
        if value is not None and value < 0.78:
            metric_penalty += 4
    for key in ("pixel_luma_similarity", "pixel_rgb_similarity"):
        value = _coerce_float(metrics.get(key))
        if value is not None and value < 0.78:
            metric_penalty += 5
    return max(0, min(100, score - min(metric_penalty, 24)))


def _screenshot_visual_qa_from_report(report: dict[str, Any] | None) -> dict[str, Any]:
    detail = _find_report_check(report, "screenshot")
    if not isinstance(detail, dict):
        return {
            "available": False,
            "status": "missing",
            "ready": False,
            "grade": "unavailable",
            "score": 0,
            "reason": "No screenshot check was present in the verification report.",
        }
    detail_payload = detail.get("details") if isinstance(detail.get("details"), dict) else {}
    metrics = detail_payload.get("metrics") if isinstance(detail_payload.get("metrics"), dict) else {}
    drift_flags = detail_payload.get("drift_flags") if isinstance(detail_payload.get("drift_flags"), list) else []
    if not drift_flags and isinstance(detail.get("drift_flags"), list):
        drift_flags = detail.get("drift_flags") or []
    similarity = _coerce_float(detail.get("similarity"))
    status = str(detail.get("status") or "missing")
    if status != "present":
        return {
            "available": True,
            "status": status,
            "ready": False,
            "grade": "fail",
            "score": 0,
            "drift_flags": drift_flags,
            "metrics": _visual_qa_metric_summary(metrics),
            "focus_checks": ["screenshot"],
            "priority_findings": [
                {
                    "check": "visual QA screenshot",
                    "summary": "Screenshot visual QA failed because a comparable persisted PNG was missing.",
                    "focus": "recapture screenshot evidence",
                }
            ],
            "recommended_actions": [
                "Recapture reference and rendered screenshots before treating the rebuild as visually verified."
            ],
        }

    score = _visual_qa_score_from_metrics(similarity, metrics, drift_flags)
    blocking_flags = {
        "viewport-or-breakpoint drift",
        "composition drift",
        "vertical-flow drift",
        "layout-or-large-visual drift",
        "pixel-structure drift",
    }
    if score >= 88 and not any(flag in blocking_flags for flag in drift_flags):
        grade = "pass"
    elif score >= 72:
        grade = "watch"
    else:
        grade = "fail"
    focus_checks = _visual_qa_focus_for_flags(drift_flags)
    actions = _visual_qa_actions_for_flags(drift_flags if grade != "pass" else [])
    finding_summary = f"Screenshot visual QA {grade} ({score}/100)"
    if drift_flags:
        finding_summary += ": " + ", ".join(drift_flags[:5])
    return {
        "available": True,
        "status": status,
        "ready": grade == "pass",
        "grade": grade,
        "score": score,
        "similarity": round(similarity, 4) if similarity is not None else None,
        "drift_flags": [str(flag) for flag in drift_flags],
        "metrics": _visual_qa_metric_summary(metrics),
        "focus_checks": focus_checks,
        "priority_findings": [
            {
                "check": "visual QA screenshot",
                "summary": finding_summary,
                "focus": ", ".join(focus_checks[1:] or ["screenshot parity"]),
            }
        ]
        if grade != "pass"
        else [],
        "recommended_actions": actions if grade != "pass" else [],
    }


def _compact_visual_qa(visual_qa: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(visual_qa, dict):
        return {"available": False}
    return {
        "available": bool(visual_qa.get("available")),
        "status": visual_qa.get("status"),
        "ready": bool(visual_qa.get("ready")),
        "grade": visual_qa.get("grade"),
        "score": visual_qa.get("score"),
        "similarity": visual_qa.get("similarity"),
        "drift_flags": visual_qa.get("drift_flags") or [],
        "focus_checks": visual_qa.get("focus_checks") or [],
        "metrics": visual_qa.get("metrics") or {},
    }


def _combined_visual_qa(
    root_visual_qa: dict[str, Any] | None,
    breakpoint_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    qas: list[dict[str, Any]] = []
    if isinstance(root_visual_qa, dict) and root_visual_qa.get("available"):
        qas.append(root_visual_qa)
    for report in breakpoint_reports:
        if not isinstance(report, dict):
            continue
        qa = report.get("visual_qa")
        if isinstance(qa, dict) and qa.get("available"):
            qas.append(qa)
    if not qas:
        return {"available": False}
    grade_rank = {"fail": 3, "watch": 2, "pass": 1, "unavailable": 0}
    worst = max(qas, key=lambda qa: (grade_rank.get(str(qa.get("grade")), 0), -int(qa.get("score") or 0)))
    focus_checks: list[str] = []
    drift_flags: list[str] = []
    priority_findings: list[Any] = []
    recommended_actions: list[str] = []
    for qa in qas:
        for item in qa.get("focus_checks") or []:
            if item and item not in focus_checks:
                focus_checks.append(str(item))
        for item in qa.get("drift_flags") or []:
            if item and item not in drift_flags:
                drift_flags.append(str(item))
        for item in qa.get("priority_findings") or []:
            if item and item not in priority_findings:
                priority_findings.append(item)
        for item in qa.get("recommended_actions") or []:
            if item and str(item) not in recommended_actions:
                recommended_actions.append(str(item))
    return {
        "available": True,
        "ready": all(bool(qa.get("ready")) for qa in qas),
        "grade": worst.get("grade"),
        "score": min(int(qa.get("score") or 0) for qa in qas),
        "root": _compact_visual_qa(root_visual_qa),
        "breakpoints": [
            {
                "name": report.get("name"),
                **_compact_visual_qa(report.get("visual_qa")),
            }
            for report in breakpoint_reports
            if isinstance(report, dict) and isinstance(report.get("visual_qa"), dict)
        ],
        "drift_flags": drift_flags[:10],
        "focus_checks": focus_checks[:8],
        "priority_findings": priority_findings[:6],
        "recommended_actions": recommended_actions[:6],
    }


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
    preferred_renderer = rebuild_artifacts.get("preferred_renderer", {})
    if not isinstance(preferred_renderer, dict):
        preferred_renderer = {}
    preferred_name = str(preferred_renderer.get("name") or "").strip()
    starter_html_path = rebuild_artifacts.get("starter.html")
    if starter_html_path:
        candidates.append(
            {
                "name": "starter",
                "entrypoint": starter_html_path,
                "kind": "static",
                "artifact_paths": [starter_html_path],
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
                "artifact_paths": [app_preview_path],
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
                "artifact_paths": [
                    rebuild_artifacts[key]
                    for key in (
                        "next-app/app/layout.tsx",
                        "next-app/app/page.tsx",
                        "next-app/app/globals.css",
                        "next-app/components/BoundedReferencePage.tsx",
                        "next-app/components/reference-data.ts",
                    )
                    if rebuild_artifacts.get(key)
                ],
                "note": "Booted Next runtime using the generated next-app scaffold for higher-fidelity verification.",
            }
        )
    def candidate_freshness(candidate: dict[str, str]) -> float:
        paths = [Path(path) for path in candidate.get("artifact_paths", []) if path]
        mtimes = []
        for path in paths:
            try:
                mtimes.append(path.stat().st_mtime)
            except OSError:
                continue
        return max(mtimes) if mtimes else 0.0

    priority = {"next-runtime-app": 3, "role-inferred-app": 2, "starter": 1}
    candidates.sort(
        key=lambda candidate: (
            1 if candidate.get("name") == preferred_name else 0,
            candidate_freshness(candidate),
            priority.get(candidate.get("name"), 0),
        ),
        reverse=True,
    )
    return candidates


def _comparison_score(report: dict[str, Any]) -> int:
    summary = report.get("comparison_summary", {}) if isinstance(report, dict) else {}
    try:
        return int(summary.get("score") or 0)
    except (TypeError, ValueError):
        return 0


def _report_score(report: dict[str, Any] | None) -> int:
    if not isinstance(report, dict):
        return 0
    for value in (report.get("score"), (report.get("comparison_summary") or {}).get("score")):
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            continue
    return 0


def _breakpoint_ready_count(self_verify: dict[str, Any] | None) -> int:
    if not isinstance(self_verify, dict):
        return 0
    reports = ((self_verify.get("breakpoints") or {}).get("reports") or [])
    return sum(1 for report in reports if isinstance(report, dict) and report.get("available") and report.get("ready_for_exact_clone"))


def _renderer_summary(renderer: dict[str, Any] | None) -> dict[str, Any]:
    renderer = renderer or {}
    root_report = renderer.get("root_report") if isinstance(renderer, dict) else {}
    breakpoints = renderer.get("breakpoints") if isinstance(renderer, dict) else {}
    reports = (breakpoints or {}).get("reports") or []
    artifact_quality = renderer.get("artifact_quality") if isinstance(renderer, dict) else {}
    if not isinstance(artifact_quality, dict):
        artifact_quality = {}
    visual_qa = renderer.get("visual_qa") if isinstance(renderer, dict) else {}
    if not isinstance(visual_qa, dict):
        visual_qa = {}
    return {
        "name": renderer.get("name"),
        "kind": renderer.get("kind"),
        "entrypoint": renderer.get("entrypoint"),
        "preview_url": renderer.get("preview_url"),
        "score": renderer.get("score"),
        "ready_for_exact_clone": renderer.get("ready_for_exact_clone"),
        "report_path": (root_report or {}).get("report_path"),
        "rendered_capture_manifest": renderer.get("rendered_capture_manifest"),
        "breakpoint_count": (breakpoints or {}).get("compared"),
        "breakpoint_ready_count": sum(1 for report in reports if isinstance(report, dict) and report.get("available") and report.get("ready_for_exact_clone")),
        "artifact_quality_ready": artifact_quality.get("ready"),
        "artifact_quality_missing": artifact_quality.get("missing_required") or [],
        "visual_qa_ready": visual_qa.get("ready"),
        "visual_qa_grade": visual_qa.get("grade"),
        "visual_qa_score": visual_qa.get("score"),
        "visual_qa_drift_flags": visual_qa.get("drift_flags") or [],
    }


def _self_verify_summary(self_verify: dict[str, Any] | None) -> dict[str, Any]:
    self_verify = self_verify or {}
    root_report = self_verify.get("root_report", {}) if isinstance(self_verify, dict) else {}
    preferred_renderer = self_verify.get("preferred_renderer", {}) if isinstance(self_verify, dict) else {}
    renderers = self_verify.get("renderers", []) if isinstance(self_verify, dict) else []
    breakpoint_reports = ((self_verify.get("breakpoints") or {}).get("reports") or [])
    renderer_scores = [
        int(item.get("score") or 0)
        for item in renderers
        if isinstance(item, dict)
    ]
    root_score = _report_score(root_report)
    preferred_score = 0
    try:
        preferred_score = int(preferred_renderer.get("score") or 0)
    except (TypeError, ValueError):
        preferred_score = 0
    breakpoint_scores = [
        int(report.get("score") or 0)
        for report in breakpoint_reports
        if isinstance(report, dict) and report.get("available")
    ]
    breakpoint_ready_count = _breakpoint_ready_count(self_verify)
    available_breakpoints = [
        report
        for report in breakpoint_reports
        if isinstance(report, dict) and report.get("available")
    ]
    score = max([root_score, preferred_score, *renderer_scores]) if renderer_scores or preferred_score or root_score else 0
    if breakpoint_scores:
        score = int(round((score * 0.8) + ((sum(breakpoint_scores) / len(breakpoint_scores)) * 0.2)))
    score = max(score, root_score)
    preferred_artifact_quality = preferred_renderer.get("artifact_quality")
    if not isinstance(preferred_artifact_quality, dict):
        preferred_artifact_quality = {}
    preferred_visual_qa = preferred_renderer.get("visual_qa")
    if not isinstance(preferred_visual_qa, dict):
        preferred_visual_qa = {}
    visual_qa_available = bool(preferred_visual_qa.get("available"))
    visual_qa_score = preferred_visual_qa.get("score")
    try:
        screen_clone_score = int(visual_qa_score if visual_qa_available else score)
    except (TypeError, ValueError):
        screen_clone_score = score
    return {
        "score": score,
        "screen_clone_score": screen_clone_score,
        "screen_clone_ready": bool(preferred_visual_qa.get("ready")) if visual_qa_available else bool(self_verify.get("overall_ready_for_exact_clone")),
        "root_score": root_score,
        "preferred_renderer_score": preferred_score,
        "preferred_visual_qa_score": preferred_visual_qa.get("score") or 0,
        "preferred_visual_qa_grade": preferred_visual_qa.get("grade"),
        "renderer_count": len(renderers),
        "breakpoint_count": len(available_breakpoints),
        "breakpoint_ready_count": breakpoint_ready_count,
        "breakpoint_score_average": round(sum(breakpoint_scores) / len(breakpoint_scores), 2) if breakpoint_scores else 0,
        "preferred_renderer": {
            "name": preferred_renderer.get("name"),
            "kind": preferred_renderer.get("kind"),
            "score": preferred_renderer.get("score"),
            "ready_for_exact_clone": preferred_renderer.get("ready_for_exact_clone"),
            "report_path": preferred_renderer.get("report_path"),
            "artifact_quality_ready": preferred_artifact_quality.get("ready"),
            "visual_qa_ready": preferred_visual_qa.get("ready"),
            "visual_qa_grade": preferred_visual_qa.get("grade"),
            "visual_qa_score": preferred_visual_qa.get("score"),
        },
        "renderers": [_renderer_summary(item) for item in renderers if isinstance(item, dict)],
        "root_ready_for_exact_clone": bool((root_report or {}).get("ready_for_exact_clone")),
    }


def _self_verify_rank(self_verify: dict[str, Any] | None) -> tuple[int, int, int, int, int, int]:
    summary = _self_verify_summary(self_verify)
    preferred = summary.get("preferred_renderer", {}) if isinstance(summary, dict) else {}
    return (
        1 if bool((self_verify or {}).get("overall_ready_for_exact_clone")) else 0,
        1 if str(preferred.get("kind") or "") == "next-runtime" else 0,
        1 if bool(preferred.get("ready_for_exact_clone")) else 0,
        int(summary.get("breakpoint_ready_count") or 0),
        int(summary.get("preferred_renderer_score") or 0),
        int(summary.get("score") or 0),
    )


def _renderer_ready(report: dict[str, Any]) -> bool:
    return bool(((report.get("downstream_guidance") or {}).get("ready_for_exact_clone")))


def _build_repair_plan(
    preferred_renderer: dict[str, Any] | None,
    breakpoint_reports: list[dict[str, Any]],
    artifact_quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    renderer = preferred_renderer or {}
    root_report = renderer.get("report", {}) if isinstance(renderer, dict) else {}
    guidance = root_report.get("downstream_guidance", {}) if isinstance(root_report, dict) else {}
    comparison = root_report.get("comparison_summary", {}) if isinstance(root_report, dict) else {}
    renderer_name = renderer.get("name") or "starter"
    renderer_score = renderer.get("score")
    root_visual_qa = renderer.get("visual_qa") if isinstance(renderer, dict) else {}
    if not isinstance(root_visual_qa, dict):
        root_visual_qa = _screenshot_visual_qa_from_report(root_report)
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
    visual_qa = _combined_visual_qa(root_visual_qa, breakpoint_reports)
    focus_checks = [item.get("name") for item in (comparison.get("weakest_checks") or []) if isinstance(item, dict)]
    normalized_focus_checks = [str(item) for item in focus_checks if item]
    score = 0
    try:
        score = int(renderer_score or 0)
    except (TypeError, ValueError):
        score = 0
    priority_findings = [item for item in (guidance.get("priority_findings") or []) if item]
    recommended_actions = [str(item) for item in (guidance.get("recommended_actions") or []) if item]
    if visual_qa.get("available") and not visual_qa.get("ready"):
        for focus in visual_qa.get("focus_checks") or []:
            if focus and focus not in normalized_focus_checks:
                normalized_focus_checks.append(str(focus))
        for finding in reversed(visual_qa.get("priority_findings") or []):
            if finding and finding not in priority_findings:
                priority_findings.insert(0, finding)
        for action in reversed(visual_qa.get("recommended_actions") or []):
            if action and str(action) not in recommended_actions:
                recommended_actions.insert(0, str(action))
    artifact_quality = artifact_quality if isinstance(artifact_quality, dict) else {}
    missing_quality = [
        str(item)
        for item in (artifact_quality.get("missing_required") or [])
        if item
    ]
    if missing_quality:
        priority_findings.insert(
            0,
            {
                "check": "capture-backed renderer anchors",
                "summary": "Generated rebuild artifacts are missing required capture anchors: "
                + ", ".join(missing_quality[:6]),
                "focus": "preserve visual stage references, overlay geometry, and shell region topology",
            },
        )
        quality_focus_map = {
            "visual-stage-reference": "screenshot",
            "visual-layers-model": "stage geometry",
            "visual-stage-tsx": "stage geometry",
            "visual-layers-tsx": "overlay chrome",
            "visual-stage-preview": "screenshot",
            "visual-layers-preview": "overlay chrome",
            "shell-regions-model": "shell regions",
            "shell-regions-tsx": "shell regions",
            "shell-regions-preview": "shell regions",
        }
        for item in missing_quality:
            focus = quality_focus_map.get(item)
            if focus and focus not in normalized_focus_checks:
                normalized_focus_checks.append(focus)
        quality_actions = []
        if any(str(item).startswith("visual-stage") for item in missing_quality):
            quality_actions.append("Keep the captured screenshot as the bounded-stage-reference image before adding synthesized overlays.")
        if any("visual-layers" in str(item) for item in missing_quality):
            quality_actions.append("Render captured visualLayers geometry as bounded-visual-layer overlays in both Next and static previews.")
        if any("shell-regions" in str(item) for item in missing_quality):
            quality_actions.append("Render captured shellRegions geometry and bounded-shell-panel-grid so app-shell topology stays anchored to the source capture.")
        for action in quality_actions:
            if action not in recommended_actions:
                recommended_actions.insert(0, action)
    priority_text = [
        item.get("summary") or item.get("focus") or item.get("check")
        if isinstance(item, dict)
        else str(item)
        for item in priority_findings
    ]
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
    if any("interaction" in str(item).lower() for item in priority_text + recommended_actions):
        for name in ("interaction states", "interaction trace"):
            if name not in normalized_focus_checks:
                normalized_focus_checks.append(name)
    return {
        "available": True,
        "status": "generated",
        "target_renderer": renderer_name,
        "score": renderer_score,
        "focus_checks": normalized_focus_checks[:6],
        "artifact_quality": artifact_quality,
        "visual_qa": visual_qa,
        "priority_findings": priority_findings[:6],
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
                *[f"- {item}" for item in priority_text[:6]],
                "Recommended actions:",
                *[f"- {item}" for item in recommended_actions[:6]],
                "Visual QA:",
                *(
                    [
                        f"- grade {visual_qa.get('grade')} / score {visual_qa.get('score')}",
                        f"- drift flags: {', '.join((visual_qa.get('drift_flags') or [])[:6]) or 'none'}",
                    ]
                    if visual_qa.get("available")
                    else ["- unavailable"]
                ),
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

    static_roots = [
        Path(candidate["entrypoint"]).expanduser().resolve().parent
        for candidate in renderer_candidates
        if candidate.get("kind") == "static"
    ]
    if static_roots:
        rebuild_root = Path(os.path.commonpath([str(path) for path in static_roots]))
    else:
        rebuild_root = Path(renderer_candidates[0]["entrypoint"]).expanduser().resolve().parent
    primary_request = reference_bundle.get("session_request", {}) if isinstance(reference_bundle, dict) else {}
    breakpoint_summary = reference_bundle.get("breakpoints", {}) if isinstance(reference_bundle, dict) else {}
    breakpoint_profiles = breakpoint_summary.get("requested_profiles") if isinstance(breakpoint_summary, dict) else []
    self_verify_dir = output_dir / "reproduction" / Path(stage_path)
    persisted: dict[str, Any] = {"renderers": {}}
    renderer_results: list[dict[str, Any]] = []
    runtime_cache_root = output_dir / "reproduction" / "_next-runtime-cache"
    artifact_quality = _artifact_quality_signals(rebuild_artifacts)
    persisted["artifact_quality"] = _persist_report(self_verify_dir / "artifact-quality.json", artifact_quality)
    preferred_renderer_name = ""
    preferred_renderer = rebuild_artifacts.get("preferred_renderer", {}) if isinstance(rebuild_artifacts, dict) else {}
    if isinstance(preferred_renderer, dict):
        preferred_renderer_name = str(preferred_renderer.get("name") or "").strip()

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
                    "artifact_quality": artifact_quality,
                    "visual_qa": {"available": False, "reason": runtime_error or "Renderer did not produce a capture."},
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
            visual_qa = _screenshot_visual_qa_from_report(root_report)
            visual_qa_path = _persist_report(renderer_dir / "visual-qa.json", visual_qa)

            reference_variants = _breakpoint_variant_map(reference_bundle)
            rendered_variants = _breakpoint_variant_map(rendered_bundle)
            breakpoint_reports: list[dict[str, Any]] = []
            if reference_variants:
                for variant_name in sorted(reference_variants):
                    if variant_name not in rendered_variants:
                        breakpoint_reports.append(
                            {
                                "name": variant_name,
                                "available": False,
                                "reason": "Rendered breakpoint variant was missing.",
                                "ready_for_exact_clone": False,
                            }
                        )
                        continue
                    reference_variant_bundle = _load_json_file(reference_variants[variant_name].get("capture_manifest"))
                    rendered_variant_bundle = _load_json_file(rendered_variants[variant_name].get("capture_manifest"))
                    if not reference_variant_bundle or not rendered_variant_bundle:
                        breakpoint_reports.append(
                            {
                                "name": variant_name,
                                "available": False,
                                "reason": "Variant capture bundle was missing.",
                                "ready_for_exact_clone": False,
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
                    breakpoint_visual_qa = _screenshot_visual_qa_from_report(report)
                    breakpoint_visual_qa_path = _persist_report(
                        renderer_dir / "breakpoints" / f"{variant_name}-visual-qa.json",
                        breakpoint_visual_qa,
                    )
                    breakpoint_reports.append(
                        {
                            "name": variant_name,
                            "available": True,
                            "verdict": report.get("verdict"),
                            "score": (report.get("comparison_summary") or {}).get("score"),
                            "ready_for_exact_clone": (report.get("downstream_guidance") or {}).get("ready_for_exact_clone"),
                            "focus": ((report.get("downstream_guidance") or {}).get("priority_findings") or [None])[0],
                            "report_path": report_path,
                            "visual_qa": breakpoint_visual_qa,
                            "visual_qa_path": breakpoint_visual_qa_path,
                        }
                    )
            overall_ready = _renderer_ready(root_report)
            quality_required = bool(artifact_quality.get("required"))
            quality_ready = bool(artifact_quality.get("ready"))
            if quality_required and not quality_ready:
                overall_ready = False
            if visual_qa.get("available") and not visual_qa.get("ready"):
                overall_ready = False
            if breakpoint_reports:
                overall_ready = overall_ready and all(
                    bool(report.get("ready_for_exact_clone"))
                    and not (
                        isinstance(report.get("visual_qa"), dict)
                        and report["visual_qa"].get("available")
                        and not report["visual_qa"].get("ready")
                    )
                    for report in breakpoint_reports
                )
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
                "artifact_quality": artifact_quality,
                "visual_qa": visual_qa,
                "visual_qa_path": visual_qa_path,
                "artifact_quality_ready": quality_ready,
                "artifact_quality_required": quality_required,
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
                "visual_qa": visual_qa_path,
                "breakpoint_reports": {
                    report["name"]: report["report_path"]
                    for report in breakpoint_reports
                    if report.get("available") and report.get("report_path")
                },
                "breakpoint_visual_qa": {
                    report["name"]: report["visual_qa_path"]
                    for report in breakpoint_reports
                    if report.get("available") and report.get("visual_qa_path")
                },
            }
            if renderer_result.get("ready_for_exact_clone"):
                if not preferred_renderer_name or name == preferred_renderer_name or renderer is renderer_candidates[0]:
                    break

    preferred_renderer = max(
        renderer_results,
        key=lambda item: (
            1 if item.get("ready_for_exact_clone") else 0,
            int(item.get("score") or 0),
        ),
    ) if renderer_results else None
    preferred_breakpoints = ((preferred_renderer or {}).get("breakpoints") or {}).get("reports") or []
    overall_ready = any(bool(item.get("ready_for_exact_clone")) for item in renderer_results)
    repair_plan = _build_repair_plan(preferred_renderer, preferred_breakpoints, artifact_quality)
    persisted["repair_plan"] = _persist_report(self_verify_dir / "repair-plan.json", repair_plan)
    repair_prompt_path = self_verify_dir / "repair-prompt.txt"
    repair_prompt_path.write_text(str(repair_plan.get("prompt") or "").rstrip() + "\n")
    persisted["repair_prompt"] = str(repair_prompt_path)
    renderer_summaries = [_renderer_summary(item) for item in renderer_results]
    preferred_renderer_summary = _renderer_summary(preferred_renderer)
    self_verify_summary = _self_verify_summary(
        {
            "root_report": (preferred_renderer or {}).get("root_report"),
            "preferred_renderer": preferred_renderer or {},
            "renderers": renderer_results,
            "breakpoints": (preferred_renderer or {}).get("breakpoints") or {"compared": 0, "reports": []},
            "overall_ready_for_exact_clone": overall_ready,
        }
    )

    result = {
        "available": True,
        "status": "completed",
        "renderer_count": len(renderer_results),
        "renderer_summaries": renderer_summaries,
        "preferred_renderer": {
            "name": (preferred_renderer or {}).get("name"),
            "kind": (preferred_renderer or {}).get("kind"),
            "entrypoint": (preferred_renderer or {}).get("entrypoint"),
            "score": (preferred_renderer or {}).get("score"),
            "ready_for_exact_clone": (preferred_renderer or {}).get("ready_for_exact_clone"),
            "report_path": (((preferred_renderer or {}).get("root_report") or {}).get("report_path")),
            "artifact_quality_ready": ((preferred_renderer or {}).get("artifact_quality") or {}).get("ready"),
            "visual_qa": _compact_visual_qa((preferred_renderer or {}).get("visual_qa")),
        },
        "preferred_renderer_summary": preferred_renderer_summary,
        "renderers": [
            {
                "name": item.get("name"),
                "kind": item.get("kind"),
                "score": item.get("score"),
                "ready_for_exact_clone": item.get("ready_for_exact_clone"),
                "entrypoint": item.get("entrypoint"),
                "report_path": ((item.get("root_report") or {}).get("report_path")),
                "artifact_quality_ready": ((item.get("artifact_quality") or {}).get("ready")),
                "artifact_quality_missing": ((item.get("artifact_quality") or {}).get("missing_required") or []),
                "visual_qa": _compact_visual_qa(item.get("visual_qa")),
            }
            for item in renderer_results
        ],
        "artifact_quality": artifact_quality,
        "visual_qa": repair_plan.get("visual_qa"),
        "rendered_capture_manifest": (preferred_renderer or {}).get("rendered_capture_manifest"),
        "root_report": (preferred_renderer or {}).get("root_report"),
        "breakpoints": (preferred_renderer or {}).get("breakpoints") or {"compared": 0, "reports": []},
        "overall_ready_for_exact_clone": overall_ready,
        "score": self_verify_summary.get("score"),
        "screen_clone_score": self_verify_summary.get("screen_clone_score"),
        "screen_clone_ready": self_verify_summary.get("screen_clone_ready"),
        "breakpoint_ready_count": self_verify_summary.get("breakpoint_ready_count"),
        "breakpoint_score_average": self_verify_summary.get("breakpoint_score_average"),
        "repair_plan": {
            "target_renderer": repair_plan.get("target_renderer"),
            "score": repair_plan.get("score"),
            "focus_checks": repair_plan.get("focus_checks"),
            "breakpoint_focus": repair_plan.get("breakpoint_focus"),
            "priority_findings": repair_plan.get("priority_findings"),
            "recommended_actions": repair_plan.get("recommended_actions"),
            "artifact_quality": repair_plan.get("artifact_quality"),
            "visual_qa": repair_plan.get("visual_qa"),
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
