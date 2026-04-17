"""Shared constants for the source-first clone MCP package."""

from __future__ import annotations

import re


SERVER_NAME = "source-first-clone"
SERVER_VERSION = "0.3.1"
USER_AGENT = "webEmbedding/0.3.1"
CAPTURE_SCHEMA_VERSION = "0.3.1"
DEFAULT_BROWSER_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]

BREAKPOINT_PROFILES = {
    "desktop": {"width": 1440, "height": 1200},
    "tablet": {"width": 1024, "height": 1366},
    "mobile": {"width": 390, "height": 844},
}

URL_PATTERNS = [
    ("spline-preview", re.compile(r"https://app\.spline\.design/file/[^\"'\s>]+\?view=preview", re.I)),
    ("spline-community", re.compile(r"https://app\.spline\.design/community/file/[^\"'\s>]+", re.I)),
    ("spline-viewer", re.compile(r"https://viewer\.spline\.design/[^\"'\s>]+", re.I)),
    ("spline-code", re.compile(r"https://[^\"'\s>]+\.splinecode", re.I)),
    ("figma-share", re.compile(r"https://(?:www\.)?figma\.com/(?:community/file|file|proto|board|slides)/[^\"'\s>]+", re.I)),
    ("figma-embed", re.compile(r"https://www\.figma\.com/embed[^\"'\s>]+", re.I)),
    ("youtube-embed", re.compile(r"https://www\.youtube\.com/embed/[^\"'\s>]+", re.I)),
    ("vimeo-embed", re.compile(r"https://player\.vimeo\.com/video/[^\"'\s>]+", re.I)),
    ("codepen-embed", re.compile(r"https://codepen\.io/[^\"'\s>]+/embed/[^\"'\s>]+", re.I)),
    ("framer-publish", re.compile(r"https://(?:[a-z0-9-]+\.)?(?:framer\.app|framer\.website)(?:/[^\"'\s>]*)?", re.I)),
    ("webflow-publish", re.compile(r"https://(?:[a-z0-9-]+\.)?webflow\.io(?:/[^\"'\s>]*)?", re.I)),
    ("iframe-src", re.compile(r"<iframe[^>]+src=[\"']([^\"']+)", re.I)),
    ("generic-embed", re.compile(r"https://[^\"'\s>]*(?:embed|preview|viewer)[^\"'\s>]*", re.I)),
]

LICENSE_HINTS = [
    "cc0",
    "creative commons",
    "mit license",
    "apache license",
    "all rights reserved",
    "copyright",
    "licensed under",
    "remix",
    "fork",
    "export",
]
