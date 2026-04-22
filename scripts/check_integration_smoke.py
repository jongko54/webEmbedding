#!/usr/bin/env python3
"""Run a URL-only clone and release-install smoke test."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def run(command: list[str], cwd: Path, *, output: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if output is None:
        completed = subprocess.run(command, cwd=cwd, env=env, text=True, check=False)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w") as handle:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                text=True,
                stdout=handle,
                stderr=subprocess.PIPE,
                check=False,
            )
    if completed.returncode != 0:
        command_text = " ".join(command)
        stderr = completed.stderr or ""
        raise RuntimeError(f"{command_text} failed with {completed.returncode}\n{stderr}")
    return completed


def assert_release_archive_clean(archive_path: Path) -> None:
    with tarfile.open(archive_path, "r:gz") as archive:
        bad_members = [
            member.name
            for member in archive.getmembers()
            if "__pycache__" in Path(member.name).parts or member.name.endswith(".pyc")
        ]
    if bad_members:
        raise AssertionError(f"release archive contains generated Python cache files: {bad_members[:5]}")


def assert_dist_contents(dist_dir: Path) -> None:
    expected = {
        "source-first-clone-bundle.tar.gz",
        "install.py",
        "install.sh",
        "SHA256SUMS",
    }
    actual = {path.name for path in dist_dir.iterdir() if path.is_file()}
    if actual != expected:
        raise AssertionError(f"dist contents mismatch: expected {sorted(expected)}, got {sorted(actual)}")


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise AssertionError(f"{path} did not contain a JSON object")
    return payload


def assert_exact_ready_semantics(root: Path) -> None:
    sys.path.insert(0, str(root / "bundle" / "source-first-clone" / "mcp"))
    from source_first_clone.orchestration import _exact_reuse_ready  # noqa: PLC0415

    if _exact_reuse_ready({"verification": {"status": "captured-candidate"}}):
        raise AssertionError("exact_ready should stay false when verification lacks ready flags")
    if _exact_reuse_ready(
        {"verification": {"ready_for_exact_reuse": False, "ready_for_exact_clone": False}}
    ):
        raise AssertionError("exact_ready should stay false when verification reports not ready")
    if _exact_reuse_ready({"snippets": {"html": "<iframe></iframe>"}}):
        raise AssertionError("exact_ready should stay false when exact_reuse lacks verification")
    if not _exact_reuse_ready({"verification": {"ready_for_exact_reuse": True}}):
        raise AssertionError("exact_ready should follow ready_for_exact_reuse=true")


def assert_site_profile_routing_semantics() -> None:
    from source_first_clone.planning import plan_reproduction_path  # noqa: PLC0415
    from source_first_clone.site_profile import classify_site_profile  # noqa: PLC0415

    def profile(
        html: str,
        *,
        frame_policy: dict[str, Any] | None = None,
        platform_adapter: dict[str, Any] | None = None,
        candidate_urls: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return classify_site_profile(
            final_url="https://fixture.example/",
            html=html,
            headers={},
            frame_policy=frame_policy or {"embeddable": True},
            platform_adapter=platform_adapter or {"platform": "generic"},
            candidate_urls=candidate_urls or [],
        )

    def assert_contains_all(name: str, values: Any, expected: set[str], label: str) -> None:
        if not isinstance(values, list):
            raise AssertionError(f"{name}: {label} should be a list, got {type(values).__name__}")
        missing = expected.difference(str(item) for item in values)
        if missing:
            raise AssertionError(f"{name}: missing {label} values {sorted(missing)}")

    cases = [
        {
            "name": "direct iframe exact candidate",
            "html": "<main><h1>Static document</h1><p>Stable source.</p></main>",
            "candidates": [{"kind": "direct-iframe", "url": "https://fixture.example/"}],
            "surface": "static-document",
            "mode": "embed",
            "route": {
                "acquisition_profile": "static-first",
                "renderer_route": "exact-reuse",
                "renderer_family": "document-next-app",
            },
            "depths": {"dom", "computed-styles", "interactions"},
        },
        {
            "name": "app shell",
            "html": "<div id='root'></div><script src='/react-dom.js'></script>",
            "surface": "js-app-shell-surface",
            "mode": "rebuild",
            "route": {
                "acquisition_profile": "browser-deep-capture",
                "renderer_route": "runtime-first-bounded-rebuild",
                "renderer_family": "app-shell-dashboard-next-app",
            },
            "depths": {"runtime-html", "network"},
            "plan_required": {"shell topology / panel summary", "breakpoint variants"},
            "plan_stage": "renderer",
        },
        {
            "name": "auth app shell",
            "html": "<input type='password'><script src='/react-dom.js'></script>",
            "surface": "authenticated-app-surface",
            "mode": "rebuild",
            "route": {
                "acquisition_profile": "session-aware-browser-capture",
                "renderer_route": "runtime-first-bounded-rebuild",
                "renderer_family": "app-shell-dashboard-next-app",
            },
            "depths": {"runtime-html", "network", "session-state"},
            "plan_required": {"shell topology / panel summary", "session context or storage state"},
            "plan_stage": "renderer",
        },
        {
            "name": "visual canvas",
            "html": "<canvas></canvas><script>three.js webgl</script>",
            "surface": "canvas-or-webgl-surface",
            "mode": "rebuild",
            "route": {
                "acquisition_profile": "visual-runtime-capture",
                "renderer_route": "visual-fallback-rebuild",
                "renderer_family": "visual-fallback-next-app",
            },
            "depths": {"canvas-surface"},
            "plan_required": {"viewport screenshot set", "network manifest"},
            "visual_fallback_model": "full-viewport-stage-with-overlay-chrome",
        },
        {
            "name": "decorative app canvas",
            "html": "<canvas></canvas><div id='root'></div><script src='/react-dom.js'></script>",
            "surface": "js-app-shell-surface",
            "mode": "rebuild",
            "route": {
                "acquisition_profile": "browser-deep-capture",
                "renderer_route": "runtime-first-bounded-rebuild",
                "renderer_family": "app-shell-dashboard-next-app",
            },
            "depths": {"runtime-html", "network", "canvas-surface"},
            "signals": {"canvas_detected": True, "canvas_dominant": False},
            "plan_required": {"shell topology / panel summary"},
            "plan_stage": "renderer",
        },
        {
            "name": "multi-frame",
            "html": "<iframe src='/a'></iframe><iframe src='/b'></iframe>",
            "surface": "multi-frame-document-surface",
            "mode": "rebuild",
            "route": {
                "acquisition_profile": "frame-aware-capture",
                "renderer_route": "bounded-rebuild",
                "renderer_family": "frame-aware-document-next-app",
            },
            "depths": {"frame-documents"},
            "plan_required": {"frame document summaries", "network manifest"},
        },
    ]
    for case in cases:
        name = str(case["name"])
        candidates = case.get("candidates", [])
        site_profile = profile(str(case["html"]), candidate_urls=candidates)
        route_hints = site_profile.get("route_hints", {})
        expected_surface = case["surface"]
        if site_profile.get("primary_surface") != expected_surface:
            raise AssertionError(f"{name}: expected {expected_surface}, got {site_profile.get('primary_surface')}")
        expected_route = case["route"]
        for key, expected_value in expected_route.items():
            if route_hints.get(key) != expected_value:
                raise AssertionError(f"{name}: expected {key}={expected_value}, got {route_hints.get(key)}")
        assert_contains_all(name, route_hints.get("critical_depths"), case.get("depths", set()), "critical depths")

        for key, expected_value in case.get("signals", {}).items():
            signals = site_profile.get("signals", {}) if isinstance(site_profile.get("signals"), dict) else {}
            if signals.get(key) is not expected_value:
                raise AssertionError(f"{name}: expected signal {key}={expected_value}, got {signals.get(key)}")

        plan = plan_reproduction_path(site_profile=site_profile, candidates=candidates)
        if plan.get("mode") != case["mode"]:
            raise AssertionError(f"{name}: expected planner mode {case['mode']}, got {plan.get('mode')}")
        plan_hints = plan.get("route_hints", {})
        for key, expected_value in expected_route.items():
            if plan_hints.get(key) != expected_value:
                raise AssertionError(f"{name}: planner {key} drifted to {plan_hints.get(key)}")
        assert_contains_all(name, plan_hints.get("critical_depths"), case.get("depths", set()), "planner critical depths")
        assert_contains_all(name, plan.get("required_artifacts"), case.get("plan_required", set()), "required artifacts")

        expected_stage = case.get("plan_stage")
        if expected_stage:
            plan_steps = plan.get("plan", []) if isinstance(plan.get("plan"), list) else []
            if not any(step.get("stage") == expected_stage for step in plan_steps if isinstance(step, dict)):
                raise AssertionError(f"{name}: planner did not include {expected_stage!r} stage")

        expected_visual_model = case.get("visual_fallback_model")
        if expected_visual_model:
            visual_fallback = plan.get("visual_fallback")
            if not isinstance(visual_fallback, dict):
                raise AssertionError(f"{name}: planner did not expose visual fallback metadata")
            if visual_fallback.get("rendering_model") != expected_visual_model:
                raise AssertionError(
                    f"{name}: expected visual model {expected_visual_model}, got {visual_fallback.get('rendering_model')}"
                )


def assert_rebuild_scaffold_visual_semantics() -> None:
    from source_first_clone.rebuild_scaffold import build_rebuild_scaffold, persist_rebuild_scaffold  # noqa: PLC0415
    from source_first_clone.repair_scaffold import build_repair_scaffold  # noqa: PLC0415
    from source_first_clone.self_verify import _artifact_quality_signals, _build_repair_plan  # noqa: PLC0415
    from source_first_clone.site_profile import classify_site_profile  # noqa: PLC0415

    tiny_png = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )

    def site_profile(html: str) -> dict[str, Any]:
        return classify_site_profile(
            final_url="https://fixture.example/app",
            html=html,
            headers={},
            frame_policy={"embeddable": False},
            platform_adapter={"platform": "generic"},
            candidate_urls=[],
        )

    style_entries = [
        {
            "tag": "header",
            "text": "Project navigation",
            "rect": {"x": 0, "y": 0, "width": 960, "height": 72},
            "styles": {
                "display": "flex",
                "backgroundColor": "rgb(18, 24, 38)",
                "color": "rgb(248, 250, 252)",
                "fontSize": "14px",
                "fontFamily": "Inter, sans-serif",
                "borderRadius": "0px",
            },
        },
        {
            "tag": "aside",
            "text": "Layers Assets Settings",
            "rect": {"x": 0, "y": 72, "width": 220, "height": 568},
            "styles": {
                "display": "block",
                "backgroundColor": "rgb(15, 23, 42)",
                "color": "rgb(226, 232, 240)",
                "fontSize": "13px",
                "fontFamily": "Inter, sans-serif",
            },
        },
        {
            "tag": "main",
            "text": "Canvas workspace with selected card and chart preview",
            "rect": {"x": 220, "y": 72, "width": 740, "height": 568},
            "styles": {
                "display": "grid",
                "backgroundColor": "rgb(241, 245, 249)",
                "color": "rgb(15, 23, 42)",
                "fontSize": "16px",
                "fontFamily": "Inter, sans-serif",
                "borderRadius": "12px",
                "boxShadow": "0 20px 48px rgba(15, 23, 42, 0.18)",
            },
        },
        {
            "tag": "canvas",
            "text": "",
            "rect": {"x": 280, "y": 128, "width": 560, "height": 360},
            "styles": {
                "display": "block",
                "backgroundColor": "rgb(30, 41, 59)",
                "borderRadius": "10px",
            },
        },
    ]

    def capture_bundle(profile: dict[str, Any]) -> dict[str, Any]:
        return {
            "url": "https://fixture.example/app",
            "session_request": {"viewport_width": 960, "viewport_height": 640},
            "static": {
                "title": "Fixture App",
                "final_url": "https://fixture.example/app",
                "frame_policy": {"embeddable": False},
                "platform": "generic",
                "platform_adapter": {"platform": "generic"},
                "candidate_urls": [],
                "site_profile": profile,
            },
            "policy": {"mode": "rebuild"},
            "runtime": {
                "available": True,
                "captures": {
                    "screenshot": {
                        "available": True,
                        "mimeType": "image/png",
                        "byteLength": 68,
                        "base64": tiny_png,
                    },
                    "styles": {"available": True, "content": style_entries},
                    "cssAnalysis": {
                        "available": True,
                        "content": {
                            "bodyComputedStyle": {
                                "backgroundColor": "rgb(241, 245, 249)",
                                "color": "rgb(15, 23, 42)",
                                "fontFamily": "Inter, sans-serif",
                            }
                        },
                    },
                    "interactions": {"available": True, "content": []},
                    "interactionTrace": {"available": True, "content": {"steps": []}},
                    "assets": {"available": True, "content": {"images": [], "scripts": []}},
                },
            },
        }

    canvas_profile = site_profile("<canvas></canvas><script>three.js webgl</script>")
    canvas_bundle = capture_bundle(canvas_profile)
    canvas_scaffold = build_rebuild_scaffold(canvas_bundle)
    canvas_artifacts = canvas_scaffold.get("artifacts", {})
    summary = canvas_artifacts["layout-summary.json"]
    app_model = canvas_artifacts["app-model.json"]
    tsx = canvas_artifacts["next-app/components/BoundedReferencePage.tsx"]
    preview = canvas_artifacts["app-preview.html"]
    visual_stage = summary.get("visualStage", {})
    reference_image = visual_stage.get("referenceImage", {}) if isinstance(visual_stage, dict) else {}
    if summary.get("renderer", {}).get("kind") != "visual-fallback-next-app":
        raise AssertionError("visual scaffold did not select visual-fallback-next-app")
    if not str(reference_image.get("src") or "").startswith("data:image/png;base64,"):
        raise AssertionError("visual scaffold did not carry screenshot data URL")
    if not app_model.get("visualStage") or not app_model.get("visualLayers"):
        raise AssertionError("visual scaffold did not expose visualStage/visualLayers in app model")
    if "bounded-stage-reference" not in tsx or "visualLayers.slice" not in tsx:
        raise AssertionError("visual scaffold TSX does not render screenshot-backed visual layers")
    if "bounded-stage-reference" not in preview or "bounded-visual-layer" not in preview:
        raise AssertionError("visual scaffold preview does not render screenshot-backed visual layers")
    temp_parent = repo_root() / ".tmp"
    temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="scaffold-quality-", dir=temp_parent) as temp_name:
        persisted_canvas = persist_rebuild_scaffold(Path(temp_name) / "canvas", canvas_scaffold)
        canvas_quality = _artifact_quality_signals(persisted_canvas)
        if not canvas_quality.get("ready") or canvas_quality.get("missing_required"):
            raise AssertionError(f"visual scaffold quality gate failed: {canvas_quality}")
        broken_canvas = dict(persisted_canvas)
        broken_canvas["next-app/components/BoundedReferencePage.tsx"] = str(Path(temp_name) / "missing.tsx")
        broken_quality = _artifact_quality_signals(broken_canvas)
        if broken_quality.get("ready") or "visual-stage-tsx" not in (broken_quality.get("missing_required") or []):
            raise AssertionError(f"visual scaffold quality gate did not catch missing TSX anchor: {broken_quality}")
        repair_plan = _build_repair_plan(
            {"name": "role-inferred-app", "score": 80, "report": {}},
            [],
            broken_quality,
        )
        if "stage geometry" not in (repair_plan.get("focus_checks") or []):
            raise AssertionError(f"repair plan did not promote missing visual stage geometry: {repair_plan}")
        if "bounded-stage-reference" not in str(repair_plan.get("prompt") or ""):
            raise AssertionError(f"repair prompt did not mention the screenshot stage anchor: {repair_plan}")
        repair_pass = build_repair_scaffold(
            capture_bundle=canvas_bundle,
            rebuild_artifacts=persisted_canvas,
            self_verify={
                "repair_plan": {
                    "target_renderer": "role-inferred-app",
                    "focus_checks": ["screenshot"],
                    "artifact_quality": canvas_quality,
                    "priority_findings": [],
                    "recommended_actions": [],
                }
            },
        )
        repaired_model = (repair_pass.get("artifacts") or {}).get("app-model.json")
        if not isinstance(repaired_model, dict):
            raise AssertionError(f"visual repair did not emit app model: {repair_pass}")
        presentation = repaired_model.get("presentation") if isinstance(repaired_model.get("presentation"), dict) else {}
        if presentation.get("variant") == "compact-center-stage":
            raise AssertionError("visual repair collapsed screenshot-backed stage into compact generic layout")
        if not repaired_model.get("visualStage") or not repaired_model.get("visualLayers"):
            raise AssertionError("visual repair dropped visualStage/visualLayers anchors")

    app_profile = site_profile("<div id='root'></div><script src='/react-dom.js'></script>")
    app_scaffold = build_rebuild_scaffold(capture_bundle(app_profile))
    app_artifacts = app_scaffold.get("artifacts", {})
    app_model = app_artifacts["app-model.json"]
    preview = app_artifacts["app-preview.html"]
    if app_artifacts["layout-summary.json"].get("renderer", {}).get("kind") != "app-shell-dashboard-next-app":
        raise AssertionError("app scaffold did not select app-shell-dashboard-next-app")
    if not app_model.get("shellRegions"):
        raise AssertionError("app scaffold did not derive shellRegions")
    if "bounded-shell-region" not in preview or "bounded-shell-panel-grid" not in preview:
        raise AssertionError("app scaffold preview did not render shell region geometry")
    with tempfile.TemporaryDirectory(prefix="scaffold-quality-", dir=temp_parent) as temp_name:
        persisted_app = persist_rebuild_scaffold(Path(temp_name) / "app", app_scaffold)
        app_quality = _artifact_quality_signals(persisted_app)
        if not app_quality.get("ready") or app_quality.get("missing_required"):
            raise AssertionError(f"app scaffold quality gate failed: {app_quality}")


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return


def assert_frame_shadow_parity_fixture(root: Path, temp_root: Path) -> None:
    fixture_dir = root / "fixtures" / "frame-shadow-parity"
    if not fixture_dir.exists():
        raise AssertionError(f"missing frame/shadow parity fixture: {fixture_dir}")

    from source_first_clone.capture_bundle import capture_reference_bundle  # noqa: PLC0415

    handler = partial(QuietHandler, directory=str(fixture_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/index.html"
        reference_dir = temp_root / "frame-shadow-reference"
        candidate_dir = temp_root / "frame-shadow-candidate"
        for output_dir in (reference_dir, candidate_dir):
            capture_reference_bundle(
                url=url,
                timeout_seconds=10,
                wait_seconds=1,
                include_runtime_trace=True,
                capture_html=True,
                capture_screenshot=False,
                viewport_width=900,
                viewport_height=700,
                output_dir=str(output_dir),
                exact_requested=False,
            )
        run(
            [
                sys.executable,
                "scripts/check_demo_root_interaction_parity.py",
                "--reference",
                str(reference_dir / "capture.json"),
                "--candidate",
                str(candidate_dir / "capture.json"),
            ],
            cwd=root,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def assert_clone_summary(payload: dict[str, Any], output_dir: Path) -> None:
    if payload.get("exact_ready") is not True:
        raise AssertionError("clone summary did not report exact_ready=true")
    if payload.get("coverage") != "exact-reuse":
        raise AssertionError(f"expected exact-reuse coverage, got {payload.get('coverage')!r}")
    if payload.get("next_action") != "embed":
        raise AssertionError(f"expected embed next_action, got {payload.get('next_action')!r}")
    exact_reuse = payload.get("exact_reuse")
    if not isinstance(exact_reuse, dict):
        raise AssertionError("clone summary is missing exact_reuse")
    verification = exact_reuse.get("verification")
    if not isinstance(verification, dict):
        raise AssertionError("clone summary is missing exact_reuse.verification")
    verification_ready = bool(
        verification.get("ready_for_exact_reuse")
        or verification.get("ready_for_exact_clone")
    )
    if payload.get("exact_ready") is not verification_ready:
        raise AssertionError("clone summary exact_ready did not match verification readiness")
    if verification.get("status") != "source-equivalent":
        raise AssertionError(f"expected source-equivalent exact reuse, got {verification.get('status')!r}")
    site_profile = payload.get("site_profile")
    if not isinstance(site_profile, dict):
        raise AssertionError("clone summary is missing site_profile")
    route_hints = site_profile.get("route_hints")
    if not isinstance(route_hints, dict):
        raise AssertionError("clone summary is missing site_profile.route_hints")
    expected_route = {
        "acquisition_profile": "static-first",
        "renderer_route": "exact-reuse",
        "renderer_family": "document-next-app",
    }
    for key, expected_value in expected_route.items():
        if route_hints.get(key) != expected_value:
            raise AssertionError(f"clone summary expected {key}={expected_value}, got {route_hints.get(key)}")
    critical_depths = route_hints.get("critical_depths")
    if not isinstance(critical_depths, list) or not {"dom", "computed-styles", "interactions"}.issubset(
        {str(item) for item in critical_depths}
    ):
        raise AssertionError(f"clone summary critical depths were incomplete: {critical_depths!r}")
    signals = site_profile.get("signals") if isinstance(site_profile.get("signals"), dict) else {}
    if signals.get("exact_candidate_present") is not True:
        raise AssertionError("clone summary did not preserve exact candidate routing signal")
    if "direct-iframe" not in (signals.get("exact_candidate_kinds") or []):
        raise AssertionError("clone summary did not report direct-iframe exact candidate")

    reproduction = payload.get("reproduction")
    if not isinstance(reproduction, dict):
        raise AssertionError("clone summary is missing reproduction")
    plan = reproduction.get("plan")
    if not isinstance(plan, dict):
        raise AssertionError("clone summary is missing reproduction.plan")
    plan_hints = plan.get("route_hints")
    if not isinstance(plan_hints, dict):
        raise AssertionError("clone summary is missing reproduction.plan.route_hints")
    for key, expected_value in expected_route.items():
        if plan_hints.get(key) != expected_value:
            raise AssertionError(f"clone planner expected {key}={expected_value}, got {plan_hints.get(key)}")

    capture_depth = payload.get("capture_depth")
    if not isinstance(capture_depth, dict):
        raise AssertionError("clone summary is missing capture_depth")
    network_depth = capture_depth.get("network")
    if not isinstance(network_depth, dict):
        raise AssertionError("clone summary is missing capture_depth.network")
    if int(network_depth.get("request_count") or 0) < 1:
        raise AssertionError("capture_depth.network.request_count was not populated")
    if int(network_depth.get("har_entry_count") or 0) < 1:
        raise AssertionError("capture_depth.network.har_entry_count was not populated")

    expected_files = [
        output_dir / "capture.json",
        output_dir / "network" / "har.json",
        output_dir / "network" / "har-like.json",
        output_dir / "reproduction" / "embed.html",
        output_dir / "reproduction" / "embed.tsx",
        output_dir / "reproduction" / "exact-reuse-verification.json",
    ]
    missing = [str(path) for path in expected_files if not path.exists()]
    if missing:
        raise AssertionError(f"clone smoke is missing expected artifacts: {missing}")
    persisted_verification = load_json(output_dir / "reproduction" / "exact-reuse-verification.json")
    if persisted_verification.get("status") != verification.get("status"):
        raise AssertionError("persisted exact-reuse verification status did not match CLI payload")


def main() -> int:
    root = repo_root()
    assert_exact_ready_semantics(root)
    assert_site_profile_routing_semantics()
    assert_rebuild_scaffold_visual_semantics()
    temp_root = root / ".tmp" / "integration-smoke"
    if temp_root.exists():
        shutil.rmtree(temp_root)
    temp_root.mkdir(parents=True, exist_ok=True)
    assert_frame_shadow_parity_fixture(root, temp_root)

    dist_root = root / "dist"
    dist_root.mkdir(parents=True, exist_ok=True)
    stale_sentinel = dist_root / "stale-release-sentinel.txt"
    stale_sentinel.write_text("stale\n")
    run([sys.executable, "scripts/release_bundle.py"], cwd=root)
    if stale_sentinel.exists():
        raise AssertionError("release build did not clean stale dist contents")
    assert_dist_contents(dist_root)
    archive_path = root / "dist" / "source-first-clone-bundle.tar.gz"
    assert_release_archive_clean(archive_path)

    install_home = temp_root / "home"
    run(
        [
            sys.executable,
            "dist/install.py",
            "install",
            "--target-home",
            str(install_home),
            "--bundle-archive",
            str(archive_path),
            "--force",
        ],
        cwd=root,
    )
    doctor = subprocess.run(
        [sys.executable, "dist/install.py", "doctor", "--target-home", str(install_home)],
        cwd=root,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        check=False,
    )
    if doctor.returncode != 0:
        raise RuntimeError(f"doctor failed with {doctor.returncode}\n{doctor.stderr}\n{doctor.stdout}")
    doctor_payload = json.loads(doctor.stdout)
    if not doctor_payload.get("plugin_exists") or not doctor_payload.get("marketplace_entry"):
        raise AssertionError(f"doctor did not report a complete install: {doctor_payload}")

    clone_dir = temp_root / "clone-example"
    clone_output = temp_root / "clone-example-output.json"
    run(
        [
            "node",
            "./bin/web-embedding.mjs",
            "clone",
            "--url",
            "https://www.example.com",
            "--output-dir",
            str(clone_dir),
            "--wait-seconds",
            "1",
            "--timeout-seconds",
            "20",
        ],
        cwd=root,
        output=clone_output,
    )
    assert_clone_summary(load_json(clone_output), clone_dir)

    print("Integration smoke passed: release archive, install doctor, and URL-only clone are healthy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
