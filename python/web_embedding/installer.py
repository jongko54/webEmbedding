#!/usr/bin/env python3
"""Install, uninstall, and package the source-first clone plugin bundle."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PLUGIN_NAME = "source-first-clone"
MARKETPLACE_ENTRY = {
    "name": PLUGIN_NAME,
    "source": {
        "source": "local",
        "path": f"./plugins/{PLUGIN_NAME}",
    },
    "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    },
    "category": "Developer Tools",
}
DEFAULT_MARKETPLACE = {
    "name": "local-plugins",
    "interface": {"displayName": "Local Plugins"},
    "plugins": [],
}


@dataclass
class InstallPaths:
    home_root: Path
    plugins_root: Path
    plugin_root: Path
    marketplace_path: Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_bundle_dir() -> Path:
    return repo_root() / "bundle" / PLUGIN_NAME


def load_capture_api() -> tuple[Any, Any, Any, Any, Any, Any]:
    capture_root = repo_root() / "bundle" / PLUGIN_NAME / "mcp"
    if str(capture_root) not in sys.path:
        sys.path.insert(0, str(capture_root))
    from source_first_clone.acquisition import detect_runtime_capabilities
    from source_first_clone.capture_bundle import capture_reference_bundle
    from source_first_clone.orchestration import clone_reference_url
    from source_first_clone.rebuild_scaffold import build_rebuild_scaffold
    from source_first_clone.reproduction import build_reproduction_bundle
    from source_first_clone.verification import verify_fidelity_report

    return (
        detect_runtime_capabilities,
        capture_reference_bundle,
        build_reproduction_bundle,
        clone_reference_url,
        verify_fidelity_report,
        build_rebuild_scaffold,
    )


def load_json_file(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).expanduser().resolve().read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return payload


def build_paths(target_home: str | None) -> InstallPaths:
    home_root = Path(target_home).expanduser().resolve() if target_home else Path.home()
    return InstallPaths(
        home_root=home_root,
        plugins_root=home_root / "plugins",
        plugin_root=home_root / "plugins" / PLUGIN_NAME,
        marketplace_path=home_root / ".agents" / "plugins" / "marketplace.json",
    )


def load_marketplace(path: Path) -> dict[str, Any]:
    if not path.exists():
        return json.loads(json.dumps(DEFAULT_MARKETPLACE))
    with path.open() as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    if "plugins" not in payload or not isinstance(payload["plugins"], list):
        raise ValueError(f"{path} must contain a top-level 'plugins' array.")
    payload.setdefault("interface", {"displayName": "Local Plugins"})
    payload.setdefault("name", "local-plugins")
    return payload


def write_marketplace(path: Path, payload: dict[str, Any], dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] write marketplace: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def install_marketplace_entry(paths: InstallPaths, dry_run: bool) -> None:
    payload = load_marketplace(paths.marketplace_path)
    plugins = [entry for entry in payload["plugins"] if entry.get("name") != PLUGIN_NAME]
    plugins.append(json.loads(json.dumps(MARKETPLACE_ENTRY)))
    payload["plugins"] = plugins
    write_marketplace(paths.marketplace_path, payload, dry_run=dry_run)


def uninstall_marketplace_entry(paths: InstallPaths, dry_run: bool) -> None:
    if not paths.marketplace_path.exists():
        return
    payload = load_marketplace(paths.marketplace_path)
    plugins = [entry for entry in payload["plugins"] if entry.get("name") != PLUGIN_NAME]
    if len(plugins) == len(payload["plugins"]):
        return
    payload["plugins"] = plugins
    write_marketplace(paths.marketplace_path, payload, dry_run=dry_run)


def copy_bundle(source_dir: Path, target_dir: Path, force: bool, dry_run: bool) -> None:
    if not source_dir.exists():
        raise FileNotFoundError(f"Bundle source does not exist: {source_dir}")

    if target_dir.exists():
        if not force:
            raise FileExistsError(
                f"Plugin already exists at {target_dir}. Re-run with --force to overwrite it."
            )
        if dry_run:
            print(f"[dry-run] remove existing plugin dir: {target_dir}")
        else:
            shutil.rmtree(target_dir)

    if dry_run:
        print(f"[dry-run] copy bundle: {source_dir} -> {target_dir}")
        return

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, target_dir, copy_function=shutil.copy2)


def safe_extract_archive(archive: tarfile.TarFile, destination: Path) -> None:
    destination = destination.resolve()
    members = archive.getmembers()
    for member in members:
        member_path = (destination / member.name).resolve()
        if destination not in member_path.parents and member_path != destination:
            raise ValueError(f"Archive member escapes destination: {member.name}")
    archive.extractall(destination)


def resolve_bundle_source(bundle_dir: str | None, bundle_archive: str | None) -> tuple[Path, str | None]:
    if bundle_dir and bundle_archive:
        raise ValueError("Use either --bundle-dir or --bundle-archive, not both.")

    if bundle_archive:
        archive_path = Path(bundle_archive).expanduser().resolve()
        if not archive_path.exists():
            raise FileNotFoundError(f"Bundle archive does not exist: {archive_path}")
        temp_root = tempfile.mkdtemp(prefix="web-embedding-")
        with tarfile.open(archive_path, "r:gz") as archive:
            safe_extract_archive(archive, Path(temp_root))
        extracted = Path(temp_root) / PLUGIN_NAME
        if not extracted.exists():
            raise FileNotFoundError(
                f"Archive {archive_path} did not contain a top-level {PLUGIN_NAME}/ directory."
            )
        return extracted, temp_root

    if bundle_dir:
        return Path(bundle_dir).expanduser().resolve(), None

    return default_bundle_dir(), None


def remove_plugin_dir(paths: InstallPaths, dry_run: bool) -> None:
    if not paths.plugin_root.exists():
        return
    if dry_run:
        print(f"[dry-run] remove plugin dir: {paths.plugin_root}")
        return
    shutil.rmtree(paths.plugin_root)


def command_install(args: argparse.Namespace) -> int:
    paths = build_paths(args.target_home)
    bundle_source, temp_root = resolve_bundle_source(args.bundle_dir, args.bundle_archive)

    try:
        copy_bundle(bundle_source, paths.plugin_root, force=args.force, dry_run=args.dry_run)
        install_marketplace_entry(paths, dry_run=args.dry_run)
    finally:
        if temp_root:
            shutil.rmtree(temp_root, ignore_errors=True)

    print(f"Installed {PLUGIN_NAME}")
    print(f"  plugin: {paths.plugin_root}")
    print(f"  marketplace: {paths.marketplace_path}")
    return 0


def command_uninstall(args: argparse.Namespace) -> int:
    paths = build_paths(args.target_home)
    remove_plugin_dir(paths, dry_run=args.dry_run)
    uninstall_marketplace_entry(paths, dry_run=args.dry_run)
    print(f"Removed {PLUGIN_NAME} from {paths.home_root}")
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    paths = build_paths(args.target_home)
    marketplace_exists = paths.marketplace_path.exists()
    plugin_exists = paths.plugin_root.exists()
    marketplace_entry = False

    if marketplace_exists:
        payload = load_marketplace(paths.marketplace_path)
        marketplace_entry = any(
            entry.get("name") == PLUGIN_NAME for entry in payload.get("plugins", [])
        )

    report = {
        "home_root": str(paths.home_root),
        "plugin_root": str(paths.plugin_root),
        "plugin_exists": plugin_exists,
        "marketplace_path": str(paths.marketplace_path),
        "marketplace_exists": marketplace_exists,
        "marketplace_entry": marketplace_entry,
    }
    print(json.dumps(report, indent=2))
    return 0 if plugin_exists and marketplace_entry else 1


def command_paths(args: argparse.Namespace) -> int:
    paths = build_paths(args.target_home)
    report = {
        "home_root": str(paths.home_root),
        "plugins_root": str(paths.plugins_root),
        "plugin_root": str(paths.plugin_root),
        "marketplace_path": str(paths.marketplace_path),
        "default_bundle_dir": str(default_bundle_dir()),
    }
    print(json.dumps(report, indent=2))
    return 0


def command_capabilities(args: argparse.Namespace) -> int:
    del args
    detect_runtime_capabilities, _capture_reference_bundle, _build_reproduction_bundle, _clone_reference_url, _verify_fidelity_report, _build_rebuild_scaffold = load_capture_api()
    print(json.dumps(detect_runtime_capabilities(), indent=2))
    return 0


def compact_capture_result(result: dict[str, Any]) -> dict[str, Any]:
    summary = json.loads(json.dumps(result))
    runtime = summary.get("runtime", {})
    captures = runtime.get("captures", {}) if isinstance(runtime, dict) else {}
    if isinstance(runtime.get("networkHits"), list):
        hits = runtime["networkHits"]
        runtime["networkHitCount"] = len(hits)
        runtime["networkHitsSample"] = hits[:15]
        runtime.pop("networkHits", None)
    if isinstance(runtime.get("htmlMatches"), list):
        matches = runtime["htmlMatches"]
        runtime["htmlMatchCount"] = len(matches)
        runtime["htmlMatchesSample"] = matches[:15]
        runtime.pop("htmlMatches", None)
    html_capture = captures.get("html")
    if isinstance(html_capture, dict) and html_capture.get("available"):
        html_capture.pop("content", None)
    dom_capture = captures.get("dom")
    if isinstance(dom_capture, dict):
        dom_capture.pop("content", None)
    accessibility_capture = captures.get("accessibility")
    if isinstance(accessibility_capture, dict):
        accessibility_capture.pop("content", None)
    styles_capture = captures.get("styles")
    if isinstance(styles_capture, dict):
        styles_capture.pop("content", None)
    network_capture = captures.get("network")
    if isinstance(network_capture, dict):
        network_capture.pop("content", None)
    assets_capture = captures.get("assets")
    if isinstance(assets_capture, dict):
        assets_capture.pop("content", None)
    interactions_capture = captures.get("interactions")
    if isinstance(interactions_capture, dict):
        interactions_capture.pop("content", None)
    interaction_trace_capture = captures.get("interactionTrace")
    if isinstance(interaction_trace_capture, dict):
        interaction_trace_capture.pop("content", None)
    screenshot_capture = captures.get("screenshot")
    if isinstance(screenshot_capture, dict) and screenshot_capture.get("available"):
        screenshot_capture.pop("base64", None)
    bundle = summary.get("bundle", {})
    captured_artifacts = bundle.get("captured_artifacts", {}) if isinstance(bundle, dict) else {}
    artifact_html = captured_artifacts.get("html")
    if isinstance(artifact_html, dict):
        artifact_html.pop("content", None)
    artifact_trace = captured_artifacts.get("interaction_trace")
    if isinstance(artifact_trace, dict):
        artifact_trace.pop("content", None)
    breakpoint_summary = summary.get("breakpoints")
    if isinstance(breakpoint_summary, dict):
        variants = breakpoint_summary.get("variants")
        if isinstance(variants, list):
            breakpoint_summary["variant_count"] = len(variants)
            breakpoint_summary["variant_sample"] = variants[:3]
            breakpoint_summary.pop("variants", None)
    return summary


def command_capture(args: argparse.Namespace) -> int:
    _detect_runtime_capabilities, capture_reference_bundle, _build_reproduction_bundle, _clone_reference_url, _verify_fidelity_report, _build_rebuild_scaffold = load_capture_api()
    result = capture_reference_bundle(
        url=args.url,
        timeout_seconds=args.timeout_seconds,
        wait_seconds=args.wait_seconds,
        include_runtime_trace=not args.skip_runtime_trace,
        user_data_dir=args.user_data_dir,
        storage_state_path=args.storage_state_path,
        storage_state_output_path=args.storage_state_output_path,
        capture_html=not args.skip_html,
        capture_screenshot=not args.skip_screenshot,
        viewport_width=args.viewport_width,
        viewport_height=args.viewport_height,
        breakpoint_profiles=args.breakpoints,
        output_dir=args.output_dir,
        exact_requested=not args.not_exact,
        license_text=args.license_text,
        source_signals=args.source_signals,
    )
    payload = result if args.full_json else compact_capture_result(result)
    print(json.dumps(payload, indent=2))
    return 0


def compact_reproduction_result(result: dict[str, Any]) -> dict[str, Any]:
    summary = json.loads(json.dumps(result))
    exact_reuse = summary.get("exact_reuse")
    if isinstance(exact_reuse, dict):
        exact_reuse.pop("snippets", None)
    rebuild_scaffold = summary.get("rebuild_scaffold")
    if isinstance(rebuild_scaffold, dict):
        summary["rebuild_scaffold"] = compact_rebuild_scaffold_summary(rebuild_scaffold)
    candidates = summary.get("candidates")
    if isinstance(candidates, list):
        summary["candidateCount"] = len(candidates)
        summary["candidateSample"] = candidates[:15]
        summary.pop("candidates", None)
    self_verify = summary.get("self_verify")
    if isinstance(self_verify, dict):
        breakpoint_summary = self_verify.get("breakpoints", {})
        reports = breakpoint_summary.get("reports") if isinstance(breakpoint_summary, dict) else None
        compact = {
            "status": self_verify.get("status"),
            "overall_ready_for_exact_clone": self_verify.get("overall_ready_for_exact_clone"),
            "root_report": self_verify.get("root_report"),
            "persisted": self_verify.get("persisted"),
            "note": self_verify.get("note"),
        }
        if isinstance(breakpoint_summary, dict):
            compact["breakpoints"] = {
                "compared": breakpoint_summary.get("compared"),
                "reports": reports[:3] if isinstance(reports, list) else [],
            }
        summary["self_verify"] = compact
    repair_pass = summary.get("repair_pass")
    if isinstance(repair_pass, dict):
        compact_repair = compact_rebuild_scaffold_summary(repair_pass)
        repair_verify = repair_pass.get("self_verify")
        if isinstance(repair_verify, dict):
            compact_repair["self_verify"] = {
                "status": repair_verify.get("status"),
                "overall_ready_for_exact_clone": repair_verify.get("overall_ready_for_exact_clone"),
                "preferred_renderer": repair_verify.get("preferred_renderer"),
                "root_report": repair_verify.get("root_report"),
                "persisted": repair_verify.get("persisted"),
            }
        summary["repair_pass"] = compact_repair
    return summary


def compact_rebuild_scaffold_summary(scaffold: dict[str, Any]) -> dict[str, Any]:
    summary = json.loads(json.dumps(scaffold))
    artifacts = summary.get("artifacts")
    if isinstance(artifacts, dict):
        summary["artifact_files"] = list(artifacts.keys())
        manifest = artifacts.get("manifest.json")
        if isinstance(manifest, dict):
            summary["app_entrypoints"] = manifest.get("app_entrypoints")
        summary.pop("artifacts", None)
    nested_summary = summary.get("summary")
    if isinstance(nested_summary, dict):
        summary["summary"] = {
            "coverage": nested_summary.get("coverage"),
            "source_url": nested_summary.get("source_url"),
            "final_url": nested_summary.get("final_url"),
            "title": nested_summary.get("title"),
            "policy_mode": nested_summary.get("policy_mode"),
            "frame_policy": nested_summary.get("frame_policy"),
            "viewport": nested_summary.get("viewport"),
            "breakpoints": nested_summary.get("breakpoints"),
            "signals": nested_summary.get("signals"),
            "block_count": len(nested_summary.get("blocks", []) or []),
            "outline_count": len(nested_summary.get("outline", []) or []),
            "interaction_count": (nested_summary.get("interactions", {}) or {}).get("count"),
            "renderer": nested_summary.get("renderer"),
        }
    return summary


def command_reproduce(args: argparse.Namespace) -> int:
    _detect_runtime_capabilities, capture_reference_bundle, build_reproduction_bundle, _clone_reference_url, _verify_fidelity_report, _build_rebuild_scaffold = load_capture_api()
    capture_bundle = capture_reference_bundle(
        url=args.url,
        timeout_seconds=args.timeout_seconds,
        wait_seconds=args.wait_seconds,
        include_runtime_trace=not args.skip_runtime_trace,
        user_data_dir=args.user_data_dir,
        storage_state_path=args.storage_state_path,
        storage_state_output_path=args.storage_state_output_path,
        capture_html=not args.skip_html,
        capture_screenshot=not args.skip_screenshot,
        viewport_width=args.viewport_width,
        viewport_height=args.viewport_height,
        breakpoint_profiles=args.breakpoints,
        output_dir=args.output_dir,
        exact_requested=not args.not_exact,
        license_text=args.license_text,
        source_signals=args.source_signals,
    )
    result = build_reproduction_bundle(
        capture_bundle=capture_bundle,
        output_dir=args.output_dir,
    )
    payload = result if args.full_json else compact_reproduction_result(result)
    print(json.dumps(payload, indent=2))
    return 0


def compact_clone_result(result: dict[str, Any]) -> dict[str, Any]:
    summary = json.loads(json.dumps(result))
    exact_reuse = summary.get("exact_reuse")
    if isinstance(exact_reuse, dict):
        exact_reuse.pop("snippets", None)
    reproduction = summary.get("reproduction")
    if isinstance(reproduction, dict):
        summary["reproduction"] = compact_reproduction_result(reproduction)
    capture_bundle = summary.get("capture_bundle")
    if isinstance(capture_bundle, dict):
        summary["capture_bundle"] = compact_capture_result(capture_bundle)
    return summary


def command_clone(args: argparse.Namespace) -> int:
    _detect_runtime_capabilities, _capture_reference_bundle, _build_reproduction_bundle, clone_reference_url, _verify_fidelity_report, _build_rebuild_scaffold = load_capture_api()
    result = clone_reference_url(
        url=args.url,
        timeout_seconds=args.timeout_seconds,
        wait_seconds=args.wait_seconds,
        include_runtime_trace=not args.skip_runtime_trace,
        user_data_dir=args.user_data_dir,
        storage_state_path=args.storage_state_path,
        storage_state_output_path=args.storage_state_output_path,
        capture_html=not args.skip_html,
        capture_screenshot=not args.skip_screenshot,
        viewport_width=args.viewport_width,
        viewport_height=args.viewport_height,
        breakpoint_profiles=args.breakpoints,
        output_dir=args.output_dir,
        exact_requested=not args.not_exact,
        license_text=args.license_text,
        source_signals=args.source_signals,
    )
    payload = result if args.full_json else compact_clone_result(result)
    print(json.dumps(payload, indent=2))
    return 0


def compact_scaffold_result(result: dict[str, Any]) -> dict[str, Any]:
    return compact_rebuild_scaffold_summary(result)


def command_scaffold(args: argparse.Namespace) -> int:
    _detect_runtime_capabilities, _capture_reference_bundle, _build_reproduction_bundle, _clone_reference_url, _verify_fidelity_report, build_rebuild_scaffold = load_capture_api()
    capture_bundle = load_json_file(args.capture_bundle)
    result = build_rebuild_scaffold(capture_bundle)
    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser().resolve()
        from source_first_clone.rebuild_scaffold import persist_rebuild_scaffold

        result["persisted"] = persist_rebuild_scaffold(output_dir, result)
    payload = result if args.full_json else compact_scaffold_result(result)
    print(json.dumps(payload, indent=2))
    return 0


def command_verify(args: argparse.Namespace) -> int:
    _detect_runtime_capabilities, _capture_reference_bundle, _build_reproduction_bundle, _clone_reference_url, verify_fidelity_report, _build_rebuild_scaffold = load_capture_api()
    reference_bundle = load_json_file(args.reference_bundle)
    candidate_bundle = load_json_file(args.candidate_bundle)
    result = verify_fidelity_report(
        reference_bundle=reference_bundle,
        candidate_bundle=candidate_bundle,
        reference_url=args.reference_url,
        candidate_url=args.candidate_url,
    )
    print(json.dumps(result, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install or inspect the source-first clone plugin.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser("install", help="Install the plugin bundle.")
    install_parser.add_argument("--target-home", help="Override the home root used for installation.")
    install_parser.add_argument("--bundle-dir", help="Use a local bundle directory instead of the repo bundle.")
    install_parser.add_argument("--bundle-archive", help="Use a release tarball instead of a bundle directory.")
    install_parser.add_argument("--force", action="store_true", help="Overwrite an existing install.")
    install_parser.add_argument("--dry-run", action="store_true", help="Print actions without writing files.")
    install_parser.set_defaults(func=command_install)

    uninstall_parser = subparsers.add_parser("uninstall", help="Remove the installed plugin.")
    uninstall_parser.add_argument("--target-home", help="Override the home root used for removal.")
    uninstall_parser.add_argument("--dry-run", action="store_true", help="Print actions without writing files.")
    uninstall_parser.set_defaults(func=command_uninstall)

    doctor_parser = subparsers.add_parser("doctor", help="Check the install state.")
    doctor_parser.add_argument("--target-home", help="Override the home root used for inspection.")
    doctor_parser.set_defaults(func=command_doctor)

    paths_parser = subparsers.add_parser("paths", help="Print the important install paths.")
    paths_parser.add_argument("--target-home", help="Override the home root used for inspection.")
    paths_parser.set_defaults(func=command_paths)

    capabilities_parser = subparsers.add_parser("capabilities", help="Detect runtime capture dependencies.")
    capabilities_parser.set_defaults(func=command_capabilities)

    capture_parser = subparsers.add_parser("capture", help="Run a session-aware capture bundle flow.")
    capture_parser.add_argument("--url", required=True, help="Reference URL to capture.")
    capture_parser.add_argument("--output-dir", required=True, help="Directory where the capture bundle will be written.")
    capture_parser.add_argument("--timeout-seconds", type=int, default=20, help="Static fetch timeout in seconds.")
    capture_parser.add_argument("--wait-seconds", type=int, default=8, help="Browser settle time after navigation.")
    capture_parser.add_argument("--user-data-dir", help="Persistent browser profile directory for Playwright.")
    capture_parser.add_argument("--storage-state-path", help="Existing Playwright storage state JSON to apply.")
    capture_parser.add_argument("--storage-state-output-path", help="Where to export Playwright storage state JSON.")
    capture_parser.add_argument("--viewport-width", type=int, default=1440, help="Capture viewport width.")
    capture_parser.add_argument("--viewport-height", type=int, default=1200, help="Capture viewport height.")
    capture_parser.add_argument("--breakpoints", nargs="*", choices=["desktop", "tablet", "mobile"], default=[], help="Additional breakpoint profiles to capture alongside the primary viewport.")
    capture_parser.add_argument("--license-text", help="Optional license text for policy classification.")
    capture_parser.add_argument("--source-signals", nargs="*", default=[], help="Optional source/reuse hints such as remix or export.")
    capture_parser.add_argument("--skip-runtime-trace", action="store_true", help="Skip Playwright runtime capture.")
    capture_parser.add_argument("--skip-html", action="store_true", help="Do not save runtime HTML.")
    capture_parser.add_argument("--skip-screenshot", action="store_true", help="Do not save runtime screenshot.")
    capture_parser.add_argument("--not-exact", action="store_true", help="Mark the request as approximate instead of exact.")
    capture_parser.add_argument("--full-json", action="store_true", help="Print the full capture payload including inline runtime artifacts.")
    capture_parser.set_defaults(func=command_capture)

    reproduce_parser = subparsers.add_parser("reproduce", help="Capture a reference and build an exact-reuse or reproduction bundle.")
    reproduce_parser.add_argument("--url", required=True, help="Reference URL to reproduce.")
    reproduce_parser.add_argument("--output-dir", required=True, help="Directory where the bundle and reproduction files will be written.")
    reproduce_parser.add_argument("--timeout-seconds", type=int, default=20, help="Static fetch timeout in seconds.")
    reproduce_parser.add_argument("--wait-seconds", type=int, default=8, help="Browser settle time after navigation.")
    reproduce_parser.add_argument("--user-data-dir", help="Persistent browser profile directory for Playwright.")
    reproduce_parser.add_argument("--storage-state-path", help="Existing Playwright storage state JSON to apply.")
    reproduce_parser.add_argument("--storage-state-output-path", help="Where to export Playwright storage state JSON.")
    reproduce_parser.add_argument("--viewport-width", type=int, default=1440, help="Capture viewport width.")
    reproduce_parser.add_argument("--viewport-height", type=int, default=1200, help="Capture viewport height.")
    reproduce_parser.add_argument("--breakpoints", nargs="*", choices=["desktop", "tablet", "mobile"], default=[], help="Additional breakpoint profiles to capture alongside the primary viewport.")
    reproduce_parser.add_argument("--license-text", help="Optional license text for policy classification.")
    reproduce_parser.add_argument("--source-signals", nargs="*", default=[], help="Optional source/reuse hints such as remix or export.")
    reproduce_parser.add_argument("--skip-runtime-trace", action="store_true", help="Skip Playwright runtime capture.")
    reproduce_parser.add_argument("--skip-html", action="store_true", help="Do not save runtime HTML.")
    reproduce_parser.add_argument("--skip-screenshot", action="store_true", help="Do not save runtime screenshot.")
    reproduce_parser.add_argument("--not-exact", action="store_true", help="Mark the request as approximate instead of exact.")
    reproduce_parser.add_argument("--full-json", action="store_true", help="Print the full reproduction payload.")
    reproduce_parser.set_defaults(func=command_reproduce)

    clone_parser = subparsers.add_parser("clone", help="Run the full source-first clone workflow from a single URL.")
    clone_parser.add_argument("--url", required=True, help="Reference URL to clone.")
    clone_parser.add_argument("--output-dir", required=True, help="Directory where capture and reproduction files will be written.")
    clone_parser.add_argument("--timeout-seconds", type=int, default=20, help="Static fetch timeout in seconds.")
    clone_parser.add_argument("--wait-seconds", type=int, default=8, help="Browser settle time after navigation.")
    clone_parser.add_argument("--user-data-dir", help="Persistent browser profile directory for Playwright.")
    clone_parser.add_argument("--storage-state-path", help="Existing Playwright storage state JSON to apply.")
    clone_parser.add_argument("--storage-state-output-path", help="Where to export Playwright storage state JSON.")
    clone_parser.add_argument("--viewport-width", type=int, default=1440, help="Capture viewport width.")
    clone_parser.add_argument("--viewport-height", type=int, default=1200, help="Capture viewport height.")
    clone_parser.add_argument("--breakpoints", nargs="*", choices=["desktop", "tablet", "mobile"], default=[], help="Additional breakpoint profiles to capture alongside the primary viewport.")
    clone_parser.add_argument("--license-text", help="Optional license text for policy classification.")
    clone_parser.add_argument("--source-signals", nargs="*", default=[], help="Optional source/reuse hints such as remix or export.")
    clone_parser.add_argument("--skip-runtime-trace", action="store_true", help="Skip Playwright runtime capture.")
    clone_parser.add_argument("--skip-html", action="store_true", help="Do not save runtime HTML.")
    clone_parser.add_argument("--skip-screenshot", action="store_true", help="Do not save runtime screenshot.")
    clone_parser.add_argument("--not-exact", action="store_true", help="Mark the request as approximate instead of exact.")
    clone_parser.add_argument("--full-json", action="store_true", help="Print the full clone payload.")
    clone_parser.set_defaults(func=command_clone)

    scaffold_parser = subparsers.add_parser("scaffold", help="Generate a bounded rebuild scaffold from an existing capture bundle JSON.")
    scaffold_parser.add_argument("--capture-bundle", required=True, help="Path to a capture bundle JSON file.")
    scaffold_parser.add_argument("--output-dir", help="Optional directory where scaffold artifacts will be written.")
    scaffold_parser.add_argument("--full-json", action="store_true", help="Print the full scaffold payload.")
    scaffold_parser.set_defaults(func=command_scaffold)

    verify_parser = subparsers.add_parser("verify", help="Compare two capture/reproduction bundle JSON files with bounded fidelity checks.")
    verify_parser.add_argument("--reference-bundle", required=True, help="Path to the reference bundle JSON file.")
    verify_parser.add_argument("--candidate-bundle", required=True, help="Path to the candidate bundle JSON file.")
    verify_parser.add_argument("--reference-url", help="Optional explicit reference URL.")
    verify_parser.add_argument("--candidate-url", help="Optional explicit candidate URL.")
    verify_parser.set_defaults(func=command_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # pragma: no cover - thin CLI wrapper
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
