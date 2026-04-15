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

curl -fsSL "${INSTALL_URL}" -o "${TMP_DIR}/install.py"
curl -fsSL "${BUNDLE_URL}" -o "${TMP_DIR}/${ASSET_BASE}-bundle.tar.gz"

exec python3 "${TMP_DIR}/install.py" install --bundle-archive "${TMP_DIR}/${ASSET_BASE}-bundle.tar.gz" "${@}"
