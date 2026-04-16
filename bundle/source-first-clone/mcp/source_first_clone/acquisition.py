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
    paddingTop: style.paddingTop,
    paddingRight: style.paddingRight,
    paddingBottom: style.paddingBottom,
    paddingLeft: style.paddingLeft,
    marginTop: style.marginTop,
    marginRight: style.marginRight,
    marginBottom: style.marginBottom,
    marginLeft: style.marginLeft,
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

function captureSemanticState(element, style) {
  const datasetEntries = Object.entries(element.dataset || {}).slice(0, 8);
  const semanticState = {
    text: (element.innerText || element.textContent || "").replace(/\s+/g, " ").trim().slice(0, 120) || null,
    ariaLabel: element.getAttribute("aria-label"),
    ariaDescription: element.getAttribute("aria-description"),
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
    scrollY: Math.round(window.scrollY || window.pageYOffset || 0),
  };
  if (style) {
    semanticState.styleSnapshot = styleSnapshotFromComputed(style);
  }
  return semanticState;
}

function captureToggleState(selector) {
  const element = document.querySelector(selector);
  if (!element) {
    return { available: false, selector };
  }
  const style = window.getComputedStyle(element);
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
    controlledTargets,
    semanticState: captureSemanticState(element, style),
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
    return {
      id,
      tag: target.tagName.toLowerCase(),
      role: target.getAttribute("role"),
      open: target.open === true,
      hidden: Boolean(target.hidden),
      ariaHidden: target.getAttribute("aria-hidden"),
      ariaExpanded: target.getAttribute("aria-expanded"),
      ariaSelected: target.getAttribute("aria-selected"),
      display: style.display,
      visibility: style.visibility,
      rect: {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      },
      text: (target.innerText || target.textContent || "").replace(/\s+/g, " ").trim().slice(0, 120) || null,
    };
  });
}

function captureInteractionState(selector) {
  const element = document.querySelector(selector);
  if (!element) {
    return { available: false, selector };
  }
  const style = window.getComputedStyle(element);
  return {
    available: true,
    selector,
    inForm: Boolean(element.closest("form")),
    controlledTargets: captureControlledTargetState(element),
    semanticState: captureSemanticState(element, style),
    baseStyles: styleSnapshotFromComputed(style),
  };
}

function summarizeInteractionState(state) {
  if (!state || typeof state !== "object") {
    return null;
  }
  const semantic = state.semanticState && typeof state.semanticState === "object" ? state.semanticState : state;
  const pick = (key) => (Object.prototype.hasOwnProperty.call(semantic, key) ? semantic[key] : null);
  return {
    tag: pick("tag") ?? state.tag ?? null,
    role: pick("role") ?? state.role ?? null,
    text: pick("text") ?? state.text ?? null,
    href: pick("href") ?? state.href ?? null,
    type: pick("type") ?? state.type ?? null,
    value: pick("value"),
    ariaExpanded: pick("ariaExpanded"),
    ariaPressed: pick("ariaPressed"),
    ariaSelected: pick("ariaSelected"),
    open: pick("open"),
    checked: pick("checked"),
    selected: pick("selected"),
    disabled: pick("disabled"),
    hidden: pick("hidden"),
    scrollY: pick("scrollY"),
    activeElementTag: pick("activeElementTag") ?? state.activeElementTag ?? null,
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
    "contentEditable",
    "tabIndex",
    "open",
    "hidden",
    "disabled",
    "scrollY",
    "activeElementTag",
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
  if (role === "tab" || role === "menuitem") {
    return true;
  }
  if (tag === "button" && type === "button") {
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
      const nodes = Array.from(document.querySelectorAll("body *"))
        .filter((element) => {
          const text = (element.textContent || "").replace(/\s+/g, " ").trim();
          const rect = element.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0 && (text || element.tagName === "IMG" || element.tagName === "VIDEO" || element.tagName === "CANVAS");
        })
        .slice(0, 80);
      return nodes.map((element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return {
          tag: element.tagName.toLowerCase(),
          id: element.id || null,
          className: typeof element.className === "string" && element.className ? element.className.slice(0, 120) : null,
          text: (element.textContent || "").replace(/\s+/g, " ").trim().slice(0, 120) || null,
          rect: {
            x: Math.round(rect.x),
            y: Math.round(rect.y),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
          },
          styles: {
            display: style.display,
            position: style.position,
            color: style.color,
            backgroundColor: style.backgroundColor,
            fontFamily: style.fontFamily,
            fontSize: style.fontSize,
            fontWeight: style.fontWeight,
            lineHeight: style.lineHeight,
            borderRadius: style.borderRadius,
            zIndex: style.zIndex,
            opacity: style.opacity,
          },
        };
      });
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
        '[onclick]',
        '[tabindex]:not([tabindex="-1"])'
      ].join(',');
      const nodes = Array.from(document.querySelectorAll(selector))
        .filter((element) => {
          const rect = element.getBoundingClientRect();
          const style = window.getComputedStyle(element);
          return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
        })
        .slice(0, 16);
      return nodes.map((element, index) => {
        const id = `web-embedding-${index}`;
        element.setAttribute('data-web-embedding-interaction-id', id);
        const rect = element.getBoundingClientRect();
        const style = window.getComputedStyle(element);
        return {
          id,
          selector: `[data-web-embedding-interaction-id="${id}"]`,
          tag: element.tagName.toLowerCase(),
          role: element.getAttribute('role'),
          text: (element.innerText || element.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120) || null,
          href: element.getAttribute('href'),
          type: element.getAttribute('type'),
          ariaLabel: element.getAttribute('aria-label'),
          ariaExpanded: element.getAttribute('aria-expanded'),
          ariaPressed: element.getAttribute('aria-pressed'),
          ariaSelected: element.getAttribute('aria-selected'),
          ariaControls: element.getAttribute('aria-controls'),
          inForm: Boolean(element.closest('form')),
          inputCapable: (
            element.tagName.toLowerCase() === 'textarea' ||
            (
              element.tagName.toLowerCase() === 'input' &&
              !['hidden', 'password', 'checkbox', 'radio', 'file', 'submit', 'button', 'reset', 'range', 'color', 'date', 'datetime-local', 'month', 'time', 'week'].includes((element.getAttribute('type') || 'text').toLowerCase())
            )
          ),
          clickCapable: (
            !element.getAttribute('href') &&
            (
              ['button', 'summary'].includes(element.tagName.toLowerCase()) ||
              ['button', 'tab', 'menuitem'].includes((element.getAttribute('role') || '').toLowerCase()) ||
              Boolean(element.getAttribute('aria-controls')) ||
              element.hasAttribute('aria-expanded')
            )
          ),
          rect: {
            x: Math.round(rect.x),
            y: Math.round(rect.y),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
          },
          baseStyles: styleSnapshotFromComputed(style),
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
    for (const candidate of interactiveCandidates) {
      const x = Math.max(1, Math.round(candidate.rect.x + Math.min(candidate.rect.width / 2, Math.max(candidate.rect.width - 1, 1))));
      const y = Math.max(1, Math.round(candidate.rect.y + Math.min(candidate.rect.height / 2, Math.max(candidate.rect.height - 1, 1))));
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

      interactionStates.push({
        ...candidate,
        hoverStyles,
        hoverDelta: diffStyleSnapshots(candidate.baseStyles, hoverStyles),
        hoverState,
        hoverError,
        focusStyles,
        focusDelta: diffStyleSnapshots(candidate.baseStyles, focusStyles),
        focusState,
        focusError,
      });
      const hoverStep = pushTraceStep({
        kind: "hover",
        safeToExecute: true,
        targetId: candidate.id,
        selector: candidate.selector,
        label: candidate.text || candidate.ariaLabel || candidate.tag,
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
        label: candidate.text || candidate.ariaLabel || candidate.tag,
      });
      pushExecution({
        stepId: focusStep.id,
        kind: focusStep.kind,
        status: focusError ? "failed" : "executed",
        changedKeys: Object.keys(diffStyleSnapshots(candidate.baseStyles, focusStyles)),
        stateSummary: summarizeInteractionState(focusState),
        error: focusError,
      });

      if (candidate.inputCapable) {
        const typeValue = safeReplayValue(candidate);
        let beforeTypeState = null;
        let afterTypeState = null;
        const typeStep = pushTraceStep({
          kind: "type",
          safeToExecute: true,
          targetId: candidate.id,
          selector: candidate.selector,
          label: candidate.text || candidate.ariaLabel || candidate.tag,
          value: typeValue,
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
          label: candidate.text || candidate.ariaLabel || candidate.tag,
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
