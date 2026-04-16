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
from urllib.request import Request, urlopen

from .constants import DEFAULT_BROWSER_PATHS, LICENSE_HINTS, URL_PATTERNS, USER_AGENT


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


def build_candidates(base_url: str, html: str) -> list[dict[str, str]]:
    final_candidates: list[dict[str, str]] = []
    seen: set[str] = set()

    for kind, pattern in URL_PATTERNS:
        matches = pattern.findall(html)
        for raw_match in matches:
            candidate = raw_match if isinstance(raw_match, str) else raw_match[0]
            normalized = urljoin(base_url, candidate.strip())
            if normalized in seen:
                continue
            seen.add(normalized)
            final_candidates.append({"kind": kind, "url": normalized})

    generic_urls = re.findall(r"https://[^\"'\s>]+", html)
    for raw in generic_urls:
        if not re.search(r"(spline|preview|embed|viewer|scene|iframe|remix|export)", raw, re.I):
            continue
        if raw in seen:
            continue
        seen.add(raw)
        final_candidates.append({"kind": "runtime-hint", "url": raw})

    return final_candidates


def inspect_reference(url: str, timeout_seconds: int = 20) -> dict[str, Any]:
    fetched = fetch_url(url, timeout_seconds=timeout_seconds)
    html = fetched["html"]
    frame_policy = analyze_frame_policy(fetched.get("headers"))
    candidate_urls = build_candidates(fetched["final_url"], html)
    if frame_policy.get("embeddable") is True:
        candidate_urls = [{"kind": "direct-iframe", "url": fetched["final_url"]}, *candidate_urls]
    return {
        "url": url,
        "final_url": fetched["final_url"],
        "status": fetched["status"],
        "title": extract_title(html),
        "meta": extract_meta(html),
        "license_hints": extract_license_hints(html),
        "headers": fetched.get("headers", {}),
        "frame_policy": frame_policy,
        "candidate_urls": candidate_urls,
    }


def discover_embed_candidates(url: str, timeout_seconds: int = 20) -> dict[str, Any]:
    fetched = fetch_url(url, timeout_seconds=timeout_seconds)
    frame_policy = analyze_frame_policy(fetched.get("headers"))
    candidates = build_candidates(fetched["final_url"], fetched["html"])
    if frame_policy.get("embeddable") is True:
        candidates = [{"kind": "direct-iframe", "url": fetched["final_url"]}, *candidates]
    return {
        "url": url,
        "final_url": fetched["final_url"],
        "frame_policy": frame_policy,
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
    for (const candidate of interactiveCandidates) {
      const x = Math.max(1, Math.round(candidate.rect.x + Math.min(candidate.rect.width / 2, Math.max(candidate.rect.width - 1, 1))));
      const y = Math.max(1, Math.round(candidate.rect.y + Math.min(candidate.rect.height / 2, Math.max(candidate.rect.height - 1, 1))));
      let hoverStyles = null;
      let focusStyles = null;
      let hoverError = null;
      let focusError = null;

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
        hoverError,
        focusStyles,
        focusDelta: diffStyleSnapshots(candidate.baseStyles, focusStyles),
        focusError,
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
