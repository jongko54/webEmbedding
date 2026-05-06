"""Deterministic HAR replay helpers.

The engine intentionally stays runtime-agnostic: it parses standard HAR,
near-HAR, or webEmbedding network manifests, indexes request/response pairs,
and returns Playwright-friendly response payloads plus an audit report. Browser
adapters can call ``replay_request`` from a route handler, while CI can run the
same matching logic without launching a browser.
"""

from __future__ import annotations

import base64
import hashlib
import html as html_lib
import json
import posixpath
import re
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit


SCHEMA_VERSION = 1
REPLAY_MAPPING_VERSION = "har-replay.v1"
PARITY_REPORT_VERSION = "har-replay-parity.v1"
HOP_BY_HOP_RESPONSE_HEADERS = {
    "connection",
    "content-encoding",
    "content-length",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
REDACTED_MARKERS = {"", "***", "[redacted]", "redacted", "[sensitive]", "sensitive", "[removed]"}
SENSITIVE_NAME_RE = re.compile(
    r"(authorization|cookie|set-cookie|password|passwd|token|secret|session|api[_-]?key|access[_-]?key|email|code)",
    re.I,
)
DEFAULT_IGNORED_HEADER_NAMES = frozenset(
    {
        "age",
        "alt-svc",
        "cf-ray",
        "date",
        "expires",
        "last-modified",
        "nel",
        "report-to",
        "server-timing",
        "timing-allow-origin",
        "traceparent",
        "tracestate",
        "x-amzn-trace-id",
        "x-request-id",
        "x-runtime",
    }
)
HASH_FIELD_NAMES = (
    "bodySha256",
    "bodySHA256",
    "body_sha256",
    "bodyHash",
    "body_hash",
    "contentSha256",
    "contentSHA256",
    "content_hash",
    "contentHash",
    "sha256",
    "sha256Hash",
    "hash",
)


class HarReplayError(RuntimeError):
    """Base error raised by the HAR replay engine."""


class HarReplayLoadError(HarReplayError):
    """Raised when a file cannot be interpreted as HAR replay input."""


@dataclass(frozen=True)
class ReplayEntry:
    """One replayable HAR entry."""

    index: int
    method: str
    url: str
    normalized_url: str
    request_body_hash: str | None
    request_body_size: int
    status: int
    status_text: str | None
    headers: dict[str, str]
    body_text: str
    body_encoding: str
    body_sha256: str
    body_size: int
    body_available: bool
    content_type: str | None
    started_at: str | None
    source: dict[str, Any]


def load_json(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    try:
        payload = json.loads(resolved.read_text())
    except json.JSONDecodeError as exc:
        raise HarReplayLoadError(f"{resolved} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise HarReplayLoadError(f"{resolved} must contain a JSON object")
    return payload


def _entry_source(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    log = payload.get("log")
    if isinstance(log, dict) and isinstance(log.get("entries"), list):
        return [entry for entry in log["entries"] if isinstance(entry, dict)], "har", payload.get("summary") if isinstance(payload.get("summary"), dict) else {}

    if isinstance(payload.get("entries"), list):
        return [entry for entry in payload["entries"] if isinstance(entry, dict)], "near-har", payload.get("summary") if isinstance(payload.get("summary"), dict) else {}

    nested_har = payload.get("har")
    if isinstance(nested_har, dict):
        entries, _source, summary = _entry_source(nested_har)
        if entries:
            return entries, "network-manifest.har", summary

    har_like = payload.get("harLike")
    if isinstance(har_like, dict):
        entries, _source, summary = _entry_source(har_like)
        if entries:
            return entries, "network-manifest.harLike", summary

    raise HarReplayLoadError("input does not contain HAR log.entries, near-HAR entries, or network manifest har/harLike entries")


def normalize_url(
    url: str,
    *,
    base_url: str | None = None,
    sort_query: bool = True,
    include_fragment: bool = False,
    strip_fragment: bool | None = None,
    drop_default_port: bool = True,
    redact_sensitive_query_values: bool = True,
) -> str:
    raw_url = str(url or "").strip()
    if base_url and raw_url:
        raw_url = urljoin(base_url, raw_url)
    parts = urlsplit(raw_url)
    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    if drop_default_port and ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        port = None
    userinfo = ""
    if parts.username:
        userinfo = quote(parts.username, safe="")
        if parts.password:
            userinfo += ":[REDACTED]"
        userinfo += "@"
    netloc = userinfo + hostname
    if port is not None:
        netloc += f":{port}"
    path = parts.path or "/"
    if path.startswith("/"):
        normalized_path = posixpath.normpath(path)
        if path.endswith("/") and not normalized_path.endswith("/"):
            normalized_path += "/"
        path = normalized_path if normalized_path.startswith("/") else "/" + normalized_path
    query = parts.query
    if sort_query and query:
        pairs = parse_qsl(query, keep_blank_values=True)
        if redact_sensitive_query_values:
            pairs = [(name, "[REDACTED]" if SENSITIVE_NAME_RE.search(name) else value) for name, value in pairs]
        query = urlencode(sorted(pairs), doseq=True)
    if strip_fragment is None:
        fragment = parts.fragment if include_fragment else ""
    else:
        fragment = "" if strip_fragment else parts.fragment
    return urlunsplit((scheme, netloc, path, query, fragment))


def _headers_to_dict(headers: Any, *, drop_hop_by_hop: bool = True) -> dict[str, str]:
    if isinstance(headers, dict):
        iterable = [{"name": key, "value": value} for key, value in headers.items()]
    elif isinstance(headers, list):
        iterable = headers
    else:
        iterable = []

    result: dict[str, str] = {}
    for item in iterable:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("key") or "").strip()
        if not name:
            continue
        lowered = name.lower()
        if drop_hop_by_hop and lowered in HOP_BY_HOP_RESPONSE_HEADERS:
            continue
        value = "" if item.get("value") is None else str(item.get("value"))
        if lowered in result and lowered != "set-cookie":
            result[lowered] = f"{result[lowered]}, {value}"
        else:
            result[lowered] = value
    return result


def _request_body_bytes(request: dict[str, Any]) -> bytes:
    post_data = request.get("postData")
    if isinstance(post_data, dict):
        text = post_data.get("text")
        if isinstance(text, str):
            return text.encode("utf-8")
        params = post_data.get("params")
        if isinstance(params, list):
            pairs = []
            for param in params:
                if isinstance(param, dict) and param.get("name") is not None:
                    pairs.append((str(param.get("name")), "" if param.get("value") is None else str(param.get("value"))))
            if pairs:
                return urlencode(pairs).encode("utf-8")
    if isinstance(post_data, str):
        return post_data.encode("utf-8")
    return b""


def request_body_bytes(value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, dict):
        for key in ("postData", "post_data", "body", "text"):
            if key in value:
                return request_body_bytes(value.get(key))
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return str(value).encode("utf-8")


def _sha256_or_none(data: bytes) -> str | None:
    return hashlib.sha256(data).hexdigest() if data else None


def _response_content(response: dict[str, Any]) -> tuple[str, str, str, int, bool, str | None]:
    content = response.get("content") if isinstance(response.get("content"), dict) else {}
    content_type = content.get("mimeType") or response.get("contentType")
    raw_text = content.get("text")
    encoding = str(content.get("encoding") or "").lower()
    if isinstance(raw_text, str):
        if encoding == "base64":
            try:
                body_bytes = base64.b64decode(raw_text, validate=False)
            except Exception:
                body_bytes = raw_text.encode("utf-8")
            body_text = base64.b64encode(body_bytes).decode("ascii")
            body_encoding = "base64"
        else:
            body_bytes = raw_text.encode("utf-8")
            body_text = raw_text
            body_encoding = "text"
        return body_text, body_encoding, hashlib.sha256(body_bytes).hexdigest(), len(body_bytes), True, str(content_type) if content_type else None

    status = int(response.get("status") or 0)
    if status in {204, 205, 304}:
        return "", "text", hashlib.sha256(b"").hexdigest(), 0, True, str(content_type) if content_type else None
    return "", "text", hashlib.sha256(b"").hexdigest(), 0, False, str(content_type) if content_type else None


def _entry_from_har(index: int, entry: dict[str, Any]) -> ReplayEntry | None:
    request = entry.get("request") if isinstance(entry.get("request"), dict) else {}
    response = entry.get("response") if isinstance(entry.get("response"), dict) else {}
    if not request or not response:
        return None

    method = str(request.get("method") or "GET").upper()
    url = str(request.get("url") or "")
    if not url:
        return None

    body_bytes = _request_body_bytes(request)
    body_text, body_encoding, body_sha256, body_size, body_available, content_type = _response_content(response)
    status = int(response.get("status") or 0)
    return ReplayEntry(
        index=index,
        method=method,
        url=url,
        normalized_url=normalize_url(url),
        request_body_hash=_sha256_or_none(body_bytes),
        request_body_size=len(body_bytes),
        status=status,
        status_text=str(response.get("statusText")) if response.get("statusText") is not None else None,
        headers=_headers_to_dict(response.get("headers")),
        body_text=body_text,
        body_encoding=body_encoding,
        body_sha256=body_sha256,
        body_size=body_size,
        body_available=body_available,
        content_type=content_type,
        started_at=str(entry.get("startedDateTime")) if entry.get("startedDateTime") else None,
        source=entry,
    )


def normalize_request_spec(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("request specs must be JSON objects")
    method = str(value.get("method") or "GET").upper()
    url = value.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError("request specs require a non-empty url")
    post_data = value.get("postData", value.get("post_data", value.get("body")))
    normalized: dict[str, Any] = {"method": method, "url": url.strip()}
    if post_data is not None:
        normalized["postData"] = post_data
    if value.get("id") is not None:
        normalized["id"] = str(value.get("id"))
    return normalized


def load_request_specs(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).expanduser().resolve().read_text())
    if isinstance(payload, dict):
        raw_requests = payload.get("requests")
    else:
        raw_requests = payload
    if not isinstance(raw_requests, list):
        raise ValueError("request JSON must be an array or an object with a requests array")
    return [normalize_request_spec(item) for item in raw_requests]


class HarReplayEngine:
    """In-memory HAR replay index."""

    def __init__(self, entries: list[ReplayEntry], *, source_path: str | None = None, source_format: str = "har", source_summary: dict[str, Any] | None = None) -> None:
        self.entries = entries
        self.source_path = source_path
        self.source_format = source_format
        self.source_summary = dict(source_summary or {})
        self._by_url: dict[tuple[str, str], list[ReplayEntry]] = {}
        self._cursor: dict[tuple[str, str, str | None], int] = {}
        for entry in self.entries:
            self._by_url.setdefault((entry.method, entry.normalized_url), []).append(entry)

    @classmethod
    def from_file(cls, path: str | Path) -> "HarReplayEngine":
        resolved = Path(path).expanduser().resolve()
        payload = load_json(resolved)
        raw_entries, source_format, summary = _entry_source(payload)
        entries = [
            parsed
            for index, entry in enumerate(raw_entries)
            for parsed in [_entry_from_har(index, entry)]
            if parsed is not None
        ]
        return cls(entries, source_path=str(resolved), source_format=source_format, source_summary=summary)

    def summary(self) -> dict[str, Any]:
        status_counts: dict[str, int] = {}
        method_counts: dict[str, int] = {}
        for entry in self.entries:
            status_counts[str(entry.status)] = status_counts.get(str(entry.status), 0) + 1
            method_counts[entry.method] = method_counts.get(entry.method, 0) + 1
        return {
            "source_path": self.source_path,
            "source_format": self.source_format,
            "entry_count": len(self.entries),
            "replayable_entry_count": len([entry for entry in self.entries if entry.status > 0]),
            "entries_with_response_body": len([entry for entry in self.entries if entry.body_available]),
            "entries_with_request_body": len([entry for entry in self.entries if entry.request_body_hash]),
            "status_counts": status_counts,
            "method_counts": method_counts,
            "source_summary": self.source_summary,
        }

    def _select_entry(self, candidates: list[ReplayEntry], body_hash: str | None, *, consume: bool) -> tuple[ReplayEntry | None, str, str | None]:
        exact_body = [entry for entry in candidates if entry.request_body_hash == body_hash]
        if exact_body:
            key = (exact_body[0].method, exact_body[0].normalized_url, body_hash)
            index = self._cursor.get(key, 0) if consume else 0
            if index >= len(exact_body):
                index = len(exact_body) - 1
            if consume:
                self._cursor[key] = index + 1
            return exact_body[index], "exact", None

        if body_hash is not None:
            no_body = [entry for entry in candidates if entry.request_body_hash is None]
            if no_body:
                return no_body[0], "url_only", "request body was not present in the HAR entry; matched by method and URL"
            return None, "body_mismatch", "method and URL matched, but request body hash did not match any HAR entry"

        no_body = [entry for entry in candidates if entry.request_body_hash is None]
        if no_body:
            return no_body[0], "exact", None
        return None, "body_mismatch", "method and URL matched, but the HAR entry requires a request body"

    def replay_request(self, method: str, url: str, post_data: Any = None, *, consume: bool = False) -> dict[str, Any]:
        normalized_method = str(method or "GET").upper()
        normalized_url = normalize_url(url)
        body = request_body_bytes(post_data)
        body_hash = _sha256_or_none(body)
        candidates = self._by_url.get((normalized_method, normalized_url), [])

        if not candidates:
            methodless_matches = sum(1 for entry in self.entries if entry.normalized_url == normalized_url)
            return {
                "matched": False,
                "match_type": "missing",
                "request": {
                    "method": normalized_method,
                    "url": url,
                    "normalized_url": normalized_url,
                    "body_sha256": body_hash,
                },
                "reason": "no HAR entry matched request method and normalized URL",
                "candidate_counts": {
                    "same_url_different_method": methodless_matches,
                },
            }

        entry, match_type, reason = self._select_entry(candidates, body_hash, consume=consume)
        if entry is None:
            return {
                "matched": False,
                "match_type": match_type,
                "request": {
                    "method": normalized_method,
                    "url": url,
                    "normalized_url": normalized_url,
                    "body_sha256": body_hash,
                },
                "reason": reason,
                "candidate_counts": {
                    "same_method_url": len(candidates),
                    "request_body_hashes": sorted({entry.request_body_hash for entry in candidates if entry.request_body_hash}),
                },
            }

        response = {
            "status": entry.status,
            "status_text": entry.status_text,
            "headers": entry.headers,
            "body": entry.body_text,
            "body_encoding": entry.body_encoding,
            "body_sha256": entry.body_sha256,
            "body_size": entry.body_size,
            "body_available": entry.body_available,
            "content_type": entry.content_type,
        }
        return {
            "matched": True,
            "match_type": match_type,
            "request": {
                "method": normalized_method,
                "url": url,
                "normalized_url": normalized_url,
                "body_sha256": body_hash,
            },
            "response": response,
            "entry": {
                "index": entry.index,
                "method": entry.method,
                "url": entry.url,
                "normalized_url": entry.normalized_url,
                "request_body_sha256": entry.request_body_hash,
                "request_body_size": entry.request_body_size,
                "started_at": entry.started_at,
            },
            "reason": reason,
        }

    def replay_requests(self, requests: list[dict[str, Any]], *, consume: bool = False) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for index, raw_request in enumerate(requests):
            request = normalize_request_spec(raw_request)
            result = self.replay_request(
                request["method"],
                request["url"],
                request.get("postData"),
                consume=consume,
            )
            result["id"] = request.get("id", f"request-{index + 1}")
            results.append(result)
        return results


def summarize_replay_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    match_type_counts: dict[str, int] = {}
    for result in results:
        match_type = str(result.get("match_type") or "unknown")
        match_type_counts[match_type] = match_type_counts.get(match_type, 0) + 1
    matched = sum(1 for result in results if result.get("matched") is True)
    body_available = sum(
        1
        for result in results
        if isinstance(result.get("response"), dict) and result["response"].get("body_available") is True
    )
    return {
        "request_count": len(results),
        "matched": matched,
        "missing": len(results) - matched,
        "body_available": body_available,
        "match_type_counts": match_type_counts,
        "ready": bool(results) and matched == len(results),
    }


def build_replay_report(
    har_path: str | Path,
    requests: list[dict[str, Any]] | None = None,
    *,
    output_path: str | Path | None = None,
    consume: bool = True,
) -> dict[str, Any]:
    engine = HarReplayEngine.from_file(har_path)
    replay_results = engine.replay_requests(requests or [], consume=consume) if requests else []
    replay_summary = summarize_replay_results(replay_results)
    report = {
        "schema_version": SCHEMA_VERSION,
        "tool": "webEmbedding HAR replay engine",
        "source": engine.summary(),
        "summary": replay_summary,
        "requests": replay_results,
    }
    if output_path:
        resolved = Path(output_path).expanduser().resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        report["path"] = str(resolved)
    return report


@dataclass
class _ReplayLoadedPayload:
    payload: Any
    source_label: str
    path: Path | None = None


@dataclass
class _ReplayMappingEntry:
    index: int
    key: str
    base_key: str
    method_url_key: str
    url_key: str
    occurrence: int
    method_url_occurrence: int
    url_occurrence: int
    method: str
    url: str
    normalized_url: str
    status: int | None
    status_text: str | None
    request_headers: dict[str, list[str]]
    response_headers: dict[str, list[str]]
    request_body_hash: str | None
    request_body_hash_source: str | None
    content: dict[str, Any]
    resource_type: str | None
    started_datetime: str | None
    response_body_text: str | None
    raw_entry: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "key": self.key,
            "base_key": self.base_key,
            "method_url_key": self.method_url_key,
            "url_key": self.url_key,
            "occurrence": self.occurrence,
            "method_url_occurrence": self.method_url_occurrence,
            "url_occurrence": self.url_occurrence,
            "method": self.method,
            "url": self.url,
            "normalized_url": self.normalized_url,
            "status": self.status,
            "status_text": self.status_text,
            "request_headers": self.request_headers,
            "response_headers": self.response_headers,
            "request_body_hash": self.request_body_hash,
            "request_body_hash_source": self.request_body_hash_source,
            "content": self.content,
            "resource_type": self.resource_type,
            "started_datetime": self.started_datetime,
            "response_body_text": self.response_body_text,
        }

    def replay_response(self, *, include_body_text: bool = True) -> dict[str, Any]:
        return {
            "status": self.status,
            "status_text": self.status_text,
            "headers": _har_map_headers_for_replay(self.response_headers),
            "content": self.content,
            "body_text": self.response_body_text if include_body_text else None,
            "raw_entry_index": self.index,
            "key": self.key,
        }


class ReplayRequestMatcher:
    """Stateful deterministic matcher for real network replay adapters."""

    def __init__(self, mapping_or_source: Any, *, consume_default: bool = True, base_url: str | None = None):
        self.mapping = build_replay_mapping(mapping_or_source, base_url=base_url)
        self.entries = _har_map_entries_from_mapping(self.mapping)
        self.consume_default = consume_default
        self.used_keys: set[str] = set()
        self._by_base_key: dict[str, deque[_ReplayMappingEntry]] = defaultdict(deque)
        self._by_method_url_key: dict[str, deque[_ReplayMappingEntry]] = defaultdict(deque)
        self._by_url_key: dict[str, deque[_ReplayMappingEntry]] = defaultdict(deque)
        for entry in self.entries:
            self._by_base_key[entry.base_key].append(entry)
            self._by_method_url_key[entry.method_url_key].append(entry)
            self._by_url_key[entry.url_key].append(entry)

    def reset(self) -> None:
        self.used_keys.clear()

    def match(
        self,
        method: str | dict[str, Any],
        url: str | None = None,
        *,
        headers: Any = None,
        body: Any = None,
        post_data: Any = None,
        consume: bool | None = None,
        strict_method: bool = True,
        allow_url_fallback: bool = True,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        request = dict(method) if isinstance(method, dict) else {"method": method, "url": url, "headers": headers}
        if body is not None:
            request["body"] = body
        if post_data is not None:
            request["postData"] = post_data
        parts = _har_map_request_parts(request, base_url=base_url)
        consume_match = self.consume_default if consume is None else bool(consume)

        probes: list[tuple[str, str, deque[_ReplayMappingEntry]]] = [
            ("exact", parts["base_key"], self._by_base_key.get(parts["base_key"], deque())),
            (
                "method-url",
                parts["method_url_key"],
                self._by_method_url_key.get(parts["method_url_key"], deque()),
            ),
        ]
        if allow_url_fallback and not strict_method:
            probes.append(("url", parts["url_key"], self._by_url_key.get(parts["url_key"], deque())))

        for strength, key, candidates in probes:
            entry = self._first_available(
                candidates,
                consume=consume_match,
                request_body_hash=parts.get("request_body_hash"),
            )
            if entry is None:
                continue
            return {
                "matched": True,
                "match_strength": strength,
                "lookup_key": key,
                "request": parts,
                "entry": entry.to_manifest(),
                "response": entry.replay_response(),
            }

        reason = "no replay entry matched request"
        if parts.get("request_body_hash"):
            reason = "no replay entry matched request method, URL, and request body"
        return {"matched": False, "request": parts, "reason": reason}

    def _first_available(
        self,
        candidates: deque[_ReplayMappingEntry],
        *,
        consume: bool,
        request_body_hash: str | None,
    ) -> _ReplayMappingEntry | None:
        for entry in list(candidates):
            if entry.key in self.used_keys:
                continue
            if request_body_hash and entry.request_body_hash not in {None, request_body_hash}:
                continue
            if request_body_hash is None and entry.request_body_hash is not None:
                continue
            if consume:
                try:
                    candidates.remove(entry)
                except ValueError:
                    pass
                self.used_keys.add(entry.key)
            return entry
        return None


def load_har_payload(source: Any) -> Any:
    """Load a HAR, near-HAR, network manifest, capture dir, or capture JSON."""

    return _har_map_load_payload(source).payload


def build_replay_mapping(
    source: Any,
    *,
    base_url: str | None = None,
    include_request_body_hash_in_key: bool = True,
) -> dict[str, Any]:
    """Build a deterministic replay mapping from persisted HAR-like evidence."""

    loaded = _har_map_load_payload(source)
    if _har_map_is_replay_mapping(loaded.payload):
        return loaded.payload

    raw_entries = _har_map_extract_entries(loaded.payload, source_path=loaded.path)
    entries: list[_ReplayMappingEntry] = []
    base_counts: Counter[str] = Counter()
    method_url_counts: Counter[str] = Counter()
    url_counts: Counter[str] = Counter()

    for index, raw_entry in enumerate(raw_entries):
        entry = _har_map_entry_from_raw(raw_entry, index=index, base_url=base_url)
        method_url_key = f"{entry.method} {entry.normalized_url}"
        base_key = method_url_key
        if include_request_body_hash_in_key and entry.request_body_hash:
            base_key = f"{method_url_key} body={entry.request_body_hash}"
        url_key = entry.normalized_url

        base_counts[base_key] += 1
        method_url_counts[method_url_key] += 1
        url_counts[url_key] += 1
        entry.method_url_key = method_url_key
        entry.base_key = base_key
        entry.url_key = url_key
        entry.occurrence = base_counts[base_key]
        entry.method_url_occurrence = method_url_counts[method_url_key]
        entry.url_occurrence = url_counts[url_key]
        entry.key = f"{base_key} @{entry.occurrence}"
        entries.append(entry)

    routes_by_base: dict[str, list[str]] = defaultdict(list)
    routes_by_method_url: dict[str, list[str]] = defaultdict(list)
    routes_by_url: dict[str, list[str]] = defaultdict(list)
    status_counts: Counter[str] = Counter()
    method_counts: Counter[str] = Counter()
    for entry in entries:
        routes_by_base[entry.base_key].append(entry.key)
        routes_by_method_url[entry.method_url_key].append(entry.key)
        routes_by_url[entry.url_key].append(entry.key)
        status_counts[str(entry.status if entry.status is not None else "missing")] += 1
        method_counts[entry.method] += 1

    return {
        "version": REPLAY_MAPPING_VERSION,
        "source": loaded.source_label,
        "entry_count": len(entries),
        "entries": [entry.to_manifest() for entry in entries],
        "routes": {
            "by_base_key": dict(routes_by_base),
            "by_method_url": dict(routes_by_method_url),
            "by_url": dict(routes_by_url),
        },
        "summary": {
            "method_counts": dict(method_counts),
            "status_counts": dict(status_counts),
            "unique_base_route_count": len(routes_by_base),
            "unique_url_count": len(routes_by_url),
            "with_response_body_hash": sum(1 for entry in entries if entry.content.get("body_hash")),
            "with_request_body_hash": sum(1 for entry in entries if entry.request_body_hash),
        },
    }


def request_matcher(mapping_or_source: Any, *, consume_default: bool = True, base_url: str | None = None) -> ReplayRequestMatcher:
    """Return a stateful request matcher for replay adapters."""

    return ReplayRequestMatcher(mapping_or_source, consume_default=consume_default, base_url=base_url)


def compare_live_or_candidate_manifest(
    reference: Any,
    candidate: Any,
    *,
    base_url: str | None = None,
    ignored_headers: set[str] | frozenset[str] | None = None,
    compare_request_headers: bool = True,
    compare_response_headers: bool = True,
    compare_content: bool = True,
) -> dict[str, Any]:
    """Compare URL, method, status, headers, and content metadata parity."""

    ignored = {name.lower() for name in (ignored_headers or DEFAULT_IGNORED_HEADER_NAMES)}
    reference_mapping = build_replay_mapping(reference, base_url=base_url)
    candidate_mapping = build_replay_mapping(candidate, base_url=base_url)
    reference_entries = _har_map_entries_from_mapping(reference_mapping)
    candidate_entries = _har_map_entries_from_mapping(candidate_mapping)
    candidate_by_base = _har_map_index_entries(candidate_entries, "base")
    candidate_by_method_url = _har_map_index_entries(candidate_entries, "method_url")
    candidate_by_url = _har_map_index_entries(candidate_entries, "url")
    used_candidate_keys: set[str] = set()
    matches: list[dict[str, Any]] = []
    missing_entries: list[dict[str, Any]] = []
    drift_groups: dict[str, list[dict[str, Any]]] = {
        "url": [],
        "method": [],
        "status": [],
        "request_headers": [],
        "response_headers": [],
        "request_body": [],
        "content": [],
    }

    for reference_entry in reference_entries:
        candidate_entry, match_strength = _har_map_claim_candidate(
            reference_entry,
            candidate_by_base=candidate_by_base,
            candidate_by_method_url=candidate_by_method_url,
            candidate_by_url=candidate_by_url,
            used_candidate_keys=used_candidate_keys,
        )
        if candidate_entry is None:
            missing = _har_map_entry_public(reference_entry)
            missing_entries.append(missing)
            matches.append(
                {
                    "status": "missing",
                    "match_strength": "missing",
                    "reference": missing,
                    "candidate": None,
                    "drifts": ["missing"],
                    "comparisons": {},
                }
            )
            continue

        comparison = _har_map_compare_entries(
            reference_entry,
            candidate_entry,
            ignored_headers=ignored,
            compare_request_headers=compare_request_headers,
            compare_response_headers=compare_response_headers,
            compare_content=compare_content,
        )
        for drift_name in comparison["drifts"]:
            if drift_name in drift_groups:
                drift_groups[drift_name].append(
                    {
                        "reference_key": reference_entry.key,
                        "candidate_key": candidate_entry.key,
                        "reference": _har_map_entry_public(reference_entry),
                        "candidate": _har_map_entry_public(candidate_entry),
                        "details": comparison["comparisons"].get(drift_name),
                    }
                )
        matches.append(
            {
                "status": "pass" if not comparison["drifts"] else "drift",
                "match_strength": match_strength,
                "reference": _har_map_entry_public(reference_entry),
                "candidate": _har_map_entry_public(candidate_entry),
                "drifts": comparison["drifts"],
                "comparisons": comparison["comparisons"],
            }
        )

    extra_entries = [_har_map_entry_public(entry) for entry in candidate_entries if entry.key not in used_candidate_keys]
    matched_count = sum(1 for item in matches if item["status"] in {"pass", "drift"})
    drift_entry_count = sum(1 for item in matches if item["status"] == "drift")
    missing_count = len(missing_entries)
    extra_count = len(extra_entries)
    blocking_count = drift_entry_count + missing_count + extra_count
    denominator = max(1, len(reference_entries) + extra_count)
    score = max(0.0, round(1.0 - (blocking_count / denominator), 4))
    verdict = "pass" if blocking_count == 0 else "drift"

    return {
        "available": True,
        "report_version": PARITY_REPORT_VERSION,
        "verdict": verdict,
        "ready_for_replay": verdict == "pass",
        "summary": {
            "reference_entry_count": len(reference_entries),
            "candidate_entry_count": len(candidate_entries),
            "matched_count": matched_count,
            "clean_match_count": sum(1 for item in matches if item["status"] == "pass"),
            "drift_entry_count": drift_entry_count,
            "missing_count": missing_count,
            "extra_count": extra_count,
            "url_drift_count": len(drift_groups["url"]),
            "method_drift_count": len(drift_groups["method"]),
            "status_drift_count": len(drift_groups["status"]),
            "request_header_drift_count": len(drift_groups["request_headers"]),
            "response_header_drift_count": len(drift_groups["response_headers"]),
            "request_body_drift_count": len(drift_groups["request_body"]),
            "content_drift_count": len(drift_groups["content"]),
            "score": score,
        },
        "ignored_headers": sorted(ignored),
        "matches": matches,
        "missing_entries": missing_entries,
        "extra_entries": extra_entries,
        "drifts": drift_groups,
        "reference_mapping_summary": reference_mapping.get("summary", {}),
        "candidate_mapping_summary": candidate_mapping.get("summary", {}),
    }


def export_offline_html_renderer(
    source: Any,
    output_path: str | Path | None = None,
    *,
    title: str = "HAR Replay Renderer",
    base_url: str | None = None,
) -> dict[str, Any]:
    """Export a self-contained offline HTML inspector for a replay mapping."""

    mapping = build_replay_mapping(source, base_url=base_url)
    safe_payload = json.dumps(mapping, ensure_ascii=True, sort_keys=True).replace("</", "<\\/")
    escaped_title = html_lib.escape(title)
    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escaped_title}</title>
  <style>
    :root {{ font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f7f8fb; color: #1f2937; }}
    body {{ margin: 0; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 24px; }}
    header {{ display: flex; justify-content: space-between; gap: 16px; align-items: flex-end; margin-bottom: 18px; }}
    h1 {{ margin: 0; font-size: 22px; line-height: 1.2; }}
    .summary {{ color: #4b5563; font-size: 13px; }}
    input {{ width: 100%; box-sizing: border-box; padding: 10px 12px; border: 1px solid #c8d0dc; border-radius: 6px; font: inherit; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 14px; background: #fff; border: 1px solid #d8dee8; }}
    th, td {{ padding: 9px 10px; border-bottom: 1px solid #e5e9f0; text-align: left; vertical-align: top; font-size: 13px; }}
    th {{ background: #eef2f7; color: #374151; font-weight: 650; }}
    tr:hover {{ background: #f7fbff; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; overflow-wrap: anywhere; }}
    .status {{ font-weight: 700; }}
    .muted {{ color: #6b7280; }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>{escaped_title}</h1>
        <div class="summary" id="summary"></div>
      </div>
    </header>
    <input id="filter" type="search" placeholder="Filter by method, status, URL, or content type" />
    <table>
      <thead><tr><th>Method</th><th>Status</th><th>URL</th><th>Content</th><th>Replay Key</th></tr></thead>
      <tbody id="rows"></tbody>
    </table>
  </main>
  <script id="replay-data" type="application/json">{safe_payload}</script>
  <script>
    const data = JSON.parse(document.getElementById("replay-data").textContent);
    const rows = document.getElementById("rows");
    const filter = document.getElementById("filter");
    const summary = document.getElementById("summary");
    const entries = data.entries || [];
    summary.textContent = `${{entries.length}} entries, ${{data.summary?.unique_url_count || 0}} unique URLs`;
    function render() {{
      const q = filter.value.trim().toLowerCase();
      rows.textContent = "";
      for (const entry of entries) {{
        const content = entry.content || {{}};
        const haystack = [entry.method, entry.status, entry.normalized_url, content.mime_type, entry.key].join(" ").toLowerCase();
        if (q && !haystack.includes(q)) continue;
        const tr = document.createElement("tr");
        tr.innerHTML = `<td><code>${{entry.method || ""}}</code></td><td class="status">${{entry.status ?? ""}}</td><td><code>${{entry.normalized_url || entry.url || ""}}</code></td><td><span>${{content.mime_type || ""}}</span><br><span class="muted">${{content.body_hash || "no body hash"}}</span></td><td><code>${{entry.key || ""}}</code></td>`;
        rows.appendChild(tr);
      }}
    }}
    filter.addEventListener("input", render);
    render();
  </script>
</body>
</html>
"""
    result = {"available": True, "entry_count": mapping.get("entry_count", 0), "mapping_version": mapping.get("version")}
    if output_path is not None:
        target = Path(output_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html_text, encoding="utf-8")
        result["path"] = str(target)
    else:
        result["html"] = html_text
    return result


def _har_map_load_payload(source: Any) -> _ReplayLoadedPayload:
    if isinstance(source, (dict, list)):
        return _ReplayLoadedPayload(payload=source, source_label="memory")
    if isinstance(source, str) and source.strip().startswith(("{", "[")):
        return _ReplayLoadedPayload(payload=json.loads(source), source_label="json-string")
    path = Path(source).expanduser().resolve()
    if path.is_dir():
        for candidate in (
            path / "network" / "har.json",
            path / "network" / "har-like.json",
            path / "network" / "manifest.json",
            path / "har.json",
            path / "har-like.json",
            path / "manifest.json",
        ):
            if candidate.is_file():
                return _har_map_load_payload(candidate)
        raise HarReplayLoadError(f"No HAR-like network artifact found under {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _ReplayLoadedPayload(payload=payload, source_label=str(path), path=path)


def _har_map_is_replay_mapping(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("version") == REPLAY_MAPPING_VERSION and isinstance(payload.get("entries"), list)


def _har_map_extract_entries(payload: Any, *, source_path: Path | None = None) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [_har_map_as_dict(item) for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    log = _har_map_as_dict(payload.get("log"))
    if isinstance(log.get("entries"), list):
        return [_har_map_as_dict(item) for item in log["entries"] if isinstance(item, dict)]
    for key in ("har", "harLike", "har_like"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            entries = _har_map_extract_entries(nested, source_path=source_path)
            if entries:
                return entries
    if isinstance(payload.get("entries"), list):
        return [_har_map_as_dict(item) for item in payload["entries"] if isinstance(item, dict)]
    captures = _har_map_as_dict(_har_map_as_dict(payload.get("runtime")).get("captures"))
    network_content = _har_map_as_dict(captures.get("network")).get("content")
    if isinstance(network_content, dict):
        entries = _har_map_extract_entries(network_content, source_path=source_path)
        if entries:
            return entries
    if isinstance(payload.get("requests"), list) or isinstance(payload.get("responses"), list):
        return _har_map_entries_from_network_manifest(payload)
    persisted = _har_map_as_dict(_har_map_as_dict(_har_map_as_dict(payload.get("bundle")).get("persisted")).get("files"))
    for key in ("network_har", "network_har_like", "network_manifest"):
        path_value = persisted.get(key)
        if not path_value:
            continue
        candidate = Path(str(path_value)).expanduser()
        if not candidate.is_absolute() and source_path is not None:
            candidate = source_path.parent / candidate
        if candidate.is_file():
            loaded = _har_map_load_payload(candidate)
            entries = _har_map_extract_entries(loaded.payload, source_path=loaded.path)
            if entries:
                return entries
    return []


def _har_map_entries_from_network_manifest(payload: dict[str, Any]) -> list[dict[str, Any]]:
    requests = [item for item in payload.get("requests", []) if isinstance(item, dict)]
    responses = [item for item in payload.get("responses", []) if isinstance(item, dict)]
    failures = [item for item in payload.get("failures", []) if isinstance(item, dict)]
    responses_by_id = {item.get("requestId"): item for item in responses if item.get("requestId") is not None}
    failures_by_id = {item.get("requestId"): item for item in failures if item.get("requestId") is not None}
    responses_by_method_url: dict[tuple[str, str], deque[dict[str, Any]]] = defaultdict(deque)
    for response in responses:
        responses_by_method_url[(str(response.get("method") or "GET").upper(), str(response.get("url") or ""))].append(response)
    entries: list[dict[str, Any]] = []
    used_response_ids: set[int] = set()
    for request in requests:
        response = responses_by_id.get(request.get("requestId"))
        if response is not None:
            used_response_ids.add(id(response))
        if response is None:
            response = _har_map_popleft_response(responses_by_method_url, request, used_response_ids=used_response_ids)
        request_headers = request.get("requestHeadersArray") or request.get("headers") or request.get("requestHeaders")
        response_headers = response.get("responseHeadersArray") or response.get("headers") or response.get("responseHeaders") if isinstance(response, dict) else []
        entries.append(
            {
                "startedDateTime": request.get("startedDateTime") or request.get("observedAt"),
                "request": {
                    "method": request.get("method"),
                    "url": request.get("url"),
                    "headers": request_headers,
                    "queryString": request.get("queryString") or [],
                    "postData": _har_map_manifest_post_data(request),
                    "resourceType": request.get("resourceType"),
                    "bodySize": request.get("postDataSize") or request.get("bodySize") or 0,
                },
                "response": {
                    "status": response.get("status") if isinstance(response, dict) else None,
                    "statusText": response.get("statusText") if isinstance(response, dict) else None,
                    "headers": response_headers or [],
                    "content": {
                        "size": _har_map_int_or_none(response.get("contentLength")) if isinstance(response, dict) else None,
                        "mimeType": response.get("contentType") if isinstance(response, dict) else None,
                        "text": None,
                    },
                    "bodySize": _har_map_int_or_none(response.get("contentLength")) if isinstance(response, dict) else -1,
                }
                if response
                else None,
                "failure": failures_by_id.get(request.get("requestId")),
            }
        )
    return entries


def _har_map_popleft_response(
    responses_by_method_url: dict[tuple[str, str], deque[dict[str, Any]]],
    request: dict[str, Any],
    *,
    used_response_ids: set[int],
) -> dict[str, Any] | None:
    queue = responses_by_method_url.get((str(request.get("method") or "GET").upper(), str(request.get("url") or "")))
    while queue:
        response = queue.popleft()
        if id(response) in used_response_ids:
            continue
        used_response_ids.add(id(response))
        return response
    return None


def _har_map_manifest_post_data(request: dict[str, Any]) -> dict[str, Any] | None:
    text = request.get("postDataText")
    params = request.get("postDataParams")
    if text is None and not params and not request.get("hasPostData"):
        return None
    return {
        "text": text,
        "params": params or [],
        "mimeType": _har_map_header_value(_har_map_normalize_headers(request.get("requestHeadersArray") or request.get("headers")), "content-type"),
    }


def _har_map_entry_from_raw(raw_entry: dict[str, Any], *, index: int, base_url: str | None) -> _ReplayMappingEntry:
    request = _har_map_as_dict(raw_entry.get("request"))
    response = _har_map_as_dict(raw_entry.get("response"))
    if not request:
        request = {
            "method": raw_entry.get("method"),
            "url": raw_entry.get("url"),
            "headers": _har_map_first_non_none(raw_entry.get("request_headers"), raw_entry.get("requestHeaders"), raw_entry.get("requestHeadersArray")),
            "postData": _har_map_first_non_none(raw_entry.get("postData"), raw_entry.get("post_data")),
            "resourceType": raw_entry.get("resourceType") or raw_entry.get("resource_type"),
        }
    if not response and (
        raw_entry.get("status") is not None
        or raw_entry.get("content") is not None
        or raw_entry.get("response_headers") is not None
        or raw_entry.get("responseHeaders") is not None
    ):
        response = {
            "status": raw_entry.get("status"),
            "statusText": raw_entry.get("statusText") or raw_entry.get("status_text"),
            "headers": _har_map_first_non_none(raw_entry.get("response_headers"), raw_entry.get("responseHeaders"), raw_entry.get("headers")),
            "content": raw_entry.get("content"),
            "bodySize": raw_entry.get("bodySize") or raw_entry.get("body_size"),
        }

    method = str(request.get("method") or raw_entry.get("method") or "GET").upper()
    url = str(request.get("url") or raw_entry.get("url") or response.get("url") or "")
    request_headers = _har_map_normalize_headers(request.get("headers") or request.get("requestHeadersArray") or request.get("requestHeaders") or raw_entry.get("requestHeaders"))
    response_headers = _har_map_normalize_headers(response.get("headers") or response.get("responseHeadersArray") or response.get("responseHeaders") or raw_entry.get("responseHeaders"))
    request_body_hash, request_body_hash_source = _har_map_request_body_hash(request)
    content = _har_map_content_metadata(response, response_headers=response_headers)
    response_body_text = _har_map_body_text(_har_map_as_dict(response.get("content")), response)
    if _har_map_is_redacted_value(response_body_text):
        response_body_text = None
    return _ReplayMappingEntry(
        index=index,
        key="",
        base_key="",
        method_url_key="",
        url_key=normalize_url(url, base_url=base_url),
        occurrence=0,
        method_url_occurrence=0,
        url_occurrence=0,
        method=method,
        url=url,
        normalized_url=normalize_url(url, base_url=base_url),
        status=_har_map_int_or_none(response.get("status") if response else raw_entry.get("status")),
        status_text=_har_map_string_or_none(response.get("statusText") or response.get("status_text")),
        request_headers=request_headers,
        response_headers=response_headers,
        request_body_hash=request_body_hash,
        request_body_hash_source=request_body_hash_source,
        content=content,
        resource_type=_har_map_string_or_none(request.get("resourceType") or raw_entry.get("resourceType")),
        started_datetime=_har_map_string_or_none(raw_entry.get("startedDateTime") or raw_entry.get("started_datetime")),
        response_body_text=response_body_text if isinstance(response_body_text, str) else None,
        raw_entry=raw_entry,
    )


def _har_map_entries_from_mapping(mapping: dict[str, Any]) -> list[_ReplayMappingEntry]:
    entries: list[_ReplayMappingEntry] = []
    for item in mapping.get("entries", []):
        if not isinstance(item, dict):
            continue
        entries.append(
            _ReplayMappingEntry(
                index=int(item.get("index") or 0),
                key=str(item.get("key") or ""),
                base_key=str(item.get("base_key") or ""),
                method_url_key=str(item.get("method_url_key") or ""),
                url_key=str(item.get("url_key") or item.get("normalized_url") or ""),
                occurrence=int(item.get("occurrence") or 0),
                method_url_occurrence=int(item.get("method_url_occurrence") or 0),
                url_occurrence=int(item.get("url_occurrence") or 0),
                method=str(item.get("method") or "GET").upper(),
                url=str(item.get("url") or ""),
                normalized_url=str(item.get("normalized_url") or ""),
                status=_har_map_int_or_none(item.get("status")),
                status_text=_har_map_string_or_none(item.get("status_text")),
                request_headers=_har_map_headers_from_mapping(item.get("request_headers")),
                response_headers=_har_map_headers_from_mapping(item.get("response_headers")),
                request_body_hash=_har_map_string_or_none(item.get("request_body_hash")),
                request_body_hash_source=_har_map_string_or_none(item.get("request_body_hash_source")),
                content=_har_map_as_dict(item.get("content")),
                resource_type=_har_map_string_or_none(item.get("resource_type")),
                started_datetime=_har_map_string_or_none(item.get("started_datetime")),
                response_body_text=_har_map_string_or_none(item.get("response_body_text")),
                raw_entry={},
            )
        )
    return entries


def _har_map_headers_from_mapping(value: Any) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for key, raw_values in _har_map_as_dict(value).items():
        if isinstance(raw_values, list):
            result[str(key)] = [str(item) for item in raw_values]
        elif raw_values is not None:
            result[str(key)] = [str(raw_values)]
    return result


def _har_map_request_parts(request: dict[str, Any], *, base_url: str | None) -> dict[str, Any]:
    method = str(request.get("method") or "GET").upper()
    raw_url = str(request.get("url") or "")
    normalized = normalize_url(raw_url, base_url=base_url)
    request_hash, request_hash_source = _har_map_request_body_hash(request)
    method_url_key = f"{method} {normalized}"
    base_key = f"{method_url_key} body={request_hash}" if request_hash else method_url_key
    return {
        "method": method,
        "url": raw_url,
        "normalized_url": normalized,
        "url_key": normalized,
        "method_url_key": method_url_key,
        "base_key": base_key,
        "request_body_hash": request_hash,
        "request_body_hash_source": request_hash_source,
    }


def _har_map_index_entries(entries: list[_ReplayMappingEntry], kind: str) -> dict[tuple[str, int], deque[_ReplayMappingEntry]]:
    index: dict[tuple[str, int], deque[_ReplayMappingEntry]] = defaultdict(deque)
    for entry in entries:
        if kind == "base":
            index[(entry.base_key, entry.occurrence)].append(entry)
        elif kind == "method_url":
            index[(entry.method_url_key, entry.method_url_occurrence)].append(entry)
        else:
            index[(entry.url_key, entry.url_occurrence)].append(entry)
    return index


def _har_map_claim_candidate(
    reference_entry: _ReplayMappingEntry,
    *,
    candidate_by_base: dict[tuple[str, int], deque[_ReplayMappingEntry]],
    candidate_by_method_url: dict[tuple[str, int], deque[_ReplayMappingEntry]],
    candidate_by_url: dict[tuple[str, int], deque[_ReplayMappingEntry]],
    used_candidate_keys: set[str],
) -> tuple[_ReplayMappingEntry | None, str]:
    probes = [
        ("exact", candidate_by_base.get((reference_entry.base_key, reference_entry.occurrence))),
        ("method-url", candidate_by_method_url.get((reference_entry.method_url_key, reference_entry.method_url_occurrence))),
        ("url", candidate_by_url.get((reference_entry.url_key, reference_entry.url_occurrence))),
    ]
    for strength, queue in probes:
        if not queue:
            continue
        while queue:
            entry = queue.popleft()
            if entry.key in used_candidate_keys:
                continue
            used_candidate_keys.add(entry.key)
            return entry, strength
    return None, "missing"


def _har_map_compare_entries(
    reference: _ReplayMappingEntry,
    candidate: _ReplayMappingEntry,
    *,
    ignored_headers: set[str],
    compare_request_headers: bool,
    compare_response_headers: bool,
    compare_content: bool,
) -> dict[str, Any]:
    comparisons: dict[str, Any] = {
        "url": {"match": reference.normalized_url == candidate.normalized_url, "reference": reference.normalized_url, "candidate": candidate.normalized_url},
        "method": {"match": reference.method == candidate.method, "reference": reference.method, "candidate": candidate.method},
        "status": {"match": reference.status == candidate.status, "reference": reference.status, "candidate": candidate.status},
    }
    if reference.request_body_hash or candidate.request_body_hash:
        comparisons["request_body"] = {
            "match": reference.request_body_hash == candidate.request_body_hash,
            "reference_body_hash": reference.request_body_hash,
            "candidate_body_hash": candidate.request_body_hash,
            "reference_source": reference.request_body_hash_source,
            "candidate_source": candidate.request_body_hash_source,
        }
    else:
        comparisons["request_body"] = {"match": True, "reference_body_hash": None, "candidate_body_hash": None}
    comparisons["request_headers"] = _har_map_compare_headers(reference.request_headers, candidate.request_headers, ignored_headers=ignored_headers) if compare_request_headers else {"match": True, "skipped": True}
    comparisons["response_headers"] = _har_map_compare_headers(reference.response_headers, candidate.response_headers, ignored_headers=ignored_headers) if compare_response_headers else {"match": True, "skipped": True}
    comparisons["content"] = _har_map_compare_content(reference.content, candidate.content) if compare_content else {"match": True, "skipped": True}
    drifts = [name for name in ("url", "method", "status", "request_headers", "response_headers", "request_body", "content") if not comparisons[name].get("match")]
    return {"drifts": drifts, "comparisons": comparisons}


def _har_map_compare_headers(reference: dict[str, list[str]], candidate: dict[str, list[str]], *, ignored_headers: set[str]) -> dict[str, Any]:
    reference_names = {name for name in reference if name not in ignored_headers}
    candidate_names = {name for name in candidate if name not in ignored_headers}
    missing = sorted(reference_names - candidate_names)
    extra = sorted(candidate_names - reference_names)
    changed = []
    for name in sorted(reference_names & candidate_names):
        reference_values = sorted(reference.get(name) or [])
        candidate_values = sorted(candidate.get(name) or [])
        if reference_values != candidate_values:
            changed.append({"name": name, "reference": reference_values, "candidate": candidate_values})
    return {"match": not missing and not extra and not changed, "compared_count": len(reference_names | candidate_names), "missing": missing, "extra": extra, "changed": changed}


def _har_map_compare_content(reference: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    changed = []
    for field in ("mime_type", "size", "encoding", "body_hash"):
        reference_value = reference.get(field)
        candidate_value = candidate.get(field)
        if reference_value is None and candidate_value is None:
            continue
        if reference_value != candidate_value:
            changed.append({"field": field, "reference": reference_value, "candidate": candidate_value})
    limitations = []
    if reference.get("body_redacted") and not reference.get("body_hash"):
        limitations.append("reference body is redacted and has no hash")
    if candidate.get("body_redacted") and not candidate.get("body_hash"):
        limitations.append("candidate body is redacted and has no hash")
    return {"match": not changed, "changed": changed, "limitations": limitations, "reference": _har_map_content_public(reference), "candidate": _har_map_content_public(candidate)}


def _har_map_content_public(content: dict[str, Any]) -> dict[str, Any]:
    return {
        "mime_type": content.get("mime_type"),
        "size": content.get("size"),
        "encoding": content.get("encoding"),
        "body_hash": content.get("body_hash"),
        "body_hash_source": content.get("body_hash_source"),
        "body_redacted": bool(content.get("body_redacted")),
        "text_present": bool(content.get("text_present")),
    }


def _har_map_entry_public(entry: _ReplayMappingEntry) -> dict[str, Any]:
    return {
        "key": entry.key,
        "index": entry.index,
        "method": entry.method,
        "url": entry.url,
        "normalized_url": entry.normalized_url,
        "status": entry.status,
        "content": _har_map_content_public(entry.content),
        "request_body_hash": entry.request_body_hash,
        "resource_type": entry.resource_type,
    }


def _har_map_content_metadata(response: dict[str, Any], *, response_headers: dict[str, list[str]]) -> dict[str, Any]:
    content = _har_map_as_dict(response.get("content"))
    content_length = _har_map_first_non_none(content.get("size"), response.get("bodySize"), response.get("contentLength"), _har_map_header_value(response_headers, "content-length"))
    body_hash, body_hash_source = _har_map_body_hash(response, content)
    body_text = _har_map_body_text(content, response)
    mime_type = _har_map_string_or_none(_har_map_first_non_none(content.get("mimeType"), content.get("mime_type"), response.get("contentType"), response.get("content_type"), _har_map_header_value(response_headers, "content-type")))
    return {
        "mime_type": mime_type,
        "size": _har_map_int_or_none(content_length),
        "encoding": _har_map_string_or_none(content.get("encoding") or _har_map_header_value(response_headers, "content-encoding")),
        "compression": _har_map_string_or_none(content.get("compression")),
        "body_hash": body_hash,
        "body_hash_source": body_hash_source,
        "body_redacted": _har_map_is_redacted_value(body_text),
        "text_present": body_text is not None,
    }


def _har_map_body_hash(container: dict[str, Any], nested: dict[str, Any]) -> tuple[str | None, str | None]:
    explicit = _har_map_explicit_hash(nested) or _har_map_explicit_hash(container)
    if explicit:
        return explicit, "explicit"
    body_text = _har_map_body_text(nested, container)
    if body_text is None or _har_map_is_redacted_value(body_text):
        return None, None
    encoding = str(nested.get("encoding") or "").lower()
    if encoding == "base64":
        try:
            body_bytes = base64.b64decode(str(body_text), validate=False)
        except Exception:
            body_bytes = str(body_text).encode("utf-8", "replace")
    else:
        body_bytes = str(body_text).encode("utf-8", "replace")
    return "sha256:" + hashlib.sha256(body_bytes).hexdigest(), "computed"


def _har_map_request_body_hash(request: dict[str, Any]) -> tuple[str | None, str | None]:
    post_data = _har_map_as_dict(request.get("postData"))
    explicit = _har_map_explicit_hash(post_data) or _har_map_explicit_hash(request)
    if explicit:
        return explicit, "explicit"
    text = _har_map_first_non_none(post_data.get("text"), request.get("postDataText"), request.get("body"), request.get("bodyText"))
    if text is not None:
        if _har_map_is_redacted_value(text):
            return None, None
        return "sha256:" + hashlib.sha256(str(text).encode("utf-8", "replace")).hexdigest(), "computed"
    params = post_data.get("params") or request.get("postDataParams")
    if params:
        stable = json.dumps(params, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(stable.encode("utf-8")).hexdigest(), "computed-params"
    return None, None


def _har_map_explicit_hash(payload: dict[str, Any]) -> str | None:
    for field in HASH_FIELD_NAMES:
        normalized = _har_map_normalize_hash(payload.get(field))
        if normalized:
            return normalized
    for field in ("metadata", "_metadata", "webEmbedding", "_webEmbedding"):
        nested = payload.get(field)
        if isinstance(nested, dict):
            normalized = _har_map_explicit_hash(nested)
            if normalized:
                return normalized
    return None


def _har_map_normalize_hash(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if _har_map_is_redacted_value(text):
        return None
    lowered = text.lower()
    if lowered.startswith("sha256:"):
        return "sha256:" + lowered.split(":", 1)[1]
    if re.fullmatch(r"[a-f0-9]{64}", lowered):
        return "sha256:" + lowered
    return text


def _har_map_body_text(primary: dict[str, Any], secondary: dict[str, Any]) -> Any:
    return _har_map_first_non_none(primary.get("text"), primary.get("bodyText"), primary.get("body_text"), secondary.get("text"), secondary.get("bodyText"), secondary.get("body_text"))


def _har_map_normalize_headers(headers: Any) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = defaultdict(list)
    for name, value in _har_map_headers_to_pairs(headers):
        key = str(name or "").strip().lower()
        if not key:
            continue
        value_text = _har_map_safe_header_value(key, value)
        if value_text is not None:
            normalized[key].append(value_text)
    return {name: values for name, values in sorted(normalized.items())}


def _har_map_headers_to_pairs(headers: Any) -> list[tuple[str, Any]]:
    if isinstance(headers, dict):
        pairs: list[tuple[str, Any]] = []
        for name, value in headers.items():
            if isinstance(value, list):
                pairs.extend((str(name), item) for item in value)
            else:
                pairs.append((str(name), value))
        return pairs
    if isinstance(headers, list):
        pairs = []
        for item in headers:
            if isinstance(item, dict):
                pairs.append((str(item.get("name") or item.get("key") or ""), item.get("value")))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                pairs.append((str(item[0]), item[1]))
        return pairs
    return []


def _har_map_safe_header_value(name: str, value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text:
        return None
    if SENSITIVE_NAME_RE.search(name):
        return "[REDACTED]" if _har_map_is_redacted_value(text) else "[SENSITIVE]"
    if _har_map_is_redacted_value(text):
        return "[REDACTED]"
    return text


def _har_map_headers_for_replay(headers: Any) -> dict[str, str]:
    normalized = _har_map_normalize_headers(headers)
    return {name: ", ".join(values) for name, values in normalized.items()}


def _har_map_header_value(headers: dict[str, list[str]], name: str) -> str | None:
    values = headers.get(name.lower()) or []
    return values[0] if values else None


def _har_map_as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _har_map_first_non_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _har_map_int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _har_map_string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _har_map_is_redacted_value(value: Any) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in REDACTED_MARKERS


__all__ = [
    "HarReplayEngine",
    "HarReplayError",
    "HarReplayLoadError",
    "ReplayEntry",
    "ReplayRequestMatcher",
    "build_replay_report",
    "build_replay_mapping",
    "compare_live_or_candidate_manifest",
    "export_offline_html_renderer",
    "load_har_payload",
    "load_request_specs",
    "normalize_request_spec",
    "normalize_url",
    "request_body_bytes",
    "request_matcher",
    "summarize_replay_results",
]
