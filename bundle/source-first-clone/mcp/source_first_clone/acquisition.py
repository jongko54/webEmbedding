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
    return {
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


def build_candidates(base_url: str, html: str, adapter_candidates: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    final_candidates: list[dict[str, str]] = []
    seen: set[str] = set()

    for kind, pattern in URL_PATTERNS:
        matches = pattern.findall(html)
        for raw_match in matches:
            candidate = raw_match if isinstance(raw_match, str) else raw_match[0]
            normalized = urljoin(base_url, candidate.strip())
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
        if is_candidate_noise(raw):
            continue
        if raw in seen:
            continue
        seen.add(raw)
        final_candidates.append({"kind": "runtime-hint", "url": raw})

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
    return {
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
                "candidates": fallback.get("candidate_urls", []),
            }
        raise
    frame_policy = analyze_frame_policy(fetched.get("headers"))
    meta = extract_meta(fetched["html"])
    platform_adapter = inspect_platform_adapter(fetched["final_url"], fetched["html"], meta)
    candidates = build_candidates(fetched["final_url"], fetched["html"], adapter_candidates=platform_adapter.get("candidates"))
    if frame_policy.get("embeddable") is True and should_promote_direct_iframe(fetched["final_url"], platform_adapter):
        candidates = [{"kind": "direct-iframe", "url": fetched["final_url"]}, *candidates]
    return {
        "url": url,
        "final_url": fetched["final_url"],
        "frame_policy": frame_policy,
        "platform": platform_adapter.get("platform"),
        "platform_adapter": platform_adapter,
        "source_signals": platform_adapter.get("source_signals", []),
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
    activeElementTag: document.activeElement ? document.activeElement.tagName.toLowerCase() : null,
    activeElementMatches: document.activeElement === element,
    focusable: isFocusableElement(element, style),
    interactiveKind: getInteractionKind(element),
    scrollY: Math.round(window.scrollY || window.pageYOffset || 0),
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

function captureToggleState(selector) {
  const element = document.querySelector(selector);
  if (!element) {
    return { available: false, selector };
  }
  const style = window.getComputedStyle(element);
  const semanticState = captureSemanticState(element, style);
  const ids = String(element.getAttribute("aria-controls") || "")
    .split(/\s+/)
    .map((value) => value.trim())
    .filter(Boolean)
    .slice(0, 6);
  const controlledTargets = ids.map((id) => {
    const target = document.getElementById(id);
    if (!target) {
      return { id, missing: true };
    }
    const targetStyle = window.getComputedStyle(target);
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
    const target = document.getElementById(id);
    if (!target) {
      return { id, missing: true };
    }
    const style = window.getComputedStyle(target);
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
  const element = document.querySelector(selector);
  if (!element) {
    return { available: false, selector };
  }
  const style = window.getComputedStyle(element);
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

    page.on("request", (request) => {
      requestEntries.push({
        url: request.url(),
        method: request.method(),
        resourceType: request.resourceType(),
        isNavigationRequest: request.isNavigationRequest(),
      });
    });

    page.on("response", (response) => {
      const target = response.url();
      const request = response.request();
      responseEntries.push({
        url: target,
        status: response.status(),
        resourceType: request.resourceType(),
        method: request.method(),
        contentType: response.headers()["content-type"] || null,
      });
      if (!regex.test(target)) {
        return;
      }
      hits.push({ status: response.status(), url: target });
    });

    await page.goto(pageUrl, { waitUntil: "domcontentloaded", timeout: 90000 });
    await page.waitForTimeout(waitSeconds * 1000);

    const html = captureHtml ? await page.content() : null;
    const screenshotBytes = captureScreenshot ? await page.screenshot({ fullPage: true, type: "png" }) : null;
    const accessibilityTree = page.accessibility && typeof page.accessibility.snapshot === "function"
      ? await page.accessibility.snapshot({ interestingOnly: false }).catch(() => null)
      : null;
    const domSnapshot = await page.evaluate(() => {
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
        const children = Array.from(element.childNodes).slice(0, maxChildren);
        for (const child of children) {
          const summarized = summarize(child, depth + 1, maxDepth, maxChildren);
          if (summarized) {
            entry.children.push(summarized);
          }
        }
        return entry;
      }
      return summarize(document.documentElement, 0, 5, 12);
    });
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
      const normalize = (value) => {
        if (!value) return null;
        try {
          return new URL(value, document.baseURI).href;
        } catch (error) {
          return value;
        }
      };
      const uniq = (values) => Array.from(new Set(values.filter(Boolean)));
      return {
        images: uniq(Array.from(document.images, (node) => normalize(node.currentSrc || node.src))),
        scripts: uniq(Array.from(document.scripts, (node) => normalize(node.src))),
        stylesheets: uniq(Array.from(document.querySelectorAll('link[rel="stylesheet"]'), (node) => normalize(node.href))),
        videos: uniq(Array.from(document.querySelectorAll("video"), (node) => normalize(node.currentSrc || node.src))),
        audios: uniq(Array.from(document.querySelectorAll("audio"), (node) => normalize(node.currentSrc || node.src))),
        iframes: uniq(Array.from(document.querySelectorAll("iframe"), (node) => normalize(node.src))),
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
        const style = window.getComputedStyle(element);
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
      const pushNode = (element) => {
        if (!element || seen.has(element) || !isVisibleCandidate(element)) {
          return;
        }
        seen.add(element);
        nodes.push(element);
      };
      for (const element of Array.from(document.querySelectorAll(selector))) {
        pushNode(element);
      }
      for (const element of Array.from(document.querySelectorAll(surfaceSelector))) {
        pushNode(element);
      }
      for (const element of Array.from(document.querySelectorAll("body *")).slice(0, 1200)) {
        const style = window.getComputedStyle(element);
        const classification = classifySurfaceKinds(element, style);
        if (classification.kinds.length) {
          pushNode(element);
        }
        if (nodes.length >= 24) {
          break;
        }
      }
      return nodes.slice(0, 24).map((element, index) => {
        const id = `web-embedding-${index}`;
        element.setAttribute('data-web-embedding-interaction-id', id);
        const rect = element.getBoundingClientRect();
        const style = window.getComputedStyle(element);
        const classification = classifySurfaceKinds(element, style);
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
    const pushTraceStep = (step) => {
      traceOrder += 1;
      const entry = {
        id: `trace-${traceOrder}`,
        order: traceOrder,
        ...step,
      };
      interactionTrace.steps.push(entry);
      return entry;
    };
    const pushExecution = (execution) => {
      interactionTrace.executions.push({
        order: interactionTrace.executions.length + 1,
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
        });
      } catch (error) {
        pushExecution({
          stepId: step.id,
          kind: step.kind,
          status: "failed",
          error: error.message,
        });
      }
    }
    await page.evaluate((value) => window.scrollTo({ top: value, behavior: 'instant' }), pageMetrics.initialScrollY || 0);
    let scrollSurfaceReplayCount = 0;
    for (const candidate of interactiveCandidates) {
      const x = Math.max(1, Math.round(candidate.rect.x + Math.min(candidate.rect.width / 2, Math.max(candidate.rect.width - 1, 1))));
      const y = Math.max(1, Math.round(candidate.rect.y + Math.min(candidate.rect.height / 2, Math.max(candidate.rect.height - 1, 1))));
      const traceLabel = candidate.summaryLabel || candidate.labelText || candidate.label || candidate.text || candidate.ariaLabel || candidate.tag;
      let hoverStyles = null;
      let focusStyles = null;
      let hoverState = null;
      let focusState = null;
      let hoverError = null;
      let focusError = null;
      let clickState = null;

      try {
        await page.mouse.move(x, y);
        await page.waitForTimeout(80);
        hoverStyles = await page.evaluate((selector) => {
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
          const element = document.querySelector(selector);
          if (!element) return null;
          return styleSnapshotFromComputed(window.getComputedStyle(element));
        }, candidate.selector);
        hoverState = await page.evaluate(captureInteractionState, candidate.selector);
      } catch (error) {
        hoverError = error.message;
      }

      try {
        await page.focus(candidate.selector);
        await page.waitForTimeout(60);
        focusStyles = await page.evaluate((selector) => {
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
          const element = document.querySelector(selector);
          if (!element) return null;
          return styleSnapshotFromComputed(window.getComputedStyle(element));
        }, candidate.selector);
        focusState = await page.evaluate(captureInteractionState, candidate.selector);
        await page.evaluate((selector) => {
          const element = document.querySelector(selector);
          if (element && typeof element.blur === 'function') {
            element.blur();
          }
        }, candidate.selector);
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
      });
      pushExecution({
        stepId: hoverStep.id,
        kind: hoverStep.kind,
        status: hoverError ? "failed" : "executed",
        changedKeys: Object.keys(diffStyleSnapshots(candidate.baseStyles, hoverStyles)),
        stateSummary: summarizeInteractionState(hoverState),
        error: hoverError,
      });
      const focusStep = pushTraceStep({
        kind: "focus",
        safeToExecute: true,
        targetId: candidate.id,
        selector: candidate.selector,
        label: traceLabel,
        interactionKind: "focus",
      });
      pushExecution({
        stepId: focusStep.id,
        kind: focusStep.kind,
        status: focusError ? "failed" : "executed",
        changedKeys: Object.keys(diffStyleSnapshots(candidate.baseStyles, focusStyles)),
        stateSummary: summarizeInteractionState(focusState),
        error: focusError,
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
        });
        let beforeScrollState = null;
        let afterScrollState = null;
        try {
          beforeScrollState = await page.evaluate(captureInteractionState, candidate.selector);
          const scrollDeltaX = candidate.scrollableAxis === "x" || candidate.scrollableAxis === "both" ? Math.max(40, Math.round((candidate.clientWidth || 0) * 0.4)) : 0;
          const scrollDeltaY = candidate.scrollableAxis === "y" || candidate.scrollableAxis === "both" ? Math.max(40, Math.round((candidate.clientHeight || 0) * 0.4)) : 0;
          const beforeScroll = await page.evaluate((selector) => {
            const element = document.querySelector(selector);
            if (!element) return null;
            return {
              scrollTop: Math.round(element.scrollTop || 0),
              scrollLeft: Math.round(element.scrollLeft || 0),
            };
          }, candidate.selector);
          await page.evaluate(({ selector, deltaX, deltaY }) => {
            const element = document.querySelector(selector);
            if (!element) return;
            element.scrollLeft = Math.max(0, (element.scrollLeft || 0) + deltaX);
            element.scrollTop = Math.max(0, (element.scrollTop || 0) + deltaY);
          }, { selector: candidate.selector, deltaX: scrollDeltaX, deltaY: scrollDeltaY });
          await page.waitForTimeout(80);
          afterScrollState = await page.evaluate(captureInteractionState, candidate.selector);
          await page.evaluate(({ selector, scrollTop, scrollLeft }) => {
            const element = document.querySelector(selector);
            if (!element) return;
            element.scrollTop = scrollTop;
            element.scrollLeft = scrollLeft;
          }, {
            selector: candidate.selector,
            scrollTop: beforeScroll && typeof beforeScroll.scrollTop === "number" ? beforeScroll.scrollTop : 0,
            scrollLeft: beforeScroll && typeof beforeScroll.scrollLeft === "number" ? beforeScroll.scrollLeft : 0,
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
          });
        } catch (error) {
          pushExecution({
            stepId: scrollStep.id,
            kind: scrollStep.kind,
            status: "failed",
            error: error.message,
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
        });
        try {
          beforeTypeState = await page.evaluate(captureInteractionState, candidate.selector);
          const beforeValue = await page.$eval(candidate.selector, (element) => ('value' in element ? String(element.value || '') : ''));
          await page.fill(candidate.selector, typeValue);
          await page.waitForTimeout(80);
          afterTypeState = await page.evaluate(captureInteractionState, candidate.selector);
          const afterValue = await page.$eval(candidate.selector, (element) => ('value' in element ? String(element.value || '') : ''));
          await page.fill(candidate.selector, beforeValue);
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
          });
        } catch (error) {
          pushExecution({
            stepId: typeStep.id,
            kind: typeStep.kind,
            status: "failed",
            error: error.message,
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
        });
        if (!safeToggleLike) {
          pushExecution({
            stepId: clickStep.id,
            kind: clickStep.kind,
            status: "planned",
            reason: "Click replay is captured as a plan only unless the control looks like a safe toggle.",
          });
        } else {
          try {
            const beforeState = await page.evaluate(captureToggleState, candidate.selector);
            await page.click(candidate.selector, { timeout: 2500 });
            await page.waitForTimeout(80);
            const afterState = await page.evaluate(captureToggleState, candidate.selector);
            const stateDelta = diffInteractionStates(beforeState, afterState);
            const stateChanged = Object.keys(stateDelta).length > 0;
            let restoredState = null;
            let restoreError = null;
            if (stateChanged) {
              try {
                await page.click(candidate.selector, { timeout: 2500 });
                await page.waitForTimeout(60);
                restoredState = await page.evaluate(captureToggleState, candidate.selector);
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
    await page.evaluate(() => {
      document
        .querySelectorAll('[data-web-embedding-interaction-id]')
        .forEach((element) => element.removeAttribute('data-web-embedding-interaction-id'));
    });
    await page.mouse.move(1, 1);
    const networkManifest = {
      requests: uniqueByUrl(requestEntries).slice(0, 400),
      responses: uniqueByUrl(responseEntries).slice(0, 400),
    };
    const assetSummary = {
      images: assetInventory.images.length,
      scripts: assetInventory.scripts.length,
      stylesheets: assetInventory.stylesheets.length,
      videos: assetInventory.videos.length,
      audios: assetInventory.audios.length,
      iframes: assetInventory.iframes.length,
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
          content: cssAnalysis,
        },
        network: {
          available: true,
          requestCount: networkManifest.requests.length,
          responseCount: networkManifest.responses.length,
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
