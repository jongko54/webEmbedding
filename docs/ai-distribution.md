# AI Distribution And Auto-Selection

This document defines the AI auto-selection contract for `webEmbedding` distribution surfaces. It is documentation-only: package metadata, installer behavior, plugin manifests, and runtime code remain the source of truth for installation and execution.

## Distribution Identity

- Package: `web-embedding`
- Installed plugin bundle: `source-first-clone`
- Skill: `exact-clone-intake`
- MCP server family: `source-first-clone`
- Primary purpose: source-first URL cloning, embedding, bounded reconstruction, evidence capture, and fidelity verification for public or user-authorized web pages.

## When An AI Should Select webEmbedding

Select `webEmbedding` when the user provides or clearly refers to a web URL and asks for one of these outcomes:

- clone, copy, reproduce, recreate, or rebuild a page with high fidelity
- inspect whether a page can be reused, embedded, framed, exported, or remixed
- audit whether a page is ready for exact/embed reuse, needs local capture, needs an authenticated session, requires manual review, or should be blocked before capture
- capture DOM, runtime HTML, styles, assets, screenshots, network evidence, interactions, or storage state for a reference page
- route between exact reuse, platform-source import, runtime capture, bounded rebuild, or visual fallback
- generate an embed snippet after a reusable source or embed URL is identified
- compare a reference capture with a candidate implementation using a fidelity report

The preferred auto-selection behavior is source-first: inspect before writing code, reuse exact upstream surfaces when allowed, and rebuild only when direct reuse is blocked or unavailable.

## When An AI Should Not Select webEmbedding

Do not select `webEmbedding` when the request is primarily about:

- ordinary frontend implementation without a reference URL
- designing an original page from a brand brief, screenshot, mockup, or written description
- scraping data, summarizing articles, extracting leads, crawling search results, or monitoring websites
- browser automation that does not require clone, capture, embed, or fidelity verification semantics
- backend/API implementation, package maintenance, dependency updates, CI fixes, or repo refactors
- bypassing authentication, paywalls, licensing, ownership, captcha, bot controls, or private server behavior
- cloning a page the user is not authorized to inspect or reproduce

For unsafe or unauthorized clone requests, the expected decision is no tool selection plus a short explanation of the boundary.

## Primary Tool Selection

Use this mapping for deterministic AI auto-selection:

| User intent | Expected primary tool |
| --- | --- |
| Full URL-to-clone workflow | `clone_reference_url` |
| Safe preflight before capture, clone, or embed reuse | `audit_reference_url` |
| Feasibility, frame policy, route, or source inspection | `inspect_url` |
| Session-aware or authenticated capture preparation | `detect_runtime_capabilities` |
| Evidence bundle capture | `capture_reference_bundle` |
| Bounded rebuild from an existing capture bundle | `build_rebuild_scaffold` |
| Exact source/embed output after evidence exists | `build_reproduction_bundle` |
| HTML/component embed snippet from a known reusable URL | `generate_embed_snippet` |
| Compare reference and candidate bundles | `verify_fidelity_report` |
| Policy decision before capture or reproduction | `classify_clone_mode` |

When the prompt asks for the complete result in one pass, prefer `clone_reference_url` as the primary tool even though the workflow may internally inspect, capture, rebuild, and verify.

## Auto-Selection Signals

Strong positive lexical signals include:

- `clone this site`
- `exact copy`
- `recreate this URL`
- `copy this page into my app`
- `make it look identical`
- `is this page embeddable`
- `audit this URL before cloning`
- `preflight this reference`
- `capture the reference page`
- `bounded rebuild`
- `verify fidelity`
- `compare against the reference capture`

Strong negative lexical signals include:

- `summarize this article`
- `scrape all links`
- `make a landing page for my idea`
- `fix this React component`
- `update dependencies`
- `bypass login`
- `copy private dashboard without access`
- `defeat captcha`

URL presence alone is not sufficient. The request must also express clone, source-reuse, capture, embed, route, or fidelity-verification intent.

## Evaluation Coverage

The golden prompt set in `evals/ai-selection/webembedding-golden-prompts.json` covers:

- positive URL clone, inspect, capture, embed, rebuild, and verification requests
- negative general coding, research, scraping, design, unsafe, and non-URL requests
- expected trigger decisions
- expected primary MCP tool for selected cases
- deterministic check descriptions for a future selection harness

The evals intentionally test selection behavior only. They do not execute clone jobs, mutate package metadata, or validate visual fidelity scores.

## Distribution Targets

### MCP Registry

- Server name: `io.github.jongko54/web-embedding`
- npm package: `web-embedding`
- Launch command for clients: `npx -y web-embedding@latest mcp`
- Remote intake endpoint: `https://webembedding-mcp.vercel.app/mcp`
- Metadata files: `server.json` and `package.json#mcpName`

Publishing order:

1. Publish `web-embedding` to npm.
2. Ensure `server.json.version`, `server.json.packages[0].version`, and `package.json.version` match.
3. Run `mcp-publisher login github` locally, or enable the `Publish MCP Registry` GitHub Action with OIDC.
4. Run `mcp-publisher publish`.

The registry metadata can include both transports: the local npm stdio package for full clone/capture workflows and the hosted Streamable HTTP intake endpoint for low-risk Apps SDK routing.

### Codex

The repository exposes a Codex marketplace entry at `.agents/plugins/marketplace.json`, pointing to `./bundle/source-first-clone`. The bundle's `.codex-plugin/plugin.json` declares the skill and `.mcp.json` server configuration.

Public Codex distribution through OpenAI currently flows from Apps SDK app submission after Developer Mode testing. The local plugin package remains useful for direct Codex installs and for review readiness.

### Claude Code

The repository exposes a Claude marketplace at `.claude-plugin/marketplace.json`. The plugin bundle includes `.claude-plugin/plugin.json`, `skills/`, and an inline MCP server config that uses `${CLAUDE_PLUGIN_ROOT}` so cached marketplace installs can locate `mcp/server.py`.

Suggested install commands after the repository is public:

```text
/plugin marketplace add jongko54/webEmbedding
/plugin install source-first-clone@webembedding
```

### OpenAI Apps SDK

The hosted Apps SDK intake endpoint is deployed at `https://webembedding-mcp.vercel.app/mcp`. It intentionally exposes only low-risk read-only/routing tools: `detect_runtime_capabilities`, `inspect_url`, `discover_embed_candidates`, `classify_clone_mode`, `generate_embed_snippet`, and `plan_reproduction_path`.

Full browser capture, filesystem output, queue workers, HAR replay, bounded rebuilds, and one-pass clone execution stay on the local stdio MCP package until a containerized hosted worker with auth, workspace isolation, quotas, and stronger review controls is ready.

Apps SDK public app submission still requires Developer Mode testing, app listing assets, precise tool metadata, privacy/terms review material, screenshots, test prompts, and dashboard review. Do not describe the hosted endpoint as an approved ChatGPT app until that review has passed.
