---
name: "exact-clone-intake"
description: "Use when a user says a page, scene, or reference should be brought in exactly, copied as-is, kept the same, or embedded instead of merely approximated. Prefer original preview, embed, remix, export, or runtime source URLs before rebuilding."
---

# Exact Clone Intake

Treat requests like `그대로 가져와줘`, `완전 똑같이`, `same`, `exact`, `as-is`, and `clone this` as source-first clone requests.

## Workflow

1. Inspect the reference URL before writing code.
2. Use MCP tools in this order:
   - `inspect_url`
   - `discover_embed_candidates`
   - `trace_runtime_sources` when static HTML is not enough
   - `classify_clone_mode`
   - `generate_embed_snippet`
3. Prefer these outcomes in order:
   - original embed or preview
   - original remix, export, or source
   - rebuild with a clear note that it is not exact
4. Respect license and ownership signals. If the page is not clearly reusable, say so before cloning.

## Output rules

- Do not call a rebuild exact when it is only approximate.
- If an original preview or embed exists, use it instead of recreating the page.
- If the original source is private, explain what is missing and what permission or export is needed.

## Tool notes

- `trace_runtime_sources` is especially useful for pages that hide the real scene URL behind client-side rendering.
- `generate_embed_snippet` is for fast HTML or Next.js integration once the exact source URL is known.

