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
)


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
    }

    return {
        "available": True,
        "status": "bounded",
        "verdict": verdict,
        "reference_url": reference.url,
        "candidate_url": candidate.url,
        "message": message,
        "checks": [detail["summary"] for detail in check_details],
        "check_details": check_details,
        "comparison_summary": comparison_summary,
        "artifact_coverage": artifact_coverage,
        "missing_artifacts": missing_artifacts,
        "limitations": [
            "This report is bounded to persisted artifacts and metadata.",
            "It uses bounded PNG fingerprinting, not full pixel-perfect diffing.",
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

    bucket_count = 8
    bucket_sums = [0.0] * (bucket_count * bucket_count)
    bucket_counts = [0] * (bucket_count * bucket_count)
    luma_total = 0.0
    opaque_pixels = 0

    for y in range(height):
        row_start = y * width * 4
        bucket_y = min(bucket_count - 1, (y * bucket_count) // height)
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
            opaque_pixels += 1
            bucket_x = min(bucket_count - 1, (x * bucket_count) // width)
            bucket_index = bucket_y * bucket_count + bucket_x
            bucket_sums[bucket_index] += luma
            bucket_counts[bucket_index] += 1

    if opaque_pixels == 0:
        return None

    bucket_values = [
        (bucket_sums[index] / bucket_counts[index]) if bucket_counts[index] else 0.0
        for index in range(bucket_count * bucket_count)
    ]
    average = sum(bucket_values) / len(bucket_values)
    bits = "".join("1" if value >= average else "0" for value in bucket_values)
    return {
        "width": width,
        "height": height,
        "mean_luma": round(luma_total / opaque_pixels, 4),
        "ahash": f"{int(bits, 2):016x}",
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
    if ref_dims and cand_dims:
        if ref_dims == cand_dims:
            detail.append(f"matching PNG dimensions {ref_dims[0]}x{ref_dims[1]}")
        else:
            detail.append(f"PNG dimensions differ {ref_dims[0]}x{ref_dims[1]} vs {cand_dims[0]}x{cand_dims[1]}")
    if ref_size and cand_size:
        ratio = _safe_ratio(ref_size, cand_size)
        detail.append(f"byte-size ratio {ratio:.2f}")
    visual_similarity = None
    if ref_fingerprint and cand_fingerprint:
        hash_similarity = _hash_similarity(ref_fingerprint.get("ahash"), cand_fingerprint.get("ahash"))
        luma_similarity = _count_similarity(ref_fingerprint.get("mean_luma"), cand_fingerprint.get("mean_luma"))
        if hash_similarity is not None:
            detail.append(f"ahash similarity {hash_similarity:.2f}")
        if luma_similarity is not None:
            detail.append(f"mean-luma similarity {luma_similarity:.2f}")
        visual_parts = [score for score in (hash_similarity, luma_similarity) if score is not None]
        visual_similarity = sum(visual_parts) / len(visual_parts) if visual_parts else None
    summary = "screenshot parity present" if status == "present" else "screenshot parity missing"
    if detail:
        summary = f"{summary} ({'; '.join(detail)})"
    similarity_parts = []
    if ref_dims and cand_dims:
        similarity_parts.append(1.0 if ref_dims == cand_dims else 0.0)
    if ref_size and cand_size:
        similarity_parts.append(_count_similarity(ref_size, cand_size))
    if visual_similarity is not None:
        similarity_parts.append(visual_similarity)
    similarity = sum(similarity_parts) / len(similarity_parts) if similarity_parts else 0.5
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
            "reference_fingerprint": ref_fingerprint,
            "candidate_fingerprint": cand_fingerprint,
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
    similarity_parts = [score for score in [signature_overlap, sample_score, tag_score] if score is not None]
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
        if name in CORE_ARTIFACTS or name in {"accessibility_tree", "network_manifest", "asset_inventory", "html", "session"}
    }


def _missing_artifacts(artifacts: dict[str, dict[str, Any]]) -> list[str]:
    return [
        label
        for label, key in (
            ("screenshot", "screenshot"),
            ("DOM snapshot", "dom_snapshot"),
            ("computed styles", "computed_styles"),
            ("interaction states", "interaction_states"),
        )
        if not artifacts.get(key, {}).get("available")
    ]


def _compute_bounded_score(check_details: list[dict[str, Any]]) -> int:
    weights = {
        "screenshot": 45,
        "dom snapshot": 20,
        "computed styles": 20,
        "interaction states": 15,
    }
    total = 0.0
    for detail in check_details:
        weight = weights.get(detail["name"], 0)
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
    for entry in entries[:24]:
        if not isinstance(entry, dict):
            continue
        tag = str(entry.get("tag") or "").lower()
        if tag:
            tags.add(tag)
        rect = entry.get("rect") if isinstance(entry.get("rect"), dict) else {}
        styles = entry.get("styles") if isinstance(entry.get("styles"), dict) else {}
        signature_parts = [
            tag,
            _clean_text(entry.get("text")),
            _clean_text(rect.get("width")),
            _clean_text(rect.get("height")),
            _clean_text(styles.get("fontFamily")),
            _clean_text(styles.get("fontSize")),
            _clean_text(styles.get("fontWeight")),
            _clean_text(styles.get("color")),
            _clean_text(styles.get("backgroundColor")),
        ]
        signatures.add("|".join(signature_parts))
    return {
        "sample_count": len(entries),
        "tags": sorted(tags),
        "signatures": sorted(signatures),
        "sample_signatures": sorted(signatures)[:8],
    }


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
        return 0.0
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
