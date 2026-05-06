#!/usr/bin/env python3
"""Install, uninstall, and package the source-first clone plugin bundle."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PLUGIN_NAME = "source-first-clone"
PACKAGE_NAME = "web-embedding"
PACKAGE_VERSION = "0.3.3"
TELEMETRY_SCHEMA_VERSION = 1
TELEMETRY_CONFIG_DIR = ".web-embedding"
TELEMETRY_CONFIG_FILE = "telemetry.json"
DEFAULT_TELEMETRY_ENDPOINT = ""
TELEMETRY_PROMPT_ENV = "WEB_EMBEDDING_TELEMETRY_PROMPT"
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


TRUE_VALUES = {"1", "true", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "no", "n", "off"}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_bundle_dir() -> Path:
    return repo_root() / "bundle" / PLUGIN_NAME


def parse_bool_env(value: str | None) -> bool | None:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in TRUE_VALUES:
        return True
    if lowered in FALSE_VALUES:
        return False
    return None


def telemetry_config_path(home_root: Path) -> Path:
    return home_root / TELEMETRY_CONFIG_DIR / TELEMETRY_CONFIG_FILE


def load_telemetry_config(home_root: Path) -> dict[str, Any]:
    path = telemetry_config_path(home_root)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_telemetry_config(home_root: Path, payload: dict[str, Any], dry_run: bool = False) -> None:
    path = telemetry_config_path(home_root)
    if dry_run:
        print(f"[dry-run] write telemetry config: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def telemetry_endpoint(config: dict[str, Any]) -> str:
    env_endpoint = os.environ.get("WEB_EMBEDDING_TELEMETRY_ENDPOINT")
    if env_endpoint:
        return env_endpoint.strip()
    configured = config.get("endpoint")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    return DEFAULT_TELEMETRY_ENDPOINT


def telemetry_log_path() -> Path | None:
    raw_path = os.environ.get("WEB_EMBEDDING_TELEMETRY_LOG")
    if not raw_path:
        return None
    return Path(raw_path).expanduser().resolve()


def telemetry_is_enabled(config: dict[str, Any]) -> bool:
    disabled_override = parse_bool_env(os.environ.get("WEB_EMBEDDING_NO_TELEMETRY"))
    if disabled_override is True:
        return False
    env_enabled = parse_bool_env(os.environ.get("WEB_EMBEDDING_TELEMETRY"))
    if env_enabled is not None:
        return env_enabled
    return config.get("enabled") is True


def telemetry_timeout_seconds() -> float:
    raw_timeout = os.environ.get("WEB_EMBEDDING_TELEMETRY_TIMEOUT_SECONDS")
    if not raw_timeout:
        return 1.5
    try:
        return max(0.1, min(10.0, float(raw_timeout)))
    except ValueError:
        return 1.5


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def debug_telemetry(message: str) -> None:
    if parse_bool_env(os.environ.get("WEB_EMBEDDING_TELEMETRY_DEBUG")) is True:
        print(f"telemetry: {message}", file=sys.stderr)


def package_version() -> str:
    package_json = repo_root() / "package.json"
    if package_json.exists():
        try:
            payload = json.loads(package_json.read_text())
        except Exception:
            payload = {}
        if isinstance(payload, dict) and isinstance(payload.get("version"), str):
            return payload["version"]
    return PACKAGE_VERSION


def ensure_anonymous_id(home_root: Path, config: dict[str, Any]) -> tuple[str, bool]:
    anonymous_id = config.get("anonymous_id")
    if isinstance(anonymous_id, str) and anonymous_id.strip():
        return anonymous_id.strip(), False
    anonymous_id = str(uuid.uuid4())
    config["anonymous_id"] = anonymous_id
    write_telemetry_config(home_root, config)
    return anonymous_id, True


def safe_telemetry_value(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value[:160]
    if isinstance(value, list):
        return [safe_telemetry_value(item) for item in value[:20]]
    if isinstance(value, dict):
        return {
            str(key)[:80]: safe_telemetry_value(nested)
            for key, nested in list(value.items())[:30]
        }
    return str(type(value).__name__)


def configure_telemetry(
    home_root: Path,
    *,
    enabled: bool | None = None,
    endpoint: str | None = None,
    reset_id: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    config = load_telemetry_config(home_root)
    if enabled is not None:
        config["enabled"] = enabled
    if endpoint is not None:
        if endpoint.strip():
            config["endpoint"] = endpoint.strip()
        else:
            config.pop("endpoint", None)
    if reset_id or (config.get("enabled") is True and not isinstance(config.get("anonymous_id"), str)):
        config["anonymous_id"] = str(uuid.uuid4())
    write_telemetry_config(home_root, config, dry_run=dry_run)
    return config


def mark_telemetry_prompted(home_root: Path, *, enabled: bool, endpoint: str | None = None) -> None:
    config = configure_telemetry(home_root, enabled=enabled, endpoint=endpoint)
    config["prompted_at"] = utc_now_iso()
    write_telemetry_config(home_root, config)


def telemetry_prompt_was_answered(config: dict[str, Any]) -> bool:
    return isinstance(config.get("enabled"), bool) or isinstance(config.get("prompted_at"), str)


def can_prompt_for_telemetry(args: argparse.Namespace, home_root: Path) -> bool:
    if args.telemetry or args.no_telemetry or args.dry_run:
        return False
    if parse_bool_env(os.environ.get("WEB_EMBEDDING_NO_TELEMETRY")) is True:
        return False
    if parse_bool_env(os.environ.get("WEB_EMBEDDING_TELEMETRY")) is not None:
        return False

    prompt_override = parse_bool_env(os.environ.get(TELEMETRY_PROMPT_ENV))
    if prompt_override is False:
        return False

    config = load_telemetry_config(home_root)
    if telemetry_prompt_was_answered(config):
        return False
    if prompt_override is True:
        return True
    return sys.stdin.isatty() and sys.stdout.isatty()


def prompt_for_telemetry(home_root: Path, endpoint: str | None = None) -> None:
    print()
    print("Help improve webEmbedding by sending anonymous command-completion telemetry?")
    print("It never sends target URLs, local paths, captured HTML, screenshots, storage state,")
    print("environment variables, API keys, or command output. You can disable it any time with:")
    print("  web-embedding telemetry disable")
    resolved_endpoint = endpoint or telemetry_endpoint(load_telemetry_config(home_root))
    if resolved_endpoint:
        print(f"Telemetry endpoint: {resolved_endpoint}")
    else:
        print("No telemetry endpoint is configured yet, so no events will be sent until one is set.")
    try:
        answer = input("Enable anonymous telemetry? [y/N]: ").strip().lower()
    except EOFError:
        answer = ""
    enabled = answer in {"y", "yes"}
    mark_telemetry_prompted(
        home_root,
        enabled=enabled,
        endpoint=resolved_endpoint if enabled and resolved_endpoint else endpoint,
    )
    print("Telemetry enabled." if enabled else "Telemetry disabled.")


def telemetry_status_payload(home_root: Path) -> dict[str, Any]:
    config = load_telemetry_config(home_root)
    endpoint = telemetry_endpoint(config)
    log_path = telemetry_log_path()
    return {
        "config_path": str(telemetry_config_path(home_root)),
        "enabled": telemetry_is_enabled(config),
        "configured_enabled": config.get("enabled") is True,
        "prompted": telemetry_prompt_was_answered(config),
        "prompted_at": config.get("prompted_at"),
        "anonymous_id": config.get("anonymous_id"),
        "endpoint_configured": bool(endpoint),
        "endpoint": endpoint if endpoint else None,
        "log_path": str(log_path) if log_path else None,
        "env_override": os.environ.get("WEB_EMBEDDING_TELEMETRY") is not None,
        "env_disabled": parse_bool_env(os.environ.get("WEB_EMBEDDING_NO_TELEMETRY")) is True,
    }


def telemetry_home_root(args: argparse.Namespace) -> Path:
    target_home = getattr(args, "target_home", None)
    if isinstance(target_home, str) and target_home:
        return Path(target_home).expanduser().resolve()
    return Path.home().resolve()


def telemetry_properties_for_args(
    args: argparse.Namespace,
    *,
    exit_code: int,
    error_type: str | None = None,
) -> dict[str, Any]:
    command = getattr(args, "command", None)
    properties: dict[str, Any] = {
        "command": command,
        "success": exit_code == 0,
        "exit_code": exit_code,
    }
    if error_type:
        properties["error_type"] = error_type

    if command in {"install", "uninstall", "doctor", "paths", "telemetry"}:
        properties["target_home_custom"] = bool(getattr(args, "target_home", None))

    if command == "install":
        if getattr(args, "bundle_archive", None):
            install_source = "archive"
        elif getattr(args, "bundle_dir", None):
            install_source = "directory"
        else:
            install_source = "bundled"
        properties.update(
            {
                "install_source": install_source,
                "force": bool(getattr(args, "force", False)),
                "dry_run": bool(getattr(args, "dry_run", False)),
            }
        )
    elif command in {"inspect", "capture", "reproduce", "clone", "queue"}:
        properties.update(
            {
                "breakpoint_count": len(getattr(args, "breakpoints", []) or []),
                "full_json": bool(getattr(args, "full_json", False)),
                "skip_runtime_trace": bool(getattr(args, "skip_runtime_trace", False)),
                "skip_html": bool(getattr(args, "skip_html", False)),
                "skip_screenshot": bool(getattr(args, "skip_screenshot", False)),
                "not_exact": bool(getattr(args, "not_exact", False)),
                "output_dir_set": bool(getattr(args, "output_dir", None)),
                "has_user_data_dir": bool(getattr(args, "user_data_dir", None)),
                "has_storage_state_path": bool(getattr(args, "storage_state_path", None)),
                "has_storage_state_output_path": bool(
                    getattr(args, "storage_state_output_path", None)
                ),
            }
        )
        if command == "queue":
            properties.update(
                {
                    "action": getattr(args, "queue_action", None),
                    "queue_root_set": bool(getattr(args, "queue_root", None)),
                    "job_id_set": bool(getattr(args, "job_id", None)),
                }
            )
    elif command == "har-replay":
        properties.update(
            {
                "request_count": len(getattr(args, "request", []) or []),
                "requests_json_set": bool(getattr(args, "requests_json", None)),
                "out_set": bool(getattr(args, "out", None)),
                "strict": bool(getattr(args, "strict", False)),
            }
        )
    elif command == "benchmark":
        properties.update(
            {
                "capture": bool(getattr(args, "capture", False)),
                "skip_runtime_trace": bool(getattr(args, "skip_runtime_trace", False)),
                "url_count": len(getattr(args, "url", []) or []),
                "urls_file_set": bool(getattr(args, "urls_file", None)),
                "corpus_name_set": bool(getattr(args, "corpus_name", None)),
            }
        )
    elif command == "telemetry":
        properties.update(
            {
                "action": getattr(args, "telemetry_action", None),
                "endpoint_argument_set": bool(getattr(args, "endpoint", None)),
                "reset_id": bool(getattr(args, "reset_id", False)),
            }
        )
    return {key: safe_telemetry_value(value) for key, value in properties.items()}


def emit_telemetry_event(
    home_root: Path,
    event: str,
    properties: dict[str, Any],
) -> None:
    config = load_telemetry_config(home_root)
    if not telemetry_is_enabled(config):
        return

    endpoint = telemetry_endpoint(config)
    log_path = telemetry_log_path()
    if not endpoint and not log_path:
        debug_telemetry("enabled, but no endpoint or log sink is configured")
        return

    try:
        anonymous_id, _created = ensure_anonymous_id(home_root, config)
        payload = {
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "event": event,
            "anonymous_id": anonymous_id,
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "app": {
                "name": PACKAGE_NAME,
                "version": package_version(),
                "plugin": PLUGIN_NAME,
            },
            "runtime": {
                "os": platform.system(),
                "os_release": platform.release(),
                "machine": platform.machine(),
                "python": platform.python_version(),
            },
            "properties": safe_telemetry_value(properties),
        }

        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a") as handle:
                handle.write(json.dumps(payload, sort_keys=True) + "\n")

        if endpoint:
            body = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(
                endpoint,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": f"{PACKAGE_NAME}/{package_version()}",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=telemetry_timeout_seconds()) as response:
                response.read(64)
    except Exception as exc:
        debug_telemetry(str(exc))


def load_capture_api() -> tuple[Any, Any, Any, Any, Any, Any, Any]:
    capture_root = repo_root() / "bundle" / PLUGIN_NAME / "mcp"
    if str(capture_root) not in sys.path:
        sys.path.insert(0, str(capture_root))
    from source_first_clone.acquisition import inspect_reference
    from source_first_clone.acquisition import detect_runtime_capabilities
    from source_first_clone.capture_bundle import capture_reference_bundle
    from source_first_clone.orchestration import clone_reference_url
    from source_first_clone.rebuild_scaffold import build_rebuild_scaffold
    from source_first_clone.reproduction import build_reproduction_bundle
    from source_first_clone.verification import verify_fidelity_report

    return (
        inspect_reference,
        detect_runtime_capabilities,
        capture_reference_bundle,
        build_reproduction_bundle,
        clone_reference_url,
        verify_fidelity_report,
        build_rebuild_scaffold,
    )


def load_job_queue_api() -> Any:
    capture_root = repo_root() / "bundle" / PLUGIN_NAME / "mcp"
    if str(capture_root) not in sys.path:
        sys.path.insert(0, str(capture_root))
    from source_first_clone.job_queue import JobQueue

    return JobQueue


def load_har_replay_api() -> tuple[Any, Any]:
    capture_root = repo_root() / "bundle" / PLUGIN_NAME / "mcp"
    if str(capture_root) not in sys.path:
        sys.path.insert(0, str(capture_root))
    from source_first_clone.har_replay import build_replay_report, load_request_specs

    return build_replay_report, load_request_specs


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
    if args.telemetry and args.no_telemetry:
        raise ValueError("Use either --telemetry or --no-telemetry, not both.")

    paths = build_paths(args.target_home)
    bundle_source, temp_root = resolve_bundle_source(args.bundle_dir, args.bundle_archive)

    try:
        copy_bundle(bundle_source, paths.plugin_root, force=args.force, dry_run=args.dry_run)
        install_marketplace_entry(paths, dry_run=args.dry_run)
        if args.telemetry or args.no_telemetry or args.telemetry_endpoint is not None:
            configure_telemetry(
                paths.home_root,
                enabled=True if args.telemetry else False if args.no_telemetry else None,
                endpoint=args.telemetry_endpoint,
                dry_run=args.dry_run,
            )
        if can_prompt_for_telemetry(args, paths.home_root):
            prompt_for_telemetry(paths.home_root, endpoint=args.telemetry_endpoint)
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
    if args.no_telemetry:
        configure_telemetry(paths.home_root, enabled=False, dry_run=args.dry_run)
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
    _inspect_reference, detect_runtime_capabilities, _capture_reference_bundle, _build_reproduction_bundle, _clone_reference_url, _verify_fidelity_report, _build_rebuild_scaffold = load_capture_api()
    print(json.dumps(detect_runtime_capabilities(), indent=2))
    return 0


def compact_site_profile(profile: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(profile, dict):
        return None
    route_hints = profile.get("route_hints", {}) if isinstance(profile.get("route_hints"), dict) else {}
    signals = profile.get("signals", {}) if isinstance(profile.get("signals"), dict) else {}
    return {
        "primary_surface": profile.get("primary_surface"),
        "confidence": profile.get("confidence"),
        "platform": profile.get("platform"),
        "route_hints": {
            "acquisition_profile": route_hints.get("acquisition_profile"),
            "renderer_route": route_hints.get("renderer_route"),
            "renderer_family": route_hints.get("renderer_family"),
            "critical_depths": route_hints.get("critical_depths"),
            "evidence_limit": route_hints.get("evidence_limit"),
            "evidence_note": route_hints.get("evidence_note"),
        },
        "signals": {
            "frame_blocked": signals.get("frame_blocked"),
            "app_shell": signals.get("app_shell"),
            "auth_detected": signals.get("auth_detected"),
            "app_gate_detected": signals.get("app_gate_detected"),
            "app_deep_link_detected": signals.get("app_deep_link_detected"),
            "app_promo_detected": signals.get("app_promo_detected"),
            "app_login_gate_detected": signals.get("app_login_gate_detected"),
            "app_gate_signals": signals.get("app_gate_signals"),
            "canvas_detected": signals.get("canvas_detected"),
            "shadow_dom_detected": signals.get("shadow_dom_detected"),
            "multi_frame": signals.get("multi_frame"),
            "longform": signals.get("longform"),
            "runtime_frameworks": signals.get("runtime_frameworks"),
            "exact_candidate_present": signals.get("exact_candidate_present"),
            "exact_candidate_kinds": signals.get("exact_candidate_kinds"),
        },
        "notes": profile.get("notes"),
    }


def compact_network_replay_readiness(network_summary: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(network_summary, dict):
        return {"status": "limited", "reasons": ["no network summary"], "next_action": "run runtime capture with network tracing"}
    request_count = int(network_summary.get("requestCount") or network_summary.get("request_count") or 0)
    response_count = int(network_summary.get("responseCount") or network_summary.get("response_count") or 0)
    failure_count = int(network_summary.get("failureCount") or network_summary.get("failure_count") or 0)
    har_entry_count = int(
        network_summary.get("harEntryCount")
        or network_summary.get("har_entry_count")
        or network_summary.get("harLikeEntryCount")
        or network_summary.get("har_like_entry_count")
        or 0
    )
    status_counts = network_summary.get("responseStatusCounts") or network_summary.get("response_status_counts") or {}
    if not isinstance(status_counts, dict):
        status_counts = {}
    auth_error_count = sum(int(status_counts.get(code) or 0) for code in ("401", "403"))
    rate_limit_count = int(status_counts.get("429") or 0)
    reasons: list[str] = []
    if request_count <= 0:
        reasons.append("no captured requests")
    if har_entry_count <= 0:
        reasons.append("no HAR entries")
    elif request_count and har_entry_count < request_count:
        reasons.append("HAR entries do not cover every request")
    if request_count and response_count < request_count:
        reasons.append("responses do not cover every request")
    if failure_count:
        reasons.append("failed requests were captured")
    if auth_error_count:
        reasons.append("auth/permission responses were captured")
    if rate_limit_count:
        reasons.append("rate-limit responses were captured")
    if request_count <= 0 or har_entry_count <= 0:
        status = "limited"
        next_action = "rerun capture with runtime network tracing before claiming replay parity"
    elif failure_count or auth_error_count or rate_limit_count:
        status = "needs-retry-or-session"
        next_action = "retry with supplied session, longer wait, or explicit network allowlist"
    elif reasons:
        status = "partial"
        next_action = "review HAR gaps before using responses as replay-grade evidence"
    else:
        status = "ready"
        next_action = "network evidence is sufficient for replay-oriented inspection"
    return {
        "status": status,
        "request_count": request_count,
        "response_count": response_count,
        "failure_count": failure_count,
        "har_entry_count": har_entry_count,
        "auth_error_count": auth_error_count,
        "rate_limit_count": rate_limit_count,
        "reasons": reasons,
        "next_action": next_action,
    }


def compact_capture_depth(captures: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(captures, dict):
        return None
    html_capture = captures.get("html", {}) if isinstance(captures.get("html"), dict) else {}
    accessibility_capture = captures.get("accessibility", {}) if isinstance(captures.get("accessibility"), dict) else {}
    dom_capture = captures.get("dom", {}) if isinstance(captures.get("dom"), dict) else {}
    css_capture = captures.get("cssAnalysis", {}) if isinstance(captures.get("cssAnalysis"), dict) else {}
    assets_capture = captures.get("assets", {}) if isinstance(captures.get("assets"), dict) else {}
    interactions_capture = captures.get("interactions", {}) if isinstance(captures.get("interactions"), dict) else {}
    interaction_trace_capture = captures.get("interactionTrace", {}) if isinstance(captures.get("interactionTrace"), dict) else {}
    screenshot_capture = captures.get("screenshot", {}) if isinstance(captures.get("screenshot"), dict) else {}
    network_capture = captures.get("network", {}) if isinstance(captures.get("network"), dict) else {}
    asset_summary = assets_capture.get("summary") if isinstance(assets_capture.get("summary"), dict) else None
    network_summary: dict[str, Any] | None = None
    if isinstance(network_capture.get("content"), dict):
        content_summary = network_capture["content"].get("summary")
        if isinstance(content_summary, dict):
            network_summary = dict(content_summary)
    if network_capture.get("available"):
        network_summary = dict(network_summary or {})
        network_summary.setdefault("requestCount", network_capture.get("requestCount"))
        network_summary.setdefault("responseCount", network_capture.get("responseCount"))
        network_summary.setdefault("failureCount", network_capture.get("failureCount"))
        network_summary.setdefault("frameUrlCount", network_capture.get("frameUrlCount"))
    if not any([html_capture, accessibility_capture, dom_capture, css_capture, asset_summary, interactions_capture, interaction_trace_capture, screenshot_capture, network_summary]):
        return None
    network_depth = None
    if isinstance(network_summary, dict):
        network_depth = {
            "request_count": network_summary.get("requestCount"),
            "response_count": network_summary.get("responseCount"),
            "failure_count": network_summary.get("failureCount"),
            "redirect_count": network_summary.get("redirectCount"),
            "navigation_request_count": network_summary.get("navigationRequestCount"),
            "post_data_request_count": network_summary.get("postDataRequestCount"),
            "service_worker_response_count": network_summary.get("serviceWorkerResponseCount"),
            "frame_url_count": network_summary.get("frameUrlCount"),
            "resource_type_counts": network_summary.get("resourceTypeCounts"),
            "response_status_counts": network_summary.get("responseStatusCounts"),
            "failure_reason_counts": network_summary.get("failureReasonCounts"),
            "timing_bucket_counts": network_summary.get("timingBucketCounts"),
            "request_header_presence_summary": network_summary.get("requestHeaderPresenceSummary"),
            "response_header_presence_summary": network_summary.get("responseHeaderPresenceSummary"),
            "response_body_availability": network_summary.get("responseBodyAvailability"),
            "frame_url_sample": network_summary.get("frameUrlSample"),
            "page_timings": network_summary.get("pageTimings"),
            "query_parameter_count": network_summary.get("queryParameterCount"),
            "request_cookie_count": network_summary.get("requestCookieCount"),
            "response_cookie_count": network_summary.get("responseCookieCount"),
            "request_header_bytes": network_summary.get("requestHeaderBytes"),
            "response_header_bytes": network_summary.get("responseHeaderBytes"),
            "request_body_bytes": network_summary.get("requestBodyBytes"),
            "response_body_bytes": network_summary.get("responseBodyBytes"),
            "response_redirect_count": network_summary.get("responseRedirectCount"),
            "har_export_path": network_summary.get("harExportPath"),
            "har_page_count": network_summary.get("harPageCount"),
            "har_entry_count": network_summary.get("harEntryCount"),
            "har_like_entry_count": network_summary.get("harLikeEntryCount"),
            "har_like_page_count": network_summary.get("harLikePageCount"),
            "replay_readiness": compact_network_replay_readiness(network_summary),
        }
    return {
        "html": {
            "available": html_capture.get("available"),
            "length": html_capture.get("length"),
        },
        "accessibility": {
            "available": accessibility_capture.get("available"),
        },
        "dom": {
            "node_count": dom_capture.get("nodeCount"),
            "shadow_root_count": dom_capture.get("shadowRootCount"),
            "frame_document_count": dom_capture.get("frameDocumentCount"),
            "inaccessible_frame_count": dom_capture.get("inaccessibleFrameCount"),
        },
        "css": {
            "stylesheet_count": css_capture.get("stylesheetCount"),
            "accessible_stylesheet_count": css_capture.get("accessibleStylesheetCount"),
            "linked_stylesheet_count": css_capture.get("linkedStylesheetCount"),
            "preload_link_count": css_capture.get("preloadLinkCount"),
            "font_face_rule_count": css_capture.get("fontFaceRuleCount"),
            "inline_style_tag_count": css_capture.get("inlineStyleTagCount"),
            "style_attribute_count": css_capture.get("styleAttributeCount"),
        },
        "network": network_depth,
        "assets": asset_summary,
        "interactions": {
            "available": interactions_capture.get("available"),
            "entry_count": interactions_capture.get("entryCount"),
        },
        "interaction_trace": {
            "available": interaction_trace_capture.get("available"),
            "step_count": interaction_trace_capture.get("stepCount"),
            "replayed_count": interaction_trace_capture.get("replayedCount"),
        },
        "screenshot": {
            "available": screenshot_capture.get("available"),
            "byte_length": screenshot_capture.get("byteLength"),
            "mime_type": screenshot_capture.get("mimeType"),
        },
    }


def command_inspect(args: argparse.Namespace) -> int:
    inspect_reference, _detect_runtime_capabilities, _capture_reference_bundle, _build_reproduction_bundle, _clone_reference_url, _verify_fidelity_report, _build_rebuild_scaffold = load_capture_api()
    result = inspect_reference(args.url, timeout_seconds=args.timeout_seconds)
    if args.full_json:
        print(json.dumps(result, indent=2))
        return 0
    payload = {
        "url": result.get("url"),
        "final_url": result.get("final_url"),
        "status": result.get("status"),
        "title": result.get("title"),
        "platform": result.get("platform"),
        "frame_policy": result.get("frame_policy"),
        "source_signals": result.get("source_signals"),
        "site_profile": compact_site_profile(result.get("site_profile")),
        "candidate_count": len(result.get("candidate_urls") or []),
        "candidate_sample": (result.get("candidate_urls") or [])[:12],
    }
    print(json.dumps(payload, indent=2))
    return 0


def compact_capture_result(result: dict[str, Any]) -> dict[str, Any]:
    summary = json.loads(json.dumps(result))
    runtime = summary.get("runtime", {})
    captures = runtime.get("captures", {}) if isinstance(runtime, dict) else {}
    static_root = summary.get("static", {}) if isinstance(summary.get("static"), dict) else {}
    summary["site_profile"] = compact_site_profile(summary.get("site_profile") or static_root.get("site_profile"))
    summary["capture_depth"] = compact_capture_depth(captures)
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
    network_artifact = captured_artifacts.get("network") if isinstance(captured_artifacts.get("network"), dict) else {}
    artifact_html = captured_artifacts.get("html")
    if isinstance(artifact_html, dict):
        artifact_html.pop("content", None)
    artifact_trace = captured_artifacts.get("interaction_trace")
    if isinstance(artifact_trace, dict):
        artifact_trace.pop("content", None)
    if isinstance(network_artifact, dict):
        capture_depth = summary.get("capture_depth")
        if isinstance(capture_depth, dict):
            network_depth = capture_depth.get("network")
            if not isinstance(network_depth, dict):
                network_depth = {}
                capture_depth["network"] = network_depth
            for key in (
                "request_count",
                "response_count",
                "failure_count",
                "redirect_count",
                "frame_url_count",
                "timing_bucket_counts",
                "request_header_presence_summary",
                "response_header_presence_summary",
                "response_body_availability",
                "page_timings",
                "query_parameter_count",
                "request_cookie_count",
                "response_cookie_count",
                "request_header_bytes",
                "response_header_bytes",
                "request_body_bytes",
                "response_body_bytes",
                "response_redirect_count",
                "har_export_path",
                "har_like_path",
                "har_page_count",
                "har_entry_count",
                "har_like_page_count",
                "har_like_entry_count",
            ):
                if network_artifact.get(key) is not None:
                    network_depth[key] = network_artifact.get(key)
    breakpoint_summary = summary.get("breakpoints")
    if isinstance(breakpoint_summary, dict):
        variants = breakpoint_summary.get("variants")
        if isinstance(variants, list):
            breakpoint_summary["variant_count"] = len(variants)
            breakpoint_summary["variant_sample"] = variants[:3]
            breakpoint_summary.pop("variants", None)
    static = summary.get("static", {})
    if isinstance(static, dict):
        static["site_profile"] = compact_site_profile(static.get("site_profile"))
    return summary


def command_capture(args: argparse.Namespace) -> int:
    _inspect_reference, _detect_runtime_capabilities, capture_reference_bundle, _build_reproduction_bundle, _clone_reference_url, _verify_fidelity_report, _build_rebuild_scaffold = load_capture_api()
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
    summary["site_profile"] = compact_site_profile(summary.get("site_profile"))
    capture_bundle = summary.get("capture_bundle")
    if isinstance(capture_bundle, dict):
        runtime = capture_bundle.get("runtime", {})
        captures = runtime.get("captures", {}) if isinstance(runtime, dict) else {}
        summary["capture_depth"] = compact_capture_depth(captures)
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
        iteration = repair_pass.get("iteration")
        if isinstance(repair_verify, dict):
            compact_repair["self_verify"] = {
                "status": repair_verify.get("status"),
                "overall_ready_for_exact_clone": repair_verify.get("overall_ready_for_exact_clone"),
                "preferred_renderer": repair_verify.get("preferred_renderer"),
                "root_report": repair_verify.get("root_report"),
                "persisted": repair_verify.get("persisted"),
            }
        if isinstance(iteration, dict):
            compact_repair["iteration"] = {
                "index": iteration.get("index"),
                "source_score": iteration.get("source_score"),
                "score": iteration.get("score"),
                "score_delta": iteration.get("score_delta"),
                "meets_minimum_delta": iteration.get("meets_minimum_delta"),
                "overall_ready_for_exact_clone": iteration.get("overall_ready_for_exact_clone"),
            }
        summary["repair_pass"] = compact_repair
    repair_passes = summary.get("repair_passes")
    if isinstance(repair_passes, list):
        condensed_passes = []
        for item in repair_passes[:3]:
            if not isinstance(item, dict):
                continue
            condensed_passes.append(
                {
                    "summary": compact_rebuild_scaffold_summary(item).get("summary"),
                    "iteration": item.get("iteration"),
                    "self_verify": {
                        "overall_ready_for_exact_clone": ((item.get("self_verify") or {}).get("overall_ready_for_exact_clone")),
                        "preferred_renderer": ((item.get("self_verify") or {}).get("preferred_renderer")),
                        "root_report": ((item.get("self_verify") or {}).get("root_report")),
                    },
                }
            )
        summary["repair_passes"] = condensed_passes
    repair_loop = summary.get("repair_loop")
    if isinstance(repair_loop, dict):
        summary["repair_loop"] = {
            "status": repair_loop.get("status"),
            "pass_count": repair_loop.get("pass_count"),
            "max_passes": repair_loop.get("max_passes"),
            "minimum_score_delta": repair_loop.get("minimum_score_delta"),
            "initial_score": repair_loop.get("initial_score"),
            "best_score": repair_loop.get("best_score"),
            "best_pass_index": repair_loop.get("best_pass_index"),
            "overall_ready_for_exact_clone": repair_loop.get("overall_ready_for_exact_clone"),
            "stop_reason": repair_loop.get("stop_reason"),
            "persisted": repair_loop.get("persisted"),
            "note": repair_loop.get("note"),
        }
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
    _inspect_reference, _detect_runtime_capabilities, capture_reference_bundle, build_reproduction_bundle, _clone_reference_url, _verify_fidelity_report, _build_rebuild_scaffold = load_capture_api()
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
        summary["site_profile"] = summary["capture_bundle"].get("site_profile")
        summary["capture_depth"] = summary["capture_bundle"].get("capture_depth")
    reproduction = summary.get("reproduction")
    if isinstance(reproduction, dict) and not summary.get("site_profile"):
        summary["site_profile"] = reproduction.get("site_profile")
    return summary


def command_clone(args: argparse.Namespace) -> int:
    _inspect_reference, _detect_runtime_capabilities, _capture_reference_bundle, _build_reproduction_bundle, clone_reference_url, _verify_fidelity_report, _build_rebuild_scaffold = load_capture_api()
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
    _inspect_reference, _detect_runtime_capabilities, _capture_reference_bundle, _build_reproduction_bundle, _clone_reference_url, _verify_fidelity_report, build_rebuild_scaffold = load_capture_api()
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
    _inspect_reference, _detect_runtime_capabilities, _capture_reference_bundle, _build_reproduction_bundle, _clone_reference_url, verify_fidelity_report, _build_rebuild_scaffold = load_capture_api()
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


def command_benchmark(args: argparse.Namespace) -> int:
    script_path = repo_root() / "scripts" / "benchmark_routes.py"
    command: list[str] = [sys.executable, str(script_path)]
    for url in args.url or []:
        command.extend(["--url", url])
    if args.urls_file:
        command.extend(["--urls-file", args.urls_file])
    if args.corpus_name:
        command.extend(["--corpus-name", args.corpus_name])
    command.extend(["--out", args.out])
    command.extend(["--timeout-seconds", str(args.timeout_seconds)])
    if args.capture:
        command.append("--capture")
    if args.skip_runtime_trace:
        command.append("--skip-runtime-trace")
    completed = subprocess.run(command, check=False)
    return completed.returncode


def queue_clone_args_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "timeout_seconds": int(args.timeout_seconds),
        "wait_seconds": int(args.wait_seconds),
        "user_data_dir": args.user_data_dir,
        "storage_state_path": args.storage_state_path,
        "storage_state_output_path": args.storage_state_output_path,
        "capture_html": not bool(args.skip_html),
        "capture_screenshot": not bool(args.skip_screenshot),
        "viewport_width": int(args.viewport_width),
        "viewport_height": int(args.viewport_height),
        "breakpoint_profiles": args.breakpoints,
        "exact_requested": not bool(args.not_exact),
        "license_text": args.license_text,
        "source_signals": args.source_signals,
        "include_runtime_trace": not bool(args.skip_runtime_trace),
    }


def command_queue(args: argparse.Namespace) -> int:
    JobQueue = load_job_queue_api()
    queue = JobQueue(
        args.queue_root,
        max_attempts=int(getattr(args, "max_attempts", 2) or 2),
        retry_delay_seconds=int(getattr(args, "retry_delay_seconds", 30) or 30),
    )
    action = args.queue_action
    if action == "enqueue":
        job = queue.enqueue(
            args.url,
            output_dir=args.output_dir,
            clone_args=queue_clone_args_from_args(args),
            metadata={"cli": "web-embedding queue enqueue"},
            max_attempts=args.max_attempts,
            retry_delay_seconds=args.retry_delay_seconds,
        )
        print(json.dumps(job, indent=2))
        return 0
    if action == "list":
        jobs = queue.list(states=args.state)
        print(json.dumps({"queue_root": str(queue.queue_root), "count": len(jobs), "jobs": jobs}, indent=2))
        return 0
    if action == "status":
        print(json.dumps(queue.load(args.job_id), indent=2))
        return 0
    if action == "cancel":
        print(json.dumps(queue.cancel(args.job_id, reason=args.reason), indent=2))
        return 0
    if action == "run-next":
        job = queue.run_next(worker_id=args.worker_id)
        print(json.dumps({"processed": job is not None, "job": job}, indent=2))
        return 0
    if action == "run-job":
        print(json.dumps(queue.run_job(args.job_id, worker_id=args.worker_id), indent=2))
        return 0
    raise ValueError(f"Unknown queue action: {action}")


def command_har_replay(args: argparse.Namespace) -> int:
    build_replay_report, load_request_specs = load_har_replay_api()
    requests: list[dict[str, Any]] = []
    if args.requests_json:
        requests.extend(load_request_specs(args.requests_json))
    for method, url in args.request or []:
        requests.append({"method": method, "url": url})
    report = build_replay_report(
        args.har,
        requests=requests,
        output_path=args.out,
        consume=not bool(args.no_consume),
    )
    payload = report if args.full_json else {
        "source": report.get("source"),
        "summary": report.get("summary"),
        "path": report.get("path"),
    }
    print(json.dumps(payload, indent=2))
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    if args.strict and int(summary.get("missing") or 0) > 0:
        return 1
    return 0


def command_telemetry(args: argparse.Namespace) -> int:
    paths = build_paths(args.target_home)
    if args.telemetry_action == "enable":
        configure_telemetry(
            paths.home_root,
            enabled=True,
            endpoint=args.endpoint,
            reset_id=args.reset_id,
        )
        status = telemetry_status_payload(paths.home_root)
        print(json.dumps(status, indent=2))
        return 0
    if args.telemetry_action == "disable":
        configure_telemetry(
            paths.home_root,
            enabled=False,
            reset_id=args.reset_id,
        )
        status = telemetry_status_payload(paths.home_root)
        print(json.dumps(status, indent=2))
        return 0
    if args.telemetry_action == "reset-id":
        configure_telemetry(paths.home_root, reset_id=True)
        status = telemetry_status_payload(paths.home_root)
        print(json.dumps(status, indent=2))
        return 0
    if args.telemetry_action == "status":
        print(json.dumps(telemetry_status_payload(paths.home_root), indent=2))
        return 0
    raise ValueError(f"Unknown telemetry action: {args.telemetry_action}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install or inspect the source-first clone plugin.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser("install", help="Install the plugin bundle.")
    install_parser.add_argument("--target-home", help="Override the home root used for installation.")
    install_parser.add_argument("--bundle-dir", help="Use a local bundle directory instead of the repo bundle.")
    install_parser.add_argument("--bundle-archive", help="Use a release tarball instead of a bundle directory.")
    install_parser.add_argument("--force", action="store_true", help="Overwrite an existing install.")
    install_parser.add_argument("--dry-run", action="store_true", help="Print actions without writing files.")
    install_parser.add_argument("--telemetry", action="store_true", help="Opt in to anonymous usage telemetry.")
    install_parser.add_argument("--no-telemetry", action="store_true", help="Opt out of anonymous usage telemetry.")
    install_parser.add_argument("--telemetry-endpoint", help="JSON POST endpoint for opt-in telemetry events.")
    install_parser.set_defaults(func=command_install)

    uninstall_parser = subparsers.add_parser("uninstall", help="Remove the installed plugin.")
    uninstall_parser.add_argument("--target-home", help="Override the home root used for removal.")
    uninstall_parser.add_argument("--dry-run", action="store_true", help="Print actions without writing files.")
    uninstall_parser.add_argument("--no-telemetry", action="store_true", help="Disable telemetry during removal.")
    uninstall_parser.set_defaults(func=command_uninstall)

    doctor_parser = subparsers.add_parser("doctor", help="Check the install state.")
    doctor_parser.add_argument("--target-home", help="Override the home root used for inspection.")
    doctor_parser.set_defaults(func=command_doctor)

    paths_parser = subparsers.add_parser("paths", help="Print the important install paths.")
    paths_parser.add_argument("--target-home", help="Override the home root used for inspection.")
    paths_parser.set_defaults(func=command_paths)

    capabilities_parser = subparsers.add_parser("capabilities", help="Detect runtime capture dependencies.")
    capabilities_parser.set_defaults(func=command_capabilities)

    inspect_parser = subparsers.add_parser("inspect", help="Inspect a URL and print its universal site profile and route hints.")
    inspect_parser.add_argument("--url", required=True, help="Reference URL to inspect.")
    inspect_parser.add_argument("--timeout-seconds", type=int, default=20, help="Static fetch timeout in seconds.")
    inspect_parser.add_argument("--full-json", action="store_true", help="Print the full raw inspection payload.")
    inspect_parser.set_defaults(func=command_inspect)

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

    benchmark_parser = subparsers.add_parser("benchmark", help="Run the universal route benchmark wrapper.")
    benchmark_parser.add_argument("--url", action="append", default=[], help="URL to include. Repeat for multiple URLs.")
    benchmark_parser.add_argument("--urls-file", help="Text file with one URL per line.")
    benchmark_parser.add_argument("--corpus-name", help="Optional label for the benchmark corpus or URL set.")
    benchmark_parser.add_argument("--out", required=True, help="Output directory for the benchmark run.")
    benchmark_parser.add_argument("--timeout-seconds", type=int, default=20, help="Static fetch timeout in seconds.")
    benchmark_parser.add_argument("--capture", action="store_true", help="Also persist a shallow capture bundle per URL.")
    benchmark_parser.add_argument("--skip-runtime-trace", action="store_true", help="When capturing, skip deep runtime trace and keep the benchmark static-only.")
    benchmark_parser.set_defaults(func=command_benchmark)

    queue_parser = subparsers.add_parser("queue", help="Manage the filesystem-backed async clone job queue.")
    queue_subparsers = queue_parser.add_subparsers(dest="queue_action", required=True)

    queue_enqueue_parser = queue_subparsers.add_parser("enqueue", help="Persist a clone job for later worker execution.")
    queue_enqueue_parser.add_argument("--queue-root", required=True, help="Directory containing queue job JSON files.")
    queue_enqueue_parser.add_argument("--url", required=True, help="Reference URL to clone.")
    queue_enqueue_parser.add_argument("--output-dir", help="Directory where this job should write clone artifacts.")
    queue_enqueue_parser.add_argument("--timeout-seconds", type=int, default=20, help="Static fetch timeout in seconds.")
    queue_enqueue_parser.add_argument("--wait-seconds", type=int, default=8, help="Browser settle time after navigation.")
    queue_enqueue_parser.add_argument("--user-data-dir", help="Persistent browser profile directory for Playwright.")
    queue_enqueue_parser.add_argument("--storage-state-path", help="Existing Playwright storage state JSON to apply.")
    queue_enqueue_parser.add_argument("--storage-state-output-path", help="Where to export Playwright storage state JSON.")
    queue_enqueue_parser.add_argument("--viewport-width", type=int, default=1440, help="Capture viewport width.")
    queue_enqueue_parser.add_argument("--viewport-height", type=int, default=1200, help="Capture viewport height.")
    queue_enqueue_parser.add_argument("--breakpoints", nargs="*", choices=["desktop", "tablet", "mobile"], default=[], help="Additional breakpoint profiles to capture alongside the primary viewport.")
    queue_enqueue_parser.add_argument("--license-text", help="Optional license text for policy classification.")
    queue_enqueue_parser.add_argument("--source-signals", nargs="*", default=[], help="Optional source/reuse hints such as remix or export.")
    queue_enqueue_parser.add_argument("--skip-runtime-trace", action="store_true", help="Skip Playwright runtime capture.")
    queue_enqueue_parser.add_argument("--skip-html", action="store_true", help="Do not save runtime HTML.")
    queue_enqueue_parser.add_argument("--skip-screenshot", action="store_true", help="Do not save runtime screenshot.")
    queue_enqueue_parser.add_argument("--not-exact", action="store_true", help="Mark the request as approximate instead of exact.")
    queue_enqueue_parser.add_argument("--max-attempts", type=int, default=2, help="Maximum worker attempts before terminal failure.")
    queue_enqueue_parser.add_argument("--retry-delay-seconds", type=int, default=30, help="Initial retry delay for retryable failures.")
    queue_enqueue_parser.set_defaults(func=command_queue)

    queue_list_parser = queue_subparsers.add_parser("list", help="List queued jobs.")
    queue_list_parser.add_argument("--queue-root", required=True, help="Directory containing queue job JSON files.")
    queue_list_parser.add_argument("--state", action="append", default=[], help="Filter by state. Repeat for multiple states.")
    queue_list_parser.set_defaults(func=command_queue)

    queue_status_parser = queue_subparsers.add_parser("status", help="Print one job record.")
    queue_status_parser.add_argument("--queue-root", required=True, help="Directory containing queue job JSON files.")
    queue_status_parser.add_argument("--job-id", required=True, help="Job id to load.")
    queue_status_parser.set_defaults(func=command_queue)

    queue_cancel_parser = queue_subparsers.add_parser("cancel", help="Cancel a queued or retry-wait job.")
    queue_cancel_parser.add_argument("--queue-root", required=True, help="Directory containing queue job JSON files.")
    queue_cancel_parser.add_argument("--job-id", required=True, help="Job id to cancel.")
    queue_cancel_parser.add_argument("--reason", help="Optional cancellation reason.")
    queue_cancel_parser.set_defaults(func=command_queue)

    queue_run_next_parser = queue_subparsers.add_parser("run-next", help="Run the next due queued or retry-wait job.")
    queue_run_next_parser.add_argument("--queue-root", required=True, help="Directory containing queue job JSON files.")
    queue_run_next_parser.add_argument("--worker-id", help="Stable worker id for job history.")
    queue_run_next_parser.set_defaults(func=command_queue)

    queue_run_job_parser = queue_subparsers.add_parser("run-job", help="Run one queued or due retry-wait job by id.")
    queue_run_job_parser.add_argument("--queue-root", required=True, help="Directory containing queue job JSON files.")
    queue_run_job_parser.add_argument("--job-id", required=True, help="Job id to run.")
    queue_run_job_parser.add_argument("--worker-id", help="Stable worker id for job history.")
    queue_run_job_parser.set_defaults(func=command_queue)

    har_replay_parser = subparsers.add_parser("har-replay", help="Replay request specs against a HAR or webEmbedding network manifest.")
    har_replay_parser.add_argument("--har", required=True, help="Path to network/har.json, network/har-like.json, or network/manifest.json.")
    har_replay_parser.add_argument("--request", nargs=2, action="append", metavar=("METHOD", "URL"), default=[], help="Request to replay. Repeat for multiple requests.")
    har_replay_parser.add_argument("--requests-json", help="JSON array or object with a requests array.")
    har_replay_parser.add_argument("--out", help="Optional path for replay-report.json.")
    har_replay_parser.add_argument("--strict", action="store_true", help="Exit non-zero when any requested replay is missing.")
    har_replay_parser.add_argument("--no-consume", action="store_true", help="Do not advance duplicate-entry cursors while matching requests.")
    har_replay_parser.add_argument("--full-json", action="store_true", help="Print the full replay report.")
    har_replay_parser.set_defaults(func=command_har_replay)

    telemetry_parser = subparsers.add_parser("telemetry", help="Manage opt-in telemetry settings.")
    telemetry_subparsers = telemetry_parser.add_subparsers(dest="telemetry_action", required=True)

    telemetry_status_parser = telemetry_subparsers.add_parser("status", help="Print telemetry status.")
    telemetry_status_parser.add_argument("--target-home", help="Override the home root used for telemetry config.")
    telemetry_status_parser.set_defaults(func=command_telemetry)

    telemetry_enable_parser = telemetry_subparsers.add_parser("enable", help="Enable anonymous telemetry.")
    telemetry_enable_parser.add_argument("--target-home", help="Override the home root used for telemetry config.")
    telemetry_enable_parser.add_argument("--endpoint", help="JSON POST endpoint for telemetry events.")
    telemetry_enable_parser.add_argument("--reset-id", action="store_true", help="Generate a new anonymous install id.")
    telemetry_enable_parser.set_defaults(func=command_telemetry)

    telemetry_disable_parser = telemetry_subparsers.add_parser("disable", help="Disable telemetry.")
    telemetry_disable_parser.add_argument("--target-home", help="Override the home root used for telemetry config.")
    telemetry_disable_parser.add_argument("--reset-id", action="store_true", help="Generate a new anonymous install id while disabling.")
    telemetry_disable_parser.set_defaults(func=command_telemetry)

    telemetry_reset_parser = telemetry_subparsers.add_parser("reset-id", help="Generate a new anonymous install id.")
    telemetry_reset_parser.add_argument("--target-home", help="Override the home root used for telemetry config.")
    telemetry_reset_parser.set_defaults(func=command_telemetry)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    exit_code = 1
    error_type: str | None = None
    try:
        exit_code = args.func(args)
    except Exception as exc:  # pragma: no cover - thin CLI wrapper
        error_type = type(exc).__name__
        print(f"error: {exc}", file=sys.stderr)
        exit_code = 1
    emit_telemetry_event(
        telemetry_home_root(args),
        f"{args.command}_completed",
        telemetry_properties_for_args(args, exit_code=exit_code, error_type=error_type),
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
