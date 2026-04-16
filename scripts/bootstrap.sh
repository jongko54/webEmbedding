#!/usr/bin/env bash
set -euo pipefail

REPO_SLUG="${WEB_EMBEDDING_REPO_SLUG:-jongko54/webEmbedding}"
VERSION="${WEB_EMBEDDING_VERSION:-latest}"
ASSET_BASE="${WEB_EMBEDDING_ASSET_BASE:-source-first-clone}"

if [[ -n "${WEB_EMBEDDING_LOCAL_REPO:-}" ]]; then
  exec python3 "${WEB_EMBEDDING_LOCAL_REPO}/python/web_embedding/installer.py" install "${@}"
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

if [[ "${VERSION}" == "latest" ]]; then
  DEFAULT_RELEASE_BASE="https://github.com/${REPO_SLUG}/releases/latest/download"
else
  DEFAULT_RELEASE_BASE="https://github.com/${REPO_SLUG}/releases/download/${VERSION}"
fi

INSTALL_URL="${WEB_EMBEDDING_INSTALL_URL:-${DEFAULT_RELEASE_BASE}/install.py}"
BUNDLE_URL="${WEB_EMBEDDING_BUNDLE_URL:-${DEFAULT_RELEASE_BASE}/${ASSET_BASE}-bundle.tar.gz}"
CHECKSUMS_URL="${WEB_EMBEDDING_CHECKSUMS_URL:-${DEFAULT_RELEASE_BASE}/SHA256SUMS}"

curl -fsSL "${INSTALL_URL}" -o "${TMP_DIR}/install.py"
curl -fsSL "${BUNDLE_URL}" -o "${TMP_DIR}/${ASSET_BASE}-bundle.tar.gz"
curl -fsSL "${CHECKSUMS_URL}" -o "${TMP_DIR}/SHA256SUMS"

if command -v python3 >/dev/null 2>&1; then
  python3 - <<'PY' "${TMP_DIR}/SHA256SUMS" "${TMP_DIR}/install.py" "${TMP_DIR}/${ASSET_BASE}-bundle.tar.gz"
from __future__ import annotations

import hashlib
import sys
from pathlib import Path


checksums_path = Path(sys.argv[1])
targets = [Path(sys.argv[2]), Path(sys.argv[3])]
expected: dict[str, str] = {}
for line in checksums_path.read_text().splitlines():
    line = line.strip()
    if not line:
        continue
    digest, filename = line.split(None, 1)
    expected[filename.strip().lstrip("*")] = digest.strip()

for target in targets:
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    wanted = expected.get(target.name)
    if not wanted:
        raise SystemExit(f"missing checksum for {target.name}")
    if digest != wanted:
        raise SystemExit(f"checksum mismatch for {target.name}")
PY
else
  echo "warning: sha256 verification skipped because python3 is unavailable" >&2
fi

exec python3 "${TMP_DIR}/install.py" install --bundle-archive "${TMP_DIR}/${ASSET_BASE}-bundle.tar.gz" "${@}"
