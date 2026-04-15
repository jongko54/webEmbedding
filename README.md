# webEmbedding

`webEmbedding` is a distributable Codex plugin repo for source-first web cloning.

The install flow is opinionated:

1. Inspect the reference URL.
2. Try to find the original embed, preview, viewer, remix, or export source.
3. Use the original when possible.
4. Rebuild only when exact reuse is unavailable or not allowed.

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
curl -fsSL https://github.com/jongko54/webEmbedding/releases/download/v0.1.2/install.sh | bash
```

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
2. Create a version tag such as `v0.1.1`
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
  - `inspect_url`
  - `discover_embed_candidates`
  - `trace_runtime_sources`
  - `classify_clone_mode`
  - `generate_embed_snippet`

`trace_runtime_sources` is optional and works best when the host has `node` plus `playwright` or `playwright-core`.

## Guardrails

This plugin is source-first, not piracy-first.

- Prefer public preview, embed, remix, export, or source links.
- Respect license and ownership signals.
- Say when an exact copy is unavailable.
- Fall back to rebuilding only after the exact paths are exhausted.
