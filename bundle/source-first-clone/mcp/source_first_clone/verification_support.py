"""Helpers for bounded fidelity verification."""

from __future__ import annotations

import json
import math
import re
import struct
import zlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CORE_ARTIFACTS = (
    "screenshot",
    "dom_snapshot",
    "computed_styles",
    "interaction_states",
    "interaction_trace",
)

CHECK_WEIGHTS = {
    "screenshot": 40,
    "dom snapshot": 20,
    "computed styles": 20,
    "interaction states": 10,
    "interaction trace": 10,
}

SCREENSHOT_GRID_SIZE = 16
SCREENSHOT_HASH_GRID_SIZE = 8
SCREENSHOT_HISTOGRAM_BINS = 12
SCREENSHOT_QUADRANT_COUNT = 4
SCREENSHOT_BAND_COUNT = 3
SCREENSHOT_PIXEL_DIFF_GRID_SIZE = 24
SCREENSHOT_PIXEL_LUMA_SCALE = 34.0
SCREENSHOT_PIXEL_RGB_SCALE = 42.0
SCREENSHOT_PIXEL_MISMATCH_SCALE = 0.34


@dataclass
class ArtifactRecord:
    available: bool = False
    path: str | None = None
    content: Any = None
    metadata: dict[str, Any] | None = None


@dataclass
class BundleEvidence:
    source: dict[str, Any]
    url: str | None
    title: str | None
    artifact_records: dict[str, ArtifactRecord]
    persisted_root: Path | None


def build_fidelity_report(
    reference_bundle: dict[str, Any] | None = None,
    candidate_bundle: dict[str, Any] | None = None,
    reference_url: str | None = None,
    candidate_url: str | None = None,
) -> dict[str, Any]:
    reference = _normalize_bundle(reference_bundle, reference_url)
    candidate = _normalize_bundle(candidate_bundle, candidate_url)

    reference_artifacts = _collect_artifacts(reference)
    candidate_artifacts = _collect_artifacts(candidate)

    check_details = [
        _screenshot_check(reference_artifacts["screenshot"], candidate_artifacts["screenshot"]),
        _dom_check(reference_artifacts["dom_snapshot"], candidate_artifacts["dom_snapshot"]),
        _styles_check(reference_artifacts["computed_styles"], candidate_artifacts["computed_styles"]),
        _interaction_check(reference_artifacts["interaction_states"], candidate_artifacts["interaction_states"]),
        _interaction_trace_check(reference_artifacts["interaction_trace"], candidate_artifacts["interaction_trace"]),
        _supporting_check(
            "accessibility tree",
            reference_artifacts["accessibility_tree"],
            candidate_artifacts["accessibility_tree"],
        ),
        _supporting_check(
            "network manifest",
            reference_artifacts["network_manifest"],
            candidate_artifacts["network_manifest"],
        ),
        _supporting_check(
            "asset inventory",
            reference_artifacts["asset_inventory"],
            candidate_artifacts["asset_inventory"],
        ),
    ]
    check_details = [_finalize_check_detail(detail) for detail in check_details]

    core_blockers = [
        detail["name"]
        for detail in check_details
        if detail.get("core") and detail.get("status") != "present"
    ]
    artifact_coverage = {
        "reference": _coverage_summary(reference_artifacts),
        "candidate": _coverage_summary(candidate_artifacts),
    }

    bounded_score = _compute_bounded_score(check_details)
    confidence = _confidence_from_score(bounded_score, core_blockers)
    verdict = _choose_verdict(bounded_score, core_blockers)
    message = _build_message(reference, candidate, bounded_score, confidence, core_blockers)
    score_breakdown = _score_breakdown(check_details)
    priority_findings = _priority_findings(check_details)
    recommended_actions = _recommended_actions(check_details, core_blockers)

    missing_artifacts = {
        "reference": _missing_artifacts(reference_artifacts),
        "candidate": _missing_artifacts(candidate_artifacts),
    }

    comparison_summary = {
        "score": bounded_score,
        "confidence": confidence,
        "verdict": verdict,
        "core_blockers": core_blockers,
        "artifact_coverage": artifact_coverage,
        "score_breakdown": score_breakdown,
        "strongest_checks": _ranked_checks(check_details, reverse=True),
        "weakest_checks": _ranked_checks(check_details, reverse=False),
    }
    downstream_guidance = {
        "ready_for_exact_clone": verdict == "strong" and not core_blockers,
        "priority_findings": priority_findings,
        "recommended_actions": recommended_actions,
        "missing_core_artifacts": {
            side: [name for name in missing if name in {"screenshot", "DOM snapshot", "computed styles", "interaction states", "interaction trace"}]
            for side, missing in missing_artifacts.items()
        },
    }

    return {
        "available": True,
        "status": "bounded",
        "report_version": "verification.v2",
        "verdict": verdict,
        "reference_url": reference.url,
        "candidate_url": candidate.url,
        "message": message,
        "checks": [_check_summary(detail) for detail in check_details],
        "check_details": check_details,
        "comparison_summary": comparison_summary,
        "artifact_coverage": artifact_coverage,
        "missing_artifacts": missing_artifacts,
        "downstream_guidance": downstream_guidance,
        "limitations": [
            "This report is bounded to persisted artifacts and metadata.",
            "It uses bounded persisted-PNG comparison, not full pixel-perfect diffing.",
            "If screenshot bytes are missing, screenshot parity cannot be verified.",
            "If DOM or style content is absent, structural and visual alignment is only partially assessed.",
        ],
    }


def _normalize_bundle(bundle: dict[str, Any] | None, fallback_url: str | None) -> BundleEvidence:
    source = bundle or {}
    url = fallback_url or _first_string(
        _deep_get(source, "url"),
        _deep_get(source, "bundle", "url"),
        _deep_get(source, "static", "url"),
        _deep_get(source, "runtime", "finalUrl"),
    )
    title = _first_string(
        _deep_get(source, "title"),
        _deep_get(source, "runtime", "title"),
        _deep_get(source, "static", "title"),
    )
    persisted_root = _first_path(
        _deep_get(source, "bundle", "persisted", "root"),
        _deep_get(source, "persisted", "root"),
    )
    artifact_records = {
        "screenshot": _resolve_artifact(source, "screenshot", ("bundle", "captured_artifacts", "screenshot"), ("runtime", "captures", "screenshot"), ("captures", "screenshot")),
        "dom_snapshot": _resolve_artifact(source, "dom_snapshot", ("bundle", "captured_artifacts", "dom"), ("runtime", "captures", "dom"), ("captures", "dom")),
        "computed_styles": _resolve_artifact(source, "computed_styles", ("bundle", "captured_artifacts", "styles"), ("runtime", "captures", "styles"), ("captures", "styles")),
        "interaction_states": _resolve_artifact(source, "interaction_states", ("bundle", "captured_artifacts", "interactions"), ("runtime", "captures", "interactions"), ("captures", "interactions")),
        "interaction_trace": _resolve_artifact(source, "interaction_trace", ("bundle", "captured_artifacts", "interaction_trace"), ("runtime", "captures", "interactionTrace"), ("captures", "interactionTrace")),
        "accessibility_tree": _resolve_artifact(source, "accessibility_tree", ("bundle", "captured_artifacts", "accessibility"), ("runtime", "captures", "accessibility"), ("captures", "accessibility")),
        "network_manifest": _resolve_artifact(source, "network_manifest", ("bundle", "captured_artifacts", "network"), ("runtime", "captures", "network"), ("captures", "network")),
        "asset_inventory": _resolve_artifact(source, "asset_inventory", ("bundle", "captured_artifacts", "assets"), ("runtime", "captures", "assets"), ("captures", "assets")),
        "html": _resolve_artifact(source, "html", ("bundle", "captured_artifacts", "html"), ("runtime", "captures", "html"), ("captures", "html")),
        "session": _resolve_artifact(source, "session", ("bundle", "captured_artifacts", "session"), ("runtime", "session")),
    }
    return BundleEvidence(source=source, url=url, title=title, artifact_records=artifact_records, persisted_root=persisted_root)


def _resolve_artifact(source: dict[str, Any], name: str, *paths: tuple[str, ...]) -> ArtifactRecord:
    candidates = []
    for path in paths:
        value = _deep_get(source, *path)
        if isinstance(value, dict):
            candidates.append(value)
    for candidate in candidates:
        available = bool(candidate.get("available"))
        path_value = candidate.get("path")
        content = candidate.get("content")
        metadata = {key: value for key, value in candidate.items() if key not in {"content"}}
        if path_value or content is not None or available:
            return ArtifactRecord(
                available=available or path_value is not None or content is not None,
                path=str(path_value) if path_value is not None else None,
                content=content,
                metadata=metadata,
            )

    if name == "session":
        session = _deep_get(source, "runtime", "session")
        if isinstance(session, dict) and session:
            return ArtifactRecord(available=True, content=session, metadata=dict(session))

    persisted = _extract_persisted_path(source, name)
    if persisted:
        return ArtifactRecord(available=True, path=str(persisted), metadata={"path": str(persisted)})

    return ArtifactRecord(available=False, metadata={})


def _extract_persisted_path(source: dict[str, Any], name: str) -> Path | None:
    persisted_files = _deep_get(source, "bundle", "persisted", "files")
    if not isinstance(persisted_files, dict):
        persisted_files = _deep_get(source, "persisted", "files")
    if not isinstance(persisted_files, dict):
        return None

    path_value = None
    if name == "screenshot":
        path_value = persisted_files.get("screenshot")
    elif name == "dom_snapshot":
        path_value = persisted_files.get("dom_snapshot")
    elif name == "computed_styles":
        path_value = persisted_files.get("computed_styles")
    elif name == "interaction_states":
        path_value = persisted_files.get("interaction_states")
    elif name == "interaction_trace":
        path_value = persisted_files.get("interaction_trace")
    elif name == "accessibility_tree":
        path_value = persisted_files.get("accessibility_tree")
    elif name == "network_manifest":
        path_value = persisted_files.get("network_manifest")
    elif name == "asset_inventory":
        path_value = persisted_files.get("asset_inventory")
    elif name == "html":
        path_value = persisted_files.get("html")
    elif name == "session":
        path_value = persisted_files.get("storage_state")

    if not path_value:
        return None
    path = Path(str(path_value))
    return path if path.exists() else None


def _collect_artifacts(bundle: BundleEvidence) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, record in bundle.artifact_records.items():
        content, metadata = _load_artifact(record)
        path_exists = bool(record.path and Path(record.path).exists())
        available = content is not None or path_exists
        out[name] = {
            "available": available,
            "path": record.path,
            "content": content,
            "metadata": metadata,
        }
    return out


def _load_artifact(record: ArtifactRecord) -> tuple[Any, dict[str, Any]]:
    metadata = dict(record.metadata or {})
    if record.content is not None:
        return record.content, metadata
    if not record.path:
        return None, metadata

    path = Path(record.path)
    if not path.exists():
        return None, metadata

    if path.suffix.lower() in {".json", ".txt", ".html", ".htm"}:
        text = path.read_text()
        if path.suffix.lower() == ".json":
            try:
                return json.loads(text), metadata
            except json.JSONDecodeError:
                return text, metadata
        return text, metadata

    if path.suffix.lower() == ".png":
        metadata.update(_png_metadata(path))
        return None, metadata

    return None, metadata


def _png_metadata(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            header = handle.read(24)
        if len(header) >= 24 and header.startswith(b"\x89PNG\r\n\x1a\n") and header[12:16] == b"IHDR":
            width, height = struct.unpack(">II", header[16:24])
            return {"width": width, "height": height, "byteLength": path.stat().st_size}
    except OSError:
        return {}
    return {"byteLength": path.stat().st_size if path.exists() else None}


def _png_fingerprint(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    png_path = Path(path)
    if not png_path.exists():
        return None
    try:
        decoded = _decode_png_rgba(png_path)
    except Exception:
        return None
    width = decoded["width"]
    height = decoded["height"]
    pixels = decoded["pixels"]
    if width <= 0 or height <= 0 or not pixels:
        return None

    hash_bucket_sums = [0.0] * (SCREENSHOT_HASH_GRID_SIZE * SCREENSHOT_HASH_GRID_SIZE)
    hash_bucket_counts = [0] * (SCREENSHOT_HASH_GRID_SIZE * SCREENSHOT_HASH_GRID_SIZE)
    grid_bucket_sums = [0.0] * (SCREENSHOT_GRID_SIZE * SCREENSHOT_GRID_SIZE)
    grid_bucket_counts = [0] * (SCREENSHOT_GRID_SIZE * SCREENSHOT_GRID_SIZE)
    quadrant_luma_sums = [0.0] * SCREENSHOT_QUADRANT_COUNT
    quadrant_luma_counts = [0] * SCREENSHOT_QUADRANT_COUNT
    band_luma_sums = [0.0] * SCREENSHOT_BAND_COUNT
    band_luma_counts = [0] * SCREENSHOT_BAND_COUNT
    histogram = [0] * SCREENSHOT_HISTOGRAM_BINS
    luma_total = 0.0
    luma_squared_total = 0.0
    red_total = 0.0
    green_total = 0.0
    blue_total = 0.0
    opaque_pixels = 0

    for y in range(height):
        row_start = y * width * 4
        hash_bucket_y = min(SCREENSHOT_HASH_GRID_SIZE - 1, (y * SCREENSHOT_HASH_GRID_SIZE) // height)
        grid_bucket_y = min(SCREENSHOT_GRID_SIZE - 1, (y * SCREENSHOT_GRID_SIZE) // height)
        for x in range(width):
            offset = row_start + x * 4
            red = pixels[offset]
            green = pixels[offset + 1]
            blue = pixels[offset + 2]
            alpha = pixels[offset + 3]
            if alpha == 0:
                continue
            luma = (0.2126 * red) + (0.7152 * green) + (0.0722 * blue)
            luma_total += luma
            luma_squared_total += luma * luma
            red_total += red
            green_total += green
            blue_total += blue
            opaque_pixels += 1
            hash_bucket_x = min(SCREENSHOT_HASH_GRID_SIZE - 1, (x * SCREENSHOT_HASH_GRID_SIZE) // width)
            hash_bucket_index = hash_bucket_y * SCREENSHOT_HASH_GRID_SIZE + hash_bucket_x
            hash_bucket_sums[hash_bucket_index] += luma
            hash_bucket_counts[hash_bucket_index] += 1
            grid_bucket_x = min(SCREENSHOT_GRID_SIZE - 1, (x * SCREENSHOT_GRID_SIZE) // width)
            grid_bucket_index = grid_bucket_y * SCREENSHOT_GRID_SIZE + grid_bucket_x
            grid_bucket_sums[grid_bucket_index] += luma
            grid_bucket_counts[grid_bucket_index] += 1
            quadrant_index = (0 if y < (height / 2) else 2) + (0 if x < (width / 2) else 1)
            quadrant_luma_sums[quadrant_index] += luma
            quadrant_luma_counts[quadrant_index] += 1
            band_index = min(SCREENSHOT_BAND_COUNT - 1, (y * SCREENSHOT_BAND_COUNT) // height)
            band_luma_sums[band_index] += luma
            band_luma_counts[band_index] += 1
            histogram_index = min(SCREENSHOT_HISTOGRAM_BINS - 1, int((luma / 256.0) * SCREENSHOT_HISTOGRAM_BINS))
            histogram[histogram_index] += 1

    if opaque_pixels == 0:
        return None

    hash_bucket_values = [
        (hash_bucket_sums[index] / hash_bucket_counts[index]) if hash_bucket_counts[index] else 0.0
        for index in range(SCREENSHOT_HASH_GRID_SIZE * SCREENSHOT_HASH_GRID_SIZE)
    ]
    grid_values = [
        (grid_bucket_sums[index] / grid_bucket_counts[index]) if grid_bucket_counts[index] else 0.0
        for index in range(SCREENSHOT_GRID_SIZE * SCREENSHOT_GRID_SIZE)
    ]
    quadrant_values = [
        (quadrant_luma_sums[index] / quadrant_luma_counts[index]) if quadrant_luma_counts[index] else 0.0
        for index in range(SCREENSHOT_QUADRANT_COUNT)
    ]
    band_values = [
        (band_luma_sums[index] / band_luma_counts[index]) if band_luma_counts[index] else 0.0
        for index in range(SCREENSHOT_BAND_COUNT)
    ]
    average = sum(hash_bucket_values) / len(hash_bucket_values)
    bits = "".join("1" if value >= average else "0" for value in hash_bucket_values)
    mean_luma = luma_total / opaque_pixels
    variance = max(0.0, (luma_squared_total / opaque_pixels) - (mean_luma * mean_luma))
    return {
        "width": width,
        "height": height,
        "mean_luma": round(mean_luma, 4),
        "contrast": round(math.sqrt(variance), 4),
        "aspect_ratio": round(width / height, 4),
        "opaque_ratio": round(opaque_pixels / (width * height), 4),
        "rgb_mean": [
            round(red_total / opaque_pixels, 2),
            round(green_total / opaque_pixels, 2),
            round(blue_total / opaque_pixels, 2),
        ],
        "histogram": [round(count / opaque_pixels, 4) for count in histogram],
        "grid_luma": [round(value, 2) for value in grid_values],
        "quadrant_luma": [round(value, 2) for value in quadrant_values],
        "band_luma": [round(value, 2) for value in band_values],
        "edge_density": round(_grid_edge_density(grid_values, SCREENSHOT_GRID_SIZE), 4),
        "ahash": f"{int(bits, 2):016x}",
        **(_pixel_downsample_signature(decoded, SCREENSHOT_PIXEL_DIFF_GRID_SIZE) or {}),
    }


def _pixel_downsample_signature(decoded: dict[str, Any], grid_size: int) -> dict[str, Any] | None:
    width = int(decoded.get("width") or 0)
    height = int(decoded.get("height") or 0)
    pixels = decoded.get("pixels") or b""
    if width <= 0 or height <= 0 or not isinstance(pixels, (bytes, bytearray)):
        return None

    cell_count = grid_size * grid_size
    luma_sums = [0.0] * cell_count
    red_sums = [0.0] * cell_count
    green_sums = [0.0] * cell_count
    blue_sums = [0.0] * cell_count
    cell_counts = [0] * cell_count

    for y in range(height):
        row_start = y * width * 4
        grid_y = min(grid_size - 1, (y * grid_size) // height)
        for x in range(width):
            offset = row_start + (x * 4)
            alpha = pixels[offset + 3]
            if alpha == 0:
                continue
            grid_x = min(grid_size - 1, (x * grid_size) // width)
            index = (grid_y * grid_size) + grid_x
            red = pixels[offset]
            green = pixels[offset + 1]
            blue = pixels[offset + 2]
            luma = (0.2126 * red) + (0.7152 * green) + (0.0722 * blue)
            luma_sums[index] += luma
            red_sums[index] += red
            green_sums[index] += green
            blue_sums[index] += blue
            cell_counts[index] += 1

    total_cells = sum(1 for count in cell_counts if count)
    if total_cells == 0:
        return None

    luma_grid = [
        (luma_sums[index] / cell_counts[index]) if cell_counts[index] else 0.0
        for index in range(cell_count)
    ]
    rgb_grid: list[float] = []
    for index in range(cell_count):
        count = cell_counts[index]
        if not count:
            rgb_grid.extend([0.0, 0.0, 0.0])
            continue
        rgb_grid.extend(
            [
                round(red_sums[index] / count, 2),
                round(green_sums[index] / count, 2),
                round(blue_sums[index] / count, 2),
            ]
        )
    return {
        "pixel_grid_size": grid_size,
        "pixel_luma_grid": [round(value, 2) for value in luma_grid],
        "pixel_rgb_grid": rgb_grid,
    }


def _decode_png_rgba(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) < 8 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Not a PNG file")

    offset = 8
    width = height = bit_depth = color_type = interlace = None
    idat_chunks: list[bytes] = []

    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_data = data[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _compression, _filter_method, interlace = struct.unpack(
                ">IIBBBBB", chunk_data
            )
        elif chunk_type == b"IDAT":
            idat_chunks.append(chunk_data)
        elif chunk_type == b"IEND":
            break

    if width is None or height is None or bit_depth != 8 or interlace not in {0, None}:
        raise ValueError("Unsupported PNG layout")
    if color_type not in {0, 2, 6}:
        raise ValueError("Unsupported PNG color type")

    channel_count = {0: 1, 2: 3, 6: 4}[int(color_type)]
    bytes_per_pixel = channel_count
    stride = width * bytes_per_pixel
    raw = zlib.decompress(b"".join(idat_chunks))
    expected = height * (stride + 1)
    if len(raw) < expected:
        raise ValueError("PNG data truncated")

    rgba = bytearray(width * height * 4)
    previous_row = bytearray(stride)
    cursor = 0
    output_cursor = 0
    for _ in range(height):
        filter_type = raw[cursor]
        cursor += 1
        row = bytearray(raw[cursor : cursor + stride])
        cursor += stride
        _apply_png_filter(row, previous_row, filter_type, bytes_per_pixel)

        if color_type == 6:
            rgba[output_cursor : output_cursor + (width * 4)] = row
        elif color_type == 2:
            for x in range(width):
                source = x * 3
                target = output_cursor + (x * 4)
                rgba[target] = row[source]
                rgba[target + 1] = row[source + 1]
                rgba[target + 2] = row[source + 2]
                rgba[target + 3] = 255
        else:
            for x in range(width):
                value = row[x]
                target = output_cursor + (x * 4)
                rgba[target] = value
                rgba[target + 1] = value
                rgba[target + 2] = value
                rgba[target + 3] = 255

        previous_row = row
        output_cursor += width * 4

    return {"width": width, "height": height, "pixels": bytes(rgba)}


def _apply_png_filter(
    row: bytearray,
    previous_row: bytearray,
    filter_type: int,
    bytes_per_pixel: int,
) -> None:
    if filter_type == 0:
        return
    if filter_type == 1:
        for index in range(len(row)):
            left = row[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            row[index] = (row[index] + left) & 0xFF
        return
    if filter_type == 2:
        for index in range(len(row)):
            row[index] = (row[index] + previous_row[index]) & 0xFF
        return
    if filter_type == 3:
        for index in range(len(row)):
            left = row[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            up = previous_row[index]
            row[index] = (row[index] + ((left + up) // 2)) & 0xFF
        return
    if filter_type == 4:
        for index in range(len(row)):
            left = row[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            up = previous_row[index]
            up_left = previous_row[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            row[index] = (row[index] + _paeth_predictor(left, up, up_left)) & 0xFF
        return
    raise ValueError(f"Unsupported PNG filter type: {filter_type}")


def _paeth_predictor(left: int, up: int, up_left: int) -> int:
    prediction = left + up - up_left
    left_distance = abs(prediction - left)
    up_distance = abs(prediction - up)
    up_left_distance = abs(prediction - up_left)
    if left_distance <= up_distance and left_distance <= up_left_distance:
        return left
    if up_distance <= up_left_distance:
        return up
    return up_left


def _hash_similarity(reference_hash: Any, candidate_hash: Any) -> float | None:
    if not isinstance(reference_hash, str) or not isinstance(candidate_hash, str):
        return None
    if len(reference_hash) != len(candidate_hash):
        return None
    reference_bits = bin(int(reference_hash, 16))[2:].zfill(len(reference_hash) * 4)
    candidate_bits = bin(int(candidate_hash, 16))[2:].zfill(len(candidate_hash) * 4)
    distance = sum(1 for left, right in zip(reference_bits, candidate_bits) if left != right)
    total = len(reference_bits)
    return max(0.0, 1.0 - (distance / total)) if total else None


def _grid_edge_density(values: list[float], side_length: int) -> float:
    if side_length <= 1 or len(values) != side_length * side_length:
        return 0.0
    total = 0.0
    comparisons = 0
    for y in range(side_length):
        for x in range(side_length):
            value = values[(y * side_length) + x]
            if x > 0:
                total += abs(value - values[(y * side_length) + (x - 1)])
                comparisons += 1
            if y > 0:
                total += abs(value - values[((y - 1) * side_length) + x])
                comparisons += 1
    return (total / comparisons) / 255.0 if comparisons else 0.0


def _series_similarity(reference: Any, candidate: Any, scale: float = 1.0) -> float | None:
    if not isinstance(reference, list) or not isinstance(candidate, list):
        return None
    if not reference or len(reference) != len(candidate):
        return None
    comparisons = []
    denominator = max(scale, 1.0)
    for left, right in zip(reference, candidate):
        if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
            continue
        comparisons.append(max(0.0, 1.0 - abs(float(left) - float(right)) / denominator))
    if not comparisons:
        return None
    return sum(comparisons) / len(comparisons)


def _pixel_grid_metrics(reference: dict[str, Any] | None, candidate: dict[str, Any] | None) -> dict[str, float] | None:
    if not isinstance(reference, dict) or not isinstance(candidate, dict):
        return None
    ref_luma = reference.get("pixel_luma_grid")
    cand_luma = candidate.get("pixel_luma_grid")
    ref_rgb = reference.get("pixel_rgb_grid")
    cand_rgb = candidate.get("pixel_rgb_grid")
    if not isinstance(ref_luma, list) or not isinstance(cand_luma, list):
        return None
    if not isinstance(ref_rgb, list) or not isinstance(cand_rgb, list):
        return None
    if not ref_luma or len(ref_luma) != len(cand_luma) or len(ref_rgb) != len(cand_rgb):
        return None

    luma_deltas: list[float] = []
    rgb_deltas: list[float] = []
    mismatch_cells = 0
    luma_mismatch_threshold = 26.0
    rgb_mismatch_threshold = 30.0

    cell_count = len(ref_luma)
    for index in range(cell_count):
        ref_luma_value = ref_luma[index]
        cand_luma_value = cand_luma[index]
        if not isinstance(ref_luma_value, (int, float)) or not isinstance(cand_luma_value, (int, float)):
            continue
        luma_delta = abs(float(ref_luma_value) - float(cand_luma_value))
        luma_deltas.append(luma_delta)

        ref_rgb_offset = index * 3
        cand_rgb_offset = index * 3
        ref_rgb_values = ref_rgb[ref_rgb_offset : ref_rgb_offset + 3]
        cand_rgb_values = cand_rgb[cand_rgb_offset : cand_rgb_offset + 3]
        if len(ref_rgb_values) != 3 or len(cand_rgb_values) != 3:
            continue
        channel_deltas = []
        for ref_value, cand_value in zip(ref_rgb_values, cand_rgb_values):
            if not isinstance(ref_value, (int, float)) or not isinstance(cand_value, (int, float)):
                channel_deltas = []
                break
            channel_deltas.append(abs(float(ref_value) - float(cand_value)))
        if not channel_deltas:
            continue
        rgb_delta = sum(channel_deltas) / len(channel_deltas)
        rgb_deltas.append(rgb_delta)
        if luma_delta > luma_mismatch_threshold or rgb_delta > rgb_mismatch_threshold:
            mismatch_cells += 1

    if not luma_deltas or not rgb_deltas:
        return None

    mean_abs_luma_delta = sum(luma_deltas) / len(luma_deltas)
    mean_abs_rgb_delta = sum(rgb_deltas) / len(rgb_deltas)
    mismatch_ratio = mismatch_cells / max(1, len(luma_deltas))
    return {
        "pixel_mean_abs_luma_delta": mean_abs_luma_delta,
        "pixel_mean_abs_rgb_delta": mean_abs_rgb_delta,
        "pixel_mismatch_ratio": mismatch_ratio,
        "pixel_luma_similarity": max(0.0, 1.0 - (mean_abs_luma_delta / SCREENSHOT_PIXEL_LUMA_SCALE)),
        "pixel_rgb_similarity": max(0.0, 1.0 - (mean_abs_rgb_delta / SCREENSHOT_PIXEL_RGB_SCALE)),
        "pixel_mismatch_similarity": max(0.0, 1.0 - (mismatch_ratio / SCREENSHOT_PIXEL_MISMATCH_SCALE)),
    }


def _histogram_similarity(reference: Any, candidate: Any) -> float | None:
    return _series_similarity(reference, candidate, scale=1.0)


def _signature_summary(signature: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(signature, dict):
        return None
    return {
        "width": signature.get("width"),
        "height": signature.get("height"),
        "aspect_ratio": signature.get("aspect_ratio"),
        "mean_luma": signature.get("mean_luma"),
        "contrast": signature.get("contrast"),
        "edge_density": signature.get("edge_density"),
        "opaque_ratio": signature.get("opaque_ratio"),
        "rgb_mean": signature.get("rgb_mean"),
        "ahash": signature.get("ahash"),
        "histogram": signature.get("histogram"),
        "quadrant_luma": signature.get("quadrant_luma"),
        "band_luma": signature.get("band_luma"),
    }


def _weighted_similarity(metrics: dict[str, float], weights: dict[str, float]) -> float | None:
    total = 0.0
    used_weight = 0.0
    for name, value in metrics.items():
        if not isinstance(value, (int, float)):
            continue
        weight = float(weights.get(name, 0.0))
        if weight <= 0:
            continue
        total += max(0.0, min(1.0, float(value))) * weight
        used_weight += weight
    if used_weight <= 0:
        return None
    return total / used_weight


def _screenshot_drift_flags(
    metrics: dict[str, float],
    reference_dimensions: tuple[int, int] | None,
    candidate_dimensions: tuple[int, int] | None,
) -> list[str]:
    flags: list[str] = []
    if reference_dimensions and candidate_dimensions and reference_dimensions != candidate_dimensions:
        flags.append("viewport-or-breakpoint drift")
    if metrics.get("quadrant_similarity", 1.0) < 0.8:
        flags.append("composition drift")
    if metrics.get("band_similarity", 1.0) < 0.78:
        flags.append("vertical-flow drift")
    if metrics.get("grid_similarity", 1.0) < 0.78:
        flags.append("layout-or-large-visual drift")
    if metrics.get("histogram_similarity", 1.0) < 0.72:
        flags.append("palette-or-background drift")
    if metrics.get("rgb_similarity", 1.0) < 0.8:
        flags.append("color-balance drift")
    if metrics.get("edge_similarity", 1.0) < 0.72:
        flags.append("shape-and-contrast drift")
    if metrics.get("contrast_similarity", 1.0) < 0.7:
        flags.append("contrast-or-depth drift")
    if metrics.get("pixel_mismatch_ratio", 0.0) > 0.28:
        flags.append("pixel-structure drift")
    if metrics.get("pixel_luma_similarity", 1.0) < 0.82:
        flags.append("luma-or-contrast drift")
    if metrics.get("pixel_rgb_similarity", 1.0) < 0.84:
        flags.append("channel-color drift")
    return flags


def _screenshot_check(reference: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    ref_meta = reference.get("metadata") or {}
    cand_meta = candidate.get("metadata") or {}
    ref_present = bool(reference.get("available"))
    cand_present = bool(candidate.get("available"))
    ref_path = reference.get("path")
    cand_path = candidate.get("path")
    ref_size = _artifact_size(reference)
    cand_size = _artifact_size(candidate)
    ref_dims = _image_dimensions(ref_meta, ref_path)
    cand_dims = _image_dimensions(cand_meta, cand_path)
    ref_fingerprint = _png_fingerprint(ref_path) if ref_path else None
    cand_fingerprint = _png_fingerprint(cand_path) if cand_path else None
    status = "present" if ref_present and cand_present else "missing"
    detail = []
    metrics: dict[str, float] = {}
    if ref_dims and cand_dims:
        if ref_dims == cand_dims:
            detail.append(f"matching PNG dimensions {ref_dims[0]}x{ref_dims[1]}")
        else:
            detail.append(f"PNG dimensions differ {ref_dims[0]}x{ref_dims[1]} vs {cand_dims[0]}x{cand_dims[1]}")
        metrics["dimension_similarity"] = 1.0 if ref_dims == cand_dims else _safe_ratio(ref_dims[0] * ref_dims[1], cand_dims[0] * cand_dims[1])
    if ref_size and cand_size:
        ratio = _safe_ratio(ref_size, cand_size)
        detail.append(f"byte-size ratio {ratio:.2f}")
        metrics["byte_ratio"] = ratio
    if ref_fingerprint and cand_fingerprint:
        pixel_metrics = _pixel_grid_metrics(ref_fingerprint, cand_fingerprint)
        hash_similarity = _hash_similarity(ref_fingerprint.get("ahash"), cand_fingerprint.get("ahash"))
        luma_similarity = _count_similarity(ref_fingerprint.get("mean_luma"), cand_fingerprint.get("mean_luma"))
        contrast_similarity = _count_similarity(ref_fingerprint.get("contrast"), cand_fingerprint.get("contrast"))
        aspect_similarity = _count_similarity(ref_fingerprint.get("aspect_ratio"), cand_fingerprint.get("aspect_ratio"))
        opaque_similarity = _count_similarity(ref_fingerprint.get("opaque_ratio"), cand_fingerprint.get("opaque_ratio"))
        rgb_similarity = _series_similarity(ref_fingerprint.get("rgb_mean"), cand_fingerprint.get("rgb_mean"), scale=255.0)
        histogram_similarity = _histogram_similarity(
            ref_fingerprint.get("histogram"),
            cand_fingerprint.get("histogram"),
        )
        grid_similarity = _series_similarity(
            ref_fingerprint.get("grid_luma"),
            cand_fingerprint.get("grid_luma"),
            scale=255.0,
        )
        quadrant_similarity = _series_similarity(
            ref_fingerprint.get("quadrant_luma"),
            cand_fingerprint.get("quadrant_luma"),
            scale=255.0,
        )
        band_similarity = _series_similarity(
            ref_fingerprint.get("band_luma"),
            cand_fingerprint.get("band_luma"),
            scale=255.0,
        )
        edge_similarity = _count_similarity(ref_fingerprint.get("edge_density"), cand_fingerprint.get("edge_density"))
        if hash_similarity is not None:
            detail.append(f"ahash similarity {hash_similarity:.2f}")
            metrics["ahash_similarity"] = hash_similarity
        if luma_similarity is not None:
            detail.append(f"mean-luma similarity {luma_similarity:.2f}")
            metrics["mean_luma_similarity"] = luma_similarity
        if contrast_similarity is not None:
            detail.append(f"contrast similarity {contrast_similarity:.2f}")
            metrics["contrast_similarity"] = contrast_similarity
        if rgb_similarity is not None:
            detail.append(f"rgb-balance similarity {rgb_similarity:.2f}")
            metrics["rgb_similarity"] = rgb_similarity
        if histogram_similarity is not None:
            detail.append(f"histogram similarity {histogram_similarity:.2f}")
            metrics["histogram_similarity"] = histogram_similarity
        if grid_similarity is not None:
            detail.append(f"grid similarity {grid_similarity:.2f}")
            metrics["grid_similarity"] = grid_similarity
        if quadrant_similarity is not None:
            detail.append(f"quadrant similarity {quadrant_similarity:.2f}")
            metrics["quadrant_similarity"] = quadrant_similarity
        if band_similarity is not None:
            detail.append(f"band similarity {band_similarity:.2f}")
            metrics["band_similarity"] = band_similarity
        if edge_similarity is not None:
            detail.append(f"edge similarity {edge_similarity:.2f}")
            metrics["edge_similarity"] = edge_similarity
        if aspect_similarity is not None:
            metrics["aspect_similarity"] = aspect_similarity
        if opaque_similarity is not None:
            metrics["opaque_similarity"] = opaque_similarity
        if pixel_metrics is not None:
            detail.append(f"pixel-luma delta {pixel_metrics['pixel_mean_abs_luma_delta']:.1f}")
            detail.append(f"pixel-rgb delta {pixel_metrics['pixel_mean_abs_rgb_delta']:.1f}")
            detail.append(f"pixel mismatch {pixel_metrics['pixel_mismatch_ratio']:.2f}")
            metrics.update(pixel_metrics)
    summary = "screenshot parity present" if status == "present" else "screenshot parity missing"
    if detail:
        summary = f"{summary} ({'; '.join(detail)})"
    similarity = _weighted_similarity(
        metrics,
        {
            "dimension_similarity": 0.1,
            "ahash_similarity": 0.12,
            "mean_luma_similarity": 0.08,
            "contrast_similarity": 0.1,
            "rgb_similarity": 0.08,
            "histogram_similarity": 0.16,
            "grid_similarity": 0.2,
            "quadrant_similarity": 0.08,
            "band_similarity": 0.05,
            "edge_similarity": 0.12,
            "aspect_similarity": 0.03,
            "opaque_similarity": 0.03,
            "pixel_luma_similarity": 0.18,
            "pixel_rgb_similarity": 0.12,
            "pixel_mismatch_similarity": 0.12,
        },
    )
    if similarity is None:
        similarity = 0.5
    drift_flags = _screenshot_drift_flags(metrics, ref_dims, cand_dims) if status == "present" else []
    return {
        "name": "screenshot",
        "core": True,
        "status": status,
        "summary": summary,
        "reference": _artifact_report_entry(reference),
        "candidate": _artifact_report_entry(candidate),
        "similarity": similarity if status == "present" else 0.0,
        "details": {
            "notes": detail,
            "metrics": metrics,
            "drift_flags": drift_flags,
            "reference_fingerprint": _signature_summary(ref_fingerprint),
            "candidate_fingerprint": _signature_summary(cand_fingerprint),
        },
    }


def _dom_check(reference: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    ref_present = bool(reference.get("available"))
    cand_present = bool(candidate.get("available"))
    ref_content = reference.get("content")
    cand_content = candidate.get("content")
    ref_stats = _dom_stats(ref_content)
    cand_stats = _dom_stats(cand_content)
    status = "present" if ref_present and cand_present else "missing"
    overlap = _tag_overlap(ref_stats.get("tags", {}), cand_stats.get("tags", {}))
    summary = f"DOM snapshots present; node counts {ref_stats.get('node_count', 0)} vs {cand_stats.get('node_count', 0)}"
    if ref_stats.get("node_count") and cand_stats.get("node_count"):
        summary += f"; tag overlap {overlap:.2f}"
    node_score = _count_similarity(ref_stats.get("node_count"), cand_stats.get("node_count"))
    depth_score = _count_similarity(ref_stats.get("max_depth"), cand_stats.get("max_depth"))
    similarity_parts = [score for score in [overlap, node_score, depth_score] if score is not None]
    similarity = sum(similarity_parts) / len(similarity_parts) if similarity_parts else 0.0
    return {
        "name": "dom snapshot",
        "core": True,
        "status": status,
        "summary": summary if status == "present" else "DOM snapshot missing on one or both sides",
        "reference": _artifact_report_entry(reference, extra=ref_stats),
        "candidate": _artifact_report_entry(candidate, extra=cand_stats),
        "similarity": similarity if status == "present" else 0.0,
        "details": {
            "node_count_delta": _numeric_delta(ref_stats.get("node_count"), cand_stats.get("node_count")),
            "tag_overlap": overlap,
            "depth_delta": _numeric_delta(ref_stats.get("max_depth"), cand_stats.get("max_depth")),
        },
    }


def _styles_check(reference: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    ref_present = bool(reference.get("available"))
    cand_present = bool(candidate.get("available"))
    ref_stats = _style_stats(reference.get("content"))
    cand_stats = _style_stats(candidate.get("content"))
    status = "present" if ref_present and cand_present else "missing"
    signature_overlap = _set_overlap(ref_stats.get("signatures", set()), cand_stats.get("signatures", set()))
    summary = (
        f"computed styles present; sample counts {ref_stats.get('sample_count', 0)} vs {cand_stats.get('sample_count', 0)}"
    )
    if ref_stats.get("sample_count") and cand_stats.get("sample_count"):
        summary += f"; style-signature overlap {signature_overlap:.2f}"
    sample_score = _count_similarity(ref_stats.get("sample_count"), cand_stats.get("sample_count"))
    tag_score = _set_overlap(ref_stats.get("tags", set()), cand_stats.get("tags", set()))
    font_overlap = _set_overlap(ref_stats.get("fonts", set()), cand_stats.get("fonts", set()))
    font_size_overlap = _set_overlap(ref_stats.get("font_sizes", set()), cand_stats.get("font_sizes", set()))
    display_overlap = _set_overlap(ref_stats.get("displays", set()), cand_stats.get("displays", set()))
    similarity_parts = [
        score
        for score in [signature_overlap, sample_score, tag_score, font_overlap, font_size_overlap, display_overlap]
        if score is not None
    ]
    similarity = sum(similarity_parts) / len(similarity_parts) if similarity_parts else 0.0
    return {
        "name": "computed styles",
        "core": True,
        "status": status,
        "summary": summary if status == "present" else "computed styles missing on one or both sides",
        "reference": _artifact_report_entry(reference, extra=ref_stats),
        "candidate": _artifact_report_entry(candidate, extra=cand_stats),
        "similarity": similarity if status == "present" else 0.0,
        "details": {
            "sample_count_delta": _numeric_delta(ref_stats.get("sample_count"), cand_stats.get("sample_count")),
            "signature_overlap": signature_overlap,
            "font_overlap": font_overlap,
            "font_size_overlap": font_size_overlap,
            "display_overlap": display_overlap,
            "common_tags": sorted(set(ref_stats.get("tags", [])) & set(cand_stats.get("tags", [])))[:8],
        },
    }


def _interaction_check(reference: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    ref_present = bool(reference.get("available"))
    cand_present = bool(candidate.get("available"))
    ref_stats = _interaction_stats(reference.get("content"))
    cand_stats = _interaction_stats(candidate.get("content"))
    status = "present" if ref_present and cand_present else "missing"
    label_overlap = _set_overlap(ref_stats.get("labels", set()), cand_stats.get("labels", set()))
    summary = (
        f"interaction states present; entries {ref_stats.get('entry_count', 0)} vs {cand_stats.get('entry_count', 0)}"
    )
    if ref_stats.get("entry_count") and cand_stats.get("entry_count"):
        summary += f"; label overlap {label_overlap:.2f}"
    entry_score = _count_similarity(ref_stats.get("entry_count"), cand_stats.get("entry_count"))
    hover_score = _count_similarity(ref_stats.get("hover_changed"), cand_stats.get("hover_changed"))
    focus_score = _count_similarity(ref_stats.get("focus_changed"), cand_stats.get("focus_changed"))
    similarity_parts = [score for score in [label_overlap, entry_score, hover_score, focus_score] if score is not None]
    similarity = sum(similarity_parts) / len(similarity_parts) if similarity_parts else 0.0
    return {
        "name": "interaction states",
        "core": True,
        "status": status,
        "summary": summary if status == "present" else "interaction states missing on one or both sides",
        "reference": _artifact_report_entry(reference, extra=ref_stats),
        "candidate": _artifact_report_entry(candidate, extra=cand_stats),
        "similarity": similarity if status == "present" else 0.0,
        "details": {
            "entry_count_delta": _numeric_delta(ref_stats.get("entry_count"), cand_stats.get("entry_count")),
            "label_overlap": label_overlap,
            "hover_delta_count": _numeric_delta(ref_stats.get("hover_changed"), cand_stats.get("hover_changed")),
            "focus_delta_count": _numeric_delta(ref_stats.get("focus_changed"), cand_stats.get("focus_changed")),
        },
    }


def _interaction_trace_check(reference: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    ref_present = bool(reference.get("available"))
    cand_present = bool(candidate.get("available"))
    ref_stats = _interaction_trace_stats(reference.get("content"))
    cand_stats = _interaction_trace_stats(candidate.get("content"))
    status = "present" if ref_present and cand_present else "missing"
    kind_overlap = _set_overlap(ref_stats.get("step_kinds", set()), cand_stats.get("step_kinds", set()))
    target_overlap = _set_overlap(ref_stats.get("targets", set()), cand_stats.get("targets", set()))
    summary = (
        f"interaction trace present; steps {ref_stats.get('step_count', 0)} vs {cand_stats.get('step_count', 0)}"
    )
    if ref_stats.get("step_count") and cand_stats.get("step_count"):
        summary += f"; action overlap {kind_overlap:.2f}"
    step_score = _count_similarity(ref_stats.get("step_count"), cand_stats.get("step_count"))
    executed_score = _count_similarity(ref_stats.get("executed_count"), cand_stats.get("executed_count"))
    similarity_parts = [score for score in [kind_overlap, target_overlap, step_score, executed_score] if score is not None]
    similarity = sum(similarity_parts) / len(similarity_parts) if similarity_parts else 0.0
    return {
        "name": "interaction trace",
        "core": True,
        "status": status,
        "summary": summary if status == "present" else "interaction trace missing on one or both sides",
        "reference": _artifact_report_entry(reference, extra=ref_stats),
        "candidate": _artifact_report_entry(candidate, extra=cand_stats),
        "similarity": similarity if status == "present" else 0.0,
        "details": {
            "step_count_delta": _numeric_delta(ref_stats.get("step_count"), cand_stats.get("step_count")),
            "executed_count_delta": _numeric_delta(ref_stats.get("executed_count"), cand_stats.get("executed_count")),
            "kind_overlap": kind_overlap,
            "target_overlap": target_overlap,
        },
    }


def _supporting_check(name: str, reference: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    ref_present = bool(reference.get("available"))
    cand_present = bool(candidate.get("available"))
    status = "present" if ref_present and cand_present else "missing"
    return {
        "name": name,
        "core": False,
        "status": status,
        "summary": f"{name} present" if status == "present" else f"{name} missing on one or both sides",
        "reference": _artifact_report_entry(reference),
        "candidate": _artifact_report_entry(candidate),
        "similarity": 1.0 if status == "present" else 0.0,
        "details": {},
    }


def _artifact_report_entry(artifact: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "available": bool(artifact.get("available")),
        "path": artifact.get("path"),
    }
    if artifact.get("metadata"):
        entry["metadata"] = artifact["metadata"]
    if extra:
        entry.update(extra)
    return entry


def _coverage_summary(artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        name: bool(artifact.get("available"))
        for name, artifact in artifacts.items()
        if name in CORE_ARTIFACTS or name in {"interaction_trace", "accessibility_tree", "network_manifest", "asset_inventory", "html", "session"}
    }


def _finalize_check_detail(detail: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(detail)
    weight = CHECK_WEIGHTS.get(detail["name"], 0)
    similarity = detail.get("similarity")
    try:
        similarity_value = max(0.0, min(1.0, float(similarity)))
    except (TypeError, ValueError):
        similarity_value = 0.0
    normalized["weight"] = weight
    normalized["weighted_score"] = round(weight * similarity_value, 2)
    normalized["severity"] = _check_severity(detail, similarity_value)
    return normalized


def _check_summary(detail: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": detail["name"],
        "status": detail["status"],
        "severity": detail.get("severity"),
        "similarity": detail.get("similarity"),
        "weighted_score": detail.get("weighted_score"),
        "summary": detail["summary"],
    }


def _check_severity(detail: dict[str, Any], similarity_value: float) -> str:
    if detail.get("status") != "present":
        return "blocker" if detail.get("core") else "low"
    if similarity_value >= 0.92:
        return "low"
    if similarity_value >= 0.72:
        return "medium"
    return "high"


def _score_breakdown(check_details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": detail["name"],
            "weight": detail.get("weight", 0),
            "similarity": detail.get("similarity"),
            "weighted_score": detail.get("weighted_score", 0.0),
            "severity": detail.get("severity"),
            "status": detail.get("status"),
        }
        for detail in check_details
        if detail.get("weight")
    ]


def _ranked_checks(check_details: list[dict[str, Any]], reverse: bool) -> list[dict[str, Any]]:
    sorted_checks = sorted(
        check_details,
        key=lambda detail: (
            float(detail.get("similarity") or 0.0),
            float(detail.get("weighted_score") or 0.0),
        ),
        reverse=reverse,
    )
    ranked: list[dict[str, Any]] = []
    for detail in sorted_checks[:3]:
        ranked.append(
            {
                "name": detail["name"],
                "similarity": detail.get("similarity"),
                "severity": detail.get("severity"),
                "summary": detail.get("summary"),
            }
        )
    return ranked


def _priority_findings(check_details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    severity_rank = {"blocker": 3, "high": 2, "medium": 1, "low": 0}
    findings: list[dict[str, Any]] = []
    for detail in check_details:
        severity = str(detail.get("severity") or "low")
        if severity == "low":
            continue
        similarity = float(detail.get("similarity") or 0.0)
        findings.append(
            {
                "check": detail["name"],
                "severity": severity,
                "gap": round(max(0.0, 1.0 - similarity), 4),
                "summary": detail["summary"],
                "focus": _focus_hint(detail),
            }
        )
    findings.sort(
        key=lambda finding: (
            severity_rank.get(finding["severity"], 0),
            finding["gap"],
        ),
        reverse=True,
    )
    return findings[:4]


def _focus_hint(detail: dict[str, Any]) -> str:
    name = detail["name"]
    detail_payload = detail.get("details") or {}
    if name == "screenshot":
        drift_flags = detail_payload.get("drift_flags") or []
        if "viewport-or-breakpoint drift" in drift_flags:
            return "viewport or breakpoint mismatch"
        if "composition drift" in drift_flags:
            return "hero/section placement drift"
        if "pixel-structure drift" in drift_flags:
            return "micro-layout or spacing drift"
        if "color-balance drift" in drift_flags or "palette-or-background drift" in drift_flags:
            return "palette or background drift"
        if "channel-color drift" in drift_flags:
            return "channel or accent color drift"
        if "luma-or-contrast drift" in drift_flags:
            return "contrast or depth drift"
        if "shape-and-contrast drift" in drift_flags or "contrast-or-depth drift" in drift_flags:
            return "contrast, typography, or icon-shape drift"
        return drift_flags[0] if drift_flags else "coarse visual drift"
    if name == "dom snapshot":
        return "section structure or node hierarchy drift"
    if name == "computed styles":
        return "typography, spacing, or palette token drift"
    if name == "interaction states":
        return "hover/focus interaction coverage drift"
    if name == "interaction trace":
        return "scroll/type/click replay coverage drift"
    return "supporting evidence drift"


def _recommended_actions(check_details: list[dict[str, Any]], core_blockers: list[str]) -> list[str]:
    actions: list[str] = []
    if core_blockers:
        actions.append("Recapture missing core artifacts before treating this comparison as exact-clone evidence.")
    for detail in check_details:
        severity = detail.get("severity")
        if severity not in {"blocker", "high"}:
            continue
        if detail["name"] == "screenshot":
            drift_flags = (detail.get("details") or {}).get("drift_flags") or []
            if "viewport-or-breakpoint drift" in drift_flags:
                actions.append("Recheck viewport and breakpoint first; the screenshot footprint shifted.")
            if "composition drift" in drift_flags:
                actions.append("Align hero and section placement before tuning fine styling; the large-scale composition diverges.")
            if "pixel-structure drift" in drift_flags:
                actions.append("Compare local spacing, alignment, and block placement against the reference capture.")
            if "color-balance drift" in drift_flags or "palette-or-background drift" in drift_flags:
                actions.append("Audit palette, background, and accent token usage against the reference capture.")
            if "channel-color drift" in drift_flags:
                actions.append("Audit accent, image, and channel color balance against the reference capture.")
            if "luma-or-contrast drift" in drift_flags:
                actions.append("Check brightness, contrast, and surface depth before changing geometry.")
            if "shape-and-contrast drift" in drift_flags or "contrast-or-depth drift" in drift_flags:
                actions.append("Compare typography weight, contrast, and icon/button shapes against the reference.")
            if not drift_flags:
                actions.append("Recheck viewport, breakpoint, and hero composition; screenshot drift is still materially off.")
        elif detail["name"] == "dom snapshot":
            actions.append("Align major section order and DOM depth before tuning styling; structure diverges too early.")
        elif detail["name"] == "computed styles":
            actions.append("Audit font, spacing, and color tokens against the reference before polishing micro-detail.")
        elif detail["name"] == "interaction states":
            actions.append("Replay hover/focus states on primary controls and compare visible state deltas.")
        elif detail["name"] == "interaction trace":
            actions.append("Extend replay capture to cover the scroll, type, and click sequence used by the reference.")
    deduped: list[str] = []
    for action in actions:
        if action not in deduped:
            deduped.append(action)
    return deduped[:4]


def _missing_artifacts(artifacts: dict[str, dict[str, Any]]) -> list[str]:
    return [
        label
        for label, key in (
            ("screenshot", "screenshot"),
            ("DOM snapshot", "dom_snapshot"),
            ("computed styles", "computed_styles"),
            ("interaction states", "interaction_states"),
            ("interaction trace", "interaction_trace"),
        )
        if not artifacts.get(key, {}).get("available")
    ]


def _compute_bounded_score(check_details: list[dict[str, Any]]) -> int:
    total = 0.0
    for detail in check_details:
        weight = CHECK_WEIGHTS.get(detail["name"], 0)
        if not weight:
            continue
        similarity = detail.get("similarity")
        if similarity is None:
            similarity = 0.0
        total += weight * max(0.0, min(1.0, float(similarity)))
    return int(round(total))


def _confidence_from_score(score: int, core_blockers: list[str]) -> str:
    if core_blockers:
        return "low"
    if score >= 80:
        return "high"
    if score >= 55:
        return "medium"
    return "low"


def _choose_verdict(score: int, core_blockers: list[str]) -> str:
    if core_blockers:
        return "incomplete"
    if score >= 80:
        return "strong"
    if score >= 55:
        return "plausible"
    return "weak"


def _build_message(
    reference: BundleEvidence,
    candidate: BundleEvidence,
    score: int,
    confidence: str,
    core_blockers: list[str],
) -> str:
    parts = [
        "Bounded fidelity comparison completed from persisted bundle artifacts only.",
        f"Score {score}/100 with {confidence} confidence.",
    ]
    if reference.url or candidate.url:
        parts.append(
            "Reference"
            f" {reference.url or 'unknown'}"
            + ", candidate "
            f"{candidate.url or 'unknown'}."
        )
    if core_blockers:
        parts.append("Core blockers: " + ", ".join(core_blockers) + ".")
    parts.append("This does not prove pixel-level equivalence.")
    return " ".join(parts)


def _dom_stats(content: Any) -> dict[str, Any]:
    if not isinstance(content, dict):
        return {"node_count": 0, "max_depth": 0, "tags": {}, "sample_texts": []}

    node_count = 0
    text_count = 0
    max_depth = 0
    tags: Counter[str] = Counter()
    sample_texts: list[str] = []

    def walk(node: Any, depth: int) -> None:
        nonlocal node_count, text_count, max_depth
        if not isinstance(node, dict):
            return
        max_depth = max(max_depth, depth)
        node_type = node.get("type")
        if node_type == "text":
            text = str(node.get("text") or "").strip()
            if text:
                text_count += 1
                if text not in sample_texts:
                    sample_texts.append(text[:80])
            return
        if node_type == "element":
            node_count += 1
            tag = str(node.get("tag") or "").lower()
            if tag:
                tags[tag] += 1
            text = str(node.get("text") or "").strip()
            if text and text not in sample_texts:
                sample_texts.append(text[:80])
            for child in node.get("children", []) or []:
                walk(child, depth + 1)

    walk(content, 1)
    return {
        "node_count": node_count,
        "text_node_count": text_count,
        "max_depth": max_depth,
        "tags": dict(tags),
        "sample_texts": sample_texts[:8],
    }


def _style_stats(content: Any) -> dict[str, Any]:
    entries = content if isinstance(content, list) else []
    tags: set[str] = set()
    signatures: set[str] = set()
    fonts: set[str] = set()
    sizes: set[str] = set()
    displays: set[str] = set()
    for entry in entries[:40]:
        if not isinstance(entry, dict):
            continue
        tag = str(entry.get("tag") or "").lower()
        if tag:
            tags.add(tag)
        rect = entry.get("rect") if isinstance(entry.get("rect"), dict) else {}
        styles = entry.get("styles") if isinstance(entry.get("styles"), dict) else {}
        signature = _clean_text(entry.get("styleSignature"))
        if not signature:
            signature_parts = [
                tag,
                _clean_text(entry.get("role")),
                _text_profile(entry.get("text")),
                _dimension_bucket(rect.get("width")),
                _dimension_bucket(rect.get("height")),
                _clean_text(styles.get("display")),
                _clean_text(styles.get("position")),
                _clean_text(styles.get("fontFamily")),
                _clean_text(styles.get("fontSize")),
                _clean_text(styles.get("fontWeight")),
                _clean_text(styles.get("lineHeight")),
                _clean_text(styles.get("letterSpacing")),
                _clean_text(styles.get("color")),
                _clean_text(styles.get("backgroundColor")),
                _clean_text(styles.get("borderRadius")),
                _clean_text(styles.get("borderColor")),
                _clean_text(styles.get("borderWidth")),
                _clean_text(styles.get("gap")),
            ]
            signature = "|".join(signature_parts)
        signatures.add(signature)
        if styles.get("fontFamily"):
            fonts.add(_clean_text(styles.get("fontFamily")))
        if styles.get("fontSize"):
            sizes.add(_clean_text(styles.get("fontSize")))
        if styles.get("display"):
            displays.add(_clean_text(styles.get("display")))
    return {
        "sample_count": len(entries),
        "tags": sorted(tags),
        "signatures": sorted(signatures),
        "sample_signatures": sorted(signatures)[:8],
        "fonts": sorted(fonts)[:8],
        "font_sizes": sorted(sizes)[:8],
        "displays": sorted(displays)[:8],
    }


def _dimension_bucket(value: Any) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "0"
    if numeric <= 0:
        return "0"
    if numeric >= 1200:
        return "viewport"
    if numeric >= 720:
        return "xxl"
    if numeric >= 420:
        return "xl"
    if numeric >= 240:
        return "lg"
    if numeric >= 120:
        return "md"
    if numeric >= 48:
        return "sm"
    return "xs"


def _text_profile(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return "empty"
    if len(text) <= 8:
        return "short"
    if len(text) <= 32:
        return "medium"
    if len(text) <= 96:
        return "long"
    return "block"


def _interaction_stats(content: Any) -> dict[str, Any]:
    entries = content if isinstance(content, list) else []
    labels: set[str] = set()
    hover_changed = 0
    focus_changed = 0
    for entry in entries[:24]:
        if not isinstance(entry, dict):
            continue
        label_bits = [
            str(entry.get("tag") or "").lower(),
            _clean_text(entry.get("role")),
            _clean_text(entry.get("text")),
            _clean_text(entry.get("href")),
            _clean_text(entry.get("ariaLabel")),
        ]
        label = "|".join(bit for bit in label_bits if bit)
        if label:
            labels.add(label)
        if isinstance(entry.get("hoverDelta"), dict) and entry["hoverDelta"]:
            hover_changed += 1
        if isinstance(entry.get("focusDelta"), dict) and entry["focusDelta"]:
            focus_changed += 1
    return {
        "entry_count": len(entries),
        "labels": sorted(labels),
        "label_sample": sorted(labels)[:8],
        "hover_changed": hover_changed,
        "focus_changed": focus_changed,
    }


def _interaction_trace_stats(content: Any) -> dict[str, Any]:
    trace = content if isinstance(content, dict) else {}
    steps = trace.get("steps") if isinstance(trace.get("steps"), list) else []
    executions = trace.get("executions") if isinstance(trace.get("executions"), list) else []
    step_kinds: set[str] = set()
    targets: set[str] = set()
    for step in steps[:48]:
        if not isinstance(step, dict):
            continue
        kind = _clean_text(step.get("kind"))
        target = _clean_text(step.get("targetId")) or _clean_text(step.get("selector")) or _clean_text(step.get("label"))
        if kind:
            step_kinds.add(kind)
        if target:
            targets.add(target)
    executed_count = sum(
        1
        for execution in executions[:64]
        if isinstance(execution, dict) and execution.get("status") in {"executed", "planned"}
    )
    return {
        "step_count": len(steps),
        "executed_count": executed_count,
        "step_kinds": sorted(step_kinds),
        "targets": sorted(targets),
        "step_sample": steps[:6],
    }


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def _set_overlap(reference: Any, candidate: Any) -> float:
    reference_set = set(reference or [])
    candidate_set = set(candidate or [])
    if not reference_set or not candidate_set:
        return 0.0
    intersection = len(reference_set & candidate_set)
    union = len(reference_set | candidate_set)
    return intersection / union if union else 0.0


def _tag_overlap(reference: Any, candidate: Any) -> float:
    reference_keys = set(reference.keys()) if isinstance(reference, dict) else set(reference or [])
    candidate_keys = set(candidate.keys()) if isinstance(candidate, dict) else set(candidate or [])
    if not reference_keys or not candidate_keys:
        return 0.0
    return len(reference_keys & candidate_keys) / len(reference_keys | candidate_keys)


def _numeric_delta(reference: Any, candidate: Any) -> int | None:
    if not isinstance(reference, (int, float)) or not isinstance(candidate, (int, float)):
        return None
    return int(candidate - reference)


def _safe_ratio(reference: int | float, candidate: int | float) -> float:
    if not reference or not candidate:
        return 0.0
    return min(reference, candidate) / max(reference, candidate)


def _count_similarity(reference: Any, candidate: Any) -> float:
    if not isinstance(reference, (int, float)) or not isinstance(candidate, (int, float)):
        return 0.0
    reference_value = float(reference)
    candidate_value = float(candidate)
    if reference_value < 0 or candidate_value < 0:
        return 0.0
    if reference_value == 0 and candidate_value == 0:
        return 1.0
    denominator = max(reference_value, candidate_value, 1.0)
    return max(0.0, 1.0 - abs(reference_value - candidate_value) / denominator)


def _artifact_size(artifact: dict[str, Any]) -> int | None:
    metadata = artifact.get("metadata") or {}
    size = metadata.get("byteLength")
    if isinstance(size, (int, float)) and size >= 0:
        return int(size)
    path = artifact.get("path")
    if path:
        try:
            return Path(str(path)).stat().st_size
        except OSError:
            return None
    return None


def _image_dimensions(metadata: dict[str, Any], path: str | None) -> tuple[int, int] | None:
    width = metadata.get("width")
    height = metadata.get("height")
    if isinstance(width, int) and isinstance(height, int):
        return width, height
    if path:
        try:
            with Path(path).open("rb") as handle:
                header = handle.read(24)
            if len(header) >= 24 and header.startswith(b"\x89PNG\r\n\x1a\n") and header[12:16] == b"IHDR":
                return struct.unpack(">II", header[16:24])
        except OSError:
            return None
    return None


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value
    return None


def _first_path(*values: Any) -> Path | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            path = Path(value)
            if path.exists():
                return path
    return None


def _deep_get(source: Any, *keys: str) -> Any:
    current = source
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
