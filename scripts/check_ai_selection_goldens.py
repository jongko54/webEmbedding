#!/usr/bin/env python3
"""Validate the webEmbedding AI auto-selection golden prompt contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_MCP_ROOT = REPO_ROOT / "bundle" / "source-first-clone" / "mcp"


def load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return payload


def load_known_tools() -> set[str]:
    sys.path.insert(0, str(PLUGIN_MCP_ROOT))
    from source_first_clone.tools import TOOLS

    return {str(tool.get("name")) for tool in TOOLS if isinstance(tool, dict) and tool.get("name")}


def validate_cases(cases_path: Path) -> list[str]:
    payload = load_json_object(cases_path)
    failures: list[str] = []
    known_tools = load_known_tools()
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        return ["cases must be a non-empty array"]

    seen_ids: set[str] = set()
    positive_count = 0
    negative_count = 0

    for index, case in enumerate(cases):
        label = f"cases[{index}]"
        if not isinstance(case, dict):
            failures.append(f"{label} must be an object")
            continue

        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            failures.append(f"{label}.id must be a non-empty string")
            case_id = label
        elif case_id in seen_ids:
            failures.append(f"{case_id}: duplicate id")
        seen_ids.add(str(case_id))

        prompt = case.get("prompt")
        if not isinstance(prompt, str) or len(prompt.strip()) < 20:
            failures.append(f"{case_id}: prompt must be a meaningful string")

        polarity = case.get("polarity")
        decision = case.get("expected_trigger_decision")
        tool = case.get("expected_primary_tool")
        checks = case.get("deterministic_checks")

        if polarity == "positive":
            positive_count += 1
            if decision != "trigger":
                failures.append(f"{case_id}: positive cases must trigger")
            if tool not in known_tools:
                failures.append(f"{case_id}: expected_primary_tool must be a known MCP tool, got {tool!r}")
        elif polarity == "negative":
            negative_count += 1
            if decision != "do_not_trigger":
                failures.append(f"{case_id}: negative cases must not trigger")
            if tool != "none":
                failures.append(f"{case_id}: negative cases must use expected_primary_tool=none")
        else:
            failures.append(f"{case_id}: polarity must be positive or negative")

        if not isinstance(checks, list) or not checks or not all(isinstance(item, str) for item in checks):
            failures.append(f"{case_id}: deterministic_checks must be a non-empty string array")

    if positive_count < 8:
        failures.append(f"expected at least 8 positive cases, got {positive_count}")
    if negative_count < 8:
        failures.append(f"expected at least 8 negative cases, got {negative_count}")

    return failures


def validate_manifest_alignment() -> list[str]:
    failures: list[str] = []
    package = load_json_object(REPO_ROOT / "package.json")
    server = load_json_object(REPO_ROOT / "server.json")
    codex = load_json_object(REPO_ROOT / "bundle" / "source-first-clone" / ".codex-plugin" / "plugin.json")
    claude = load_json_object(REPO_ROOT / "bundle" / "source-first-clone" / ".claude-plugin" / "plugin.json")

    version = package.get("version")
    if package.get("mcpName") != server.get("name"):
        failures.append("package.json mcpName must match server.json name")
    if server.get("version") != version:
        failures.append("server.json version must match package.json version")

    packages = server.get("packages")
    if not isinstance(packages, list) or not packages:
        failures.append("server.json packages must be non-empty")
    else:
        npm_package = packages[0]
        if npm_package.get("identifier") != package.get("name"):
            failures.append("server.json package identifier must match package.json name")
        if npm_package.get("version") != version:
            failures.append("server.json package version must match package.json version")
        if npm_package.get("transport", {}).get("type") != "stdio":
            failures.append("server.json package transport must be stdio")

    remotes = server.get("remotes", [])
    if remotes is not None:
        if not isinstance(remotes, list):
            failures.append("server.json remotes must be an array when present")
        for index, remote in enumerate(remotes if isinstance(remotes, list) else []):
            if not isinstance(remote, dict):
                failures.append(f"server.json remotes[{index}] must be an object")
                continue
            if remote.get("type") not in {"streamable-http", "sse"}:
                failures.append(f"server.json remotes[{index}].type must be streamable-http or sse")
            url = remote.get("url")
            if not isinstance(url, str) or not url.startswith("https://"):
                failures.append(f"server.json remotes[{index}].url must be an https URL")

    for name, manifest in (("Codex", codex), ("Claude", claude)):
        if manifest.get("version") != version:
            failures.append(f"{name} plugin version must match package.json version")

    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", required=True, help="Path to the golden prompt JSON file.")
    args = parser.parse_args(argv)

    failures = validate_cases(Path(args.cases).resolve())
    failures.extend(validate_manifest_alignment())

    if failures:
        print("AI selection golden validation failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print("AI selection golden validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
