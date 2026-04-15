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


def resolve_bundle_source(bundle_dir: str | None, bundle_archive: str | None) -> tuple[Path, str | None]:
    if bundle_dir and bundle_archive:
        raise ValueError("Use either --bundle-dir or --bundle-archive, not both.")

    if bundle_archive:
        archive_path = Path(bundle_archive).expanduser().resolve()
        if not archive_path.exists():
            raise FileNotFoundError(f"Bundle archive does not exist: {archive_path}")
        temp_root = tempfile.mkdtemp(prefix="web-embedding-")
        with tarfile.open(archive_path, "r:gz") as archive:
            archive.extractall(temp_root)
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
