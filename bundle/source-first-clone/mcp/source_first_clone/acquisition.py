"""Reference acquisition helpers for source-first clone workflows."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import os
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .constants import DEFAULT_BROWSER_PATHS, LICENSE_HINTS, URL_PATTERNS, USER_AGENT
from .platform_adapters import inspect_platform_adapter, merge_platform_candidates
from .site_profile import classify_site_profile


def is_candidate_noise(url: str) -> bool:
    lowered = (url or "").lower()
    if any(token in lowered for token in ("googletagmanager.com", "google-analytics.com", "doubleclick.net")):
        return True
    if lowered.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".css", ".js", ".map", ".woff", ".woff2")):
        return True
    return False


def should_promote_direct_iframe(final_url: str, platform_adapter: dict[str, Any] | None) -> bool:
    platform = str((platform_adapter or {}).get("platform") or "").lower()
    lowered_url = (final_url or "").lower()
    if platform == "spline" and ("app.spline.design/file/" in lowered_url or "/community/file/" in lowered_url):
        return False
    if platform == "figma" and "figma.com/embed" not in lowered_url:
        return False
    return True


def detect_runtime_capabilities() -> dict[str, Any]:
    node_path = shutil.which("node")
    resolved_browser = os.environ.get("WEB_EMBEDDING_CHROME_PATH")
    if resolved_browser and not Path(resolved_browser).exists():
        resolved_browser = None
    if not resolved_browser:
        for candidate in DEFAULT_BROWSER_PATHS:
            if Path(candidate).exists():
                resolved_browser = candidate
                break

    report: dict[str, Any] = {
        "node_installed": bool(node_path),
        "node_path": node_path,
        "playwright_installed": False,
        "playwright_core_installed": False,
        "browser_path": resolved_browser,
        "browser_available": bool(resolved_browser),
        "supported_session_modes": [],
        "missing_dependencies": [],
        "install_hints": [],
    }

    if not node_path:
        report["missing_dependencies"].append("node")
        report["install_hints"].append("Install Node.js 18+ to enable runtime capture.")
        return report

    node_script = r"""
const out = { playwright: false, playwrightCore: false, versions: {} };
for (const mod of ["playwright", "playwright-core"]) {
  try {
    const pkg = require(mod + "/package.json");
    out.versions[mod] = pkg.version || null;
    if (mod === "playwright") out.playwright = true;
    if (mod === "playwright-core") out.playwrightCore = true;
  } catch (error) {
    void error;
  }
}
console.log(JSON.stringify(out));
"""
    completed = subprocess.run(
        ["node", "-e", node_script],
        capture_output=True,
        text=True,
        check=False,
    )
    payload = completed.stdout.strip()
    if payload:
        try:
            parsed = json.loads(payload)
            report["playwright_installed"] = bool(parsed.get("playwright"))
            report["playwright_core_installed"] = bool(parsed.get("playwrightCore"))
            report["versions"] = parsed.get("versions", {})
        except json.JSONDecodeError:
            report["raw_runtime_probe"] = payload

    if report["playwright_installed"] or report["playwright_core_installed"]:
        report["supported_session_modes"] = ["ephemeral", "persistent", "storage-state"]
    else:
        report["missing_dependencies"].append("playwright or playwright-core")
        report["install_hints"].append("Run `npm install playwright-core` or `npm install playwright` in the repo.")

    if not report["browser_available"]:
        report["missing_dependencies"].append("browser binary")
        report["install_hints"].append("Set WEB_EMBEDDING_CHROME_PATH or install Chrome/Chromium.")

    report["capture_ready"] = bool(report["node_installed"] and (report["playwright_installed"] or report["playwright_core_installed"]) and report["browser_available"])
    return report


def fetch_url(url: str, timeout_seconds: int = 20) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout_seconds) as response:
        html = response.read().decode("utf-8", "ignore")
        headers = {key.lower(): value for key, value in response.headers.items()}
        return {
            "status": getattr(response, "status", 200),
            "final_url": response.geturl(),
            "html": html,
            "headers": headers,
        }


def build_platform_only_inspection(url: str, error: Exception | None = None) -> dict[str, Any]:
    platform_adapter = inspect_platform_adapter(url, "", {})
    platform = platform_adapter.get("platform")
    notes = list(platform_adapter.get("notes") or [])
    if error:
        notes.append(f"Static fetch fallback used because upstream fetch failed: {error}")
    inspection = {
        "url": url,
        "final_url": url,
        "status": None,
        "title": None,
        "meta": {},
        "license_hints": [],
        "headers": {},
        "frame_policy": {
            "x_frame_options": None,
            "content_security_policy": None,
            "frame_ancestors": None,
            "embeddable": "unknown",
            "reason": "Frame policy could not be determined without a successful static fetch.",
        },
        "platform": platform,
        "platform_adapter": {
            **platform_adapter,
            "notes": notes,
        },
        "source_signals": platform_adapter.get("source_signals", []),
        "candidate_urls": merge_platform_candidates([], platform_adapter.get("candidates")),
    }
    inspection["site_profile"] = classify_site_profile(
        final_url=url,
        html="",
        headers={},
        frame_policy=inspection["frame_policy"],
        platform_adapter=inspection["platform_adapter"],
        meta={},
        candidate_urls=inspection["candidate_urls"],
    )
    return inspection


def analyze_frame_policy(headers: dict[str, str] | None) -> dict[str, Any]:
    headers = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
    x_frame_options = headers.get("x-frame-options")
    csp = headers.get("content-security-policy")
    result: dict[str, Any] = {
        "x_frame_options": x_frame_options,
        "content_security_policy": csp,
        "frame_ancestors": None,
        "embeddable": "unknown",
        "reason": "No framing policy was detected.",
    }

    if x_frame_options:
        lowered = x_frame_options.lower()
        if "deny" in lowered:
            result["embeddable"] = False
            result["reason"] = "X-Frame-Options=DENY blocks exact iframe reuse."
            return result
        if "sameorigin" in lowered:
            result["embeddable"] = False
            result["reason"] = "X-Frame-Options=SAMEORIGIN blocks cross-origin iframe reuse."
            return result

    if csp:
        match = re.search(r"frame-ancestors\s+([^;]+)", csp, re.I)
        if match:
            directive = match.group(1).strip()
            frame_ancestors = [token.strip() for token in directive.split() if token.strip()]
            result["frame_ancestors"] = frame_ancestors
            lowered = {token.lower() for token in frame_ancestors}
            if "'none'" in lowered:
                result["embeddable"] = False
                result["reason"] = "Content-Security-Policy frame-ancestors 'none' blocks iframe reuse."
                return result
            if "'self'" in lowered and len(frame_ancestors) == 1:
                result["embeddable"] = False
                result["reason"] = "Content-Security-Policy frame-ancestors 'self' blocks cross-origin iframe reuse."
                return result
            if "*" in frame_ancestors or "https:" in lowered or "http:" in lowered:
                result["embeddable"] = True
                result["reason"] = "Content-Security-Policy frame-ancestors allows broad iframe reuse."
                return result
            result["embeddable"] = "restricted"
            result["reason"] = "Content-Security-Policy frame-ancestors only allows specific origins."
            return result

    if x_frame_options:
        result["embeddable"] = "unknown"
        result["reason"] = "X-Frame-Options is present but not conclusively blocking."
        return result

    result["embeddable"] = True
    result["reason"] = "No framing restrictions were detected in response headers."
    return result


def extract_title(html: str) -> str | None:
    match = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    return unescape(match.group(1).strip()) if match else None


def extract_meta(html: str) -> dict[str, str]:
    results: dict[str, str] = {}
    for key in ["description", "og:title", "og:description", "og:image", "og:url", "twitter:image"]:
        match = re.search(
            rf"<meta[^>]+(?:property|name)=[\"']{re.escape(key)}[\"'][^>]+content=[\"']([^\"']+)",
            html,
            re.I,
        )
        if match:
            results[key] = unescape(match.group(1))
    return results


def extract_license_hints(html: str) -> list[str]:
    lowered = html.lower()
    return [hint for hint in LICENSE_HINTS if hint in lowered]


def normalize_candidate_url(base_url: str, raw_url: str) -> str | None:
    candidate = urljoin(base_url, unescape((raw_url or "").strip()))
    for marker in ("&quot;", "\"", "{", "}", ",&quot;", "},", "],", "\\u003c"):
        if marker in candidate:
            candidate = candidate.split(marker, 1)[0]
    candidate = candidate.strip()
    if not candidate.startswith(("http://", "https://")):
        return None
    if len(candidate) > 2048:
        return None
    return candidate


def build_candidates(base_url: str, html: str, adapter_candidates: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    final_candidates: list[dict[str, str]] = []
    seen: set[str] = set()

    for kind, pattern in URL_PATTERNS:
        matches = pattern.findall(html)
        for raw_match in matches:
            candidate = raw_match if isinstance(raw_match, str) else raw_match[0]
            normalized = normalize_candidate_url(base_url, candidate)
            if not normalized:
                continue
            if is_candidate_noise(normalized):
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            final_candidates.append({"kind": kind, "url": normalized})

    generic_urls = re.findall(r"https://[^\"'\s>]+", html)
    for raw in generic_urls:
        if not re.search(r"(spline|preview|embed|viewer|scene|iframe|remix|export)", raw, re.I):
            continue
        normalized = normalize_candidate_url(base_url, raw)
        if not normalized:
            continue
        if is_candidate_noise(normalized):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        final_candidates.append({"kind": "runtime-hint", "url": normalized})

    return merge_platform_candidates(final_candidates, adapter_candidates)


def inspect_reference(url: str, timeout_seconds: int = 20) -> dict[str, Any]:
    try:
        fetched = fetch_url(url, timeout_seconds=timeout_seconds)
    except (HTTPError, URLError) as error:
        fallback = build_platform_only_inspection(url, error=error)
        if fallback.get("platform") != "generic":
            return fallback
        raise
    html = fetched["html"]
    frame_policy = analyze_frame_policy(fetched.get("headers"))
    meta = extract_meta(html)
    platform_adapter = inspect_platform_adapter(fetched["final_url"], html, meta)
    candidate_urls = build_candidates(fetched["final_url"], html, adapter_candidates=platform_adapter.get("candidates"))
    if frame_policy.get("embeddable") is True and should_promote_direct_iframe(fetched["final_url"], platform_adapter):
        candidate_urls = [{"kind": "direct-iframe", "url": fetched["final_url"]}, *candidate_urls]
    inspection = {
        "url": url,
        "final_url": fetched["final_url"],
        "status": fetched["status"],
        "title": extract_title(html),
        "meta": meta,
        "license_hints": extract_license_hints(html),
        "headers": fetched.get("headers", {}),
        "frame_policy": frame_policy,
        "platform": platform_adapter.get("platform"),
        "platform_adapter": platform_adapter,
        "source_signals": platform_adapter.get("source_signals", []),
        "candidate_urls": candidate_urls,
    }
    inspection["site_profile"] = classify_site_profile(
        final_url=fetched["final_url"],
        html=html,
        headers=fetched.get("headers", {}),
        frame_policy=frame_policy,
        platform_adapter=platform_adapter,
        meta=meta,
        candidate_urls=candidate_urls,
    )
    return inspection


def discover_embed_candidates(url: str, timeout_seconds: int = 20) -> dict[str, Any]:
    try:
        fetched = fetch_url(url, timeout_seconds=timeout_seconds)
    except (HTTPError, URLError) as error:
        fallback = build_platform_only_inspection(url, error=error)
        if fallback.get("platform") != "generic":
            return {
                "url": url,
                "final_url": fallback.get("final_url"),
                "frame_policy": fallback.get("frame_policy"),
                "platform": fallback.get("platform"),
                "platform_adapter": fallback.get("platform_adapter"),
                "source_signals": fallback.get("source_signals", []),
                "site_profile": fallback.get("site_profile"),
                "candidates": fallback.get("candidate_urls", []),
            }
        raise
    frame_policy = analyze_frame_policy(fetched.get("headers"))
    meta = extract_meta(fetched["html"])
    platform_adapter = inspect_platform_adapter(fetched["final_url"], fetched["html"], meta)
    candidates = build_candidates(fetched["final_url"], fetched["html"], adapter_candidates=platform_adapter.get("candidates"))
    if frame_policy.get("embeddable") is True and should_promote_direct_iframe(fetched["final_url"], platform_adapter):
        candidates = [{"kind": "direct-iframe", "url": fetched["final_url"]}, *candidates]
    site_profile = classify_site_profile(
        final_url=fetched["final_url"],
        html=fetched["html"],
        headers=fetched.get("headers", {}),
        frame_policy=frame_policy,
        platform_adapter=platform_adapter,
        meta=meta,
        candidate_urls=candidates,
    )
    return {
        "url": url,
        "final_url": fetched["final_url"],
        "frame_policy": frame_policy,
        "platform": platform_adapter.get("platform"),
        "platform_adapter": platform_adapter,
        "source_signals": platform_adapter.get("source_signals", []),
        "site_profile": site_profile,
        "candidates": candidates,
    }


def _runtime_trace_script() -> str:
    return r"""
const pageUrl = process.argv[1];
const waitSeconds = Number(process.argv[2] || "8");
const rawPattern = process.argv[3] || "spline|preview|embed|viewer|scene|iframe";
const userDataDir = process.argv[4] || "";
const storageStatePath = process.argv[5] || "";
const captureHtml = String(process.argv[6] || "false") === "true";
const captureScreenshot = String(process.argv[7] || "false") === "true";
const viewportWidth = Number(process.argv[8] || "1440");
const viewportHeight = Number(process.argv[9] || "1200");
const storageStateOutputPath = process.argv[10] || "";
const fs = require("fs");
const path = require("path");
let playwright;
try {
  playwright = require("playwright");
} catch (firstError) {
  try {
    playwright = require("playwright-core");
  } catch (secondError) {
    console.log(JSON.stringify({
      available: false,
        error: "playwright or playwright-core is not installed",
        session: {
          userDataDir: userDataDir || null,
          storageStatePath: storageStatePath || null,
          storageStateOutputPath: storageStateOutputPath || null,
          captureHtml,
          captureScreenshot
        }
    }));
    process.exit(0);
  }
}

const regex = new RegExp(rawPattern, "i");

function uniqueByUrl(items) {
  const seen = new Set();
  const out = [];
  for (const item of items) {
    if (!item || !item.url || seen.has(item.url)) {
      continue;
    }
    seen.add(item.url);
    out.push(item);
  }
  return out;
}

function trimText(text, maxLength = 160) {
  if (!text) return "";
  const normalized = String(text).replace(/\s+/g, " ").trim();
  return normalized.length > maxLength ? normalized.slice(0, maxLength) : normalized;
}

function buildLaunchOptions() {
  const options = {
    headless: true,
  };
  if (process.env.WEB_EMBEDDING_CHROME_PATH) {
    options.executablePath = process.env.WEB_EMBEDDING_CHROME_PATH;
  }
  return options;
}

function styleSnapshotFromComputed(style) {
  return {
    display: style.display,
    position: style.position,
    top: style.top,
    right: style.right,
    bottom: style.bottom,
    left: style.left,
    zIndex: style.zIndex,
    color: style.color,
    backgroundColor: style.backgroundColor,
    borderColor: style.borderColor,
    borderWidth: style.borderWidth,
    borderRadius: style.borderRadius,
    boxShadow: style.boxShadow,
    opacity: style.opacity,
    transform: style.transform,
    filter: style.filter,
    textDecoration: style.textDecoration,
    outline: style.outline,
    outlineOffset: style.outlineOffset,
    cursor: style.cursor,
    fontFamily: style.fontFamily,
    fontSize: style.fontSize,
    fontWeight: style.fontWeight,
    lineHeight: style.lineHeight,
    letterSpacing: style.letterSpacing,
    textAlign: style.textAlign,
    textTransform: style.textTransform,
    whiteSpace: style.whiteSpace,
    overflow: style.overflow,
    overflowX: style.overflowX,
    overflowY: style.overflowY,
    scrollSnapType: style.scrollSnapType,
    scrollSnapAlign: style.scrollSnapAlign,
    scrollSnapStop: style.scrollSnapStop,
    paddingTop: style.paddingTop,
    paddingRight: style.paddingRight,
    paddingBottom: style.paddingBottom,
    paddingLeft: style.paddingLeft,
    marginTop: style.marginTop,
    marginRight: style.marginRight,
    marginBottom: style.marginBottom,
    marginLeft: style.marginLeft,
    gap: style.gap,
    alignItems: style.alignItems,
    justifyContent: style.justifyContent,
    flexDirection: style.flexDirection,
    boxSizing: style.boxSizing,
  };
}

function diffStyleSnapshots(before, after) {
  if (!before || !after) {
    return {};
  }
  const diff = {};
  for (const key of Object.keys(before)) {
    if ((before[key] || "") !== (after[key] || "")) {
      diff[key] = {
        before: before[key] || "",
        after: after[key] || "",
      };
    }
  }
  return diff;
}

function safeReplayValue(candidate) {
  if (candidate && candidate.type) {
    const lowered = String(candidate.type).toLowerCase();
    if (["email"].includes(lowered)) return "web@example.com";
    if (["search", "text", "url", "tel"].includes(lowered)) return "web embedding";
  }
  return "web embedding";
}

function normalizeText(value, maxLength = 160) {
  if (!value) {
    return null;
  }
  const normalized = String(value).replace(/\s+/g, " ").trim();
  return normalized ? normalized.slice(0, maxLength) : null;
}

function resolveLabelFromReferences(element) {
  const labelledBy = String(element.getAttribute("aria-labelledby") || "")
    .split(/\s+/)
    .map((value) => value.trim())
    .filter(Boolean)
    .slice(0, 4);
  const parts = [];
  for (const id of labelledBy) {
    const labelNode = document.getElementById(id);
    if (labelNode) {
      const text = normalizeText(labelNode.innerText || labelNode.textContent || "", 80);
      if (text) {
        parts.push(text);
      }
    }
  }
  return parts.length ? parts.join(" ").trim() : null;
}

function resolveAssociatedLabel(element) {
  if (!element || !element.tagName) {
    return null;
  }
  const id = element.getAttribute("id");
  if (id) {
    const directLabels = Array.from(document.querySelectorAll(`label[for="${CSS.escape(id)}"]`))
      .map((node) => normalizeText(node.innerText || node.textContent || "", 80))
      .filter(Boolean);
    if (directLabels.length) {
      return directLabels.join(" ").trim();
    }
  }
  const wrappingLabel = element.closest("label");
  if (wrappingLabel) {
    return normalizeText(wrappingLabel.innerText || wrappingLabel.textContent || "", 80);
  }
  return null;
}

function resolveFormContextLabel(element) {
  if (!element || !element.tagName) {
    return null;
  }
  const fieldset = element.closest("fieldset");
  if (fieldset) {
    const legend = fieldset.querySelector("legend");
    if (legend) {
      const legendText = normalizeText(legend.innerText || legend.textContent || "", 80);
      if (legendText) {
        return legendText;
      }
    }
  }
  const labelledGroup = element.closest('[role="group"], [role="radiogroup"], [role="toolbar"], [role="menu"], [role="tablist"]');
  if (labelledGroup) {
    const groupLabel =
      normalizeText(labelledGroup.getAttribute("aria-label"), 80) ||
      normalizeText(labelledGroup.getAttribute("aria-labelledby"), 80) ||
      normalizeText(labelledGroup.innerText || labelledGroup.textContent || "", 80);
    if (groupLabel) {
      return groupLabel;
    }
  }
  return null;
}

function resolveDescriptionFromReferences(element) {
  if (!element || !element.tagName) {
    return null;
  }
  const describedBy = String(element.getAttribute("aria-describedby") || "")
    .split(/\s+/)
    .map((value) => value.trim())
    .filter(Boolean)
    .slice(0, 4);
  const parts = [];
  for (const id of describedBy) {
    const descriptionNode = document.getElementById(id);
    if (descriptionNode) {
      const text = normalizeText(descriptionNode.innerText || descriptionNode.textContent || "", 120);
      if (text) {
        parts.push(text);
      }
    }
  }
  return parts.length ? parts.join(" ").trim() : null;
}

function getInteractionKind(node) {
  if (!node || !node.tagName) {
    return "unknown";
  }
  const tag = node.tagName.toLowerCase();
  const role = String(node.getAttribute("role") || "").toLowerCase();
  const type = String(node.getAttribute("type") || "").toLowerCase();
  if (node.isContentEditable || role === "textbox" || role === "searchbox") return "text-entry";
  if (tag === "input" && ["text", "search", "url", "tel", "email", "password"].includes(type)) return "text-entry";
  if (tag === "textarea") return "text-entry";
  if (tag === "select" || role === "combobox") return "select";
  if (tag === "summary" || role === "button" || role === "menuitem" || role === "menuitemcheckbox" || role === "menuitemradio" || role === "switch") return "toggle";
  if (role === "tab") return "tab";
  if (role === "checkbox" || role === "radio" || type === "checkbox" || type === "radio") return "checkable";
  if (role === "link" || tag === "a") return "link";
  if (role === "slider" || type === "range") return "slider";
  if (node.hasAttribute("aria-haspopup")) return "disclosure";
  return "action";
}

function isFocusableElement(node, style) {
  if (!node || !node.tagName) {
    return false;
  }
  const tag = node.tagName.toLowerCase();
  const role = String(node.getAttribute("role") || "").toLowerCase();
  const type = String(node.getAttribute("type") || "").toLowerCase();
  if (node.isContentEditable) {
    return true;
  }
  if (["button", "select", "textarea", "summary"].includes(tag)) {
    return true;
  }
  if (tag === "input" && type !== "hidden") {
    return true;
  }
  if (tag === "a" && node.hasAttribute("href")) {
    return true;
  }
  if (role && ["button", "link", "textbox", "searchbox", "tab", "menuitem", "menuitemcheckbox", "menuitemradio", "switch", "checkbox", "radio", "combobox", "slider", "option", "treeitem"].includes(role)) {
    return true;
  }
  const tabIndex = node.tabIndex;
  if (typeof tabIndex === "number" && tabIndex >= 0) {
    return true;
  }
  if (style && style.cursor === "pointer") {
    return true;
  }
  return false;
}

function compactLabelText(node, maxLength = 80) {
  if (!node || !node.tagName) {
    return null;
  }
  const tag = node.tagName.toLowerCase();
  const role = String(node.getAttribute("role") || "").toLowerCase();
  const direct = Array.from(node.childNodes || [])
    .filter((child) => child && child.nodeType === Node.TEXT_NODE)
    .map((child) => String(child.textContent || "").replace(/\s+/g, " ").trim())
    .filter(Boolean)
    .join(" ");
  const directText = normalizeText(direct, maxLength);
  if (directText) {
    return directText;
  }
  const fullText = normalizeText(node.innerText || node.textContent || "", maxLength);
  if (!fullText) {
    return null;
  }
  const words = fullText.split(/\s+/).filter(Boolean);
  if (fullText.length <= 40 || words.length <= 6) {
    return fullText;
  }
  if (
    ["a", "button", "summary", "label", "option"].includes(tag) ||
    ["button", "link", "tab", "menuitem", "menuitemcheckbox", "menuitemradio", "switch", "checkbox", "radio"].includes(role)
  ) {
    return fullText;
  }
  return null;
}

function getInteractionLabel(node) {
  if (!node || !node.tagName) {
    return null;
  }
  const type = String(node.getAttribute("type") || "").toLowerCase();
  return (
    normalizeText(node.getAttribute("aria-label"), 80) ||
    resolveLabelFromReferences(node) ||
    resolveAssociatedLabel(node) ||
    resolveFormContextLabel(node) ||
    normalizeText(node.getAttribute("title"), 80) ||
    normalizeText(node.getAttribute("data-label"), 80) ||
    normalizeText(node.getAttribute("data-title"), 80) ||
    normalizeText(node.getAttribute("alt"), 80) ||
    (
      node.tagName.toLowerCase() === "input" && ["submit", "button", "reset"].includes(type)
        ? normalizeText("value" in node ? node.value : "", 80)
        : null
    ) ||
    normalizeText(node.getAttribute("placeholder"), 80) ||
    compactLabelText(node, 80) ||
    normalizeText("value" in node ? node.value : "", 80) ||
    normalizeText(node.tagName.toLowerCase(), 80)
  );
}

function describeInteractionTarget(node, style) {
  if (!node || !node.tagName) {
    return null;
  }
  const rect = node.getBoundingClientRect();
  const kind = getInteractionKind(node);
  const label = getInteractionLabel(node);
  return {
    tag: node.tagName.toLowerCase(),
    role: node.getAttribute("role"),
    kind,
    label,
    id: node.id || null,
    className: normalizeClassName(node.className),
    name: node.getAttribute("name"),
    title: node.getAttribute("title"),
    placeholder: node.getAttribute("placeholder"),
    type: node.getAttribute("type"),
    inputMode: node.getAttribute("inputmode"),
    autocomplete: node.getAttribute("autocomplete"),
    ariaLabel: node.getAttribute("aria-label"),
    ariaDescription: node.getAttribute("aria-description"),
    ariaCurrent: node.getAttribute("aria-current"),
    ariaControls: node.getAttribute("aria-controls"),
    ariaExpanded: node.getAttribute("aria-expanded"),
    ariaSelected: node.getAttribute("aria-selected"),
    ariaPressed: node.getAttribute("aria-pressed"),
    ariaHasPopup: node.getAttribute("aria-haspopup"),
    ariaRoleDescription: node.getAttribute("aria-roledescription"),
    ariaLive: node.getAttribute("aria-live"),
    focusable: isFocusableElement(node, style),
    inputCapable: kind === "text-entry",
    rect: {
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
    },
  };
}

function normalizeClassName(value) {
  return String(value || "").replace(/\s+/g, " ").trim().slice(0, 120) || null;
}

function classTokens(value) {
  return String(value || "")
    .toLowerCase()
    .split(/\s+/)
    .map((token) => token.trim())
    .filter(Boolean)
    .slice(0, 10);
}

function captureNodeSignature(node) {
  if (!node || !node.tagName) {
    return null;
  }
  const rect = node.getBoundingClientRect();
  return {
    tag: node.tagName.toLowerCase(),
    role: node.getAttribute("role"),
    id: node.id || null,
    className: normalizeClassName(node.className),
    text: (node.innerText || node.textContent || "").replace(/\s+/g, " ").trim().slice(0, 120) || null,
    ariaLabel: node.getAttribute("aria-label"),
    ariaDescription: node.getAttribute("aria-description"),
    ariaCurrent: node.getAttribute("aria-current"),
    ariaControls: node.getAttribute("aria-controls"),
    ariaExpanded: node.getAttribute("aria-expanded"),
    ariaSelected: node.getAttribute("aria-selected"),
    ariaModal: node.getAttribute("aria-modal"),
    ariaHasPopup: node.getAttribute("aria-haspopup"),
    ariaRoleDescription: node.getAttribute("aria-roledescription"),
    ariaLive: node.getAttribute("aria-live"),
    title: node.getAttribute("title"),
    placeholder: node.getAttribute("placeholder"),
    name: node.getAttribute("name"),
    type: node.getAttribute("type"),
    inputMode: node.getAttribute("inputmode"),
    autocomplete: node.getAttribute("autocomplete"),
    label: getInteractionLabel(node),
    open: node.open === true,
    hidden: Boolean(node.hidden),
    disabled: Boolean(node.disabled),
    rect: {
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
    },
  };
}

function isVisibleElement(node) {
  if (!node || !node.tagName) {
    return false;
  }
  const rect = node.getBoundingClientRect();
  const style = window.getComputedStyle(node);
  return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden" && style.opacity !== "0";
}

function isScrollableElement(node, style) {
  if (!node || !node.tagName || !style) {
    return false;
  }
  const overflowX = String(style.overflowX || style.overflow || "").toLowerCase();
  const overflowY = String(style.overflowY || style.overflow || "").toLowerCase();
  const horizontal = /auto|scroll|overlay/.test(overflowX) && node.scrollWidth > node.clientWidth + 4;
  const vertical = /auto|scroll|overlay/.test(overflowY) && node.scrollHeight > node.clientHeight + 4;
  return horizontal || vertical;
}

function describeScrollableElement(node, style) {
  if (!node || !node.tagName || !style) {
    return null;
  }
  const rect = node.getBoundingClientRect();
  const scrollableX = /auto|scroll|overlay/.test(String(style.overflowX || style.overflow || "").toLowerCase()) && node.scrollWidth > node.clientWidth + 4;
  const scrollableY = /auto|scroll|overlay/.test(String(style.overflowY || style.overflow || "").toLowerCase()) && node.scrollHeight > node.clientHeight + 4;
  const maxScrollX = Math.max(0, Math.round((node.scrollWidth || 0) - (node.clientWidth || 0)));
  const maxScrollY = Math.max(0, Math.round((node.scrollHeight || 0) - (node.clientHeight || 0)));
  return {
    ...captureNodeSignature(node),
    overflowX: style.overflowX,
    overflowY: style.overflowY,
    scrollTop: Math.round(node.scrollTop || 0),
    scrollLeft: Math.round(node.scrollLeft || 0),
    scrollHeight: Math.round(node.scrollHeight || 0),
    scrollWidth: Math.round(node.scrollWidth || 0),
    clientHeight: Math.round(node.clientHeight || 0),
    clientWidth: Math.round(node.clientWidth || 0),
    maxScrollX,
    maxScrollY,
    scrollProgressX: maxScrollX ? Number((Math.min(Math.max(node.scrollLeft || 0, 0), maxScrollX) / maxScrollX).toFixed(3)) : 0,
    scrollProgressY: maxScrollY ? Number((Math.min(Math.max(node.scrollTop || 0, 0), maxScrollY) / maxScrollY).toFixed(3)) : 0,
    scrollableX,
    scrollableY,
    scrollableAxis: scrollableX && scrollableY ? "both" : scrollableY ? "y" : scrollableX ? "x" : null,
    scrollSnapType: style.scrollSnapType,
    scrollSnapAlign: style.scrollSnapAlign,
    scrollSnapStop: style.scrollSnapStop,
    rect: {
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
    },
  };
}

function findMatchingAncestor(element, matcher, maxDepth = 8) {
  let current = element;
  let depth = 0;
  while (current && depth <= maxDepth) {
    if (current.tagName) {
      const style = window.getComputedStyle(current);
      const match = matcher(current, style, depth);
      if (match) {
        return {
          node: current,
          style,
          depth,
          reason: typeof match === "string" ? match : match.reason || null,
          kind: typeof match === "object" ? match.kind || null : null,
        };
      }
    }
    if (current === document.body || current === document.documentElement) {
      break;
    }
    current = current.parentElement;
    depth += 1;
  }
  return null;
}

function findScrollableAncestor(element) {
  return findMatchingAncestor(element, (node, style) => {
    if (node === element) {
      return null;
    }
    if (isScrollableElement(node, style)) {
      return "scrollable ancestor";
    }
    return null;
  });
}

function captureScrollSignals(element, style) {
  const viewport = {
    x: Math.round(window.scrollX || window.pageXOffset || 0),
    y: Math.round(window.scrollY || window.pageYOffset || 0),
    width: Math.round(window.innerWidth || 0),
    height: Math.round(window.innerHeight || 0),
    scrollHeight: Math.round(document.documentElement.scrollHeight || document.body.scrollHeight || 0),
    scrollWidth: Math.round(document.documentElement.scrollWidth || document.body.scrollWidth || 0),
    scrollableY:
      (document.documentElement.scrollHeight || 0) > (window.innerHeight || 0) + 4 ||
      (document.body && (document.body.scrollHeight || 0) > (window.innerHeight || 0) + 4),
    scrollableX:
      (document.documentElement.scrollWidth || 0) > (window.innerWidth || 0) + 4 ||
      (document.body && (document.body.scrollWidth || 0) > (window.innerWidth || 0) + 4),
  };
  const elementScrollable = describeScrollableElement(element, style);
  const ancestor = findScrollableAncestor(element);
  const primary = elementScrollable && (elementScrollable.scrollableY || elementScrollable.scrollableX)
    ? { scope: "element", ...elementScrollable }
    : ancestor
      ? { scope: "ancestor", ...describeScrollableElement(ancestor.node, ancestor.style) }
      : null;
  return {
    detected: Boolean(viewport.scrollableY || viewport.scrollableX || elementScrollable || ancestor),
    viewport,
    element: elementScrollable && (elementScrollable.scrollableY || elementScrollable.scrollableX) ? elementScrollable : null,
    ancestor: ancestor ? describeScrollableElement(ancestor.node, ancestor.style) : null,
    primary,
  };
}

function captureStickySignals(element) {
  const sticky = findMatchingAncestor(element, (node, style) => {
    const position = String(style.position || "").toLowerCase();
    if (position === "sticky") {
      return "sticky";
    }
    if (position === "fixed") {
      return "fixed";
    }
    return null;
  });
  if (!sticky) {
    return {
      detected: false,
      reason: "No sticky or fixed surface detected in the element ancestry.",
    };
  }
  const rect = sticky.node.getBoundingClientRect();
  return {
    detected: true,
    source: sticky.node === element ? "self" : "ancestor",
    kind: String(sticky.style.position || "").toLowerCase(),
    container: captureNodeSignature(sticky.node),
    label: getInteractionLabel(sticky.node),
    role: sticky.node.getAttribute("role"),
    tag: sticky.node.tagName.toLowerCase(),
    offset: {
      top: sticky.style.top,
      right: sticky.style.right,
      bottom: sticky.style.bottom,
      left: sticky.style.left,
    },
    zIndex: sticky.style.zIndex,
    position: sticky.style.position,
    box: {
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
    },
    pinnedToViewport: String(sticky.style.position || "").toLowerCase() === "fixed" || rect.top <= 16,
  };
}

function captureModalSignals(element) {
  const modalSelectors = [
    "dialog[open]",
    '[role="dialog"][aria-modal="true"]',
    '[role="alertdialog"][aria-modal="true"]',
    '[aria-modal="true"]',
    '[popover]:not([popover="manual"])',
    '[data-state="open"][role="dialog"]',
    '[data-state="open"][role="alertdialog"]',
  ];
  const candidates = [];
  for (const selector of modalSelectors) {
    const matches = Array.from(document.querySelectorAll(selector)).filter(isVisibleElement).slice(0, 4);
    for (const node of matches) {
      candidates.push({ node, selector });
    }
  }
  if (!candidates.length) {
    return {
      detected: false,
      reason: "No active modal-like surface detected.",
    };
  }
  const active = candidates.find(({ node }) => node.contains(element)) || candidates[0];
  return {
    detected: true,
    source: active.selector,
    kind: active.node.tagName.toLowerCase() === "dialog" ? "dialog" : active.node.getAttribute("role") || "modal",
    active: captureNodeSignature(active.node),
    label: getInteractionLabel(active.node),
    description: resolveDescriptionFromReferences(active.node) || normalizeText(active.node.getAttribute("data-description"), 120) || normalizeText(active.node.textContent || "", 120),
    title:
      normalizeText(active.node.getAttribute("aria-label"), 120) ||
      normalizeText(active.node.getAttribute("data-title"), 120) ||
      normalizeText(active.node.querySelector("h1, h2, h3, [role='heading']")?.textContent || "", 120),
    inside: active.node.contains(element),
    activeElementInside: active.node.contains(document.activeElement),
    activeElementTag: document.activeElement ? document.activeElement.tagName.toLowerCase() : null,
    candidateCount: candidates.length,
    closeControls: Array.from(active.node.querySelectorAll('button, [role="button"], [aria-label*="close" i], [aria-label*="dismiss" i]'))
      .filter(isVisibleElement)
      .slice(0, 4)
      .map((node) => captureNodeSignature(node)),
  };
}

function captureCarouselSignals(element) {
  const carouselMatcher = (node, style) => {
    const role = String(node.getAttribute("role") || "").toLowerCase();
    const roledescription = String(node.getAttribute("aria-roledescription") || "").toLowerCase();
    const tokens = classTokens(node.className);
    const classText = tokens.join(" ");
    const snapType = String(style.scrollSnapType || "").toLowerCase();
    const scrollable = isScrollableElement(node, style);
    if (role === "carousel" || roledescription.includes("carousel") || roledescription.includes("slider") || roledescription.includes("slideshow") || roledescription.includes("gallery")) {
      return "aria carousel";
    }
    if (/(^|[\s_-])(carousel|slider|swiper|slick|splide|embla|glide)([\s_-]|$)/.test(classText)) {
      return "class carousel";
    }
    if (scrollable && snapType && snapType !== "none") {
      return "scroll snap carousel";
    }
    return null;
  };
  const carousel = findMatchingAncestor(element, carouselMatcher, 10);
  if (!carousel) {
    return {
      detected: false,
      reason: "No carousel-like container detected.",
    };
  }
  const scope = carousel.node;
  const slideSelectors = [
    '[aria-roledescription="slide"]',
    '[role="group"]',
    '[role="tabpanel"]',
    ".swiper-slide",
    ".slick-slide",
    ".splide__slide",
    ".embla__slide",
  ];
  const slides = [];
  for (const selector of slideSelectors) {
    const matches = Array.from(scope.querySelectorAll(selector)).filter(isVisibleElement);
    for (const node of matches) {
      if (slides.length >= 24) {
        break;
      }
      if (!slides.includes(node)) {
        slides.push(node);
      }
    }
    if (slides.length >= 24) {
      break;
    }
  }
  const controls = Array.from(scope.querySelectorAll('button, [role="button"]')).filter(isVisibleElement).slice(0, 8);
  const activeSlide = slides.find((node) => node.getAttribute("aria-current") === "true" || /(active|current|selected)/i.test(normalizeClassName(node.className) || ""));
  const activeSlideIndex = activeSlide ? slides.indexOf(activeSlide) : -1;
  const orientation = String(scope.getAttribute("aria-orientation") || "").toLowerCase() || (String(carousel.style.scrollSnapType || "").toLowerCase().includes("x") ? "horizontal" : String(carousel.style.scrollSnapType || "").toLowerCase().includes("y") ? "vertical" : null);
  return {
    detected: true,
    source: carousel.reason || "ancestor",
    kind: carousel.kind || "carousel",
    container: captureNodeSignature(scope),
    label: getInteractionLabel(scope),
    orientation,
    snapType: carousel.style.scrollSnapType,
    slideCount: slides.length,
    controlCount: controls.length,
    ariaRoleDescription: scope.getAttribute("aria-roledescription"),
    ariaLive: scope.getAttribute("aria-live"),
    currentSlideIndex: activeSlideIndex >= 0 ? activeSlideIndex : null,
    activeSlide: activeSlide ? captureNodeSignature(activeSlide) : null,
    slideLabels: slides.slice(0, 8).map((node) => getInteractionLabel(node)),
    controlLabels: controls.map((node) => getInteractionLabel(node)),
    controls: controls.map((node) => captureNodeSignature(node)),
  };
}

function captureTabpanelSignals(element) {
  const tabpanelMatcher = (node) => {
    const role = String(node.getAttribute("role") || "").toLowerCase();
    const tokens = classTokens(node.className);
    const classText = tokens.join(" ");
    if (role === "tablist" || role === "tabpanel" || role === "tab") {
      return role;
    }
    if (/(^|[\s_-])(tablist|tabpanel|tabs)([\s_-]|$)/.test(classText)) {
      return "class tabpanel";
    }
    if (node.querySelector('[role="tab"], [role="tabpanel"]')) {
      return "descendant tabpanel";
    }
    return null;
  };
  const tabpanel = findMatchingAncestor(element, tabpanelMatcher, 10);
  if (!tabpanel) {
    return {
      detected: false,
      reason: "No tabpanel-like container detected.",
    };
  }
  const scope = tabpanel.node.getAttribute("role") === "tablist"
    ? tabpanel.node
    : tabpanel.node.closest('[role="tablist"]') || tabpanel.node.parentElement || tabpanel.node;
  const tabs = Array.from(scope.querySelectorAll('[role="tab"]')).filter(isVisibleElement).slice(0, 20);
  const panels = Array.from(scope.querySelectorAll('[role="tabpanel"]')).filter(isVisibleElement).slice(0, 20);
  const activeTabs = tabs.filter((node) => node.getAttribute("aria-selected") === "true" || node.getAttribute("tabindex") === "0" || /(active|selected|current)/i.test(normalizeClassName(node.className) || "")).slice(0, 4);
  const panelIds = tabs.map((node) => node.getAttribute("aria-controls")).filter(Boolean).slice(0, 8);
  const controlledPanels = panelIds.map((id) => document.getElementById(id)).filter(Boolean).slice(0, 8);
  return {
    detected: true,
    source: tabpanel.reason || "ancestor",
    kind: tabpanel.kind || "tabpanel",
    tabList: captureNodeSignature(scope),
    label: getInteractionLabel(scope),
    tabCount: tabs.length,
    panelCount: panels.length,
    activeTabs: activeTabs.map((node) => captureNodeSignature(node)),
    activeTabLabels: activeTabs.map((node) => getInteractionLabel(node)),
    controlledPanelIds: panelIds,
    activePanels: controlledPanels.map((node) => captureNodeSignature(node)),
    activePanelLabels: controlledPanels.map((node) => getInteractionLabel(node)),
    tabLabels: tabs.slice(0, 8).map((node) => getInteractionLabel(node)),
    panelLabels: panels.slice(0, 8).map((node) => getInteractionLabel(node)),
    ariaOrientation: scope.getAttribute("aria-orientation"),
  };
}

function captureInteractionSignals(element, style) {
  return {
    scroll: captureScrollSignals(element, style),
    sticky: captureStickySignals(element, style),
    modal: captureModalSignals(element),
    carousel: captureCarouselSignals(element),
    tabpanel: captureTabpanelSignals(element),
  };
}

function summarizeInteractionSignals(signals) {
  if (!signals || typeof signals !== "object") {
    return null;
  }
  const summary = {};
  const primaryScroll = signals.scroll && (signals.scroll.primary || signals.scroll.element || signals.scroll.ancestor)
    ? (signals.scroll.primary || signals.scroll.element || signals.scroll.ancestor)
    : null;
  if (signals.scroll && (primaryScroll || signals.scroll.viewport.scrollableX || signals.scroll.viewport.scrollableY)) {
    summary.scroll = {
      scope: primaryScroll ? primaryScroll.scope || (signals.scroll.element ? "element" : signals.scroll.ancestor ? "ancestor" : "viewport") : "viewport",
      axis:
        primaryScroll && primaryScroll.scrollableAxis
          ? primaryScroll.scrollableAxis
          : signals.scroll.viewport.scrollableX && signals.scroll.viewport.scrollableY
            ? "both"
            : signals.scroll.viewport.scrollableY
              ? "y"
              : signals.scroll.viewport.scrollableX
                ? "x"
                : null,
      viewportScrollableX: Boolean(signals.scroll.viewport && signals.scroll.viewport.scrollableX),
      viewportScrollableY: Boolean(signals.scroll.viewport && signals.scroll.viewport.scrollableY),
      scrollTop: primaryScroll && primaryScroll.scrollTop !== undefined ? primaryScroll.scrollTop : null,
      scrollLeft: primaryScroll && primaryScroll.scrollLeft !== undefined ? primaryScroll.scrollLeft : null,
      scrollHeight: primaryScroll && primaryScroll.scrollHeight !== undefined ? primaryScroll.scrollHeight : null,
      scrollWidth: primaryScroll && primaryScroll.scrollWidth !== undefined ? primaryScroll.scrollWidth : null,
      clientHeight: primaryScroll && primaryScroll.clientHeight !== undefined ? primaryScroll.clientHeight : null,
      clientWidth: primaryScroll && primaryScroll.clientWidth !== undefined ? primaryScroll.clientWidth : null,
      scrollSnapType: primaryScroll && primaryScroll.scrollSnapType !== undefined ? primaryScroll.scrollSnapType : null,
      scrollSnapAlign: primaryScroll && primaryScroll.scrollSnapAlign !== undefined ? primaryScroll.scrollSnapAlign : null,
      scrollSnapStop: primaryScroll && primaryScroll.scrollSnapStop !== undefined ? primaryScroll.scrollSnapStop : null,
      tag: primaryScroll && primaryScroll.tag ? primaryScroll.tag : null,
      role: primaryScroll && primaryScroll.role ? primaryScroll.role : null,
      label: primaryScroll && primaryScroll.label ? primaryScroll.label : null,
    };
  }
  if (signals.sticky && signals.sticky.detected) {
    summary.sticky = {
      kind: signals.sticky.kind || null,
      source: signals.sticky.source || null,
      label: signals.sticky.label || null,
      pinnedToViewport: Boolean(signals.sticky.pinnedToViewport),
      tag: signals.sticky.tag || null,
      role: signals.sticky.role || null,
      position: signals.sticky.position || null,
    };
  }
  if (signals.modal && signals.modal.detected) {
    summary.modal = {
      kind: signals.modal.kind || null,
      label: signals.modal.label || null,
      description: signals.modal.description || null,
      inside: Boolean(signals.modal.inside),
      activeElementInside: Boolean(signals.modal.activeElementInside),
      candidateCount: signals.modal.candidateCount || 0,
      closeControlCount: Array.isArray(signals.modal.closeControls) ? signals.modal.closeControls.length : 0,
    };
  }
  if (signals.carousel && signals.carousel.detected) {
    summary.carousel = {
      kind: signals.carousel.kind || null,
      orientation: signals.carousel.orientation || null,
      label: signals.carousel.label || null,
      slideCount: signals.carousel.slideCount || 0,
      controlCount: signals.carousel.controlCount || 0,
      currentSlideIndex: signals.carousel.currentSlideIndex ?? null,
      activeSlideLabel: signals.carousel.activeSlide ? signals.carousel.activeSlide.label || null : null,
    };
  }
  if (signals.tabpanel && signals.tabpanel.detected) {
    summary.tabpanel = {
      kind: signals.tabpanel.kind || null,
      label: signals.tabpanel.label || null,
      tabCount: signals.tabpanel.tabCount || 0,
      panelCount: signals.tabpanel.panelCount || 0,
      activeTabLabels: Array.isArray(signals.tabpanel.activeTabLabels) ? signals.tabpanel.activeTabLabels.slice(0, 4) : [],
      activePanelLabels: Array.isArray(signals.tabpanel.activePanelLabels) ? signals.tabpanel.activePanelLabels.slice(0, 4) : [],
    };
  }
  return Object.keys(summary).length ? summary : null;
}

function captureSemanticState(element, style) {
  const ownerDocument = element && element.ownerDocument ? element.ownerDocument : document;
  const ownerWindow = ownerDocument && ownerDocument.defaultView ? ownerDocument.defaultView : window;
  const datasetEntries = Object.entries(element.dataset || {}).slice(0, 8);
  const scrollableX = /auto|scroll|overlay/.test(String(style.overflowX || style.overflow || "").toLowerCase()) && element.scrollWidth > element.clientWidth + 4;
  const scrollableY = /auto|scroll|overlay/.test(String(style.overflowY || style.overflow || "").toLowerCase()) && element.scrollHeight > element.clientHeight + 4;
  const semanticState = {
    text: normalizeText(element.innerText || element.textContent || "", 120),
    labelText: getInteractionLabel(element),
    ariaLabel: element.getAttribute("aria-label"),
    ariaDescription: element.getAttribute("aria-description"),
    ariaRoleDescription: element.getAttribute("aria-roledescription"),
    ariaLive: element.getAttribute("aria-live"),
    ariaHasPopup: element.getAttribute("aria-haspopup"),
    ariaExpanded: element.getAttribute("aria-expanded"),
    ariaPressed: element.getAttribute("aria-pressed"),
    ariaSelected: element.getAttribute("aria-selected"),
    ariaControls: element.getAttribute("aria-controls"),
    ariaCurrent: element.getAttribute("aria-current"),
    role: element.getAttribute("role"),
    tag: element.tagName.toLowerCase(),
    type: element.getAttribute("type"),
    href: element.getAttribute("href"),
    target: element.getAttribute("target"),
    rel: element.getAttribute("rel"),
    name: element.getAttribute("name"),
    placeholder: element.getAttribute("placeholder"),
    title: element.getAttribute("title"),
    autocomplete: element.getAttribute("autocomplete"),
    inputMode: element.getAttribute("inputmode"),
    value: "value" in element ? String(element.value || "") : null,
    checked: "checked" in element ? Boolean(element.checked) : null,
    selected: "selected" in element ? Boolean(element.selected) : null,
    open: element.open === true,
    hidden: Boolean(element.hidden),
    disabled: Boolean(element.disabled),
    contentEditable: element.isContentEditable ? "true" : element.getAttribute("contenteditable"),
    tabIndex: element.tabIndex,
    datasetKeys: datasetEntries.map(([key]) => key),
    datasetSample: Object.fromEntries(datasetEntries),
    activeElementTag: ownerDocument.activeElement ? ownerDocument.activeElement.tagName.toLowerCase() : null,
    activeElementMatches: ownerDocument.activeElement === element,
    focusable: isFocusableElement(element, style),
    interactiveKind: getInteractionKind(element),
    scrollY: Math.round(ownerWindow.scrollY || ownerWindow.pageYOffset || 0),
    scrollTop: Math.round(element.scrollTop || 0),
    scrollLeft: Math.round(element.scrollLeft || 0),
    scrollHeight: Math.round(element.scrollHeight || 0),
    scrollWidth: Math.round(element.scrollWidth || 0),
    clientHeight: Math.round(element.clientHeight || 0),
    clientWidth: Math.round(element.clientWidth || 0),
    scrollableX,
    scrollableY,
    scrollableAxis: scrollableX && scrollableY ? "both" : scrollableY ? "y" : scrollableX ? "x" : null,
    scrollSnapType: style.scrollSnapType,
    scrollSnapAlign: style.scrollSnapAlign,
    scrollSnapStop: style.scrollSnapStop,
    position: style.position,
    zIndex: style.zIndex,
    interactionSignals: captureInteractionSignals(element, style),
  };
  if (style) {
    semanticState.styleSnapshot = styleSnapshotFromComputed(style);
  }
  semanticState.stateSummary = summarizeInteractionSignals(semanticState.interactionSignals);
  return semanticState;
}

function collectInteractionRoots(rootDocument = null, maxFrameDocuments = 12) {
  const documentRoot = rootDocument || (typeof document !== "undefined" ? document : null);
  if (!documentRoot) return [];
  const roots = [{ root: documentRoot, kind: "document", frameSrc: null, shadowHostTag: null }];
  const seenDocuments = new Set([documentRoot]);
  const seenShadows = new Set();
  const queue = [{ root: documentRoot, kind: "document", frameSrc: null, shadowHostTag: null }];
  while (queue.length && roots.length < maxFrameDocuments + 64) {
    const current = queue.shift();
    const scope = current.root;
    const elements = Array.from(scope.querySelectorAll ? scope.querySelectorAll("*") : []).slice(0, 400);
    for (const element of elements) {
      if (element.shadowRoot && !seenShadows.has(element.shadowRoot)) {
        seenShadows.add(element.shadowRoot);
        const shadowEntry = {
          root: element.shadowRoot,
          kind: "shadow-root",
          frameSrc: current.frameSrc || null,
          shadowHostTag: element.tagName ? element.tagName.toLowerCase() : null,
        };
        roots.push(shadowEntry);
        queue.push(shadowEntry);
      }
      if (element.tagName && element.tagName.toLowerCase() === "iframe") {
        let frameDoc = null;
        try {
          frameDoc = element.contentDocument;
        } catch (error) {
          frameDoc = null;
        }
        if (frameDoc && frameDoc.documentElement && !seenDocuments.has(frameDoc) && roots.length < maxFrameDocuments + 64) {
          seenDocuments.add(frameDoc);
          const frameEntry = {
            root: frameDoc,
            kind: "frame-document",
            frameSrc: element.getAttribute("src") || element.src || null,
            shadowHostTag: null,
          };
          roots.push(frameEntry);
          queue.push(frameEntry);
        }
      }
    }
  }
  return roots;
}

function findInteractionElement(selector) {
  const documentRoot = typeof document !== "undefined" ? document : null;
  if (!documentRoot) return null;
  for (const entry of collectInteractionRoots(documentRoot, 12)) {
    const root = entry.root;
    if (!root || typeof root.querySelector !== "function") continue;
    const match = root.querySelector(selector);
    if (match) {
      return match;
    }
  }
  return null;
}

function findControlledTarget(element, id) {
  if (!id) return null;
  const ownerDocument = element && element.ownerDocument ? element.ownerDocument : (typeof document !== "undefined" ? document : null);
  if (!ownerDocument) return null;
  for (const entry of collectInteractionRoots(ownerDocument, 12)) {
    const root = entry.root;
    if (root && typeof root.getElementById === "function") {
      const match = root.getElementById(id);
      if (match) return match;
    }
  }
  return null;
}

function captureToggleState(selector) {
  const element = findInteractionElement(selector);
  if (!element) {
    return { available: false, selector };
  }
  const ownerWindow = element.ownerDocument && element.ownerDocument.defaultView ? element.ownerDocument.defaultView : window;
  const style = ownerWindow.getComputedStyle(element);
  const semanticState = captureSemanticState(element, style);
  const ids = String(element.getAttribute("aria-controls") || "")
    .split(/\s+/)
    .map((value) => value.trim())
    .filter(Boolean)
    .slice(0, 6);
  const controlledTargets = ids.map((id) => {
    const target = findControlledTarget(element, id);
    if (!target) {
      return { id, missing: true };
    }
    const targetWindow = target.ownerDocument && target.ownerDocument.defaultView ? target.ownerDocument.defaultView : window;
    const targetStyle = targetWindow.getComputedStyle(target);
    const targetRect = target.getBoundingClientRect();
    return {
      id,
      tag: target.tagName.toLowerCase(),
      role: target.getAttribute("role"),
      kind: getInteractionKind(target),
      label: getInteractionLabel(target),
      focusable: isFocusableElement(target, targetStyle),
      open: target.open === true,
      hidden: Boolean(target.hidden),
      ariaHidden: target.getAttribute("aria-hidden"),
      ariaExpanded: target.getAttribute("aria-expanded"),
      ariaSelected: target.getAttribute("aria-selected"),
      display: targetStyle.display,
      visibility: targetStyle.visibility,
      rect: {
        x: Math.round(targetRect.x),
        y: Math.round(targetRect.y),
        width: Math.round(targetRect.width),
        height: Math.round(targetRect.height),
      },
      text: (target.innerText || target.textContent || "").replace(/\s+/g, " ").trim().slice(0, 120) || null,
    };
  });
  return {
    available: true,
    selector,
    inForm: Boolean(element.closest("form")),
    interactionKind: getInteractionKind(element),
    labelText: getInteractionLabel(element),
    targetSummary: describeInteractionTarget(element, style),
    controlledTargets,
    semanticState,
    stateSummary: semanticState.stateSummary,
  };
}

function captureControlledTargetState(element) {
  const ids = String(element.getAttribute("aria-controls") || "")
    .split(/\s+/)
    .map((value) => value.trim())
    .filter(Boolean)
    .slice(0, 6);
  return ids.map((id) => {
    const target = findControlledTarget(element, id);
    if (!target) {
      return { id, missing: true };
    }
    const targetWindow = target.ownerDocument && target.ownerDocument.defaultView ? target.ownerDocument.defaultView : window;
    const style = targetWindow.getComputedStyle(target);
    const rect = target.getBoundingClientRect();
    const scrollableX = /auto|scroll|overlay/.test(String(style.overflowX || style.overflow || "").toLowerCase()) && target.scrollWidth > target.clientWidth + 4;
    const scrollableY = /auto|scroll|overlay/.test(String(style.overflowY || style.overflow || "").toLowerCase()) && target.scrollHeight > target.clientHeight + 4;
    return {
      id,
      tag: target.tagName.toLowerCase(),
      role: target.getAttribute("role"),
      label: getInteractionLabel(target),
      open: target.open === true,
      hidden: Boolean(target.hidden),
      ariaHidden: target.getAttribute("aria-hidden"),
      ariaExpanded: target.getAttribute("aria-expanded"),
      ariaSelected: target.getAttribute("aria-selected"),
      display: style.display,
      visibility: style.visibility,
      position: style.position,
      zIndex: style.zIndex,
      scrollTop: Math.round(target.scrollTop || 0),
      scrollLeft: Math.round(target.scrollLeft || 0),
      scrollHeight: Math.round(target.scrollHeight || 0),
      scrollWidth: Math.round(target.scrollWidth || 0),
      clientHeight: Math.round(target.clientHeight || 0),
      clientWidth: Math.round(target.clientWidth || 0),
      scrollableX,
      scrollableY,
      scrollableAxis: scrollableX && scrollableY ? "both" : scrollableY ? "y" : scrollableX ? "x" : null,
      scrollSnapType: style.scrollSnapType,
      scrollSnapAlign: style.scrollSnapAlign,
      scrollSnapStop: style.scrollSnapStop,
      rect: {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      },
      text: normalizeText(target.innerText || target.textContent || "", 120),
      styleSnapshot: styleSnapshotFromComputed(style),
    };
  });
}

function captureInteractionState(selector) {
  const element = findInteractionElement(selector);
  if (!element) {
    return { available: false, selector };
  }
  const ownerWindow = element.ownerDocument && element.ownerDocument.defaultView ? element.ownerDocument.defaultView : window;
  const style = ownerWindow.getComputedStyle(element);
  const semanticState = captureSemanticState(element, style);
  return {
    available: true,
    selector,
    inForm: Boolean(element.closest("form")),
    interactionKind: getInteractionKind(element),
    labelText: getInteractionLabel(element),
    targetSummary: describeInteractionTarget(element, style),
    controlledTargets: captureControlledTargetState(element),
    semanticState,
    stateSummary: semanticState.stateSummary,
    baseStyles: styleSnapshotFromComputed(style),
  };
}

function captureStyleSnapshot(selector) {
  const element = findInteractionElement(selector);
  if (!element) return null;
  const ownerWindow = element.ownerDocument && element.ownerDocument.defaultView ? element.ownerDocument.defaultView : window;
  return styleSnapshotFromComputed(ownerWindow.getComputedStyle(element));
}

function hoverInteractionElement(selector) {
  const element = findInteractionElement(selector);
  if (!element) return { available: false, selector };
  const rect = element.getBoundingClientRect();
  const eventInit = { bubbles: true, cancelable: true, composed: true, clientX: rect.x + Math.max(1, rect.width / 2), clientY: rect.y + Math.max(1, rect.height / 2) };
  try {
    element.dispatchEvent(new PointerEvent("pointerover", eventInit));
  } catch (error) {}
  try {
    element.dispatchEvent(new MouseEvent("mouseover", eventInit));
    element.dispatchEvent(new MouseEvent("mouseenter", eventInit));
  } catch (error) {}
  return { available: true, selector };
}

function focusInteractionElement(selector) {
  const element = findInteractionElement(selector);
  if (!element || typeof element.focus !== "function") return { available: false, selector };
  element.focus();
  return { available: true, selector };
}

function blurInteractionElement(selector) {
  const element = findInteractionElement(selector);
  if (!element || typeof element.blur !== "function") return { available: false, selector };
  element.blur();
  return { available: true, selector };
}

function readInteractionValue(selector) {
  const element = findInteractionElement(selector);
  if (!element || !("value" in element)) return { available: false, selector, value: null };
  return { available: true, selector, value: String(element.value || "") };
}

function writeInteractionValue(payload) {
  const selector = payload && payload.selector ? payload.selector : null;
  const value = payload && Object.prototype.hasOwnProperty.call(payload, "value") ? String(payload.value || "") : "";
  const element = findInteractionElement(selector);
  if (!element || !("value" in element)) return { available: false, selector };
  const before = String(element.value || "");
  element.value = value;
  element.dispatchEvent(new Event("input", { bubbles: true, cancelable: true, composed: true }));
  element.dispatchEvent(new Event("change", { bubbles: true, cancelable: true, composed: true }));
  return { available: true, selector, before, after: String(element.value || "") };
}

function readInteractionScroll(selector) {
  const element = findInteractionElement(selector);
  if (!element) return { available: false, selector };
  return {
    available: true,
    selector,
    scrollTop: Math.round(element.scrollTop || 0),
    scrollLeft: Math.round(element.scrollLeft || 0),
  };
}

function writeInteractionScroll(payload) {
  const selector = payload && payload.selector ? payload.selector : null;
  const element = findInteractionElement(selector);
  if (!element) return { available: false, selector };
  if (payload && typeof payload.scrollTop === "number") {
    element.scrollTop = payload.scrollTop;
  }
  if (payload && typeof payload.scrollLeft === "number") {
    element.scrollLeft = payload.scrollLeft;
  }
  if (payload && typeof payload.deltaX === "number") {
    element.scrollLeft = Math.max(0, (element.scrollLeft || 0) + payload.deltaX);
  }
  if (payload && typeof payload.deltaY === "number") {
    element.scrollTop = Math.max(0, (element.scrollTop || 0) + payload.deltaY);
  }
  return {
    available: true,
    selector,
    scrollTop: Math.round(element.scrollTop || 0),
    scrollLeft: Math.round(element.scrollLeft || 0),
  };
}

function clickInteractionElement(selector) {
  const element = findInteractionElement(selector);
  if (!element) return { available: false, selector };
  if (typeof element.click === "function") {
    element.click();
  } else {
    element.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, composed: true }));
  }
  return { available: true, selector };
}

function cleanupInteractionMarkers() {
  const documentRoot = typeof document !== "undefined" ? document : null;
  if (!documentRoot) return true;
  documentRoot
    .querySelectorAll('[data-web-embedding-interaction-id]')
    .forEach((element) => element.removeAttribute('data-web-embedding-interaction-id'));
  return true;
}

function evaluateInteractionRuntime(payload) {
  const action = payload && payload.action ? String(payload.action) : null;
  const selector = payload && payload.selector ? String(payload.selector) : null;
  const value = payload && Object.prototype.hasOwnProperty.call(payload, "value") ? payload.value : null;
  const deltaX = payload && typeof payload.deltaX === "number" ? payload.deltaX : null;
  const deltaY = payload && typeof payload.deltaY === "number" ? payload.deltaY : null;
  const scrollTop = payload && typeof payload.scrollTop === "number" ? payload.scrollTop : null;
  const scrollLeft = payload && typeof payload.scrollLeft === "number" ? payload.scrollLeft : null;
  const maxFrameDocuments = payload && typeof payload.maxFrameDocuments === "number" ? payload.maxFrameDocuments : 12;
  const rootContext = payload && payload.rootContext && typeof payload.rootContext === "object" ? payload.rootContext : null;
  const buildRootSignature = (context) => {
    if (!context || typeof context !== "object") {
      return "document||||0";
    }
    return [
      context.kind || "document",
      context.frameSrc || "",
      context.frameUrl || "",
      context.shadowHostTag || "",
      typeof context.surfaceIndex === "number" ? String(context.surfaceIndex) : "",
    ].join("|");
  };

  const normalizeText = (raw, maxLength = 160) => {
    if (!raw) return null;
    const normalized = String(raw).replace(/\s+/g, " ").trim();
    return normalized ? normalized.slice(0, maxLength) : null;
  };
  const styleSnapshotFromComputed = (style) => ({
    display: style.display,
    position: style.position,
    color: style.color,
    backgroundColor: style.backgroundColor,
    borderColor: style.borderColor,
    borderWidth: style.borderWidth,
    borderRadius: style.borderRadius,
    boxShadow: style.boxShadow,
    opacity: style.opacity,
    transform: style.transform,
    filter: style.filter,
    textDecoration: style.textDecoration,
    outline: style.outline,
    outlineOffset: style.outlineOffset,
    cursor: style.cursor,
  });
  const getInteractionLabel = (element) => {
    if (!element) return null;
    const labelledBy = String(element.getAttribute("aria-labelledby") || "")
      .split(/\s+/)
      .map((value) => value.trim())
      .filter(Boolean)
      .slice(0, 4);
    const labelParts = [];
    for (const id of labelledBy) {
      const labelNode = element.ownerDocument && typeof element.ownerDocument.getElementById === "function"
        ? element.ownerDocument.getElementById(id)
        : null;
      if (labelNode) {
        const text = normalizeText(labelNode.innerText || labelNode.textContent || "", 120);
        if (text) {
          labelParts.push(text);
        }
      }
    }
    if (labelParts.length) {
      return labelParts.join(" ").trim();
    }
    if (element.id && element.ownerDocument) {
      const escapedId = typeof CSS !== "undefined" && CSS && typeof CSS.escape === "function"
        ? CSS.escape(element.id)
        : element.id.replace(/"/g, '\\"');
      const selectorText = `label[for="${escapedId}"]`;
      const directLabels = Array.from(element.ownerDocument.querySelectorAll(selectorText))
        .map((node) => normalizeText(node.innerText || node.textContent || "", 120))
        .filter(Boolean);
      if (directLabels.length) {
        return directLabels.join(" ").trim();
      }
    }
    const wrappingLabel = typeof element.closest === "function" ? element.closest("label") : null;
    if (wrappingLabel) {
      const text = normalizeText(wrappingLabel.innerText || wrappingLabel.textContent || "", 120);
      if (text) {
        return text;
      }
    }
    const fieldset = typeof element.closest === "function" ? element.closest("fieldset") : null;
    if (fieldset) {
      const legend = fieldset.querySelector("legend");
      if (legend) {
        const text = normalizeText(legend.innerText || legend.textContent || "", 120);
        if (text) {
          return text;
        }
      }
    }
    return (
      normalizeText(element.getAttribute("aria-label"), 120) ||
      normalizeText(element.getAttribute("title"), 120) ||
      normalizeText(element.getAttribute("placeholder"), 120) ||
      normalizeText(element.innerText || element.textContent || "", 120) ||
      normalizeText("value" in element ? element.value : "", 120) ||
      normalizeText(element.tagName.toLowerCase(), 80)
    );
  };
  const getInteractionKind = (element) => {
    const tag = element.tagName.toLowerCase();
    const role = String(element.getAttribute("role") || "").toLowerCase();
    const type = String(element.getAttribute("type") || "").toLowerCase();
    if (element.isContentEditable || role === "textbox" || role === "searchbox") return "text-entry";
    if (tag === "input" && ["text", "search", "url", "tel", "email", "password"].includes(type)) return "text-entry";
    if (tag === "textarea") return "text-entry";
    if (tag === "select" || role === "combobox") return "select";
    if (tag === "summary" || ["button", "menuitem", "menuitemcheckbox", "menuitemradio", "switch", "checkbox", "radio", "option", "treeitem"].includes(role) || ["checkbox", "radio", "button"].includes(type)) return "toggle";
    if (role === "tab") return "tab";
    if (role === "link" || tag === "a") return "link";
    if (role === "slider" || type === "range") return "slider";
    if (element.hasAttribute("aria-haspopup")) return "disclosure";
    return null;
  };
  const isFocusableElement = (element, style) => {
    const tag = element.tagName.toLowerCase();
    const role = String(element.getAttribute("role") || "").toLowerCase();
    const type = String(element.getAttribute("type") || "").toLowerCase();
    if (element.hasAttribute("disabled") || element.getAttribute("aria-disabled") === "true") {
      return false;
    }
    if (element.tabIndex >= 0) return true;
    if (element.isContentEditable) return true;
    if (["button", "summary", "details"].includes(tag)) return true;
    if (tag === "a" && element.hasAttribute("href")) return true;
    if (tag === "input" && !["hidden"].includes(type)) return true;
    if (["textarea", "select"].includes(tag)) return true;
    if (["button", "link", "tab", "menuitem", "menuitemcheckbox", "menuitemradio", "switch", "checkbox", "radio", "option", "treeitem", "combobox", "searchbox", "textbox", "slider"].includes(role)) return true;
    return Boolean(style && style.outlineStyle !== "none" && style.visibility !== "hidden");
  };
  const describeInteractionTarget = (element, style) => {
    const pieces = [element.tagName.toLowerCase()];
    const role = element.getAttribute("role");
    if (role) pieces.push(`role=${role}`);
    const label = getInteractionLabel(element);
    if (label) pieces.push(`label=${label}`);
    if (style && style.position) pieces.push(`position=${style.position}`);
    return pieces.join(" · ");
  };
  const summarizeInteractionSignals = (signals) => {
    if (!signals || typeof signals !== "object") return null;
    const summary = [];
    if (signals.toggle && signals.toggle.detected) summary.push("toggle");
    if (signals.textEntry && signals.textEntry.detected) summary.push("text-entry");
    if (signals.scroll && signals.scroll.detected) summary.push("scroll");
    if (signals.sticky && signals.sticky.detected) summary.push("sticky");
    if (signals.modal && signals.modal.detected) summary.push("modal");
    if (signals.carousel && signals.carousel.detected) summary.push("carousel");
    if (signals.tabpanel && signals.tabpanel.detected) summary.push("tabpanel");
    return summary.length ? summary.join(" · ") : null;
  };
  const captureInteractionSignals = (element, style) => {
    const signals = {};
    const role = String(element.getAttribute("role") || "").toLowerCase();
    const tag = element.tagName.toLowerCase();
    const type = String(element.getAttribute("type") || "").toLowerCase();
    const ariaExpanded = element.getAttribute("aria-expanded");
    const ariaPressed = element.getAttribute("aria-pressed");
    const ariaSelected = element.getAttribute("aria-selected");
    const scrollableX = /auto|scroll|overlay/.test(String(style.overflowX || style.overflow || "").toLowerCase()) && element.scrollWidth > element.clientWidth + 4;
    const scrollableY = /auto|scroll|overlay/.test(String(style.overflowY || style.overflow || "").toLowerCase()) && element.scrollHeight > element.clientHeight + 4;
    if (tag === "summary" || ariaExpanded !== null || ariaPressed !== null || ["button", "menuitem", "menuitemcheckbox", "menuitemradio", "switch", "checkbox", "radio"].includes(role) || ["checkbox", "radio", "button"].includes(type)) {
      signals.toggle = { detected: true };
    }
    if (element.isContentEditable || role === "textbox" || role === "searchbox" || tag === "textarea" || (tag === "input" && !["hidden", "checkbox", "radio", "button", "submit", "reset", "file", "range", "color", "date", "datetime-local", "month", "time", "week"].includes(type))) {
      signals.textEntry = { detected: true };
    }
    if (scrollableX || scrollableY) {
      signals.scroll = { detected: true };
    }
    if (style.position === "sticky" || style.position === "fixed") {
      signals.sticky = { detected: true };
    }
    if (role === "dialog" || element.getAttribute("aria-modal") === "true") {
      signals.modal = { detected: true };
    }
    if (role === "tablist" || role === "tabpanel" || role === "tab") {
      signals.tabpanel = { detected: true };
    }
    if (role === "carousel" || /carousel|slider|slideshow|gallery/i.test(String(element.className || ""))) {
      signals.carousel = { detected: true };
    }
    return signals;
  };
  const captureSemanticState = (element, style) => {
    const ownerDocument = element.ownerDocument || document;
    const ownerWindow = ownerDocument.defaultView || window;
    const datasetEntries = Object.entries(element.dataset || {}).slice(0, 8);
    const scrollableX = /auto|scroll|overlay/.test(String(style.overflowX || style.overflow || "").toLowerCase()) && element.scrollWidth > element.clientWidth + 4;
    const scrollableY = /auto|scroll|overlay/.test(String(style.overflowY || style.overflow || "").toLowerCase()) && element.scrollHeight > element.clientHeight + 4;
    const semanticState = {
      text: normalizeText(element.innerText || element.textContent || "", 120),
      labelText: getInteractionLabel(element),
      ariaLabel: element.getAttribute("aria-label"),
      ariaDescription: element.getAttribute("aria-description"),
      ariaRoleDescription: element.getAttribute("aria-roledescription"),
      ariaLive: element.getAttribute("aria-live"),
      ariaHasPopup: element.getAttribute("aria-haspopup"),
      ariaExpanded: element.getAttribute("aria-expanded"),
      ariaPressed: element.getAttribute("aria-pressed"),
      ariaSelected: element.getAttribute("aria-selected"),
      ariaControls: element.getAttribute("aria-controls"),
      ariaCurrent: element.getAttribute("aria-current"),
      role: element.getAttribute("role"),
      tag: element.tagName.toLowerCase(),
      type: element.getAttribute("type"),
      href: element.getAttribute("href"),
      target: element.getAttribute("target"),
      rel: element.getAttribute("rel"),
      name: element.getAttribute("name"),
      placeholder: element.getAttribute("placeholder"),
      title: element.getAttribute("title"),
      autocomplete: element.getAttribute("autocomplete"),
      inputMode: element.getAttribute("inputmode"),
      value: "value" in element ? String(element.value || "") : null,
      checked: "checked" in element ? Boolean(element.checked) : null,
      selected: "selected" in element ? Boolean(element.selected) : null,
      open: element.open === true,
      hidden: Boolean(element.hidden),
      disabled: Boolean(element.disabled),
      contentEditable: element.isContentEditable ? "true" : element.getAttribute("contenteditable"),
      tabIndex: element.tabIndex,
      datasetKeys: datasetEntries.map(([key]) => key),
      datasetSample: Object.fromEntries(datasetEntries),
      activeElementTag: ownerDocument.activeElement ? ownerDocument.activeElement.tagName.toLowerCase() : null,
      activeElementMatches: ownerDocument.activeElement === element,
      focusable: isFocusableElement(element, style),
      interactiveKind: getInteractionKind(element),
      scrollY: Math.round(ownerWindow.scrollY || ownerWindow.pageYOffset || 0),
      scrollTop: Math.round(element.scrollTop || 0),
      scrollLeft: Math.round(element.scrollLeft || 0),
      scrollHeight: Math.round(element.scrollHeight || 0),
      scrollWidth: Math.round(element.scrollWidth || 0),
      clientHeight: Math.round(element.clientHeight || 0),
      clientWidth: Math.round(element.clientWidth || 0),
      scrollableX,
      scrollableY,
      scrollableAxis: scrollableX && scrollableY ? "both" : scrollableY ? "y" : scrollableX ? "x" : null,
      scrollSnapType: style.scrollSnapType,
      scrollSnapAlign: style.scrollSnapAlign,
      scrollSnapStop: style.scrollSnapStop,
      position: style.position,
      zIndex: style.zIndex,
      interactionSignals: captureInteractionSignals(element, style),
    };
    if (style) {
      semanticState.styleSnapshot = styleSnapshotFromComputed(style);
    }
    semanticState.stateSummary = summarizeInteractionSignals(semanticState.interactionSignals);
    return semanticState;
  };
  const collectInteractionRoots = (rootDocument = document) => {
    const roots = [{
      root: rootDocument,
      kind: "document",
      frameSrc: null,
      frameUrl: rootDocument.location && rootDocument.location.href ? rootDocument.location.href : null,
      shadowHostTag: null,
      surfaceIndex: 0,
      rootSignature: buildRootSignature({
        kind: "document",
        frameSrc: null,
        frameUrl: rootDocument.location && rootDocument.location.href ? rootDocument.location.href : null,
        shadowHostTag: null,
        surfaceIndex: 0,
      }),
      rootPath: ["document"],
    }];
    const seenDocuments = new Set([rootDocument]);
    const seenShadows = new Set();
    const queue = [{
      root: rootDocument,
      kind: "document",
      frameSrc: null,
      frameUrl: rootDocument.location && rootDocument.location.href ? rootDocument.location.href : null,
      shadowHostTag: null,
      surfaceIndex: 0,
      rootSignature: buildRootSignature({
        kind: "document",
        frameSrc: null,
        frameUrl: rootDocument.location && rootDocument.location.href ? rootDocument.location.href : null,
        shadowHostTag: null,
        surfaceIndex: 0,
      }),
      rootPath: ["document"],
    }];
    while (queue.length && roots.length < maxFrameDocuments + 64) {
      const current = queue.shift();
      const scope = current.root;
      const elements = Array.from(scope.querySelectorAll ? scope.querySelectorAll("*") : []).slice(0, 400);
      for (const element of elements) {
        if (element.shadowRoot && !seenShadows.has(element.shadowRoot)) {
          seenShadows.add(element.shadowRoot);
          const shadowEntry = {
            root: element.shadowRoot,
            kind: "shadow-root",
            frameSrc: current.frameSrc || null,
            frameUrl: current.frameUrl || null,
            shadowHostTag: element.tagName ? element.tagName.toLowerCase() : null,
            surfaceIndex: roots.length,
            rootSignature: buildRootSignature({
              kind: "shadow-root",
              frameSrc: current.frameSrc || null,
              frameUrl: current.frameUrl || null,
              shadowHostTag: element.tagName ? element.tagName.toLowerCase() : null,
              surfaceIndex: roots.length,
            }),
            rootPath: (current.rootPath || ["document"]).concat([`shadow-root:${element.tagName ? element.tagName.toLowerCase() : "unknown"}`]),
          };
          roots.push(shadowEntry);
          queue.push(shadowEntry);
        }
        if (element.tagName && element.tagName.toLowerCase() === "iframe") {
          let frameDoc = null;
          try {
            frameDoc = element.contentDocument;
          } catch (error) {
            frameDoc = null;
          }
          if (frameDoc && frameDoc.documentElement && !seenDocuments.has(frameDoc) && roots.length < maxFrameDocuments + 64) {
            seenDocuments.add(frameDoc);
            const frameEntry = {
              root: frameDoc,
              kind: "frame-document",
              frameSrc: element.getAttribute("src") || element.src || null,
              frameUrl: frameDoc.location && frameDoc.location.href ? frameDoc.location.href : null,
              shadowHostTag: null,
              surfaceIndex: roots.length,
              rootSignature: buildRootSignature({
                kind: "frame-document",
                frameSrc: element.getAttribute("src") || element.src || null,
                frameUrl: frameDoc.location && frameDoc.location.href ? frameDoc.location.href : null,
                shadowHostTag: null,
                surfaceIndex: roots.length,
              }),
              rootPath: (current.rootPath || ["document"]).concat([`frame-document:${frameDoc.location && frameDoc.location.href ? frameDoc.location.href : (element.getAttribute("src") || element.src || "unknown")}`]),
            };
            roots.push(frameEntry);
            queue.push(frameEntry);
          }
        }
      }
    }
    return roots;
  };
  const scoreRootContext = (entry, targetContext) => {
    if (!targetContext || typeof targetContext !== "object") {
      return 0;
    }
    let score = 0;
    if (targetContext.rootPath && entry.rootPath && Array.isArray(targetContext.rootPath) && Array.isArray(entry.rootPath) && targetContext.rootPath.length === entry.rootPath.length && targetContext.rootPath.every((value, index) => value === entry.rootPath[index])) {
      score += 180;
    }
    if (targetContext.rootSignature && entry.rootSignature && entry.rootSignature === targetContext.rootSignature) {
      score += 160;
    }
    if (typeof targetContext.surfaceIndex === "number" && entry.surfaceIndex === targetContext.surfaceIndex) {
      score += 100;
    }
    if (targetContext.kind && entry.kind === targetContext.kind) {
      score += 20;
    }
    if (targetContext.frameSrc && entry.frameSrc && entry.frameSrc === targetContext.frameSrc) {
      score += 12;
    }
    if (targetContext.frameUrl && entry.frameUrl && entry.frameUrl === targetContext.frameUrl) {
      score += 16;
    }
    if (targetContext.shadowHostTag && entry.shadowHostTag && entry.shadowHostTag === targetContext.shadowHostTag) {
      score += 8;
    }
    return score;
  };
  const findInteractionElement = (targetSelector, targetContext = rootContext) => {
    if (!targetSelector) return null;
    const orderedRoots = collectInteractionRoots(document).slice().sort((left, right) => scoreRootContext(right, targetContext) - scoreRootContext(left, targetContext));
    for (const entry of orderedRoots) {
      const root = entry.root;
      if (!root || typeof root.querySelector !== "function") continue;
      const match = root.querySelector(targetSelector);
      if (match) return match;
    }
    return null;
  };
  const findControlledTarget = (element, id, targetContext = rootContext) => {
    if (!id) return null;
    const ownerDocument = element && element.ownerDocument ? element.ownerDocument : document;
    const orderedRoots = collectInteractionRoots(ownerDocument).slice().sort((left, right) => scoreRootContext(right, targetContext) - scoreRootContext(left, targetContext));
    for (const entry of orderedRoots) {
      const root = entry.root;
      if (!root) continue;
      if (typeof root.getElementById === "function") {
        const match = root.getElementById(id);
        if (match) return match;
      }
      if (typeof root.querySelector === "function") {
        const escaped = typeof CSS !== "undefined" && CSS && typeof CSS.escape === "function" ? CSS.escape(id) : id.replace(/"/g, '\\"');
        const match = root.querySelector(`[id="${escaped}"]`);
        if (match) return match;
      }
    }
    return null;
  };
  const resolveTargetContext = (targetPayload) => {
    if (targetPayload && targetPayload.rootContext && typeof targetPayload.rootContext === "object") {
      return targetPayload.rootContext;
    }
    return rootContext;
  };
  const captureControlledTargetState = (element, targetContext = rootContext) => {
    const ids = String(element.getAttribute("aria-controls") || "")
      .split(/\s+/)
      .map((value) => value.trim())
      .filter(Boolean)
      .slice(0, 6);
    return ids.map((id) => {
      const target = findControlledTarget(element, id, targetContext);
      if (!target) {
        return { id, missing: true };
      }
      const targetWindow = target.ownerDocument && target.ownerDocument.defaultView ? target.ownerDocument.defaultView : window;
      const targetStyle = targetWindow.getComputedStyle(target);
      const targetRect = target.getBoundingClientRect();
      return {
        id,
        tag: target.tagName.toLowerCase(),
        role: target.getAttribute("role"),
        kind: getInteractionKind(target),
        label: getInteractionLabel(target),
        focusable: isFocusableElement(target, targetStyle),
        open: target.open === true,
        hidden: Boolean(target.hidden),
        ariaHidden: target.getAttribute("aria-hidden"),
        ariaExpanded: target.getAttribute("aria-expanded"),
        ariaSelected: target.getAttribute("aria-selected"),
        display: targetStyle.display,
        visibility: targetStyle.visibility,
        rect: {
          x: Math.round(targetRect.x),
          y: Math.round(targetRect.y),
          width: Math.round(targetRect.width),
          height: Math.round(targetRect.height),
        },
        text: (target.innerText || target.textContent || "").replace(/\s+/g, " ").trim().slice(0, 120) || null,
      };
    });
  };
  const captureInteractionState = (targetPayload) => {
    const targetSelector = targetPayload && targetPayload.selector ? String(targetPayload.selector) : null;
    const targetContext = resolveTargetContext(targetPayload);
    const element = findInteractionElement(targetSelector, targetContext);
    if (!element) {
      return { available: false, selector: targetSelector };
    }
    const ownerWindow = element.ownerDocument && element.ownerDocument.defaultView ? element.ownerDocument.defaultView : window;
    const style = ownerWindow.getComputedStyle(element);
    const semanticState = captureSemanticState(element, style);
    return {
      available: true,
      selector: targetSelector,
      inForm: Boolean(element.closest("form")),
      interactionKind: getInteractionKind(element),
      labelText: getInteractionLabel(element),
      targetSummary: describeInteractionTarget(element, style),
      controlledTargets: captureControlledTargetState(element, targetContext),
      semanticState,
      stateSummary: semanticState.stateSummary,
      baseStyles: styleSnapshotFromComputed(style),
    };
  };
  const captureStyleSnapshot = (targetPayload) => {
    const targetSelector = targetPayload && targetPayload.selector ? String(targetPayload.selector) : null;
    const targetContext = resolveTargetContext(targetPayload);
    const element = findInteractionElement(targetSelector, targetContext);
    if (!element) return null;
    const ownerWindow = element.ownerDocument && element.ownerDocument.defaultView ? element.ownerDocument.defaultView : window;
    return styleSnapshotFromComputed(ownerWindow.getComputedStyle(element));
  };
  const hoverInteractionElement = (targetPayload) => {
    const targetSelector = targetPayload && targetPayload.selector ? String(targetPayload.selector) : null;
    const targetContext = resolveTargetContext(targetPayload);
    const element = findInteractionElement(targetSelector, targetContext);
    if (!element) return { available: false, selector: targetSelector };
    const rect = element.getBoundingClientRect();
    const eventInit = { bubbles: true, cancelable: true, composed: true, clientX: rect.x + Math.max(1, rect.width / 2), clientY: rect.y + Math.max(1, rect.height / 2) };
    try {
      element.dispatchEvent(new PointerEvent("pointerover", eventInit));
    } catch (error) {}
    try {
      element.dispatchEvent(new MouseEvent("mouseover", eventInit));
      element.dispatchEvent(new MouseEvent("mouseenter", eventInit));
    } catch (error) {}
    return { available: true, selector: targetSelector };
  };
  const focusInteractionElement = (targetPayload) => {
    const targetSelector = targetPayload && targetPayload.selector ? String(targetPayload.selector) : null;
    const targetContext = resolveTargetContext(targetPayload);
    const element = findInteractionElement(targetSelector, targetContext);
    if (!element || typeof element.focus !== "function") return { available: false, selector: targetSelector };
    element.focus();
    return { available: true, selector: targetSelector };
  };
  const blurInteractionElement = (targetPayload) => {
    const targetSelector = targetPayload && targetPayload.selector ? String(targetPayload.selector) : null;
    const targetContext = resolveTargetContext(targetPayload);
    const element = findInteractionElement(targetSelector, targetContext);
    if (!element || typeof element.blur !== "function") return { available: false, selector: targetSelector };
    element.blur();
    return { available: true, selector: targetSelector };
  };
  const captureToggleState = (targetPayload) => {
    const targetSelector = targetPayload && targetPayload.selector ? String(targetPayload.selector) : null;
    const targetContext = resolveTargetContext(targetPayload);
    const element = findInteractionElement(targetSelector, targetContext);
    if (!element) return { available: false, selector: targetSelector };
    const ownerWindow = element.ownerDocument && element.ownerDocument.defaultView ? element.ownerDocument.defaultView : window;
    const style = ownerWindow.getComputedStyle(element);
    const semanticState = captureSemanticState(element, style);
    const ids = String(element.getAttribute("aria-controls") || "")
      .split(/\s+/)
      .map((value) => value.trim())
      .filter(Boolean)
      .slice(0, 6);
    const controlledTargets = ids.map((id) => {
      const target = findControlledTarget(element, id, targetContext);
      if (!target) {
        return { id, missing: true };
      }
      const targetWindow = target.ownerDocument && target.ownerDocument.defaultView ? target.ownerDocument.defaultView : window;
      const targetStyle = targetWindow.getComputedStyle(target);
      const targetRect = target.getBoundingClientRect();
      return {
        id,
        tag: target.tagName.toLowerCase(),
        role: target.getAttribute("role"),
        kind: getInteractionKind(target),
        label: getInteractionLabel(target),
        focusable: isFocusableElement(target, targetStyle),
        open: target.open === true,
        hidden: Boolean(target.hidden),
        ariaHidden: target.getAttribute("aria-hidden"),
        ariaExpanded: target.getAttribute("aria-expanded"),
        ariaSelected: target.getAttribute("aria-selected"),
        display: targetStyle.display,
        visibility: targetStyle.visibility,
        rect: {
          x: Math.round(targetRect.x),
          y: Math.round(targetRect.y),
          width: Math.round(targetRect.width),
          height: Math.round(targetRect.height),
        },
        text: (target.innerText || target.textContent || "").replace(/\s+/g, " ").trim().slice(0, 120) || null,
      };
    });
    return {
      available: true,
      selector: targetSelector,
      inForm: Boolean(element.closest("form")),
      interactionKind: getInteractionKind(element),
      labelText: getInteractionLabel(element),
      targetSummary: describeInteractionTarget(element, style),
      controlledTargets,
      semanticState,
      stateSummary: semanticState.stateSummary,
    };
  };
  const readInteractionValue = (targetPayload) => {
    const targetSelector = targetPayload && targetPayload.selector ? String(targetPayload.selector) : null;
    const targetContext = resolveTargetContext(targetPayload);
    const element = findInteractionElement(targetSelector, targetContext);
    if (!element || !("value" in element)) return { available: false, selector: targetSelector, value: null };
    return { available: true, selector: targetSelector, value: String(element.value || "") };
  };
  const writeInteractionValue = (targetPayload) => {
    const targetSelector = targetPayload && targetPayload.selector ? targetPayload.selector : null;
    const targetValue = targetPayload && Object.prototype.hasOwnProperty.call(targetPayload, "value") ? String(targetPayload.value || "") : "";
    const targetContext = resolveTargetContext(targetPayload);
    const element = findInteractionElement(targetSelector, targetContext);
    if (!element || !("value" in element)) return { available: false, selector: targetSelector };
    const before = String(element.value || "");
    element.value = targetValue;
    element.dispatchEvent(new Event("input", { bubbles: true, cancelable: true, composed: true }));
    element.dispatchEvent(new Event("change", { bubbles: true, cancelable: true, composed: true }));
    return { available: true, selector: targetSelector, before, after: String(element.value || "") };
  };
  const readInteractionScroll = (targetPayload) => {
    const targetSelector = targetPayload && targetPayload.selector ? String(targetPayload.selector) : null;
    const targetContext = resolveTargetContext(targetPayload);
    const element = findInteractionElement(targetSelector, targetContext);
    if (!element) return { available: false, selector: targetSelector };
    return {
      available: true,
      selector: targetSelector,
      scrollTop: Math.round(element.scrollTop || 0),
      scrollLeft: Math.round(element.scrollLeft || 0),
    };
  };
  const writeInteractionScroll = (targetPayload) => {
    const targetSelector = targetPayload && targetPayload.selector ? targetPayload.selector : null;
    const targetContext = resolveTargetContext(targetPayload);
    const element = findInteractionElement(targetSelector, targetContext);
    if (!element) return { available: false, selector: targetSelector };
    if (targetPayload && typeof targetPayload.scrollTop === "number") {
      element.scrollTop = targetPayload.scrollTop;
    }
    if (targetPayload && typeof targetPayload.scrollLeft === "number") {
      element.scrollLeft = targetPayload.scrollLeft;
    }
    if (targetPayload && typeof targetPayload.deltaX === "number") {
      element.scrollLeft = Math.max(0, (element.scrollLeft || 0) + targetPayload.deltaX);
    }
    if (targetPayload && typeof targetPayload.deltaY === "number") {
      element.scrollTop = Math.max(0, (element.scrollTop || 0) + targetPayload.deltaY);
    }
    return {
      available: true,
      selector: targetSelector,
      scrollTop: Math.round(element.scrollTop || 0),
      scrollLeft: Math.round(element.scrollLeft || 0),
    };
  };
  const clickInteractionElement = (targetPayload) => {
    const targetSelector = targetPayload && targetPayload.selector ? String(targetPayload.selector) : null;
    const targetContext = resolveTargetContext(targetPayload);
    const element = findInteractionElement(targetSelector, targetContext);
    if (!element) return { available: false, selector: targetSelector };
    if (typeof element.click === "function") {
      element.click();
    } else {
      element.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, composed: true }));
    }
    return { available: true, selector: targetSelector };
  };
  const cleanupInteractionMarkers = () => {
    for (const entry of collectInteractionRoots(document)) {
      const root = entry.root;
      if (!root || typeof root.querySelectorAll !== "function") continue;
      root
        .querySelectorAll('[data-web-embedding-interaction-id]')
        .forEach((element) => element.removeAttribute('data-web-embedding-interaction-id'));
    }
    return true;
  };

  switch (action) {
    case "hover":
      return hoverInteractionElement(payload);
    case "focus":
      return focusInteractionElement(payload);
    case "blur":
      return blurInteractionElement(payload);
    case "capture-style":
      return captureStyleSnapshot(payload);
    case "capture-state":
      return captureInteractionState(payload);
    case "capture-toggle":
      return captureToggleState(payload);
    case "read-value":
      return readInteractionValue(payload);
    case "write-value":
      return writeInteractionValue(payload);
    case "read-scroll":
      return readInteractionScroll(payload);
    case "write-scroll":
      return writeInteractionScroll(payload);
    case "click":
      return clickInteractionElement(payload);
    case "cleanup":
      return cleanupInteractionMarkers();
    default:
      return { available: false, action, selector };
  }
}

function summarizeInteractionState(state) {
  if (!state || typeof state !== "object") {
    return null;
  }
  const semantic = state.semanticState && typeof state.semanticState === "object" ? state.semanticState : state;
  const signals = semantic.interactionSignals || state.interactionSignals || null;
  const signalSummary = summarizeInteractionSignals(signals);
  const pick = (key) => (Object.prototype.hasOwnProperty.call(semantic, key) ? semantic[key] : null);
  const summaryBits = [
    pick("interactiveKind") || state.interactionKind || null,
    pick("labelText") || state.labelText || pick("text") || state.text || null,
  ]
  .filter(Boolean)
  .map((value) => normalizeText(value, 96) || null)
  .filter(Boolean);
  if (signalSummary) {
    if (signalSummary.scroll) {
      summaryBits.push(`scroll:${signalSummary.scroll.axis || signalSummary.scroll.scope}`);
    }
    if (signalSummary.sticky) {
      summaryBits.push(`sticky:${signalSummary.sticky.kind || "surface"}`);
    }
    if (signalSummary.modal) {
      summaryBits.push(`modal:${signalSummary.modal.kind || "surface"}`);
    }
    if (signalSummary.carousel) {
      summaryBits.push(`carousel:${signalSummary.carousel.orientation || "surface"}`);
    }
    if (signalSummary.tabpanel) {
      summaryBits.push(`tabs:${signalSummary.tabpanel.tabCount || 0}`);
    }
  }
  return {
    tag: pick("tag") ?? state.tag ?? null,
    role: pick("role") ?? state.role ?? null,
    text: pick("text") ?? state.text ?? null,
    labelText: pick("labelText") ?? state.labelText ?? null,
    href: pick("href") ?? state.href ?? null,
    type: pick("type") ?? state.type ?? null,
    value: pick("value"),
    title: pick("title") ?? state.title ?? null,
    placeholder: pick("placeholder") ?? state.placeholder ?? null,
    autocomplete: pick("autocomplete") ?? state.autocomplete ?? null,
    inputMode: pick("inputMode") ?? state.inputMode ?? null,
    ariaExpanded: pick("ariaExpanded"),
    ariaPressed: pick("ariaPressed"),
    ariaSelected: pick("ariaSelected"),
    ariaHasPopup: pick("ariaHasPopup"),
    open: pick("open"),
    checked: pick("checked"),
    selected: pick("selected"),
    disabled: pick("disabled"),
    hidden: pick("hidden"),
    scrollY: pick("scrollY"),
    scrollTop: pick("scrollTop"),
    scrollLeft: pick("scrollLeft"),
    scrollHeight: pick("scrollHeight"),
    scrollWidth: pick("scrollWidth"),
    clientHeight: pick("clientHeight"),
    clientWidth: pick("clientWidth"),
    scrollableX: pick("scrollableX"),
    scrollableY: pick("scrollableY"),
    scrollableAxis: pick("scrollableAxis"),
    scrollSnapType: pick("scrollSnapType"),
    scrollSnapAlign: pick("scrollSnapAlign"),
    scrollSnapStop: pick("scrollSnapStop"),
    position: pick("position"),
    zIndex: pick("zIndex"),
    activeElementTag: pick("activeElementTag") ?? state.activeElementTag ?? null,
    kind: pick("interactiveKind") ?? state.interactionKind ?? null,
    focusable: pick("focusable") ?? state.focusable ?? null,
    summaryLabel: summaryBits.join(" · ") || null,
    stateSummary: semantic.stateSummary || state.stateSummary || null,
    signals: signalSummary,
  };
}

function diffInteractionStates(before, after) {
  if (!before || !after) {
    return {};
  }
  const beforeSemantic = before.semanticState && typeof before.semanticState === "object" ? before.semanticState : before;
  const afterSemantic = after.semanticState && typeof after.semanticState === "object" ? after.semanticState : after;
  const keys = [
    "value",
    "checked",
    "selected",
    "ariaCurrent",
    "ariaExpanded",
    "ariaPressed",
    "ariaSelected",
    "ariaHasPopup",
    "contentEditable",
    "tabIndex",
    "open",
    "hidden",
    "disabled",
    "scrollY",
    "scrollTop",
    "scrollLeft",
    "scrollHeight",
    "scrollWidth",
    "clientHeight",
    "clientWidth",
    "scrollableX",
    "scrollableY",
    "scrollableAxis",
    "scrollSnapType",
    "scrollSnapAlign",
    "scrollSnapStop",
    "position",
    "zIndex",
    "activeElementTag",
    "labelText",
    "title",
    "placeholder",
    "autocomplete",
    "inputMode",
    "interactiveKind",
    "focusable",
  ];
  const diff = {};
  for (const key of keys) {
    if ((beforeSemantic[key] ?? null) !== (afterSemantic[key] ?? null)) {
      diff[key] = {
        before: beforeSemantic[key] ?? null,
        after: afterSemantic[key] ?? null,
      };
    }
  }
  const beforeTargets = JSON.stringify(before.controlledTargets || beforeSemantic.controlledTargets || []);
  const afterTargets = JSON.stringify(after.controlledTargets || afterSemantic.controlledTargets || []);
  if (beforeTargets !== afterTargets) {
    diff.controlledTargets = {
      before: before.controlledTargets || beforeSemantic.controlledTargets || [],
      after: after.controlledTargets || afterSemantic.controlledTargets || [],
    };
  }
  const beforeStyle = JSON.stringify(beforeSemantic.styleSnapshot || before.baseStyles || {});
  const afterStyle = JSON.stringify(afterSemantic.styleSnapshot || after.baseStyles || {});
  if (beforeStyle !== afterStyle) {
    diff.styleSnapshot = {
      before: beforeSemantic.styleSnapshot || before.baseStyles || {},
      after: afterSemantic.styleSnapshot || after.baseStyles || {},
    };
  }
  const beforeSignals = beforeSemantic.interactionSignals || before.interactionSignals || null;
  const afterSignals = afterSemantic.interactionSignals || after.interactionSignals || null;
  if (JSON.stringify(beforeSignals || {}) !== JSON.stringify(afterSignals || {})) {
    diff.interactionSignals = {
      before: beforeSignals || null,
      after: afterSignals || null,
    };
  }
  return diff;
}

function isSafeToggleCandidate(candidate) {
  if (!candidate || candidate.href) {
    return false;
  }
  if (candidate.inForm) {
    return false;
  }
  const tag = String(candidate.tag || "").toLowerCase();
  const role = String(candidate.role || "").toLowerCase();
  const type = String(candidate.type || "").toLowerCase();
  if (tag === "summary") {
    return true;
  }
  if (candidate.ariaExpanded !== null || candidate.ariaControls) {
    return true;
  }
  if (role === "tab" || role === "menuitem" || role === "menuitemcheckbox" || role === "menuitemradio" || role === "switch" || role === "combobox" || role === "checkbox" || role === "radio" || role === "option" || role === "treeitem") {
    return true;
  }
  if (tag === "button" && type === "button") {
    return true;
  }
  if (tag === "input" && ["checkbox", "radio"].includes(type)) {
    return true;
  }
  return false;
}

(async () => {
  let browser = null;
  let context = null;
  let sessionMode = "ephemeral";
  let storageStateApplied = false;
  let storageStateError = null;
  let storageStateExported = false;
  let storageStateExportError = null;
  const requestEntries = [];
  const responseEntries = [];
  const contextOptions = {
    viewport: { width: viewportWidth, height: viewportHeight },
  };

  try {
    if (userDataDir) {
      sessionMode = storageStatePath ? "persistent-with-storage-state" : "persistent";
      const launchOptions = buildLaunchOptions();
      if (storageStatePath) {
        launchOptions.storageState = storageStatePath;
      }
      try {
        context = await playwright.chromium.launchPersistentContext(userDataDir, {
          ...launchOptions,
          viewport: { width: viewportWidth, height: viewportHeight },
        });
        storageStateApplied = Boolean(storageStatePath);
      } catch (firstError) {
        if (!storageStatePath) {
          throw firstError;
        }
        storageStateError = firstError.message;
        context = await playwright.chromium.launchPersistentContext(userDataDir, {
          ...buildLaunchOptions(),
          viewport: { width: viewportWidth, height: viewportHeight },
        });
        sessionMode = "persistent";
        storageStateApplied = false;
      }
    } else {
      browser = await playwright.chromium.launch(buildLaunchOptions());
      const newContextOptions = { ...contextOptions };
      if (storageStatePath) {
        newContextOptions.storageState = storageStatePath;
      }
      context = await browser.newContext(newContextOptions);
      sessionMode = storageStatePath ? "storage-state" : "ephemeral";
      storageStateApplied = Boolean(storageStatePath);
    }

    const page = context.pages()[0] || await context.newPage();
    const hits = [];
    const failedRequestEntries = [];
    const redirectRequestEntries = [];
    const requestIds = new WeakMap();
    let requestSequence = 0;
    const pageStartedDateTime = new Date().toISOString();
    const commonRequestHeaderKeys = [
      "accept",
      "accept-language",
      "cache-control",
      "content-type",
      "cookie",
      "origin",
      "pragma",
      "referer",
      "sec-ch-ua",
      "sec-ch-ua-mobile",
      "sec-ch-ua-platform",
      "sec-fetch-dest",
      "sec-fetch-mode",
      "sec-fetch-site",
      "sec-fetch-user",
      "user-agent",
      "x-requested-with",
    ];
    const commonResponseHeaderKeys = [
      "cache-control",
      "content-encoding",
      "content-length",
      "content-security-policy",
      "content-type",
      "date",
      "etag",
      "expires",
      "last-modified",
      "location",
      "set-cookie",
      "server",
      "transfer-encoding",
      "vary",
      "x-frame-options",
      "x-powered-by",
    ];
    const summarizeHeaderPresence = (headers, keys) => {
      const normalized = headers || {};
      const present = [];
      const missing = [];
      for (const key of keys) {
        if (Object.prototype.hasOwnProperty.call(normalized, key) && normalized[key] !== undefined && normalized[key] !== null && String(normalized[key]).trim() !== "") {
          present.push(key);
        } else {
          missing.push(key);
        }
      }
      return {
        total: keys.length,
        presentCount: present.length,
        missingCount: missing.length,
        present,
        missing: missing.slice(0, 8),
      };
    };
    const headersToArray = (headers, keys = null) => {
      const normalized = headers || {};
      const entries = [];
      const sourceKeys = Array.isArray(keys) && keys.length ? keys : Object.keys(normalized);
      for (const key of sourceKeys) {
        if (!Object.prototype.hasOwnProperty.call(normalized, key)) {
          continue;
        }
        const value = normalized[key];
        if (value === undefined || value === null || String(value).trim() === "") {
          continue;
        }
        if (Array.isArray(value)) {
          for (const item of value) {
            if (item === undefined || item === null || String(item).trim() === "") {
              continue;
            }
            entries.push({ name: String(key), value: String(item) });
          }
          continue;
        }
        entries.push({ name: String(key), value: String(value) });
      }
      return entries;
    };
    const queryStringFromUrl = (rawUrl) => {
      try {
        const parsed = new URL(rawUrl);
        return Array.from(parsed.searchParams.entries()).map(([name, value]) => ({
          name,
          value,
        }));
      } catch (error) {
        return [];
      }
    };
    const urlPartsFromUrl = (rawUrl) => {
      try {
        const parsed = new URL(rawUrl);
        return {
          href: parsed.href,
          origin: parsed.origin,
          protocol: parsed.protocol,
          username: parsed.username || null,
          password: parsed.password ? "***" : null,
          host: parsed.host,
          hostname: parsed.hostname,
          port: parsed.port || null,
          pathname: parsed.pathname,
          search: parsed.search || "",
          hash: parsed.hash || "",
        };
      } catch (error) {
        return {
          href: rawUrl || null,
          origin: null,
          protocol: null,
          username: null,
          password: null,
          host: null,
          hostname: null,
          port: null,
          pathname: null,
          search: null,
          hash: null,
        };
      }
    };
    const headersSizeFromArray = (entries) => {
      if (!Array.isArray(entries) || !entries.length) {
        return 0;
      }
      return entries.reduce((total, entry) => {
        if (!entry || typeof entry.name !== "string") {
          return total;
        }
        const value = typeof entry.value === "string" ? entry.value : String(entry.value || "");
        return total + entry.name.length + value.length + 4;
      }, 0);
    };
    const parseFormEncodedPostData = (postDataText, contentType) => {
      if (!postDataText) {
        return [];
      }
      const normalizedContentType = String(contentType || "").toLowerCase();
      if (!normalizedContentType.includes("application/x-www-form-urlencoded") && !/^[^=]+=[^=]+/.test(postDataText)) {
        return [];
      }
      try {
        const parsed = new URLSearchParams(String(postDataText));
        return Array.from(parsed.entries()).map(([name, value]) => ({
          name,
          value,
          fileName: null,
          contentType: null,
        }));
      } catch (error) {
        return [];
      }
    };
    const parseCookieAttributes = (parts) => {
      const attrs = { path: null, domain: null, expires: null, httpOnly: null, secure: null, sameSite: null };
      for (const rawPart of parts || []) {
        const part = String(rawPart || "").trim();
        if (!part) continue;
        const separatorIndex = part.indexOf("=");
        const key = (separatorIndex === -1 ? part : part.slice(0, separatorIndex)).trim().toLowerCase();
        const value = separatorIndex === -1 ? "" : part.slice(separatorIndex + 1).trim();
        if (key === "path") attrs.path = value || null;
        else if (key === "domain") attrs.domain = value || null;
        else if (key === "expires") attrs.expires = value || null;
        else if (key === "samesite") attrs.sameSite = value || null;
        else if (key === "httponly") attrs.httpOnly = true;
        else if (key === "secure") attrs.secure = true;
      }
      return attrs;
    };
    const splitCookiePairs = (rawCookieValue) => {
      if (!rawCookieValue) {
        return [];
      }
      return String(rawCookieValue)
        .split(/;\s*/)
        .map((part) => part.trim())
        .filter(Boolean)
        .map((pair) => {
          const separatorIndex = pair.indexOf("=");
          if (separatorIndex === -1) {
            return { name: pair, value: "" };
          }
          return {
            name: pair.slice(0, separatorIndex).trim(),
            value: pair.slice(separatorIndex + 1).trim(),
          };
        })
        .filter((item) => item.name)
        .map((item) => ({
          name: item.name,
          value: item.value,
          path: null,
          domain: null,
          expires: null,
          httpOnly: null,
          secure: null,
          sameSite: null,
          }));
    };
    const splitSetCookieValues = (rawCookieValue) => {
      if (!rawCookieValue) {
        return [];
      }
      return String(rawCookieValue)
        .split(/,(?=[^;=]+=[^;=]+)/)
        .map((part) => part.trim())
        .filter(Boolean)
        .map((cookieString) => {
          const segments = cookieString.split(";").map((part) => part.trim()).filter(Boolean);
          if (!segments.length) return null;
          const first = segments.shift();
          const separatorIndex = first.indexOf("=");
          const name = separatorIndex === -1 ? first.trim() : first.slice(0, separatorIndex).trim();
          const value = separatorIndex === -1 ? "" : first.slice(separatorIndex + 1).trim();
          if (!name) return null;
          const attrs = parseCookieAttributes(segments);
          return {
            name,
            value,
            path: attrs.path,
            domain: attrs.domain,
            expires: attrs.expires,
            httpOnly: attrs.httpOnly,
            secure: attrs.secure,
            sameSite: attrs.sameSite,
          };
        })
        .filter(Boolean);
    };
    const cookiePairsFromHeaders = (headers, key) => {
      const normalized = headers || {};
      const raw = normalized[key];
      if (!raw) {
        return [];
      }
      if (Array.isArray(raw)) {
        return raw.flatMap((item) => key === "set-cookie" ? splitSetCookieValues(item) : splitCookiePairs(item));
      }
      return key === "set-cookie" ? splitSetCookieValues(raw) : splitCookiePairs(raw);
    };
    const filterHeaders = (headers, keys) => {
      const normalized = headers || {};
      const out = {};
      for (const key of keys) {
        if (Object.prototype.hasOwnProperty.call(normalized, key) && normalized[key] !== undefined && normalized[key] !== null && String(normalized[key]).trim() !== "") {
          out[key] = String(normalized[key]);
        }
      }
      return out;
    };
    const timingBucket = (timing) => {
      if (!timing || typeof timing !== "object") {
        return "unknown";
      }
      const value = Number.isFinite(Number(timing.responseEnd)) && Number(timing.responseEnd) >= 0
        ? Number(timing.responseEnd)
        : Number.isFinite(Number(timing.responseStart)) && Number(timing.responseStart) >= 0
          ? Number(timing.responseStart)
          : Number.isFinite(Number(timing.requestStart)) && Number(timing.requestStart) >= 0
            ? Number(timing.requestStart)
            : null;
      if (value === null) {
        return "unknown";
      }
      if (value < 100) return "sub-100ms";
      if (value < 300) return "100-300ms";
      if (value < 800) return "300-800ms";
      if (value < 2000) return "800ms-2s";
      return "2s+";
    };

    page.on("request", (request) => {
      const requestId = ++requestSequence;
      requestIds.set(request, requestId);
      let frameUrl = null;
      try {
        frameUrl = request.frame() ? request.frame().url() : null;
      } catch (error) {}
      let redirectDepth = 0;
      let redirectedFromUrl = null;
      try {
        let previous = typeof request.redirectedFrom === "function" ? request.redirectedFrom() : null;
        while (previous && redirectDepth < 8) {
          redirectDepth += 1;
          if (!redirectedFromUrl) {
            redirectedFromUrl = previous.url ? previous.url() : null;
          }
          previous = typeof previous.redirectedFrom === "function" ? previous.redirectedFrom() : null;
        }
      } catch (error) {}
      const requestHeaders = typeof request.headers === "function" ? request.headers() : {};
      const requestUrl = request.url();
      const postData = typeof request.postData === "function" ? request.postData() : null;
      requestEntries.push({
        requestId,
        url: requestUrl,
        urlParts: urlPartsFromUrl(requestUrl),
        method: request.method(),
        resourceType: request.resourceType(),
        isNavigationRequest: request.isNavigationRequest(),
        frameUrl,
        hasPostData: Boolean(postData),
        postDataSize: postData ? postData.length : 0,
        postDataText: postData ? String(postData).slice(0, 4096) : null,
        postDataParams: parseFormEncodedPostData(postData ? String(postData).slice(0, 4096) : null, requestHeaders["content-type"] || requestHeaders["Content-Type"] || null),
        requestHeaders: filterHeaders(requestHeaders, commonRequestHeaderKeys),
        requestHeadersArray: headersToArray(requestHeaders),
        requestCookies: cookiePairsFromHeaders(requestHeaders, "cookie"),
        queryString: queryStringFromUrl(requestUrl),
        headerPresence: summarizeHeaderPresence(requestHeaders, commonRequestHeaderKeys),
        redirectDepth,
        redirectedFromUrl,
        timingBucket: timingBucket(typeof request.timing === "function" ? request.timing() : null),
        observedAt: Date.now(),
      });
      if (redirectDepth > 0) {
        redirectRequestEntries.push({
          requestId,
          url: request.url(),
          method: request.method(),
          resourceType: request.resourceType(),
          frameUrl,
          redirectDepth,
          redirectedFromUrl,
          observedAt: Date.now(),
        });
      }
    });

    page.on("response", (response) => {
      const target = response.url();
      const request = response.request();
      const requestId = requestIds.get(request) || null;
      let frameUrl = null;
      try {
        frameUrl = request.frame() ? request.frame().url() : null;
      } catch (error) {}
      const responseHeaders = typeof response.headers === "function" ? response.headers() : {};
      responseEntries.push({
        requestId,
        url: target,
        status: response.status(),
        statusText: typeof response.statusText === "function" ? response.statusText() : null,
        resourceType: request.resourceType(),
        method: request.method(),
        contentType: responseHeaders["content-type"] || null,
        contentLength: responseHeaders["content-length"] || null,
        redirectURL: responseHeaders["location"] || null,
        responseHeaders: filterHeaders(responseHeaders, commonResponseHeaderKeys),
        responseHeadersArray: headersToArray(responseHeaders),
        responseCookies: cookiePairsFromHeaders(responseHeaders, "set-cookie"),
        headerPresence: summarizeHeaderPresence(responseHeaders, commonResponseHeaderKeys),
        frameUrl,
        fromServiceWorker: typeof response.fromServiceWorker === "function" ? response.fromServiceWorker() : false,
        timingBucket: timingBucket(typeof response.timing === "function" ? response.timing() : (typeof request.timing === "function" ? request.timing() : null)),
        responseTiming: typeof response.timing === "function" ? response.timing() : null,
        observedAt: Date.now(),
      });
      if (!regex.test(target)) {
        return;
      }
      hits.push({ status: response.status(), url: target });
    });

    page.on("requestfailed", (request) => {
      const requestId = requestIds.get(request) || null;
      let frameUrl = null;
      try {
        frameUrl = request.frame() ? request.frame().url() : null;
      } catch (error) {}
      const failure = request.failure ? request.failure() : null;
      failedRequestEntries.push({
        requestId,
        url: request.url(),
        method: request.method(),
        resourceType: request.resourceType(),
        isNavigationRequest: request.isNavigationRequest(),
        frameUrl,
        errorText: failure && failure.errorText ? failure.errorText : null,
        observedAt: Date.now(),
      });
    });

    await page.goto(pageUrl, { waitUntil: "domcontentloaded", timeout: 90000 });
    await page.waitForTimeout(waitSeconds * 1000);
    const navigationTiming = await page.evaluate(() => {
      const navEntry = performance && typeof performance.getEntriesByType === "function"
        ? performance.getEntriesByType("navigation")[0] || null
        : null;
      const timing = navEntry || (performance && performance.timing ? performance.timing : null);
      if (!timing) {
        return null;
      }
      const fetchStart = Number.isFinite(Number(timing.fetchStart)) ? Number(timing.fetchStart) : 0;
      const domContentLoadedEventEnd = Number.isFinite(Number(timing.domContentLoadedEventEnd)) ? Number(timing.domContentLoadedEventEnd) : null;
      const domContentLoadedEventStart = Number.isFinite(Number(timing.domContentLoadedEventStart)) ? Number(timing.domContentLoadedEventStart) : null;
      const loadEventEnd = Number.isFinite(Number(timing.loadEventEnd)) ? Number(timing.loadEventEnd) : null;
      const loadEventStart = Number.isFinite(Number(timing.loadEventStart)) ? Number(timing.loadEventStart) : null;
      const responseEnd = Number.isFinite(Number(timing.responseEnd)) ? Number(timing.responseEnd) : null;
      const responseStart = Number.isFinite(Number(timing.responseStart)) ? Number(timing.responseStart) : null;
      const domInteractive = Number.isFinite(Number(timing.domInteractive)) ? Number(timing.domInteractive) : null;
      const domComplete = Number.isFinite(Number(timing.domComplete)) ? Number(timing.domComplete) : null;
      const paintEntries = performance && typeof performance.getEntriesByType === "function"
        ? performance.getEntriesByType("paint")
        : [];
      const firstPaintEntry = Array.isArray(paintEntries) ? paintEntries.find((entry) => entry && entry.name === "first-paint") || null : null;
      const firstContentfulPaintEntry = Array.isArray(paintEntries) ? paintEntries.find((entry) => entry && entry.name === "first-contentful-paint") || null : null;
      return {
        type: timing.type || null,
        startTime: Number.isFinite(Number(timing.startTime)) ? Number(timing.startTime) : 0,
        fetchStart,
        responseEnd,
        responseStart,
        domInteractive,
        domComplete,
        domContentLoadedEventStart,
        domContentLoadedEventEnd,
        loadEventStart,
        loadEventEnd,
        firstPaint: firstPaintEntry && Number.isFinite(Number(firstPaintEntry.startTime)) ? Number(firstPaintEntry.startTime) : null,
        firstContentfulPaint: firstContentfulPaintEntry && Number.isFinite(Number(firstContentfulPaintEntry.startTime)) ? Number(firstContentfulPaintEntry.startTime) : null,
        domContentLoadedDuration: domContentLoadedEventEnd !== null ? Math.max(0, domContentLoadedEventEnd - fetchStart) : null,
        loadDuration: loadEventEnd !== null ? Math.max(0, loadEventEnd - fetchStart) : null,
      };
    }).catch(() => null);

    const interactionRuntimeSources = {
      trimText: trimText.toString(),
      styleSnapshotFromComputed: styleSnapshotFromComputed.toString(),
      diffStyleSnapshots: diffStyleSnapshots.toString(),
      safeReplayValue: safeReplayValue.toString(),
      normalizeText: normalizeText.toString(),
      resolveLabelFromReferences: resolveLabelFromReferences.toString(),
      resolveAssociatedLabel: resolveAssociatedLabel.toString(),
      resolveFormContextLabel: resolveFormContextLabel.toString(),
      resolveDescriptionFromReferences: resolveDescriptionFromReferences.toString(),
      getInteractionKind: getInteractionKind.toString(),
      isFocusableElement: isFocusableElement.toString(),
      compactLabelText: compactLabelText.toString(),
      getInteractionLabel: getInteractionLabel.toString(),
      describeInteractionTarget: describeInteractionTarget.toString(),
      normalizeClassName: normalizeClassName.toString(),
      classTokens: classTokens.toString(),
      captureNodeSignature: captureNodeSignature.toString(),
      isVisibleElement: isVisibleElement.toString(),
      isScrollableElement: isScrollableElement.toString(),
      describeScrollableElement: describeScrollableElement.toString(),
      findMatchingAncestor: findMatchingAncestor.toString(),
      findScrollableAncestor: findScrollableAncestor.toString(),
      captureScrollSignals: captureScrollSignals.toString(),
      captureStickySignals: captureStickySignals.toString(),
      captureModalSignals: captureModalSignals.toString(),
      captureCarouselSignals: captureCarouselSignals.toString(),
      captureTabpanelSignals: captureTabpanelSignals.toString(),
      captureInteractionSignals: captureInteractionSignals.toString(),
      summarizeInteractionSignals: summarizeInteractionSignals.toString(),
      captureSemanticState: captureSemanticState.toString(),
      collectInteractionRoots: collectInteractionRoots.toString(),
      findInteractionElement: findInteractionElement.toString(),
      findControlledTarget: findControlledTarget.toString(),
      captureToggleState: captureToggleState.toString(),
      captureControlledTargetState: captureControlledTargetState.toString(),
      captureInteractionState: captureInteractionState.toString(),
      captureStyleSnapshot: captureStyleSnapshot.toString(),
      hoverInteractionElement: hoverInteractionElement.toString(),
      focusInteractionElement: focusInteractionElement.toString(),
      blurInteractionElement: blurInteractionElement.toString(),
      readInteractionValue: readInteractionValue.toString(),
      writeInteractionValue: writeInteractionValue.toString(),
      readInteractionScroll: readInteractionScroll.toString(),
      writeInteractionScroll: writeInteractionScroll.toString(),
      clickInteractionElement: clickInteractionElement.toString(),
      cleanupInteractionMarkers: cleanupInteractionMarkers.toString(),
    };

    await page.evaluate((sources) => {
      const target = globalThis;
      if (!target.__webEmbeddingRuntimeHelpersInstalled) {
        target.__webEmbeddingRuntimeHelpersInstalled = {};
      }
      for (const [name, source] of Object.entries(sources || {})) {
        if (target.__webEmbeddingRuntimeHelpersInstalled[name] === true) {
          continue;
        }
        target[name] = (0, eval)(`(${source})`);
        (0, eval)(`var ${name} = globalThis[${JSON.stringify(name)}];`);
        target.__webEmbeddingRuntimeHelpersInstalled[name] = true;
      }
      return Object.keys(target.__webEmbeddingRuntimeHelpersInstalled).length;
    }, interactionRuntimeSources);

    const html = captureHtml ? await page.content() : null;
    const screenshotBytes = captureScreenshot ? await page.screenshot({ fullPage: true, type: "png" }) : null;
    const accessibilityTree = page.accessibility && typeof page.accessibility.snapshot === "function"
      ? await page.accessibility.snapshot({ interestingOnly: false }).catch(() => null)
      : null;
    const domSnapshotPayload = await page.evaluate(() => {
      function safeFrameDocument(element) {
        if (!element || !element.tagName || element.tagName.toLowerCase() !== "iframe") {
          return null;
        }
        try {
          const doc = element.contentDocument;
          return doc && doc.documentElement ? doc : null;
        } catch (error) {
          return "inaccessible-frame";
        }
      }
      function summarize(node, depth, maxDepth, maxChildren) {
        if (depth > maxDepth || !node) return null;
        if (node.nodeType === Node.TEXT_NODE) {
          const text = node.textContent.replace(/\s+/g, " ").trim();
          if (!text) return null;
          return { type: "text", text: text.slice(0, 120) };
        }
        if (node.nodeType !== Node.ELEMENT_NODE) return null;
        const element = node;
        const entry = {
          type: "element",
          tag: element.tagName.toLowerCase(),
          id: element.id || null,
          className: typeof element.className === "string" && element.className ? element.className.slice(0, 120) : null,
          role: element.getAttribute("role"),
          text: (element.innerText || "").replace(/\s+/g, " ").trim().slice(0, 120) || null,
          children: [],
        };
        if (entry.tag === "iframe") {
          entry.src = element.getAttribute("src") || null;
        }
        const children = Array.from(element.childNodes).slice(0, maxChildren);
        for (const child of children) {
          const summarized = summarize(child, depth + 1, maxDepth, maxChildren);
          if (summarized) {
            entry.children.push(summarized);
          }
        }
        if (element.shadowRoot) {
          const shadowChildren = Array.from(element.shadowRoot.childNodes).slice(0, maxChildren);
          const shadowSummary = [];
          for (const child of shadowChildren) {
            const summarized = summarize(child, depth + 1, maxDepth, maxChildren);
            if (summarized) {
              shadowSummary.push(summarized);
            }
          }
          if (shadowSummary.length) {
            entry.shadowRoot = {
              mode: element.shadowRoot.mode || "open",
              children: shadowSummary,
            };
          }
        }
        if (element.tagName && element.tagName.toLowerCase() === "iframe") {
          const frameDocument = safeFrameDocument(element);
          if (frameDocument === "inaccessible-frame") {
            entry.frameDocument = { type: "inaccessible-frame" };
          } else if (frameDocument && frameDocument.documentElement) {
            const frameSummary = summarize(frameDocument.documentElement, depth + 1, maxDepth, maxChildren);
            if (frameSummary) {
              entry.frameDocument = frameSummary;
            }
          }
        }
        return entry;
      }
      function collectStats(entry) {
        const stats = {
          nodeCount: 0,
          shadowRootCount: 0,
          frameDocumentCount: 0,
          inaccessibleFrameCount: 0,
          shadowHostTags: [],
          frameSources: [],
        };
        function walk(node) {
          if (!node || typeof node !== "object") return;
          stats.nodeCount += 1;
          if (node.shadowRoot && Array.isArray(node.shadowRoot.children) && node.shadowRoot.children.length) {
            stats.shadowRootCount += 1;
            if (node.tag && stats.shadowHostTags.length < 12 && !stats.shadowHostTags.includes(node.tag)) {
              stats.shadowHostTags.push(node.tag);
            }
            for (const child of node.shadowRoot.children) {
              walk(child);
            }
          }
          if (node.frameDocument && typeof node.frameDocument === "object") {
            if (node.frameDocument.type === "inaccessible-frame") {
              stats.inaccessibleFrameCount += 1;
            } else {
              stats.frameDocumentCount += 1;
              if (node.src && stats.frameSources.length < 12 && !stats.frameSources.includes(node.src)) {
                stats.frameSources.push(node.src);
              }
              walk(node.frameDocument);
            }
          }
          if (Array.isArray(node.children)) {
            for (const child of node.children) {
              walk(child);
            }
          }
        }
        walk(entry);
        return stats;
      }
      const documentSummary = summarize(document.documentElement, 0, 5, 12);
      return {
        document: documentSummary,
        stats: collectStats(documentSummary),
      };
    });
    const domSnapshot = domSnapshotPayload && typeof domSnapshotPayload === "object" ? domSnapshotPayload.document : domSnapshotPayload;
    const domSnapshotStats = domSnapshotPayload && typeof domSnapshotPayload === "object" && domSnapshotPayload.stats
      ? domSnapshotPayload.stats
      : { nodeCount: 0, shadowRootCount: 0, frameDocumentCount: 0, inaccessibleFrameCount: 0 };
    const styleSummary = await page.evaluate(() => {
      const normalizeValue = (value) => String(value ?? "").replace(/\s+/g, " ").trim().toLowerCase();
      const normalizeText = (value, maxLength = 160) => {
        if (!value) return null;
        const normalized = String(value).replace(/\s+/g, " ").trim();
        return normalized ? normalized.slice(0, maxLength) : null;
      };
      const normalizeFontFamily = (value) => {
        const normalized = normalizeValue(value);
        if (!normalized || normalized === "none") return normalized;
        return normalized
          .split(",")
          .map((part) => part.trim().replace(/^['"]|['"]$/g, ""))
          .filter(Boolean)
          .join(",");
      };
      const normalizePaintValue = (value) => {
        const normalized = normalizeValue(value);
        if (!normalized) return normalized;
        if (normalized === "none" || normalized === "normal") return normalized;
        return normalized.replace(/\s+/g, " ");
      };
      const normalizeCssUrl = (value) => {
        const normalized = normalizeValue(value);
        if (!normalized || normalized === "none") return normalized;
        return normalized.replace(/url\((['"]?)(.*?)\1\)/g, (_, __, rawUrl) => {
          const trimmed = String(rawUrl || "").trim();
          if (!trimmed) return "url()";
          try {
            const parsed = new URL(trimmed, document.baseURI);
            return `url(${parsed.pathname}${parsed.search || ""})`;
          } catch (error) {
            return `url(${trimmed.replace(/^https?:\/\/[^/]+/i, "")})`;
          }
        });
      };
      const normalizeColor = (value) => normalizeValue(value).replace(/,\s+/g, ",");
      const bucketDimension = (value) => {
        const numeric = Number(value);
        if (!Number.isFinite(numeric) || numeric <= 0) return "0";
        if (numeric >= 1200) return "viewport";
        if (numeric >= 720) return "xxl";
        if (numeric >= 420) return "xl";
        if (numeric >= 240) return "lg";
        if (numeric >= 120) return "md";
        if (numeric >= 48) return "sm";
        return "xs";
      };
      const bucketSpacing = (value) => {
        const text = normalizeValue(value);
        if (!text || text === "0" || text === "0px" || text === "0rem" || text === "0em" || text === "auto" || text === "normal" || text === "none") {
          return "0";
        }
        if (/^(clamp|calc|var)\(/.test(text) || /^(fit-content|min-content|max-content|stretch|inherit|initial|unset)$/.test(text)) {
          return "fluid";
        }
        const numeric = Number.parseFloat(text);
        if (!Number.isFinite(numeric)) {
          return text;
        }
        const scale = /(rem|em)\b/.test(text) ? numeric * 16 : numeric;
        if (scale <= 1) return "hairline";
        if (scale <= 4) return "xs";
        if (scale <= 8) return "sm";
        if (scale <= 16) return "md";
        if (scale <= 24) return "lg";
        if (scale <= 40) return "xl";
        return "xxl";
      };
      const textProfile = (value) => {
        const text = normalizeValue(value);
        if (!text) return "empty";
        if (text.length <= 8) return "short";
        if (text.length <= 32) return "medium";
        if (text.length <= 96) return "long";
        return "block";
      };
      const getControlKind = (element) => {
        const tag = element.tagName.toLowerCase();
        const role = String(element.getAttribute("role") || "").toLowerCase();
        const type = String(element.getAttribute("type") || "").toLowerCase();
        if (element.isContentEditable || role === "textbox" || role === "searchbox") return "text-entry";
        if (tag === "input" && ["text", "search", "url", "tel", "email", "password"].includes(type)) return "text-entry";
        if (tag === "textarea") return "text-entry";
        if (tag === "select" || role === "combobox") return "select";
        if (tag === "summary" || ["button", "menuitem", "menuitemcheckbox", "menuitemradio", "switch", "checkbox", "radio", "option", "treeitem"].includes(role) || ["checkbox", "radio", "button"].includes(type)) return "toggle";
        if (role === "tab") return "tab";
        if (role === "link" || tag === "a") return "link";
        if (role === "slider" || type === "range") return "slider";
        if (element.hasAttribute("aria-haspopup")) return "disclosure";
        return null;
      };
      const getControlLabel = (element) => {
        const labelledBy = String(element.getAttribute("aria-labelledby") || "")
          .split(/\s+/)
          .map((value) => value.trim())
          .filter(Boolean)
          .slice(0, 4);
        const labelParts = [];
        for (const id of labelledBy) {
          const labelNode = document.getElementById(id);
          if (labelNode) {
            const text = normalizeText(labelNode.innerText || labelNode.textContent || "", 120);
            if (text) {
              labelParts.push(text);
            }
          }
        }
        if (labelParts.length) {
          return labelParts.join(" ").trim();
        }
        if (element.id) {
          const directLabels = Array.from(document.querySelectorAll(`label[for="${CSS.escape(element.id)}"]`))
            .map((node) => normalizeText(node.innerText || node.textContent || "", 120))
            .filter(Boolean);
          if (directLabels.length) {
            return directLabels.join(" ").trim();
          }
        }
        const wrappingLabel = element.closest("label");
        if (wrappingLabel) {
          const text = normalizeText(wrappingLabel.innerText || wrappingLabel.textContent || "", 120);
          if (text) {
            return text;
          }
        }
        const fieldset = element.closest("fieldset");
        if (fieldset) {
          const legend = fieldset.querySelector("legend");
          if (legend) {
            const text = normalizeText(legend.innerText || legend.textContent || "", 120);
            if (text) {
              return text;
            }
          }
        }
        return (
          normalizeText(element.getAttribute("aria-label"), 120) ||
          normalizeText(element.getAttribute("title"), 120) ||
          normalizeText(element.getAttribute("placeholder"), 120) ||
          normalizeText(element.innerText || element.textContent || "", 120) ||
          normalizeText("value" in element ? element.value : "", 120) ||
          normalizeText(element.tagName.toLowerCase(), 80)
        );
      };
      const directText = (element) =>
        Array.from(element.childNodes || [])
          .filter((node) => node && node.nodeType === Node.TEXT_NODE)
          .map((node) => String(node.textContent || "").replace(/\s+/g, " ").trim())
          .filter(Boolean)
          .join(" ")
          .slice(0, 120);
      const signatureText = (element) => {
        const tag = element.tagName.toLowerCase();
        const role = String(element.getAttribute("role") || "").toLowerCase();
        const inlineText = normalizeText(directText(element), 120);
        if (inlineText) {
          return inlineText;
        }
        if (["a", "button", "input", "textarea", "select", "label", "summary"].includes(tag) || role) {
          return (
            normalizeText(element.getAttribute("aria-label"), 120) ||
            normalizeText(element.getAttribute("title"), 120) ||
            normalizeText(element.getAttribute("placeholder"), 120) ||
            normalizeText(element.innerText || element.textContent || "", 120)
          );
        }
        return null;
      };
      const regionSelectors = [
        { region: "banner", selector: 'header, [role="banner"]' },
        { region: "navigation", selector: 'nav, [role="navigation"]' },
        { region: "main", selector: 'main, [role="main"]' },
        { region: "complementary", selector: 'aside, [role="complementary"]' },
        { region: "contentinfo", selector: 'footer, [role="contentinfo"]' },
        { region: "dialog", selector: 'dialog, [role="dialog"], [aria-modal="true"]' },
        { region: "tabpanel", selector: '[role="tabpanel"]' },
        { region: "tablist", selector: '[role="tablist"]' },
        { region: "menu", selector: '[role="menu"]' },
        { region: "form", selector: 'form, [role="form"]' },
        { region: "article", selector: 'article, [role="article"]' },
        { region: "section", selector: 'section' },
      ];
      const controlSelectors = [
        'a[href]',
        'button',
        'input:not([type="hidden"])',
        'select',
        'textarea',
        'summary',
        '[role="button"]',
        '[role="link"]',
        '[role="tab"]',
        '[role="menuitem"]',
        '[role="menuitemcheckbox"]',
        '[role="menuitemradio"]',
        '[role="switch"]',
        '[role="checkbox"]',
        '[role="radio"]',
        '[role="combobox"]',
        '[role="slider"]',
        '[aria-controls]',
        '[aria-expanded]',
        '[aria-haspopup]',
        '[tabindex]:not([tabindex="-1"])',
      ];
      const mediaSelectors = ['img', 'picture', 'video', 'canvas', 'svg', 'iframe', 'embed', 'object'];
      const textSelectors = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li', 'dt', 'dd', 'blockquote', 'pre', 'code', 'figcaption', 'label', 'small', 'strong', 'em'];
      const containerSelectors = ['main > *', 'article > *', 'section > *', 'header > *', 'nav > *', 'aside > *', 'footer > *', 'dialog > *'];
      const seen = new Set();
      const entries = [];
      const sampleLimit = 120;

      const isVisible = (element) => {
        const rect = element.getBoundingClientRect();
        const style = window.getComputedStyle(element);
        return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden" && style.opacity !== "0";
      };

      const detectSemanticRegion = (element) => {
        const map = [
          { region: "banner", selector: 'header, [role="banner"]' },
          { region: "navigation", selector: 'nav, [role="navigation"]' },
          { region: "main", selector: 'main, [role="main"]' },
          { region: "complementary", selector: 'aside, [role="complementary"]' },
          { region: "contentinfo", selector: 'footer, [role="contentinfo"]' },
          { region: "dialog", selector: 'dialog, [role="dialog"], [aria-modal="true"]' },
          { region: "tabpanel", selector: '[role="tabpanel"]' },
          { region: "tablist", selector: '[role="tablist"]' },
          { region: "menu", selector: '[role="menu"]' },
          { region: "form", selector: 'form, [role="form"]' },
          { region: "article", selector: 'article, [role="article"]' },
          { region: "section", selector: 'section' },
        ];
        for (const item of map) {
          const root = element.closest(item.selector);
          if (root) {
            return {
              region: item.region,
              rootTag: root.tagName ? root.tagName.toLowerCase() : null,
              rootRole: root.getAttribute ? root.getAttribute("role") : null,
              rootLabel: normalizeText(root.getAttribute ? root.getAttribute("aria-label") : "", 120) || normalizeText(root.innerText || root.textContent || "", 80),
            };
          }
        }
        return { region: "content", rootTag: null, rootRole: null, rootLabel: null };
      };

      const describeSelectorPath = (element) => {
        const segments = [];
        let current = element;
        for (let depth = 0; current && depth < 3; depth += 1, current = current.parentElement) {
          if (!current.tagName) {
            continue;
          }
          segments.push(current.tagName.toLowerCase());
        }
        return segments.reverse().join(" > ") || null;
      };
      const isPlainControl = (element) => {
        const tag = element.tagName.toLowerCase();
        if (!["a", "button", "input", "select", "textarea", "summary"].includes(tag)) {
          return false;
        }
        return Boolean(getControlKind(element));
      };
      const isStructuralShell = (element) => {
        if (!element || !element.tagName) {
          return false;
        }
        const tag = element.tagName.toLowerCase();
        if (["body", "html"].includes(tag)) {
          return true;
        }
        if (["form", "fieldset", "main", "nav", "header", "footer", "aside", "article", "section", "dialog", "details", "ul", "ol", "li", "table", "thead", "tbody", "tfoot", "tr", "td", "th"].includes(tag)) {
          return true;
        }
        const role = String(element.getAttribute("role") || "").toLowerCase();
        if (["group", "search", "toolbar", "navigation", "main", "region", "dialog", "tabpanel", "tablist", "menu", "contentinfo", "banner", "complementary", "article", "form"].includes(role)) {
          return true;
        }
        if (tag === "label") {
          return Boolean(normalizeText(element.innerText || element.textContent || "", 80));
        }
        if (tag !== "div") {
          return false;
        }
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        const hasStructure = element.children.length > 0 || rect.width > 160 || rect.height > 80;
        const structuredDisplay = /block|flex|grid|inline-block|table|list-item|contents/.test(String(style.display || "").toLowerCase());
        return hasStructure && (structuredDisplay || Boolean(normalizeText(element.textContent || "", 80)));
      };
      const getStructuralShell = (element) => {
        let current = element ? element.parentElement : null;
        let fallback = null;
        for (let depth = 0; current && depth < 5; depth += 1, current = current.parentElement) {
          if (!current.tagName || !isVisible(current)) {
            continue;
          }
          if (isStructuralShell(current)) {
            return current;
          }
          if (!fallback && current.tagName.toLowerCase() === "div") {
            const rect = current.getBoundingClientRect();
            if (current.children.length > 0 || rect.width > 220 || rect.height > 120) {
              fallback = current;
            }
          }
        }
        return fallback;
      };

      const buildStyleSignature = (entry) => {
        const styles = entry.styles || {};
        const rect = entry.rect || {};
        return [
          normalizeValue(entry.tag),
          normalizeValue(entry.role),
          textProfile(entry.text),
          bucketDimension(rect.width),
          bucketDimension(rect.height),
          normalizeValue(styles.display),
          normalizeValue(styles.position),
          normalizeColor(styles.color),
          normalizeColor(styles.backgroundColor),
          normalizeValue(styles.fontFamily),
          normalizeValue(styles.fontSize),
          normalizeValue(styles.fontWeight),
          normalizeValue(styles.lineHeight),
          normalizeValue(styles.letterSpacing),
          normalizeValue(styles.textAlign),
          normalizeValue(styles.textTransform),
          normalizeValue(styles.borderRadius),
          normalizeColor(styles.borderColor),
          normalizeValue(styles.borderWidth),
          normalizeValue(styles.gap),
        ].join("|");
      };

      const addEntry = (element, sampleBucket, sampleReason) => {
        if (!element || !element.tagName || seen.has(element)) {
          return;
        }
        if (!isVisible(element)) {
          return;
        }
        seen.add(element);
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        const semantic = detectSemanticRegion(element);
        const labelText = getControlLabel(element);
        const controlKind = getControlKind(element);
        const text = signatureText(element) || normalizeText(directText(element), 160) || labelText || normalizeText(element.getAttribute("aria-label"), 120);
        const entry = {
          tag: element.tagName.toLowerCase(),
          id: element.id || null,
          className: typeof element.className === "string" && element.className ? element.className.slice(0, 120) : null,
          role: element.getAttribute("role"),
          text: text || null,
          labelText: labelText || null,
          controlKind,
          accessibilityRole: element.getAttribute("aria-roledescription") || element.getAttribute("role") || null,
          sampleBucket,
          sampleReason,
          semanticRegion: semantic.region,
          semanticRootTag: semantic.rootTag,
          semanticRootRole: semantic.rootRole,
          semanticRootLabel: semantic.rootLabel,
          semanticPath: describeSelectorPath(element),
          childCount: element.children ? element.children.length : 0,
          descendantCount: element.querySelectorAll ? Math.min(999, element.querySelectorAll("*").length) : 0,
          rect: {
            x: Math.round(rect.x),
            y: Math.round(rect.y),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
          },
          styles: {
            display: style.display,
            position: style.position,
            float: style.float,
            clear: style.clear,
            flexDirection: style.flexDirection,
            flexWrap: style.flexWrap,
            justifyContent: style.justifyContent,
            alignItems: style.alignItems,
            alignContent: style.alignContent,
            placeContent: style.placeContent,
            placeItems: style.placeItems,
            gap: style.gap,
            rowGap: style.rowGap,
            columnGap: style.columnGap,
            gridTemplateColumns: style.gridTemplateColumns,
            gridTemplateRows: style.gridTemplateRows,
            gridAutoFlow: style.gridAutoFlow,
            gridAutoColumns: style.gridAutoColumns,
            gridAutoRows: style.gridAutoRows,
            overflow: style.overflow,
            overflowX: style.overflowX,
            overflowY: style.overflowY,
            boxSizing: style.boxSizing,
            width: style.width,
            height: style.height,
            minWidth: style.minWidth,
            minHeight: style.minHeight,
            maxWidth: style.maxWidth,
            maxHeight: style.maxHeight,
            marginTop: style.marginTop,
            marginRight: style.marginRight,
            marginBottom: style.marginBottom,
            marginLeft: style.marginLeft,
            paddingTop: style.paddingTop,
            paddingRight: style.paddingRight,
            paddingBottom: style.paddingBottom,
            paddingLeft: style.paddingLeft,
            color: style.color,
            backgroundColor: style.backgroundColor,
            backgroundImage: style.backgroundImage,
            backgroundSize: style.backgroundSize,
            backgroundPosition: style.backgroundPosition,
            backgroundRepeat: style.backgroundRepeat,
            backgroundClip: style.backgroundClip,
            backgroundOrigin: style.backgroundOrigin,
            objectFit: style.objectFit,
            objectPosition: style.objectPosition,
            borderStyle: style.borderStyle,
            borderRadius: style.borderRadius,
            borderColor: style.borderColor,
            borderWidth: style.borderWidth,
            boxShadow: style.boxShadow,
            backdropFilter: style.backdropFilter,
            filter: style.filter,
            mixBlendMode: style.mixBlendMode,
            opacity: style.opacity,
            transform: style.transform,
            transformOrigin: style.transformOrigin,
            transitionProperty: style.transitionProperty,
            transitionDuration: style.transitionDuration,
            transitionTimingFunction: style.transitionTimingFunction,
            fontFamily: style.fontFamily,
            fontSize: style.fontSize,
            fontWeight: style.fontWeight,
            fontStyle: style.fontStyle,
            lineHeight: style.lineHeight,
            letterSpacing: style.letterSpacing,
            textAlign: style.textAlign,
            textTransform: style.textTransform,
            whiteSpace: style.whiteSpace,
            wordBreak: style.wordBreak,
            textOverflow: style.textOverflow,
            webkitLineClamp: style.webkitLineClamp,
            willChange: style.willChange,
            isolation: style.isolation,
            contain: style.contain,
            zIndex: style.zIndex,
            cursor: style.cursor,
          },
        };
        entry.accessibleName = entry.labelText || entry.text || null;
        entry.layoutSignature = [
          normalizeValue(entry.semanticRegion),
          normalizeValue(entry.semanticPath),
          normalizeValue(entry.labelText),
          normalizeValue(entry.controlKind),
          normalizeValue(entry.styles.display),
          normalizeValue(entry.styles.position),
          normalizeValue(entry.styles.flexDirection),
          normalizeValue(entry.styles.flexWrap),
          normalizeValue(entry.styles.justifyContent),
          normalizeValue(entry.styles.alignItems),
          normalizeValue(entry.styles.placeContent),
          normalizeValue(entry.styles.placeItems),
          bucketSpacing(entry.styles.gap),
          bucketSpacing(entry.styles.rowGap),
          bucketSpacing(entry.styles.columnGap),
          normalizeValue(entry.styles.gridTemplateColumns),
          normalizeValue(entry.styles.gridTemplateRows),
          normalizeValue(entry.styles.gridAutoFlow),
          normalizeValue(entry.styles.overflowX),
          normalizeValue(entry.styles.overflowY),
          bucketDimension(rect.width),
          bucketDimension(rect.height),
          normalizeValue(entry.styles.width),
          normalizeValue(entry.styles.height),
          normalizeValue(entry.styles.minWidth),
          normalizeValue(entry.styles.minHeight),
          normalizeValue(entry.styles.maxWidth),
          normalizeValue(entry.styles.maxHeight),
          bucketSpacing(entry.styles.paddingTop),
          bucketSpacing(entry.styles.paddingRight),
          bucketSpacing(entry.styles.paddingBottom),
          bucketSpacing(entry.styles.paddingLeft),
          bucketSpacing(entry.styles.marginTop),
          bucketSpacing(entry.styles.marginRight),
          bucketSpacing(entry.styles.marginBottom),
          bucketSpacing(entry.styles.marginLeft),
        ].join("|");
        entry.paintSignature = [
          normalizeValue(entry.styles.color),
          normalizeColor(entry.styles.backgroundColor),
          normalizeCssUrl(entry.styles.backgroundImage),
          normalizePaintValue(entry.styles.backgroundSize),
          normalizePaintValue(entry.styles.backgroundPosition),
          normalizePaintValue(entry.styles.backgroundRepeat),
          normalizePaintValue(entry.styles.backgroundClip),
          normalizePaintValue(entry.styles.backgroundOrigin),
          normalizeValue(entry.styles.objectFit),
          normalizeValue(entry.styles.objectPosition),
          normalizeValue(entry.styles.borderStyle),
          normalizeValue(entry.styles.borderRadius),
          normalizeColor(entry.styles.borderColor),
          normalizeValue(entry.styles.borderWidth),
          normalizePaintValue(entry.styles.boxShadow),
          normalizePaintValue(entry.styles.backdropFilter),
          normalizePaintValue(entry.styles.filter),
          normalizeValue(entry.styles.mixBlendMode),
          normalizeValue(entry.styles.opacity),
          normalizeFontFamily(entry.styles.fontFamily),
          normalizeValue(entry.styles.fontSize),
          normalizeValue(entry.styles.fontWeight),
          normalizeValue(entry.styles.fontStyle),
          normalizeValue(entry.styles.lineHeight),
          normalizeValue(entry.styles.letterSpacing),
          normalizeValue(entry.styles.textAlign),
          normalizeValue(entry.styles.textTransform),
        ].join("|");
        entry.styleSignature = buildStyleSignature(entry);
        entries.push(entry);
        return entry;
      };

      const pushUnique = (element, sampleBucket, sampleReason) => {
        if (entries.length >= sampleLimit) {
          return;
        }
        addEntry(element, sampleBucket, sampleReason);
      };

      const sortByVisualPriority = (nodes) =>
        nodes
          .filter(Boolean)
          .filter(isVisible)
          .sort((left, right) => {
            const leftRect = left.getBoundingClientRect();
            const rightRect = right.getBoundingClientRect();
            const leftArea = leftRect.width * leftRect.height;
            const rightArea = rightRect.width * rightRect.height;
            return rightArea - leftArea || leftRect.top - rightRect.top || leftRect.left - rightRect.left;
          });

      const sampleFromSelectors = (sampleBucket, sampleReason, selectors, limit = 12) => {
        const bucketNodes = [];
        for (const selector of selectors) {
          if (bucketNodes.length >= limit) {
            break;
          }
          const matches = Array.from(document.querySelectorAll(selector)).slice(0, limit);
          for (const node of matches) {
            bucketNodes.push(node);
          }
        }
        for (const node of sortByVisualPriority(bucketNodes).slice(0, limit)) {
          pushUnique(node, sampleBucket, sampleReason);
        }
      };

      const sampleControlShells = () => {
        const controlNodes = sortByVisualPriority(Array.from(document.querySelectorAll(controlSelectors.join(","))).filter(isVisible));
        for (const control of controlNodes.slice(0, 14)) {
          const shell = getStructuralShell(control);
          if (shell) {
            pushUnique(shell, "control-shell", "Structural wrapper around interactive control");
          }
        }
        for (const control of controlNodes.slice(0, 6)) {
          pushUnique(control, "control-leaf", "Representative leaf control for interaction state fidelity");
        }
      };

      const sampleScrollableContainers = () => {
        const nodes = Array.from(document.querySelectorAll("body *")).filter((element) => {
          const style = window.getComputedStyle(element);
          const overflowX = String(style.overflowX || style.overflow || "").toLowerCase();
          const overflowY = String(style.overflowY || style.overflow || "").toLowerCase();
          const horizontal = /auto|scroll|overlay/.test(overflowX) && element.scrollWidth > element.clientWidth + 4;
          const vertical = /auto|scroll|overlay/.test(overflowY) && element.scrollHeight > element.clientHeight + 4;
          return isVisible(element) && (horizontal || vertical);
        });
        for (const node of sortByVisualPriority(nodes).slice(0, 10)) {
          pushUnique(node, "scroll", "Scrollable container with overflow-based interaction potential");
        }
      };

      const sampleLayoutShells = () => {
        const nodes = Array.from(document.querySelectorAll("body *")).filter((element) => {
          const style = window.getComputedStyle(element);
          const rect = element.getBoundingClientRect();
          const display = String(style.display || "").toLowerCase();
          const text = normalizeText(element.textContent || "", 80);
          const isInteractiveLeaf = isPlainControl(element) && element.children.length === 0 && rect.width < 260 && rect.height < 120;
          const significantLayout =
            /block|flex|grid|inline-block|table|list-item/.test(display) ||
            element.children.length > 1 ||
            rect.width > 240 ||
            rect.height > 120;
          const paintHeavy =
            style.backgroundImage !== "none" ||
            style.boxShadow !== "none" ||
            style.backdropFilter !== "none" ||
            style.borderRadius !== "0px" ||
            style.borderWidth !== "0px" ||
            style.position !== "static";
          return isVisible(element) && !isInteractiveLeaf && (significantLayout || paintHeavy || text);
        });
        for (const node of sortByVisualPriority(nodes).slice(0, 24)) {
          pushUnique(node, "layout", "Large layout shell or paint-heavy container");
        }
      };

      sampleFromSelectors("landmark", "Semantic landmark or region root", regionSelectors.map((item) => item.selector), 18);
      sampleFromSelectors("container", "First-level container within a semantic region", containerSelectors, 18);
      sampleLayoutShells();
      sampleControlShells();
      sampleFromSelectors("media", "Media or embedded surface", mediaSelectors, 14);
      sampleFromSelectors("text", "Text-bearing content block", textSelectors, 22);
      sampleScrollableContainers();
      sampleFromSelectors("controls", "Interactive control surface", controlSelectors, 6);

      const fallbackNodes = Array.from(document.querySelectorAll("body *")).filter((element) => {
        if (!isVisible(element)) {
          return false;
        }
        const text = normalizeText(element.textContent || "", 80);
        return Boolean(text || element.tagName === "IMG" || element.tagName === "VIDEO" || element.tagName === "CANVAS");
      });
      for (const node of sortByVisualPriority(fallbackNodes)) {
        if (entries.length >= sampleLimit) {
          break;
        }
        pushUnique(node, "fallback", "Representative visible node not yet covered by semantic buckets");
      }

      return entries.slice(0, sampleLimit);
    });
    const assetInventory = await page.evaluate(() => {
      const collectDocumentRoots = (rootDocument, maxFrames = 12) => {
        const roots = [{ kind: "document", document: rootDocument, frameSrc: null }];
        const queue = [rootDocument];
        const seen = new Set([rootDocument]);
        while (queue.length && roots.length < maxFrames + 1) {
          const current = queue.shift();
          const frames = Array.from(current.querySelectorAll("iframe")).slice(0, maxFrames);
          for (const frame of frames) {
            let frameDoc = null;
            try {
              frameDoc = frame.contentDocument;
            } catch (error) {
              frameDoc = null;
            }
            if (!frameDoc || !frameDoc.documentElement || seen.has(frameDoc)) continue;
            seen.add(frameDoc);
            roots.push({
              kind: "frame-document",
              document: frameDoc,
              frameSrc: frame.getAttribute("src") || frame.src || null,
            });
            queue.push(frameDoc);
            if (roots.length >= maxFrames + 1) break;
          }
        }
        return roots;
      };
      const normalize = (value) => {
        if (!value) return null;
        try {
          return new URL(value, document.baseURI).href;
        } catch (error) {
          return value;
        }
      };
      const uniq = (values) => Array.from(new Set(values.filter(Boolean)));
      const roots = collectDocumentRoots(document, 12);
      const backgroundImages = uniq(
        roots.flatMap(({ document: doc }) => Array.from(doc.querySelectorAll("body *")).slice(0, 200))
          .flatMap((node) => {
            const style = window.getComputedStyle(node);
            const raw = String(style.backgroundImage || "");
            if (!raw || raw === "none") return [];
            return Array.from(raw.matchAll(/url\\((['\"]?)(.*?)\\1\\)/g)).map((match) => normalize(match[2]));
          })
      );
      const preloadLinks = roots.flatMap(({ document: doc, frameSrc }) =>
        Array.from(doc.querySelectorAll('link[rel="preload"], link[rel="prefetch"], link[rel="modulepreload"]')).map((node) => ({
          rel: node.getAttribute("rel"),
          href: normalize(node.getAttribute("href") || node.href),
          as: node.getAttribute("as"),
          crossOrigin: node.getAttribute("crossorigin"),
          frameSrc,
        }))
      );
      const fontFaces = [];
      for (const { document: doc, frameSrc } of roots) {
        for (const sheet of Array.from(doc.styleSheets || []).slice(0, 24)) {
          let rules = [];
          try {
            rules = Array.from(sheet.cssRules || []);
          } catch (error) {
            continue;
          }
          for (const rule of rules) {
            if (rule && rule.type === CSSRule.FONT_FACE_RULE) {
              const style = rule.style || {};
              fontFaces.push({
                family: style.getPropertyValue("font-family") || null,
                src: style.getPropertyValue("src") || null,
                weight: style.getPropertyValue("font-weight") || null,
                style: style.getPropertyValue("font-style") || null,
                display: style.getPropertyValue("font-display") || null,
                frameSrc,
              });
            }
          }
        }
      }
      return {
        images: uniq(roots.flatMap(({ document: doc }) => Array.from(doc.images, (node) => normalize(node.currentSrc || node.src)))),
        scripts: uniq(roots.flatMap(({ document: doc }) => Array.from(doc.scripts, (node) => normalize(node.src)))),
        stylesheets: uniq(roots.flatMap(({ document: doc }) => Array.from(doc.querySelectorAll('link[rel="stylesheet"]'), (node) => normalize(node.href)))),
        videos: uniq(roots.flatMap(({ document: doc }) => Array.from(doc.querySelectorAll("video"), (node) => normalize(node.currentSrc || node.src)))),
        audios: uniq(roots.flatMap(({ document: doc }) => Array.from(doc.querySelectorAll("audio"), (node) => normalize(node.currentSrc || node.src)))),
        iframes: uniq(roots.flatMap(({ document: doc }) => Array.from(doc.querySelectorAll("iframe"), (node) => normalize(node.src)))),
        backgroundImages,
        preloadLinks: preloadLinks.filter((entry) => entry.href),
        fontFaces,
        roots: roots.map((entry) => ({
          kind: entry.kind,
          frameSrc: entry.frameSrc,
        })),
      };
    });
    const cssAnalysis = await page.evaluate(() => {
      const normalize = (value) => {
        if (!value) return null;
        try {
          return new URL(value, document.baseURI).href;
        } catch (error) {
          return value;
        }
      };
      const trimText = (value, maxLength = 240) => {
        if (!value) return "";
        const normalized = String(value).replace(/\s+/g, " ").trim();
        return normalized.length > maxLength ? normalized.slice(0, maxLength) : normalized;
      };
      const styleSheets = Array.from(document.styleSheets || []).slice(0, 24).map((sheet, index) => {
        const ownerNode = sheet.ownerNode;
        const href = normalize(sheet.href);
        const ownerTag = ownerNode && ownerNode.tagName ? ownerNode.tagName.toLowerCase() : null;
        const mediaText = sheet.media && typeof sheet.media.mediaText === "string" ? sheet.media.mediaText : null;
        const payload = {
          index,
          href,
          ownerTag,
          disabled: Boolean(sheet.disabled),
          media: mediaText,
          accessible: false,
          ruleCount: null,
          sampleRules: [],
          sampleSelectors: [],
          crossOriginRestricted: false,
        };
        try {
          const rules = Array.from(sheet.cssRules || []);
          payload.accessible = true;
          payload.ruleCount = rules.length;
          payload.sampleRules = rules.slice(0, 8).map((rule) => trimText(rule.cssText, 220));
          payload.sampleSelectors = rules
            .map((rule) => trimText(rule.selectorText || rule.conditionText || rule.name || "", 120))
            .filter(Boolean)
            .slice(0, 8);
        } catch (error) {
          payload.crossOriginRestricted = true;
          payload.error = error && error.message ? String(error.message) : "Unable to read cssRules";
        }
        return payload;
      });
      const inlineStyleBlocks = Array.from(document.querySelectorAll("style")).slice(0, 12).map((node, index) => ({
        index,
        textLength: (node.textContent || "").length,
        textSample: trimText(node.textContent || "", 320),
        media: node.getAttribute("media"),
        nonce: node.getAttribute("nonce") ? "present" : null,
      }));
      const styleAttributeNodes = Array.from(document.querySelectorAll("[style]")).slice(0, 20).map((element, index) => ({
        index,
        tag: element.tagName.toLowerCase(),
        id: element.id || null,
        className: typeof element.className === "string" && element.className ? trimText(element.className, 120) : null,
        style: trimText(element.getAttribute("style") || "", 200),
      }));
      const preloadLinks = Array.from(
        document.querySelectorAll('link[rel="preload"], link[rel="prefetch"], link[rel="modulepreload"]')
      ).slice(0, 20).map((node, index) => ({
        index,
        rel: node.getAttribute("rel"),
        href: normalize(node.getAttribute("href") || node.href),
        as: node.getAttribute("as"),
        crossOrigin: node.getAttribute("crossorigin"),
      }));
      const fontFaceRules = [];
      for (const sheet of styleSheets) {
        if (!sheet.accessible) continue;
        for (const ruleText of sheet.sampleRules || []) {
          if (/^@font-face/i.test(ruleText)) {
            fontFaceRules.push(trimText(ruleText, 220));
          }
        }
      }
      const rootStyle = window.getComputedStyle(document.documentElement);
      const bodyStyle = window.getComputedStyle(document.body || document.documentElement);
      return {
        stylesheetCount: styleSheets.length,
        accessibleStylesheetCount: styleSheets.filter((sheet) => sheet.accessible).length,
        inlineStyleTagCount: inlineStyleBlocks.length,
        styleAttributeCount: document.querySelectorAll("[style]").length,
        linkedStylesheets: styleSheets,
        inlineStyleBlocks,
        styleAttributeSample: styleAttributeNodes,
        preloadLinkSample: preloadLinks,
        fontFaceSample: fontFaceRules.slice(0, 12),
        rootComputedStyle: {
          color: rootStyle.color,
          backgroundColor: rootStyle.backgroundColor,
          fontFamily: rootStyle.fontFamily,
          fontSize: rootStyle.fontSize,
        },
        bodyComputedStyle: {
          color: bodyStyle.color,
          backgroundColor: bodyStyle.backgroundColor,
          fontFamily: bodyStyle.fontFamily,
          fontSize: bodyStyle.fontSize,
        },
      };
    });
    const pageMetrics = await page.evaluate(() => ({
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
      scrollHeight: Math.max(
        document.documentElement ? document.documentElement.scrollHeight : 0,
        document.body ? document.body.scrollHeight : 0
      ),
      scrollWidth: Math.max(
        document.documentElement ? document.documentElement.scrollWidth : 0,
        document.body ? document.body.scrollWidth : 0
      ),
      initialScrollY: Math.round(window.scrollY || window.pageYOffset || 0),
      initialScrollX: Math.round(window.scrollX || window.pageXOffset || 0),
    }));
    const interactiveCandidates = await page.evaluate(() => {
      const collectCandidateRoots = (rootDocument = document, maxFrameDocuments = 12) => {
      const roots = [{
        root: rootDocument,
        kind: "document",
        frameSrc: null,
        frameUrl: rootDocument.location && rootDocument.location.href ? rootDocument.location.href : null,
        shadowHostTag: null,
        surfaceIndex: 0,
        rootPath: ["document"],
      }];
        const seenDocuments = new Set([rootDocument]);
        const seenShadows = new Set();
      const queue = [{
        root: rootDocument,
        kind: "document",
        frameSrc: null,
        frameUrl: rootDocument.location && rootDocument.location.href ? rootDocument.location.href : null,
        shadowHostTag: null,
        surfaceIndex: 0,
        rootPath: ["document"],
      }];
        while (queue.length && roots.length < maxFrameDocuments + 64) {
          const current = queue.shift();
          const scope = current.root;
          const elements = Array.from(scope.querySelectorAll ? scope.querySelectorAll("*") : []).slice(0, 400);
          for (const element of elements) {
            if (element.shadowRoot && !seenShadows.has(element.shadowRoot)) {
              seenShadows.add(element.shadowRoot);
              const shadowEntry = {
                root: element.shadowRoot,
                kind: "shadow-root",
                frameSrc: current.frameSrc || null,
            frameUrl: current.frameUrl || null,
            shadowHostTag: element.tagName ? element.tagName.toLowerCase() : null,
            surfaceIndex: roots.length,
            rootPath: (current.rootPath || ["document"]).concat([`shadow-root:${element.tagName ? element.tagName.toLowerCase() : "unknown"}`]),
          };
              roots.push(shadowEntry);
              queue.push(shadowEntry);
            }
            if (element.tagName && element.tagName.toLowerCase() === "iframe") {
              let frameDoc = null;
              try {
                frameDoc = element.contentDocument;
              } catch (error) {
                frameDoc = null;
              }
              if (frameDoc && frameDoc.documentElement && !seenDocuments.has(frameDoc) && roots.length < maxFrameDocuments + 64) {
                seenDocuments.add(frameDoc);
            const frameEntry = {
              root: frameDoc,
              kind: "frame-document",
              frameSrc: element.getAttribute("src") || element.src || null,
              frameUrl: frameDoc.location && frameDoc.location.href ? frameDoc.location.href : null,
              shadowHostTag: null,
              surfaceIndex: roots.length,
              rootPath: (current.rootPath || ["document"]).concat([`frame-document:${frameDoc.location && frameDoc.location.href ? frameDoc.location.href : (element.getAttribute("src") || element.src || "unknown")}`]),
            };
                roots.push(frameEntry);
                queue.push(frameEntry);
              }
            }
          }
        }
        return roots;
      };
      const normalizeText = (value, maxLength = 160) => {
        if (!value) return null;
        const normalized = String(value).replace(/\s+/g, " ").trim();
        return normalized ? normalized.slice(0, maxLength) : null;
      };
      const normalizeClassName = (value) => String(value || "").replace(/\s+/g, " ").trim().slice(0, 120) || null;
      const resolveLabelFromReferences = (element) => {
        const labelledBy = String(element.getAttribute("aria-labelledby") || "")
          .split(/\s+/)
          .map((value) => value.trim())
          .filter(Boolean)
          .slice(0, 4);
        const parts = [];
        for (const id of labelledBy) {
          const labelNode = document.getElementById(id);
          if (labelNode) {
            const text = normalizeText(labelNode.innerText || labelNode.textContent || "", 80);
            if (text) parts.push(text);
          }
        }
        return parts.length ? parts.join(" ").trim() : null;
      };
      const resolveAssociatedLabel = (element) => {
        const id = element.getAttribute("id");
        if (id) {
          const directLabels = Array.from(document.querySelectorAll(`label[for="${CSS.escape(id)}"]`))
            .map((node) => normalizeText(node.innerText || node.textContent || "", 80))
            .filter(Boolean);
          if (directLabels.length) return directLabels.join(" ").trim();
        }
        const wrappingLabel = element.closest("label");
        return wrappingLabel ? normalizeText(wrappingLabel.innerText || wrappingLabel.textContent || "", 80) : null;
      };
      const compactLabelText = (element, maxLength = 80) => {
        if (!element || !element.tagName) return null;
        const tag = element.tagName.toLowerCase();
        const role = String(element.getAttribute("role") || "").toLowerCase();
        const direct = Array.from(element.childNodes || [])
          .filter((node) => node && node.nodeType === Node.TEXT_NODE)
          .map((node) => String(node.textContent || "").replace(/\s+/g, " ").trim())
          .filter(Boolean)
          .join(" ");
        const directText = normalizeText(direct, maxLength);
        if (directText) return directText;
        const fullText = normalizeText(element.innerText || element.textContent || "", maxLength);
        if (!fullText) return null;
        const words = fullText.split(/\s+/).filter(Boolean);
        if (fullText.length <= 40 || words.length <= 6) return fullText;
        if (
          ["a", "button", "summary", "label", "option"].includes(tag) ||
          ["button", "link", "tab", "menuitem", "menuitemcheckbox", "menuitemradio", "switch", "checkbox", "radio"].includes(role)
        ) {
          return fullText;
        }
        return null;
      };
      const getInteractionLabel = (element) => (
        normalizeText(element.getAttribute("aria-label"), 80) ||
        resolveLabelFromReferences(element) ||
        resolveAssociatedLabel(element) ||
        normalizeText(element.getAttribute("title"), 80) ||
        normalizeText(element.getAttribute("alt"), 80) ||
        (
          element.tagName.toLowerCase() === "input" &&
          ["submit", "button", "reset"].includes(String(element.getAttribute("type") || "").toLowerCase())
            ? normalizeText("value" in element ? element.value : "", 80)
            : null
        ) ||
        normalizeText(element.getAttribute("placeholder"), 80) ||
        compactLabelText(element, 80) ||
        normalizeText("value" in element ? element.value : "", 80) ||
        normalizeText(element.tagName.toLowerCase(), 80)
      );
      const getInteractionKind = (element) => {
        const tag = element.tagName.toLowerCase();
        const role = String(element.getAttribute("role") || "").toLowerCase();
        const type = String(element.getAttribute("type") || "").toLowerCase();
        if (element.isContentEditable || role === "textbox" || role === "searchbox") return "text-entry";
        if (tag === "input" && ["text", "search", "url", "tel", "email", "password"].includes(type)) return "text-entry";
        if (tag === "textarea") return "text-entry";
        if (tag === "select" || role === "combobox") return "select";
        if (tag === "summary" || ["button", "menuitem", "menuitemcheckbox", "menuitemradio", "switch"].includes(role)) return "toggle";
        if (role === "tab") return "tab";
        if (["checkbox", "radio"].includes(role) || ["checkbox", "radio"].includes(type)) return "checkable";
        if (role === "link" || tag === "a") return "link";
        if (role === "slider" || type === "range") return "slider";
        if (element.hasAttribute("aria-haspopup")) return "disclosure";
        return "action";
      };
      const isFocusableElement = (element, style) => {
        const tag = element.tagName.toLowerCase();
        const role = String(element.getAttribute("role") || "").toLowerCase();
        const type = String(element.getAttribute("type") || "").toLowerCase();
        if (element.isContentEditable) return true;
        if (["button", "select", "textarea", "summary"].includes(tag)) return true;
        if (tag === "input" && type !== "hidden") return true;
        if (tag === "a" && element.hasAttribute("href")) return true;
        if (role && ["button", "link", "textbox", "searchbox", "tab", "menuitem", "menuitemcheckbox", "menuitemradio", "switch", "checkbox", "radio", "combobox", "slider", "option", "treeitem"].includes(role)) return true;
        if (typeof element.tabIndex === "number" && element.tabIndex >= 0) return true;
        return Boolean(style && style.cursor === "pointer");
      };
      const styleSnapshotFromComputed = (style) => ({
        display: style.display,
        position: style.position,
        top: style.top,
        right: style.right,
        bottom: style.bottom,
        left: style.left,
        zIndex: style.zIndex,
        color: style.color,
        backgroundColor: style.backgroundColor,
        borderColor: style.borderColor,
        borderWidth: style.borderWidth,
        borderRadius: style.borderRadius,
        boxShadow: style.boxShadow,
        opacity: style.opacity,
        transform: style.transform,
        filter: style.filter,
        textDecoration: style.textDecoration,
        outline: style.outline,
        outlineOffset: style.outlineOffset,
        cursor: style.cursor,
        fontFamily: style.fontFamily,
        fontSize: style.fontSize,
        fontWeight: style.fontWeight,
        lineHeight: style.lineHeight,
        letterSpacing: style.letterSpacing,
        textTransform: style.textTransform,
        overflow: style.overflow,
        overflowX: style.overflowX,
        overflowY: style.overflowY,
        scrollSnapType: style.scrollSnapType,
        scrollSnapAlign: style.scrollSnapAlign,
        scrollSnapStop: style.scrollSnapStop,
        paddingTop: style.paddingTop,
        paddingRight: style.paddingRight,
        paddingBottom: style.paddingBottom,
        paddingLeft: style.paddingLeft,
        marginTop: style.marginTop,
        marginRight: style.marginRight,
        marginBottom: style.marginBottom,
        marginLeft: style.marginLeft,
      });
      const describeInteractionTarget = (element, style) => {
        const rect = element.getBoundingClientRect();
        return {
          tag: element.tagName.toLowerCase(),
          role: element.getAttribute("role"),
          kind: getInteractionKind(element),
          label: getInteractionLabel(element),
          id: element.id || null,
          className: normalizeClassName(element.className),
          name: element.getAttribute("name"),
          title: element.getAttribute("title"),
          placeholder: element.getAttribute("placeholder"),
          type: element.getAttribute("type"),
          inputMode: element.getAttribute("inputmode"),
          autocomplete: element.getAttribute("autocomplete"),
          ariaLabel: element.getAttribute("aria-label"),
          ariaDescription: element.getAttribute("aria-description"),
          ariaCurrent: element.getAttribute("aria-current"),
          ariaControls: element.getAttribute("aria-controls"),
          ariaExpanded: element.getAttribute("aria-expanded"),
          ariaSelected: element.getAttribute("aria-selected"),
          ariaPressed: element.getAttribute("aria-pressed"),
          ariaHasPopup: element.getAttribute("aria-haspopup"),
          ariaRoleDescription: element.getAttribute("aria-roledescription"),
          ariaLive: element.getAttribute("aria-live"),
          focusable: isFocusableElement(element, style),
          inputCapable: getInteractionKind(element) === "text-entry",
          rect: {
            x: Math.round(rect.x),
            y: Math.round(rect.y),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
          },
        };
      };
      const selector = [
        'a[href]',
        'button',
        'input:not([type="hidden"])',
        'select',
        'textarea',
        'summary',
        '[role="button"]',
        '[role="link"]',
        '[role="tab"]',
        '[role="menuitem"]',
        '[role="menuitemcheckbox"]',
        '[role="menuitemradio"]',
        '[role="switch"]',
        '[role="checkbox"]',
        '[role="radio"]',
        '[role="combobox"]',
        '[role="option"]',
        '[role="treeitem"]',
        '[role="textbox"]',
        '[role="searchbox"]',
        '[role="slider"]',
        '[onclick]',
        '[aria-haspopup]',
        '[aria-expanded]',
        '[contenteditable="true"]',
        '[tabindex]:not([tabindex="-1"])'
      ].join(',');
      const surfaceSelector = [
        'dialog[open]',
        '[role="dialog"]',
        '[role="alertdialog"]',
        '[aria-modal="true"]',
        '[popover]:not([popover="manual"])',
        '[role="tablist"]',
        '[role="tabpanel"]',
        '[role="carousel"]',
        '[aria-roledescription*="carousel" i]',
        '[aria-roledescription*="slider" i]',
        '[aria-roledescription*="slideshow" i]',
      ].join(',');
      const isVisibleCandidate = (element) => {
        const ignoredRoot = element.closest('[data-web-embedding-ignore-interactions="true"], [inert], [aria-hidden="true"]');
        if (ignoredRoot) {
          return false;
        }
        const rect = element.getBoundingClientRect();
        const view = element.ownerDocument && element.ownerDocument.defaultView ? element.ownerDocument.defaultView : window;
        const style = view.getComputedStyle(element);
        return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && style.pointerEvents !== 'none';
      };
      const classifySurfaceKinds = (element, style) => {
        const role = String(element.getAttribute("role") || "").toLowerCase();
        const roledescription = String(element.getAttribute("aria-roledescription") || "").toLowerCase();
        const classText = normalizeClassName(element.className) || "";
        const position = String(style.position || "").toLowerCase();
        const overflowX = String(style.overflowX || style.overflow || "").toLowerCase();
        const overflowY = String(style.overflowY || style.overflow || "").toLowerCase();
        const scrollableX = /auto|scroll|overlay/.test(overflowX) && element.scrollWidth > element.clientWidth + 4;
        const scrollableY = /auto|scroll|overlay/.test(overflowY) && element.scrollHeight > element.clientHeight + 4;
        const kinds = [];
        if (element.tagName.toLowerCase() === 'dialog' || role === 'dialog' || role === 'alertdialog' || element.getAttribute('aria-modal') === 'true') {
          kinds.push('modal');
        }
        if (role === 'tablist' || role === 'tabpanel' || role === 'tab' || /(^|[\s_-])(tablist|tabpanel|tabs)([\s_-]|$)/.test(classText)) {
          kinds.push('tab');
        }
        if (role === 'carousel' || roledescription.includes('carousel') || roledescription.includes('slider') || roledescription.includes('slideshow') || roledescription.includes('gallery') || /(^|[\s_-])(carousel|slider|swiper|slick|splide|embla|glide)([\s_-]|$)/.test(classText)) {
          kinds.push('carousel');
        }
        if (scrollableX || scrollableY) {
          kinds.push('scroll');
        }
        if (position === 'sticky' || position === 'fixed') {
          kinds.push('sticky');
        }
        return {
          kinds,
          scrollableX,
          scrollableY,
          scrollableAxis: scrollableX && scrollableY ? 'both' : scrollableY ? 'y' : scrollableX ? 'x' : null,
          position,
          zIndex: style.zIndex,
          overflowX: style.overflowX,
          overflowY: style.overflowY,
          scrollSnapType: style.scrollSnapType,
          scrollSnapAlign: style.scrollSnapAlign,
          scrollSnapStop: style.scrollSnapStop,
        };
      };
      const seen = new Set();
      const nodes = [];
      const rootContext = new WeakMap();
      const pushNode = (element) => {
        if (!element || seen.has(element) || !isVisibleCandidate(element)) {
          return;
        }
        seen.add(element);
        nodes.push(element);
      };
      const roots = collectCandidateRoots(document, 12);
      for (const entry of roots) {
        const scope = entry.root;
        for (const element of Array.from(scope.querySelectorAll ? scope.querySelectorAll(selector) : [])) {
          rootContext.set(element, {
            kind: entry.kind,
            frameSrc: entry.frameSrc,
            frameUrl: entry.frameUrl || null,
            shadowHostTag: entry.shadowHostTag,
            surfaceIndex: entry.surfaceIndex,
            rootPath: Array.isArray(entry.rootPath) ? entry.rootPath : ["document"],
            rootSignature: entry.rootSignature || [
              entry.kind || "document",
              entry.frameSrc || "",
              entry.frameUrl || "",
              entry.shadowHostTag || "",
              typeof entry.surfaceIndex === "number" ? String(entry.surfaceIndex) : "",
            ].join("|"),
          });
          pushNode(element);
        }
        for (const element of Array.from(scope.querySelectorAll ? scope.querySelectorAll(surfaceSelector) : [])) {
          rootContext.set(element, {
            kind: entry.kind,
            frameSrc: entry.frameSrc,
            frameUrl: entry.frameUrl || null,
            shadowHostTag: entry.shadowHostTag,
            surfaceIndex: entry.surfaceIndex,
            rootPath: Array.isArray(entry.rootPath) ? entry.rootPath : ["document"],
            rootSignature: entry.rootSignature || [
              entry.kind || "document",
              entry.frameSrc || "",
              entry.frameUrl || "",
              entry.shadowHostTag || "",
              typeof entry.surfaceIndex === "number" ? String(entry.surfaceIndex) : "",
            ].join("|"),
          });
          pushNode(element);
        }
        for (const element of Array.from(scope.querySelectorAll ? scope.querySelectorAll("*") : []).slice(0, 1200)) {
          const view = element.ownerDocument && element.ownerDocument.defaultView ? element.ownerDocument.defaultView : window;
          const style = view.getComputedStyle(element);
          const classification = classifySurfaceKinds(element, style);
          if (classification.kinds.length) {
            rootContext.set(element, {
              kind: entry.kind,
              frameSrc: entry.frameSrc,
              frameUrl: entry.frameUrl || null,
              shadowHostTag: entry.shadowHostTag,
              surfaceIndex: entry.surfaceIndex,
              rootPath: Array.isArray(entry.rootPath) ? entry.rootPath : ["document"],
              rootSignature: entry.rootSignature || [
                entry.kind || "document",
                entry.frameSrc || "",
                entry.frameUrl || "",
                entry.shadowHostTag || "",
                typeof entry.surfaceIndex === "number" ? String(entry.surfaceIndex) : "",
              ].join("|"),
            });
            pushNode(element);
          }
          if (nodes.length >= 24) {
            break;
          }
        }
      }
      return nodes.slice(0, 24).map((element, index) => {
        const id = `web-embedding-${index}`;
        element.setAttribute('data-web-embedding-interaction-id', id);
        const rect = element.getBoundingClientRect();
        const view = element.ownerDocument && element.ownerDocument.defaultView ? element.ownerDocument.defaultView : window;
        const style = view.getComputedStyle(element);
        const classification = classifySurfaceKinds(element, style);
        const contextMeta = rootContext.get(element) || {
          kind: "document",
          frameSrc: null,
          frameUrl: null,
          shadowHostTag: null,
          surfaceIndex: 0,
          rootSignature: "document||||0",
        };
        const summaryLabel = [
          getInteractionLabel(element),
          classification.kinds.length ? classification.kinds.join("+") : null,
        ]
          .filter(Boolean)
          .join(" · ");
        return {
          id,
          selector: `[data-web-embedding-interaction-id="${id}"]`,
          tag: element.tagName.toLowerCase(),
          role: element.getAttribute('role'),
          text: (element.innerText || element.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120) || null,
          labelText: getInteractionLabel(element),
          href: element.getAttribute('href'),
          type: element.getAttribute('type'),
          ariaLabel: element.getAttribute('aria-label'),
          ariaExpanded: element.getAttribute('aria-expanded'),
          ariaPressed: element.getAttribute('aria-pressed'),
          ariaSelected: element.getAttribute('aria-selected'),
          ariaCurrent: element.getAttribute('aria-current'),
          ariaControls: element.getAttribute('aria-controls'),
          ariaHasPopup: element.getAttribute('aria-haspopup'),
          ariaModal: element.getAttribute('aria-modal'),
          ariaRoleDescription: element.getAttribute('aria-roledescription'),
          inForm: Boolean(element.closest('form')),
          inputCapable: (
            element.isContentEditable ||
            element.tagName.toLowerCase() === 'textarea' ||
            (
              element.tagName.toLowerCase() === 'input' &&
              !['hidden', 'password', 'checkbox', 'radio', 'file', 'submit', 'button', 'reset', 'range', 'color', 'date', 'datetime-local', 'month', 'time', 'week'].includes((element.getAttribute('type') || 'text').toLowerCase())
            )
          ),
          scrollableX: classification.scrollableX,
          scrollableY: classification.scrollableY,
          scrollableAxis: classification.scrollableAxis,
          scrollTop: Math.round(element.scrollTop || 0),
          scrollLeft: Math.round(element.scrollLeft || 0),
          scrollHeight: Math.round(element.scrollHeight || 0),
          scrollWidth: Math.round(element.scrollWidth || 0),
          clientHeight: Math.round(element.clientHeight || 0),
          clientWidth: Math.round(element.clientWidth || 0),
          scrollSnapType: classification.scrollSnapType,
          scrollSnapAlign: classification.scrollSnapAlign,
          scrollSnapStop: classification.scrollSnapStop,
          position: classification.position,
          zIndex: classification.zIndex,
          surfaceKinds: classification.kinds,
          summaryLabel,
          clickCapable: (
            !element.getAttribute('href') &&
            (
              ['button', 'summary'].includes(element.tagName.toLowerCase()) ||
              ['button', 'tab', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'switch', 'combobox', 'checkbox', 'radio', 'option', 'treeitem'].includes((element.getAttribute('role') || '').toLowerCase()) ||
              ['checkbox', 'radio', 'button'].includes((element.getAttribute('type') || '').toLowerCase()) ||
              Boolean(element.getAttribute('aria-controls')) ||
              element.hasAttribute('aria-expanded') ||
              element.hasAttribute('aria-haspopup')
            )
          ),
          rect: {
            x: Math.round(rect.x),
            y: Math.round(rect.y),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
          },
          baseStyles: styleSnapshotFromComputed(style),
          kind: getInteractionKind(element),
          label: getInteractionLabel(element),
          focusable: isFocusableElement(element, style),
          targetSummary: describeInteractionTarget(element, style),
          surfaceSummary: {
            kinds: classification.kinds,
            scrollableAxis: classification.scrollableAxis,
            position: classification.position,
            zIndex: classification.zIndex,
          },
          rootContext: contextMeta,
        };
      });
    });
    const interactionStates = [];
    const interactionTrace = {
      version: "interaction-trace.v1",
      viewport: {
        width: pageMetrics.viewportWidth,
        height: pageMetrics.viewportHeight,
      },
      pageMetrics,
      steps: [],
      executions: [],
    };
    let traceOrder = 0;
  const normalizeTraceRootContext = (rootContext) => {
    if (!rootContext || typeof rootContext !== "object") {
      return { kind: "document", frameSrc: null, frameUrl: null, shadowHostTag: null, surfaceIndex: 0, rootSignature: "document||||0", rootPath: ["document"] };
    }
    return {
      kind: rootContext.kind || "document",
      frameSrc: rootContext.frameSrc || null,
      frameUrl: rootContext.frameUrl || null,
      shadowHostTag: rootContext.shadowHostTag || null,
      surfaceIndex: typeof rootContext.surfaceIndex === "number" ? rootContext.surfaceIndex : null,
      rootPath: Array.isArray(rootContext.rootPath) ? rootContext.rootPath : ["document"],
      rootSignature: rootContext.rootSignature || [
        rootContext.kind || "document",
        rootContext.frameSrc || "",
        rootContext.frameUrl || "",
        rootContext.shadowHostTag || "",
        typeof rootContext.surfaceIndex === "number" ? String(rootContext.surfaceIndex) : "",
      ].join("|"),
    };
  };
    const pushTraceStep = (step) => {
      traceOrder += 1;
      const entry = {
        id: `trace-${traceOrder}`,
        order: traceOrder,
        rootContext: normalizeTraceRootContext(step && step.rootContext),
        ...step,
      };
      interactionTrace.steps.push(entry);
      return entry;
    };
    const pushExecution = (execution) => {
      interactionTrace.executions.push({
        order: interactionTrace.executions.length + 1,
        rootContext: normalizeTraceRootContext(execution && execution.rootContext),
        ...execution,
      });
    };
    const maxScrollY = Math.max(0, (pageMetrics.scrollHeight || 0) - (pageMetrics.viewportHeight || 0));
    const scrollTargets = [0];
    if (maxScrollY > 40) {
      scrollTargets.push(Math.round(maxScrollY * 0.5), maxScrollY);
    }
    for (const scrollY of [...new Set(scrollTargets)]) {
      const step = pushTraceStep({
        kind: "scroll",
        safeToExecute: true,
        targetId: null,
        scrollY,
        label: scrollY === 0 ? "scroll top" : `scroll ${scrollY}px`,
        interactionKind: "scroll",
        rootContext: {
          kind: "document",
          frameSrc: null,
          frameUrl: page.url(),
          shadowHostTag: null,
          surfaceIndex: 0,
          rootPath: ["document"],
          rootSignature: ["document", "", page.url(), "", "0"].join("|"),
        },
      });
      try {
        await page.evaluate((value) => window.scrollTo({ top: value, behavior: 'instant' }), scrollY);
        await page.waitForTimeout(60);
        const observedScroll = await page.evaluate(() => Math.round(window.scrollY || window.pageYOffset || 0));
        pushExecution({
          stepId: step.id,
          kind: step.kind,
          status: "executed",
          observed: { scrollY: observedScroll },
          rootContext: step.rootContext,
        });
      } catch (error) {
        pushExecution({
          stepId: step.id,
          kind: step.kind,
          status: "failed",
          error: error.message,
          rootContext: step.rootContext,
        });
      }
    }
    await page.evaluate((value) => window.scrollTo({ top: value, behavior: 'instant' }), pageMetrics.initialScrollY || 0);
    let scrollSurfaceReplayCount = 0;
    for (const candidate of interactiveCandidates) {
      const traceLabel = candidate.summaryLabel || candidate.labelText || candidate.label || candidate.text || candidate.ariaLabel || candidate.tag;
      let hoverStyles = null;
      let focusStyles = null;
      let hoverState = null;
      let focusState = null;
      let hoverError = null;
      let focusError = null;
      let clickState = null;

      try {
        await page.evaluate(evaluateInteractionRuntime, { action: "hover", selector: candidate.selector, rootContext: candidate.rootContext });
        await page.waitForTimeout(80);
        hoverStyles = await page.evaluate(evaluateInteractionRuntime, { action: "capture-style", selector: candidate.selector, rootContext: candidate.rootContext });
        hoverState = await page.evaluate(evaluateInteractionRuntime, { action: "capture-state", selector: candidate.selector, rootContext: candidate.rootContext });
      } catch (error) {
        hoverError = error.message;
      }

      try {
        await page.evaluate(evaluateInteractionRuntime, { action: "focus", selector: candidate.selector, rootContext: candidate.rootContext });
        await page.waitForTimeout(60);
        focusStyles = await page.evaluate(evaluateInteractionRuntime, { action: "capture-style", selector: candidate.selector, rootContext: candidate.rootContext });
        focusState = await page.evaluate(evaluateInteractionRuntime, { action: "capture-state", selector: candidate.selector, rootContext: candidate.rootContext });
        await page.evaluate(evaluateInteractionRuntime, { action: "blur", selector: candidate.selector, rootContext: candidate.rootContext });
      } catch (error) {
        focusError = error.message;
      }

      const hoverStep = pushTraceStep({
        kind: "hover",
        safeToExecute: true,
        targetId: candidate.id,
        selector: candidate.selector,
        label: traceLabel,
        interactionKind: "hover",
        rootContext: candidate.rootContext,
      });
      pushExecution({
        stepId: hoverStep.id,
        kind: hoverStep.kind,
        status: hoverError ? "failed" : "executed",
        changedKeys: Object.keys(diffStyleSnapshots(candidate.baseStyles, hoverStyles)),
        stateSummary: summarizeInteractionState(hoverState),
        error: hoverError,
        rootContext: hoverStep.rootContext,
      });
      const focusStep = pushTraceStep({
        kind: "focus",
        safeToExecute: true,
        targetId: candidate.id,
        selector: candidate.selector,
        label: traceLabel,
        interactionKind: "focus",
        rootContext: candidate.rootContext,
      });
      pushExecution({
        stepId: focusStep.id,
        kind: focusStep.kind,
        status: focusError ? "failed" : "executed",
        changedKeys: Object.keys(diffStyleSnapshots(candidate.baseStyles, focusStyles)),
        stateSummary: summarizeInteractionState(focusState),
        error: focusError,
        rootContext: focusStep.rootContext,
      });

      if (scrollSurfaceReplayCount < 4 && Array.isArray(candidate.surfaceKinds) && candidate.surfaceKinds.includes("scroll") && candidate.scrollableAxis) {
        scrollSurfaceReplayCount += 1;
        const scrollStep = pushTraceStep({
          kind: "scroll",
          safeToExecute: true,
          targetId: candidate.id,
          selector: candidate.selector,
          label: `${traceLabel} scroll ${candidate.scrollableAxis}`,
          interactionKind: "scroll-surface",
          rootContext: candidate.rootContext,
        });
        let beforeScrollState = null;
        let afterScrollState = null;
        try {
          beforeScrollState = await page.evaluate(evaluateInteractionRuntime, { action: "capture-state", selector: candidate.selector, rootContext: candidate.rootContext });
          const scrollDeltaX = candidate.scrollableAxis === "x" || candidate.scrollableAxis === "both" ? Math.max(40, Math.round((candidate.clientWidth || 0) * 0.4)) : 0;
          const scrollDeltaY = candidate.scrollableAxis === "y" || candidate.scrollableAxis === "both" ? Math.max(40, Math.round((candidate.clientHeight || 0) * 0.4)) : 0;
          const beforeScroll = await page.evaluate(evaluateInteractionRuntime, { action: "read-scroll", selector: candidate.selector, rootContext: candidate.rootContext });
          await page.evaluate(evaluateInteractionRuntime, { action: "write-scroll", selector: candidate.selector, deltaX: scrollDeltaX, deltaY: scrollDeltaY, rootContext: candidate.rootContext });
          await page.waitForTimeout(80);
          afterScrollState = await page.evaluate(evaluateInteractionRuntime, { action: "capture-state", selector: candidate.selector, rootContext: candidate.rootContext });
          await page.evaluate(evaluateInteractionRuntime, {
            action: "write-scroll",
            selector: candidate.selector,
            scrollTop: beforeScroll && typeof beforeScroll.scrollTop === "number" ? beforeScroll.scrollTop : 0,
            scrollLeft: beforeScroll && typeof beforeScroll.scrollLeft === "number" ? beforeScroll.scrollLeft : 0,
            rootContext: candidate.rootContext,
          });
          pushExecution({
            stepId: scrollStep.id,
            kind: scrollStep.kind,
            status: "executed",
            changedKeys: Object.keys(diffInteractionStates(beforeScrollState, afterScrollState)),
            stateSummary: {
              before: summarizeInteractionState(beforeScrollState),
              after: summarizeInteractionState(afterScrollState),
            },
            observed: {
              scrollableAxis: candidate.scrollableAxis,
              deltaX: scrollDeltaX,
              deltaY: scrollDeltaY,
            },
            rootContext: scrollStep.rootContext,
          });
        } catch (error) {
          pushExecution({
            stepId: scrollStep.id,
            kind: scrollStep.kind,
            status: "failed",
            error: error.message,
            rootContext: scrollStep.rootContext,
          });
        }
      }

      if (candidate.inputCapable) {
        const typeValue = safeReplayValue(candidate);
        let beforeTypeState = null;
        let afterTypeState = null;
        const typeStep = pushTraceStep({
          kind: "type",
          safeToExecute: true,
          targetId: candidate.id,
          selector: candidate.selector,
          label: traceLabel,
          value: typeValue,
          interactionKind: "type",
          rootContext: candidate.rootContext,
        });
        try {
          beforeTypeState = await page.evaluate(evaluateInteractionRuntime, { action: "capture-state", selector: candidate.selector, rootContext: candidate.rootContext });
          const beforeValuePayload = await page.evaluate(evaluateInteractionRuntime, { action: "read-value", selector: candidate.selector, rootContext: candidate.rootContext });
          const beforeValue = beforeValuePayload && beforeValuePayload.available ? beforeValuePayload.value || "" : "";
          await page.evaluate(evaluateInteractionRuntime, { action: "write-value", selector: candidate.selector, value: typeValue, rootContext: candidate.rootContext });
          await page.waitForTimeout(80);
          afterTypeState = await page.evaluate(evaluateInteractionRuntime, { action: "capture-state", selector: candidate.selector, rootContext: candidate.rootContext });
          const afterValuePayload = await page.evaluate(evaluateInteractionRuntime, { action: "read-value", selector: candidate.selector, rootContext: candidate.rootContext });
          const afterValue = afterValuePayload && afterValuePayload.available ? afterValuePayload.value || "" : "";
          await page.evaluate(evaluateInteractionRuntime, { action: "write-value", selector: candidate.selector, value: beforeValue, rootContext: candidate.rootContext });
          pushExecution({
            stepId: typeStep.id,
            kind: typeStep.kind,
            status: "executed",
            beforeValue: trimText(beforeValue, 80),
            afterValue: trimText(afterValue, 80),
            stateSummary: {
              before: summarizeInteractionState(beforeTypeState),
              after: summarizeInteractionState(afterTypeState),
            },
            rootContext: typeStep.rootContext,
          });
        } catch (error) {
          pushExecution({
            stepId: typeStep.id,
            kind: typeStep.kind,
            status: "failed",
            error: error.message,
            rootContext: typeStep.rootContext,
          });
        }
      }

      if (candidate.clickCapable) {
        const safeToggleLike = isSafeToggleCandidate(candidate);
        const clickStep = pushTraceStep({
          kind: "click",
          safeToExecute: safeToggleLike,
          targetId: candidate.id,
          selector: candidate.selector,
          label: traceLabel,
          interactionKind: safeToggleLike ? "click-toggle" : "click-planned",
          rootContext: candidate.rootContext,
        });
        if (!safeToggleLike) {
          pushExecution({
            stepId: clickStep.id,
            kind: clickStep.kind,
            status: "planned",
            reason: "Click replay is captured as a plan only unless the control looks like a safe toggle.",
            rootContext: clickStep.rootContext,
          });
        } else {
          try {
            const beforeState = await page.evaluate(evaluateInteractionRuntime, { action: "capture-toggle", selector: candidate.selector, rootContext: candidate.rootContext });
            await page.evaluate(evaluateInteractionRuntime, { action: "click", selector: candidate.selector, rootContext: candidate.rootContext });
            await page.waitForTimeout(80);
            const afterState = await page.evaluate(evaluateInteractionRuntime, { action: "capture-toggle", selector: candidate.selector, rootContext: candidate.rootContext });
            const stateDelta = diffInteractionStates(beforeState, afterState);
            const stateChanged = Object.keys(stateDelta).length > 0;
            let restoredState = null;
            let restoreError = null;
            if (stateChanged) {
              try {
                await page.evaluate(evaluateInteractionRuntime, { action: "click", selector: candidate.selector, rootContext: candidate.rootContext });
                await page.waitForTimeout(60);
                restoredState = await page.evaluate(evaluateInteractionRuntime, { action: "capture-toggle", selector: candidate.selector, rootContext: candidate.rootContext });
              } catch (error) {
                restoreError = error.message;
              }
            }
            clickState = {
              safeToggleLike,
              before: beforeState,
              after: afterState,
              restored: restoredState,
              stateDelta,
              restoreError,
            };
            pushExecution({
              stepId: clickStep.id,
              kind: clickStep.kind,
              status: "executed",
              safeToggleLike,
              stateChanged,
              beforeState,
              afterState,
              restoredState,
              stateDelta,
              stateSummary: {
                before: summarizeInteractionState(beforeState),
                after: summarizeInteractionState(afterState),
                restored: summarizeInteractionState(restoredState),
              },
              error: restoreError,
              rootContext: clickStep.rootContext,
            });
          } catch (error) {
            clickState = {
              safeToggleLike,
              error: error.message,
            };
            pushExecution({
              stepId: clickStep.id,
              kind: clickStep.kind,
              status: "failed",
              safeToggleLike,
              error: error.message,
              rootContext: clickStep.rootContext,
            });
          }
        }
      }
      interactionStates.push({
        ...candidate,
        interactionLabel: traceLabel,
        summaryLabel: traceLabel,
        stateSummary: summarizeInteractionState(focusState || hoverState),
        hoverStyles,
        hoverDelta: diffStyleSnapshots(candidate.baseStyles, hoverStyles),
        hoverState,
        hoverError,
        focusStyles,
        focusDelta: diffStyleSnapshots(candidate.baseStyles, focusStyles),
        focusState,
        focusError,
        clickState,
      });
    }
    await page.evaluate(evaluateInteractionRuntime, { action: "cleanup" });
    await page.mouse.move(1, 1);
    const networkManifest = {
      requests: uniqueByUrl(requestEntries).slice(0, 400),
      responses: uniqueByUrl(responseEntries).slice(0, 400),
      failures: uniqueByUrl(failedRequestEntries).slice(0, 200),
      redirects: uniqueByUrl(redirectRequestEntries).slice(0, 120),
    };
    const requestById = new Map();
    for (const request of requestEntries) {
      if (request && request.requestId !== undefined && request.requestId !== null && !requestById.has(request.requestId)) {
        requestById.set(request.requestId, request);
      }
    }
    const responseById = new Map();
    for (const response of responseEntries) {
      if (response && response.requestId !== undefined && response.requestId !== null && !responseById.has(response.requestId)) {
        responseById.set(response.requestId, response);
      }
    }
    const failureById = new Map();
    for (const failure of failedRequestEntries) {
      if (failure && failure.requestId !== undefined && failure.requestId !== null && !failureById.has(failure.requestId)) {
        failureById.set(failure.requestId, failure);
      }
    }
    const resourceTypeCounts = {};
    for (const request of networkManifest.requests) {
      const key = request.resourceType || "unknown";
      resourceTypeCounts[key] = (resourceTypeCounts[key] || 0) + 1;
    }
    const responseStatusCounts = {};
    for (const response of networkManifest.responses) {
      const key = String(response.status || "unknown");
      responseStatusCounts[key] = (responseStatusCounts[key] || 0) + 1;
    }
    const failureReasonCounts = {};
    for (const failure of networkManifest.failures) {
      const key = String(failure.errorText || "unknown");
      failureReasonCounts[key] = (failureReasonCounts[key] || 0) + 1;
    }
    const requestHeaderPresenceSummary = {};
    for (const request of networkManifest.requests) {
      const presence = request.headerPresence || {};
      for (const key of presence.present || []) {
        requestHeaderPresenceSummary[key] = (requestHeaderPresenceSummary[key] || 0) + 1;
      }
    }
    const responseHeaderPresenceSummary = {};
    for (const response of networkManifest.responses) {
      const presence = response.headerPresence || {};
      for (const key of presence.present || []) {
        responseHeaderPresenceSummary[key] = (responseHeaderPresenceSummary[key] || 0) + 1;
      }
    }
    const redirectSample = networkManifest.redirects.slice(0, 12).map((entry) => ({
      url: entry.url,
      redirectedFromUrl: entry.redirectedFromUrl,
      redirectDepth: entry.redirectDepth,
      frameUrl: entry.frameUrl,
      resourceType: entry.resourceType,
    }));
    const timingBucketCounts = {};
    for (const entry of [...networkManifest.requests, ...networkManifest.responses]) {
      const bucket = String(entry.timingBucket || "unknown");
      timingBucketCounts[bucket] = (timingBucketCounts[bucket] || 0) + 1;
    }
    const responseBodyAvailability = {
      withContentLength: networkManifest.responses.filter((entry) => Boolean(entry.contentLength)).length,
      withTransferEncoding: networkManifest.responses.filter((entry) => Boolean((entry.headerPresence || {}).present && (entry.headerPresence.present || []).includes("transfer-encoding"))).length,
      withContentEncoding: networkManifest.responses.filter((entry) => Boolean((entry.headerPresence || {}).present && (entry.headerPresence.present || []).includes("content-encoding"))).length,
      likelyHasBody: networkManifest.responses.filter((entry) => ![204, 205, 304].includes(Number(entry.status || 0)) && (Boolean(entry.contentLength) || Boolean(entry.contentType))).length,
    };
    const queryParameterCount = networkManifest.requests.reduce((total, entry) => total + (Array.isArray(entry.queryString) ? entry.queryString.length : 0), 0);
    const requestCookieCount = networkManifest.requests.reduce((total, entry) => total + (Array.isArray(entry.requestCookies) ? entry.requestCookies.length : 0), 0);
    const responseCookieCount = networkManifest.responses.reduce((total, entry) => total + (Array.isArray(entry.responseCookies) ? entry.responseCookies.length : 0), 0);
    const requestHeaderBytes = networkManifest.requests.reduce((total, entry) => total + headersSizeFromArray(entry.requestHeadersArray || []), 0);
    const responseHeaderBytes = networkManifest.responses.reduce((total, entry) => total + headersSizeFromArray(entry.responseHeadersArray || []), 0);
    const requestBodyBytes = networkManifest.requests.reduce((total, entry) => total + Math.max(0, Number(entry.postDataSize || 0)), 0);
    const responseBodyBytes = networkManifest.responses.reduce((total, entry) => total + Math.max(0, Number(entry.contentLength || 0)), 0);
    const responseRedirectCount = networkManifest.responses.filter((entry) => Boolean(entry.redirectURL) || (Number(entry.status || 0) >= 300 && Number(entry.status || 0) < 400)).length;
    const frameUrlSample = Array.from(new Set(
      networkManifest.requests
        .map((entry) => entry.frameUrl)
        .filter(Boolean)
    )).slice(0, 20);
    const harPages = [{
      id: "page_1",
      startedDateTime: pageStartedDateTime,
      title: await page.title(),
      pageUrl: page.url(),
      frameUrlSample,
      redirectCount: networkManifest.redirects.length,
      pageTimings: navigationTiming ? {
        onContentLoad: navigationTiming.domContentLoadedDuration,
        onLoad: navigationTiming.loadDuration,
      } : {
        onContentLoad: null,
        onLoad: null,
      },
    }];
    const harEntries = [];
    for (const request of requestEntries) {
      const response = request.requestId !== undefined && request.requestId !== null ? responseById.get(request.requestId) : null;
      const failure = request.requestId !== undefined && request.requestId !== null ? failureById.get(request.requestId) : null;
      const responseTiming = response && response.responseTiming ? response.responseTiming : null;
      const responseHeadersArray = response ? (response.responseHeadersArray || []) : [];
      const responseHeaderPresence = response ? (response.headerPresence || {}) : {};
      const requestHeadersArray = request.requestHeadersArray || [];
      const requestHeaderPresence = request.headerPresence || {};
      const responseContentLength = response && response.contentLength ? Number(response.contentLength) || null : null;
      const requestBodyText = request.postDataText || null;
      const redirectInfo = request.redirectDepth > 0 || request.redirectedFromUrl
        ? {
            redirectedFromUrl: request.redirectedFromUrl || null,
            redirectDepth: request.redirectDepth || 0,
          }
        : null;
      harEntries.push({
        startedDateTime: new Date(request.observedAt || Date.now()).toISOString(),
        timeBucket: request.timingBucket || "unknown",
        time: responseTiming && Number.isFinite(Number(responseTiming.responseEnd))
          ? Math.max(0, Number(responseTiming.responseEnd))
          : responseTiming && Number.isFinite(Number(responseTiming.responseStart))
            ? Math.max(0, Number(responseTiming.responseStart))
            : null,
        pageref: "page_1",
        initiator: {
          type: request.isNavigationRequest ? "navigation" : (request.resourceType || "other"),
          frameUrl: request.frameUrl || null,
          redirectDepth: request.redirectDepth || 0,
          redirectedFromUrl: request.redirectedFromUrl || null,
          isNavigationRequest: Boolean(request.isNavigationRequest),
        },
        request: {
          method: request.method,
          url: request.url,
          httpVersion: null,
          headers: requestHeadersArray,
          queryString: Array.isArray(request.queryString) ? request.queryString : [],
          cookies: Array.isArray(request.requestCookies) ? request.requestCookies : [],
          headerPresence: requestHeaderPresence,
          postDataSize: request.postDataSize || 0,
          hasPostData: Boolean(request.hasPostData),
          postData: requestBodyText ? {
            mimeType: requestHeadersArray.find((entry) => entry.name.toLowerCase() === "content-type")?.value || null,
            text: requestBodyText,
            encoding: null,
            params: Array.isArray(request.postDataParams) ? request.postDataParams : [],
          } : null,
          resourceType: request.resourceType || null,
          frameUrl: request.frameUrl || null,
          isNavigationRequest: Boolean(request.isNavigationRequest),
          redirectDepth: request.redirectDepth || 0,
          redirectedFromUrl: request.redirectedFromUrl || null,
          bodySize: request.postDataSize || 0,
          headersSize: headersSizeFromArray(requestHeadersArray),
        },
        response: response ? {
          status: response.status,
          statusText: response.statusText || null,
          httpVersion: null,
          headers: responseHeadersArray,
          cookies: Array.isArray(response.responseCookies) ? response.responseCookies : [],
          headerPresence: responseHeaderPresence,
          contentType: response.contentType || null,
          contentLength: response.contentLength || null,
          redirectURL: response.redirectURL || null,
          fromServiceWorker: Boolean(response.fromServiceWorker),
          frameUrl: response.frameUrl || null,
          bodySize: responseContentLength !== null ? responseContentLength : -1,
          headersSize: headersSizeFromArray(responseHeadersArray),
          content: {
            size: responseContentLength !== null ? responseContentLength : 0,
            mimeType: response.contentType || null,
            compression: null,
            text: null,
          },
        } : null,
        failure: failure ? {
          errorText: failure.errorText || null,
          resourceType: failure.resourceType || null,
          frameUrl: failure.frameUrl || null,
        } : null,
        cache: {},
        timings: {
          bucket: response ? (response.timingBucket || request.timingBucket || "unknown") : (request.timingBucket || "unknown"),
          requestStart: responseTiming && Number.isFinite(Number(responseTiming.requestStart)) ? Number(responseTiming.requestStart) : null,
          responseStart: responseTiming && Number.isFinite(Number(responseTiming.responseStart)) ? Number(responseTiming.responseStart) : null,
          responseEnd: responseTiming && Number.isFinite(Number(responseTiming.responseEnd)) ? Number(responseTiming.responseEnd) : null,
          wait: responseTiming && Number.isFinite(Number(responseTiming.responseStart)) && Number.isFinite(Number(responseTiming.requestStart))
            ? Math.max(0, Number(responseTiming.responseStart) - Number(responseTiming.requestStart))
            : null,
          receive: responseTiming && Number.isFinite(Number(responseTiming.responseEnd)) && Number.isFinite(Number(responseTiming.responseStart))
            ? Math.max(0, Number(responseTiming.responseEnd) - Number(responseTiming.responseStart))
            : null,
        },
      });
      if (redirectInfo) {
        harEntries[harEntries.length - 1].redirect = redirectInfo;
      }
    }
    networkManifest.summary = {
      requestCount: networkManifest.requests.length,
      responseCount: networkManifest.responses.length,
      failureCount: networkManifest.failures.length,
      redirectCount: networkManifest.redirects.length,
      resourceTypeCounts,
      responseStatusCounts,
      failureReasonCounts,
      requestHeaderPresenceSummary,
      responseHeaderPresenceSummary,
      timingBucketCounts,
      responseBodyAvailability,
      queryParameterCount,
      requestCookieCount,
      responseCookieCount,
      requestHeaderBytes,
      responseHeaderBytes,
      requestBodyBytes,
      responseBodyBytes,
      responseRedirectCount,
      navigationRequestCount: networkManifest.requests.filter((entry) => entry.isNavigationRequest).length,
      postDataRequestCount: networkManifest.requests.filter((entry) => entry.hasPostData).length,
      serviceWorkerResponseCount: networkManifest.responses.filter((entry) => entry.fromServiceWorker).length,
      frameUrlSample,
      redirectSample,
      pageTimings: harPages[0].pageTimings,
      harPageCount: harPages.length,
      harEntryCount: harEntries.length,
      harLikePageCount: harPages.length,
      harLikeEntryCount: harEntries.length,
    };
    const harLog = {
      version: "1.2",
      creator: {
        name: "webEmbedding",
        version: "0.1",
      },
      browser: {
        name: "Playwright Chromium",
        version: null,
      },
      pages: harPages,
      entries: harEntries.slice(0, 400),
    };
    networkManifest.harLike = {
      version: "near-har.v1",
      pages: harPages,
      entries: harEntries.slice(0, 400),
      summary: {
        pageCount: harPages.length,
        entryCount: harEntries.length,
        requestCount: networkManifest.requests.length,
        responseCount: networkManifest.responses.length,
        failureCount: networkManifest.failures.length,
        redirectCount: networkManifest.redirects.length,
        queryParameterCount,
        requestHeaderBytes,
        responseHeaderBytes,
        responseRedirectCount,
      },
    };
    networkManifest.har = {
      log: harLog,
      summary: {
        pageCount: harPages.length,
        entryCount: harEntries.length,
        requestCount: networkManifest.requests.length,
        responseCount: networkManifest.responses.length,
        failureCount: networkManifest.failures.length,
        redirectCount: networkManifest.redirects.length,
        queryParameterCount,
        requestHeaderBytes,
        responseHeaderBytes,
        responseRedirectCount,
        pageTimings: harPages[0].pageTimings,
      },
    };
    const assetSummary = {
      images: assetInventory.images.length,
      scripts: assetInventory.scripts.length,
      stylesheets: assetInventory.stylesheets.length,
      videos: assetInventory.videos.length,
      audios: assetInventory.audios.length,
      iframes: assetInventory.iframes.length,
      backgroundImages: assetInventory.backgroundImages.length,
      preloadLinks: assetInventory.preloadLinks.length,
      fontFaces: assetInventory.fontFaces.length,
      roots: Array.isArray(assetInventory.roots) ? assetInventory.roots.length : 0,
    };
    const htmlMatches = html
      ? Array.from(
          html.matchAll(/https?:\/\/[^"'\\s>]+/g),
          (match) => match[0]
        ).filter((value) => regex.test(value))
      : [];

    if (storageStateOutputPath) {
      try {
        fs.mkdirSync(path.dirname(storageStateOutputPath), { recursive: true });
        await context.storageState({ path: storageStateOutputPath });
        storageStateExported = true;
      } catch (storageStateWriteError) {
        storageStateExportError = storageStateWriteError.message;
      }
    }

    console.log(JSON.stringify({
      available: true,
      engine: "playwright",
      finalUrl: page.url(),
      title: await page.title(),
      session: {
        mode: sessionMode,
        userDataDir: userDataDir || null,
        storageStatePath: storageStatePath || null,
        storageStateOutputPath: storageStateOutputPath || null,
        storageStateApplied,
        storageStateError,
        storageStateExported,
        storageStateExportError,
      },
      networkHits: hits.slice(0, 200),
      htmlMatches: [...new Set(htmlMatches)].slice(0, 200),
      captures: {
        dom: {
          available: Boolean(domSnapshot),
          nodeCountApprox: JSON.stringify(domSnapshot || {}).length,
          nodeCount: domSnapshotStats.nodeCount,
          shadowRootCount: domSnapshotStats.shadowRootCount,
          frameDocumentCount: domSnapshotStats.frameDocumentCount,
          inaccessibleFrameCount: domSnapshotStats.inaccessibleFrameCount,
          content: domSnapshot,
        },
        accessibility: {
          available: Boolean(accessibilityTree),
          content: accessibilityTree,
        },
        styles: {
          available: true,
          entryCount: styleSummary.length,
          content: styleSummary,
        },
        cssAnalysis: {
          available: true,
          stylesheetCount: cssAnalysis.stylesheetCount,
          accessibleStylesheetCount: cssAnalysis.accessibleStylesheetCount,
          inlineStyleTagCount: cssAnalysis.inlineStyleTagCount,
          styleAttributeCount: cssAnalysis.styleAttributeCount,
          linkedStylesheetCount: Array.isArray(cssAnalysis.linkedStylesheets) ? cssAnalysis.linkedStylesheets.length : 0,
          preloadLinkCount: Array.isArray(cssAnalysis.preloadLinkSample) ? cssAnalysis.preloadLinkSample.length : 0,
          fontFaceRuleCount: Array.isArray(cssAnalysis.fontFaceSample) ? cssAnalysis.fontFaceSample.length : 0,
          content: cssAnalysis,
        },
        network: {
          available: true,
          requestCount: networkManifest.requests.length,
          responseCount: networkManifest.responses.length,
          failureCount: networkManifest.failures.length,
          frameUrlCount: frameUrlSample.length,
          content: networkManifest,
        },
        assets: {
          available: true,
          summary: assetSummary,
          content: assetInventory,
        },
        interactions: {
          available: interactionStates.length > 0,
          entryCount: interactionStates.length,
          content: interactionStates,
        },
        interactionTrace: {
          available: interactionTrace.steps.length > 0,
          stepCount: interactionTrace.steps.length,
          replayedCount: interactionTrace.executions.length,
          content: interactionTrace,
        },
        html: captureHtml ? {
          available: true,
          length: html ? html.length : 0,
          content: html,
        } : {
          available: false,
          requested: true,
        },
        screenshot: captureScreenshot ? {
          available: true,
          mimeType: "image/png",
          byteLength: screenshotBytes ? screenshotBytes.length : 0,
          base64: screenshotBytes ? screenshotBytes.toString("base64") : "",
        } : {
          available: false,
          requested: true,
        },
      },
    }));
  } catch (error) {
    console.log(JSON.stringify({
      available: false,
      engine: "playwright",
      error: error.message,
      session: {
        userDataDir: userDataDir || null,
        storageStatePath: storageStatePath || null,
        storageStateOutputPath: storageStateOutputPath || null,
        captureHtml,
        captureScreenshot
      }
    }));
  } finally {
    try {
      if (context) {
        await context.close();
      }
      if (browser) {
        await browser.close();
      }
    } catch (closeError) {
      void closeError;
    }
  }
})();
"""


def trace_runtime_sources(
    url: str,
    wait_seconds: int = 8,
    pattern: str = "spline|preview|embed|viewer|scene|iframe",
    user_data_dir: str | None = None,
    storage_state_path: str | None = None,
    storage_state_output_path: str | None = None,
    capture_html: bool = False,
    capture_screenshot: bool = False,
    viewport_width: int = 1440,
    viewport_height: int = 1200,
) -> dict[str, Any]:
    if shutil.which("node") is None:
        return {
            "available": False,
            "error": "node is not installed",
            "session": {
                "user_data_dir": user_data_dir,
                "storage_state_path": storage_state_path,
                "storage_state_output_path": storage_state_output_path,
                "capture_html": capture_html,
                "capture_screenshot": capture_screenshot,
            },
        }

    if storage_state_path and not Path(storage_state_path).exists():
        return {
            "available": False,
            "error": f"storage_state_path does not exist: {storage_state_path}",
            "session": {
                "user_data_dir": user_data_dir,
                "storage_state_path": storage_state_path,
                "storage_state_output_path": storage_state_output_path,
                "capture_html": capture_html,
                "capture_screenshot": capture_screenshot,
            },
        }

    env = os.environ.copy()
    if not env.get("WEB_EMBEDDING_CHROME_PATH"):
        for candidate in DEFAULT_BROWSER_PATHS:
            if Path(candidate).exists():
                env["WEB_EMBEDDING_CHROME_PATH"] = candidate
                break

    completed = subprocess.run(
        [
            "node",
            "-e",
            _runtime_trace_script(),
            url,
            str(wait_seconds),
            pattern,
            user_data_dir or "",
            storage_state_path or "",
            "true" if capture_html else "false",
            "true" if capture_screenshot else "false",
            str(viewport_width),
            str(viewport_height),
            storage_state_output_path or "",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    payload = completed.stdout.strip() or completed.stderr.strip()
    if not payload:
        return {"available": False, "error": "runtime trace produced no output"}
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {"available": False, "raw_output": payload}
