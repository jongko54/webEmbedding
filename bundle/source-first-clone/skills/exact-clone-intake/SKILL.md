---
name: "exact-clone-intake"
description: "Use when a user says a page, scene, or reference should be brought in exactly, copied as-is, kept the same, or embedded instead of merely approximated. Prefer original preview, embed, remix, export, or runtime source URLs before rebuilding."
---

# Exact Clone Intake

Treat requests like `그대로 가져와줘`, `완전 똑같이`, `same`, `exact`, `as-is`, and `clone this` as source-first clone requests.

## Workflow

1. Inspect the reference URL before writing code.
2. Use MCP tools in this order:
   - `clone_reference_url` when you want the full workflow in one pass
   - `detect_runtime_capabilities` when session-aware capture might be needed
   - `inspect_url`
   - `discover_embed_candidates`
   - `trace_runtime_sources` when static HTML is not enough
   - `classify_clone_mode`
   - `capture_reference_bundle` when exact reuse is still unclear
   - `build_rebuild_scaffold` when exact reuse is blocked and you need a bounded reconstruction starter
   - `build_reproduction_bundle` after capture when you need an exact reuse output package
   - `plan_reproduction_path`
   - `generate_embed_snippet` only when an actual embed path exists
   - `verify_fidelity_report` when comparing a reproduced result
3. Prefer these outcomes in order:
   - direct iframe reuse of the original page when frameable
   - original embed or preview
   - original remix, export, or source
   - rebuild with a clear note that it is not exact
4. Respect license and ownership signals. If the page is not clearly reusable, say so before cloning.

## Output rules

- Do not call a rebuild exact when it is only approximate.
- If an original preview or embed exists, use it instead of recreating the page.
- If the original source is private, explain what is missing and what permission or export is needed.

## Tool notes

- `trace_runtime_sources` is especially useful for pages that hide the real scene URL behind client-side rendering. Use `user_data_dir` or `storage_state_path` when authentication matters.
- `capture_reference_bundle` is a scaffold, not a full DOM/CSS capture engine. It now persists DOM, style, asset, and basic hover/focus interaction-state artifacts. Use `storage_state_output_path` when you need to persist session state for later runs.
- `build_rebuild_scaffold` is for frame-blocked or source-blocked references. Use it to output a starter HTML/CSS scaffold plus layout summary instead of pretending an exact clone exists.
- `clone_reference_url` is the fastest path when the user has pasted a link and wants the full source-first clone pipeline executed immediately.
- `plan_reproduction_path` is useful when the policy decision is clear but the build sequence is not.
- `generate_embed_snippet` is for fast HTML or Next.js integration once the exact source URL is known.
- `verify_fidelity_report` should be used honestly: it is bounded to the persisted artifacts and does not claim pixel-perfect equivalence.
