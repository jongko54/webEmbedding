#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class DemoCase:
    case_dir: Path
    case_id: str
    label: str
    source_url: str
    source_host: str
    source_image: Path
    clone_image: Path
    clone_page: Path
    score: float | int | None
    verdict: str
    primary_surface: str
    renderer_route: str
    renderer_family: str
    acquisition_profile: str
    policy_mode: str
    metrics_by_score: dict[str, float]
    metrics_by_similarity: dict[str, float]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def resolve_path(path_value: str | Path, *, base_dir: Path | None = None) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = (base_dir or ROOT) / path
    return path.resolve()


def relative_url(from_dir: Path, target_path: Path) -> str:
    relative = os.path.relpath(target_path, start=from_dir)
    return quote(relative.replace(os.sep, "/"), safe="/-_.~")


def host_from_url(url: str) -> str:
    return urlparse(url).netloc.removeprefix("www.")


def label_from_url(url: str, fallback: str) -> str:
    host = host_from_url(url)
    return host or fallback


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "demo-case"


def metric_map(verification: dict[str, Any], field_name: str) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for check in verification.get("checks", []):
        name = check.get("name")
        value = check.get(field_name)
        if isinstance(name, str) and isinstance(value, (int, float)):
            metrics[name] = float(value)
    return metrics


def _find_renderer_dir(self_verify_dir: Path, renderer_id: str | None) -> Path:
    renderers_dir = self_verify_dir / "renderers"
    if renderer_id:
        candidate = renderers_dir / renderer_id
        if not (candidate / "verification.json").is_file():
            raise FileNotFoundError(f"Missing verification.json for renderer `{renderer_id}` in {renderers_dir}")
        return candidate

    candidates = sorted(
        candidate
        for candidate in renderers_dir.iterdir()
        if candidate.is_dir() and (candidate / "verification.json").is_file()
    )
    if not candidates:
        raise FileNotFoundError(f"No renderer verification artifacts found in {renderers_dir}")
    return candidates[0]


def load_demo_case(case_dir: str | Path, *, renderer_id: str | None = None, label: str | None = None) -> DemoCase:
    resolved_case_dir = resolve_path(case_dir)
    capture = load_json(resolved_case_dir / "capture.json")
    inspect_path = resolved_case_dir / "static" / "inspect.json"
    inspect = load_json(inspect_path) if inspect_path.is_file() else {}
    summary_path = resolved_case_dir / "reproduction" / "self-verify" / "summary.json"
    summary = load_json(summary_path)
    renderer_dir = _find_renderer_dir(summary_path.parent, renderer_id)
    verification = load_json(renderer_dir / "verification.json")
    rendered_capture_path = summary.get("rendered_capture_manifest") or renderer_dir / "rendered-capture" / "capture.json"
    rendered_capture = load_json(resolve_path(rendered_capture_path, base_dir=summary_path.parent))

    source_url = str(capture.get("url") or "")
    source_host = host_from_url(source_url)
    route_hints = inspect.get("site_profile", {}).get("route_hints", {}) if isinstance(inspect, dict) else {}
    policy = capture.get("policy", {}) if isinstance(capture.get("policy"), dict) else {}
    primary_surface = inspect.get("site_profile", {}).get("primary_surface") if isinstance(inspect, dict) else None
    case_label = label or label_from_url(source_url, resolved_case_dir.name)
    case_id = slugify(source_host or resolved_case_dir.name)

    return DemoCase(
        case_dir=resolved_case_dir,
        case_id=case_id,
        label=case_label,
        source_url=source_url,
        source_host=source_host or resolved_case_dir.name,
        source_image=resolve_path(
            capture["bundle"]["captured_artifacts"]["screenshot"]["path"],
            base_dir=resolved_case_dir,
        ),
        clone_image=resolve_path(
            rendered_capture["bundle"]["captured_artifacts"]["screenshot"]["path"],
            base_dir=renderer_dir / "rendered-capture",
        ),
        clone_page=(resolved_case_dir / "reproduction" / "rebuild" / "app-preview.html").resolve(),
        score=summary.get("score"),
        verdict=str(summary.get("root_report", {}).get("verdict") or "unknown"),
        primary_surface=str(primary_surface or "unknown"),
        renderer_route=str(route_hints.get("renderer_route") or "unknown"),
        renderer_family=str(route_hints.get("renderer_family") or "unknown"),
        acquisition_profile=str(route_hints.get("acquisition_profile") or "unknown"),
        policy_mode=str(policy.get("mode") or "unknown"),
        metrics_by_score=metric_map(verification, "score"),
        metrics_by_similarity=metric_map(verification, "similarity"),
    )
