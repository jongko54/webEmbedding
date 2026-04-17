# webEmbedding

`webEmbedding` is a source-first webpage intake and bounded exact-clone engine for Codex.

It does not assume that every URL should be rebuilt the same way. It inspects a reference, chooses the strongest available path, captures runtime evidence when needed, generates a bounded reconstruction when reuse is blocked, and verifies the result.

## Current State

As of the current worktree:

- best bounded runtime fidelity seen so far: `88 / 100`
- current universal-engine completion estimate: `86 / 100`
- current product shape:
  - `exact reuse` when the upstream surface is safely reusable
  - `bounded rebuild` when reuse is blocked
  - `verification + repair` for reconstruction quality

This is already useful as:

- an internal reconstruction tool
- an expert-operated exact-clone workflow
- an enterprise pipeline where exact reuse is preferred and bounded rebuild is reviewed

It is not yet a universal no-review clone product for arbitrary production sites.

## What The Engine Does

The pipeline is:

1. inspect the URL
2. classify the site surface
3. choose the best route
4. capture runtime evidence
5. generate exact reuse or bounded rebuild artifacts
6. verify fidelity
7. run bounded repair when needed

The current implementation supports:

- source-first routing
- session-aware runtime capture
- persisted capture bundles
- bounded rebuild scaffolds
- self-verify and repair loops
- universal site-profile classification
- generic deep-capture summaries for DOM, CSS, assets, interactions, screenshots, and network

## Universal External-Site Baseline

The current worktree includes a universal routing baseline through `site_profile`.

It can classify broader external-site shapes such as:

- `platform-managed-surface`
- `frame-blocked-app-surface`
- `authenticated-app-surface`
- `js-app-shell-surface`
- `multi-frame-document-surface`
- `canvas-or-webgl-surface`
- `longform-content-surface`
- `static-document`

Each profile emits route hints:

- `acquisition_profile`
- `renderer_route`
- `renderer_family`
- `critical_depths`

This means the engine can now explain:

- what kind of site it is looking at
- how deep capture should go
- which renderer family should be preferred
- which gaps still matter most

## Runtime Capture Depth

When runtime capture is enabled, the engine can persist:

- `dom/snapshot.json`
- `dom/runtime.html`
- `styles/computed-summary.json`
- `styles/css-analysis.json`
- `network/manifest.json`
- `network/har.json`
- `network/har-like.json`
- `assets/inventory.json`
- `interactions/states.json`
- `interactions/trace.json`
- `screenshots/runtime.png`
- `session/storage-state.json`
- `capture.json`

The generic deep-capture baseline includes:

- DOM metadata
  - `nodeCount`
  - `shadowRootCount`
  - `frameDocumentCount`
  - `inaccessibleFrameCount`
- CSS metadata
  - linked stylesheet counts
  - preload link counts
  - font-face rule counts
- asset inventory
  - images
  - scripts
  - stylesheets
  - iframes
  - background images
  - preload links
  - font faces
- network HAR-lite metadata
  - request/response/failure counts
  - redirect counts and redirect samples
  - timing buckets
  - request/response header-presence summaries
  - body-availability hints
  - frame URL samples
  - near-HAR page and entry export
  - persisted standard `har.json` export baseline
- interaction capture
  - interaction states
  - replay trace
  - root-aware `rootContext` on interaction candidates:
    - `document`
    - `frame-document`
    - `shadow-root`

Current frame/shadow status:

- structure discovery works
- local mixed-surface replay works across document, frame, and open shadow roots
- root-aware interaction verification now scores `frame_url_overlap` and `surface_index_overlap`
- arbitrary real-site frame/shadow parity is improved but not fully closed yet

## Scores

There are two separate score types in this repo.

### 1. Per-site fidelity

This is how closely one rebuilt site matches one reference.

Current notable checkpoints:

- `google.com`: `88 / 100`
- `python.org`: `61 / 100`
- exact-reuse cases are treated operationally as success paths instead of rebuild scores

### 2. Universal-engine completion

This is how complete the engine is overall as a generic external-site system.

Current estimate: **`86 / 100`**

Why it is not higher yet:

- arbitrary real-site frame/shadow parity is still partial
- HAR export exists and is richer now, but replay-grade network parity is still incomplete
- app-shell/dashboard renderer family now exists in bounded form, but panel/state reconstruction is still early
- canvas/WebGL fallback renderer family now exists in bounded form, but visual reconstruction is still early
- benchmark corpus and regression CI are still light

See:

- [docs/webEmbedding-universal-completion-score.md](./docs/webEmbedding-universal-completion-score.md)

## Install

### Release install

```bash
curl -fsSL https://github.com/jongko54/webEmbedding/releases/latest/download/install.sh | bash
```

### Local install from this checkout

```bash
python3 python/web_embedding/installer.py install
```

### Local smoke install into a temp home

```bash
python3 python/web_embedding/installer.py install --target-home ./.tmp/home
python3 python/web_embedding/installer.py doctor --target-home ./.tmp/home
python3 python/web_embedding/installer.py uninstall --target-home ./.tmp/home
```

## CLI

Quick inspection:

```bash
node ./bin/web-embedding.mjs inspect --url https://www.python.org
```

Runtime capture:

```bash
node ./bin/web-embedding.mjs capture \
  --url https://www.python.org \
  --output-dir ./.tmp/captures/python
```

One-shot clone:

```bash
node ./bin/web-embedding.mjs clone \
  --url https://www.python.org \
  --output-dir ./.tmp/reproductions/python
```

Universal benchmark on a small corpus:

```bash
python3 scripts/benchmark_routes.py \
  --urls-file docs/universal-benchmark-corpus.txt \
  --out ./.tmp/universal-benchmark \
  --capture
```

## Benchmark Corpus

This repo now includes a small generic benchmark list:

- [docs/universal-benchmark-corpus.txt](./docs/universal-benchmark-corpus.txt)

It is intentionally small, public, and generic so route/depth regressions are easy to spot.
The benchmark report now also exposes `route_quality_counts` and `depth_presence_counts` so generic capture depth changes are easier to regression-test.
It now also exposes `renderer_family_counts`.

## Repo Layout

- `bundle/source-first-clone`
  The installed plugin bundle
- `python/web_embedding/installer.py`
  Shared installer core
- `bin/web-embedding.mjs`
  Node wrapper for CLI usage
- `scripts/bootstrap.sh`
  Release bootstrap script
- `scripts/release_bundle.py`
  Release artifact builder
- `scripts/benchmark_routes.py`
  Universal route and depth benchmark

## Documentation

- [docs/webEmbedding-v0.2-architecture.md](./docs/webEmbedding-v0.2-architecture.md)
- [docs/webEmbedding-v0.3-playwright-integration.md](./docs/webEmbedding-v0.3-playwright-integration.md)
- [docs/webEmbedding-v1-universal-mode.md](./docs/webEmbedding-v1-universal-mode.md)
- [docs/webEmbedding-universal-completion-score.md](./docs/webEmbedding-universal-completion-score.md)

## Remaining Core Gaps

The most important remaining work is still generic, not platform-specific:

- arbitrary real-site `frame/shadow` interaction parity
- replay-grade HAR/network parity
- `app-shell / dashboard` renderer family, now present in bounded form but still needing richer panel/state reconstruction
- `canvas / WebGL` fallback renderer family, now present in bounded form but still needing richer visual reconstruction
- broader corpus regression CI

## Product Positioning

What this repo is now:

- a strong `source-first exact-reuse + bounded rebuild + verification` engine

What it is not yet:

- a universal one-shot pixel-perfect clone engine for every production site
- a self-serve no-review `paste any URL and get a near-perfect result` product
