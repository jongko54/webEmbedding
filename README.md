# webEmbedding

`webEmbedding` is a distributable Codex plugin repo for source-first webpage intake, session-aware capture planning, and exact-clone decisioning.

The install flow is opinionated:

1. Inspect the reference URL.
2. Try to find the original embed, preview, viewer, remix, or export source.
3. Use the original when possible.
4. Rebuild only when exact reuse is unavailable or not allowed.

In the current `v0.3.1` worktree, the MCP server is organized around:

- acquisition
- policy
- capture bundle scaffolding
- reproduction planning
- fidelity verification scaffolding

The `v0.3.1` baseline adds session-aware Playwright capture options for:

- persistent browser profiles via `user_data_dir`
- reusable auth snapshots via `storage_state_path`
- optional storage state export via `storage_state_output_path`
- optional runtime HTML and screenshot capture

This is intentionally honest about the current boundary: `webEmbedding` now goes beyond URL inspection, but it is still not a full DOM/CSS reproduction engine yet.

What changed in the current worktree is that the plugin can now:

- detect when the original page itself is safely frameable and use it as a `direct-iframe` exact-reuse target
- run a one-shot `clone` workflow from a single URL
- persist both an exact-reuse bundle and a rebuild prompt when exact reuse is unavailable

The repo ships three install entrypoints that all converge on the same Python installer core:

- `curl -fsSL https://github.com/jongko54/webEmbedding/releases/latest/download/install.sh | bash`
- `npx web-embedding install`
- `uvx web-embedding install`

## Install

### Available now

Use the latest GitHub release directly:

```bash
curl -fsSL https://github.com/jongko54/webEmbedding/releases/latest/download/install.sh | bash
```

Use a specific release tag:

```bash
curl -fsSL https://github.com/jongko54/webEmbedding/releases/download/v0.2.0/install.sh | bash
```

The repo currently contains unreleased `v0.3.1` worktree changes. Until the next release is tagged, use `latest` or an existing published tag such as `v0.2.0`.

### After registry publish

These commands are the intended registry install surface once the package is published to npm and PyPI:

```bash
npx web-embedding install
uvx web-embedding install
```

## Registry publishing

This repo now includes `.github/workflows/publish.yml` for trusted publishing to npm and PyPI.

Before the workflow can succeed, configure the registries to trust this repository:

### npm

Create the `web-embedding` package on npm and add a trusted publisher with:

- GitHub user or organization: `jongko54`
- Repository: `webEmbedding`
- Workflow filename: `publish.yml`
- Environment name: `npm`

### PyPI

Create a pending trusted publisher for `web-embedding` with:

- Owner: `jongko54`
- Repository: `webEmbedding`
- Workflow filename: `publish.yml`
- Environment: `pypi`

After those publisher records exist, pushing a `v*` tag will publish to both registries.

To avoid accidental failed publishes before the registries are configured, keep these repository variables disabled until setup is complete:

- `ENABLE_NPM_PUBLISH=false`
- `ENABLE_PYPI_PUBLISH=false`

Flip each variable to `true` when that registry is ready.

## What gets installed

The installer copies the plugin bundle into:

- `~/plugins/source-first-clone`

It also creates or updates the local Codex marketplace entry at:

- `~/.agents/plugins/marketplace.json`

## Repo layout

- `bundle/source-first-clone`
  The actual Codex plugin bundle that gets installed.
- `docs/webEmbedding-v0.2-architecture.md`
  The recommended v0.2 architecture and phased roadmap.
- `docs/webEmbedding-v0.3-playwright-integration.md`
  The session-aware Playwright integration notes for the v0.3 baseline.
- `python/web_embedding/installer.py`
  Shared installer core used by the npm wrapper, uvx script entrypoint, and curl bootstrap flow.
- `bin/web-embedding.mjs`
  Thin Node wrapper for `npx`.
- `scripts/bootstrap.sh`
  Shell bootstrap for curl installs after the repo is published.
- `scripts/release_bundle.py`
  Creates release artifacts in `dist/`.

## Local development

Install the plugin from this checkout into your real home directory:

```bash
python3 python/web_embedding/installer.py install
```

Install into a temporary target home for smoke tests:

```bash
python3 python/web_embedding/installer.py install --target-home ./.tmp/home
python3 python/web_embedding/installer.py doctor --target-home ./.tmp/home
python3 python/web_embedding/installer.py uninstall --target-home ./.tmp/home
```

Use the Node wrapper locally:

```bash
node ./bin/web-embedding.mjs install --target-home ./.tmp/home
```

## Publishing plan

After you publish this repo, release these artifacts:

- `source-first-clone-bundle.tar.gz`
- `install.py`
- `install.sh`
- `SHA256SUMS`

`install.sh` downloads `install.py` and the bundle tarball, then calls the same installer core used by `npx` and `uvx`.

### First release flow

1. Commit and push `main`
2. Create a version tag such as `v0.3.1`
3. Push the tag
4. Let the GitHub Actions release workflow build and upload:
   - `source-first-clone-bundle.tar.gz`
   - `install.py`
   - `install.sh`
   - `SHA256SUMS`

The workflow file lives at `.github/workflows/release.yml`.

## Bundled plugin capabilities

The installed plugin includes:

- A skill named `exact-clone-intake`
- An MCP server with tools for:
  - `clone_reference_url`
  - `detect_runtime_capabilities`
  - `inspect_url`
  - `discover_embed_candidates`
  - `trace_runtime_sources`
  - `classify_clone_mode`
  - `generate_embed_snippet`
- `capture_reference_bundle`
- `build_rebuild_scaffold`
- `build_reproduction_bundle`
- `plan_reproduction_path`
- `verify_fidelity_report`

`clone_reference_url` is the current one-shot entrypoint. It runs inspection, policy, session-aware capture, and reproduction bundle generation in one pass.

`trace_runtime_sources` is optional and works best when the host has `node` plus `playwright` or `playwright-core`.

The current v0.3 baseline adds session-aware runtime options:

- `user_data_dir`
- `storage_state_path`
- `storage_state_output_path`
- `capture_html`
- `capture_screenshot`
- `output_dir` for writing a canonical on-disk capture bundle

When runtime capture succeeds, the bundle can now persist:

- `dom/snapshot.json`
- `dom/runtime.html`
- `styles/computed-summary.json`
- `network/manifest.json`
- `assets/inventory.json`
- `interactions/states.json`
- `interactions/trace.json`
- `screenshots/runtime.png`
- `session/storage-state.json`
- `capture.json`

Example local capture flow:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
from pathlib import Path
import sys
repo = Path.cwd()
sys.path.insert(0, str(repo / "bundle/source-first-clone/mcp"))
from source_first_clone.capture_bundle import capture_reference_bundle

capture_reference_bundle(
    url="https://app.spline.design/community/file/1da90e69-17f1-4cdc-ab49-d3ee07c4edee",
    user_data_dir=str(repo / ".tmp/browser-profile"),
    capture_html=True,
    capture_screenshot=True,
    output_dir=str(repo / ".tmp/captures/spline-community"),
)
PY
```

Example one-shot clone flow:

```bash
node ./bin/web-embedding.mjs clone \
  --url "https://example.com" \
  --output-dir "$PWD/.tmp/reproductions/example-clone"
```

When the original page is frameable, the result now comes back as `coverage: exact-reuse` with `exact_reuse.kind: direct-iframe`.

Example bounded rebuild scaffold flow from an existing capture bundle:

```bash
node ./bin/web-embedding.mjs scaffold \
  --capture-bundle "$PWD/.tmp/reproductions/example-clone4/capture.json" \
  --output-dir "$PWD/.tmp/scaffolds/example-from-capture"
```

Example bounded fidelity verification:

```bash
node ./bin/web-embedding.mjs verify \
  --reference-bundle "$PWD/.tmp/reproductions/example-clone4/capture.json" \
  --candidate-bundle "$PWD/.tmp/reproductions/example-clone4/capture.json"
```

The persisted output directory will include:

- `capture.json`
- `reproduction/plan.json`
- `reproduction/embed.html`
- `reproduction/embed.tsx`
- `reproduction/rebuild-prompt.txt`
- `reproduction/rebuild/layout-summary.json`
- `reproduction/rebuild/app-model.json`
- `reproduction/rebuild/starter.html`
- `reproduction/rebuild/starter.css`
- `reproduction/rebuild/starter.tsx`
- `reproduction/rebuild/next-app/app/layout.tsx`
- `reproduction/rebuild/next-app/app/page.tsx`
- `reproduction/rebuild/next-app/app/globals.css`
- `reproduction/rebuild/next-app/components/BoundedReferencePage.tsx`
- `reproduction/rebuild/next-app/components/reference-data.ts`
- `reproduction/rebuild/manifest.json`

When exact reuse is unavailable, the reproduction bundle also writes a bounded rebuild scaffold under `reproduction/rebuild/` so downstream tooling has both a low-level HTML/CSS/TSX starter and a more practical role-inferred `next-app/` renderer skeleton to continue from.

The capture bundle now includes two interaction layers for visible interactive elements:

- `interactions/states.json`
  - hover style deltas
  - focus style deltas
  - interactive element bounds and text labels
- `interactions/trace.json`
  - ordered replay steps for `scroll`, `hover`, `focus`, `type`, and planned `click`
  - execution results for safe replay steps
  - viewport and page metrics for downstream reconstruction

CLI helpers:

```bash
web-embedding capabilities
web-embedding capture \
  --url "https://app.spline.design/community/file/1da90e69-17f1-4cdc-ab49-d3ee07c4edee" \
  --user-data-dir "$PWD/.tmp/browser-profile" \
  --output-dir "$PWD/.tmp/captures/spline-community"

web-embedding clone \
  --url "https://app.spline.design/community/file/1da90e69-17f1-4cdc-ab49-d3ee07c4edee" \
  --user-data-dir "$PWD/.tmp/browser-profile" \
  --output-dir "$PWD/.tmp/reproductions/spline-community"

web-embedding reproduce \
  --url "https://app.spline.design/community/file/1da90e69-17f1-4cdc-ab49-d3ee07c4edee" \
  --user-data-dir "$PWD/.tmp/browser-profile" \
  --output-dir "$PWD/.tmp/reproductions/spline-community"
```

When exact reuse is available, `reproduce` also writes:

- `reproduction/plan.json`
- `reproduction/embed.html`
- `reproduction/embed.tsx`
- `reproduction/prompt.txt`

The new tools are still scaffolds for the next phase:

- `capture_reference_bundle` builds a canonical capture-bundle skeleton from currently available signals
- `build_rebuild_scaffold` turns a saved capture bundle into starter HTML/CSS/TSX, an app-model snapshot, and a bounded role-inferred `next-app/` renderer skeleton for frame-blocked pages
- `plan_reproduction_path` turns policy and bundle state into a source-first execution plan
- `verify_fidelity_report` and `web-embedding verify` produce bounded artifact-based fidelity reports using persisted-PNG signatures, coarse grid drift, histogram and edge similarity, plus interaction-trace coverage as a core exact-clone readiness signal

## Guardrails

This plugin is source-first, not piracy-first.

- Prefer public preview, embed, remix, export, or source links.
- Respect license and ownership signals.
- Say when an exact copy is unavailable.
- Fall back to rebuilding only after the exact paths are exhausted.

## Architecture

See [docs/webEmbedding-v0.2-architecture.md](./docs/webEmbedding-v0.2-architecture.md) for the architectural roadmap and [docs/webEmbedding-v0.3-playwright-integration.md](./docs/webEmbedding-v0.3-playwright-integration.md) for the current session-aware capture boundary.
