# webEmbedding Remote MCP

Minimal hosted MCP endpoint for Apps SDK Developer Mode and submission readiness.

This deployment intentionally exposes only low-risk intake and routing tools:

- `detect_runtime_capabilities`
- `inspect_url`
- `discover_embed_candidates`
- `classify_clone_mode`
- `generate_embed_snippet`
- `plan_reproduction_path`

The full Playwright capture, filesystem queue, HAR replay, bounded rebuild, and clone workflow remains in the local stdio package:

```bash
npx -y web-embedding@latest mcp
```

## Endpoints

- `POST /api/mcp`: Streamable HTTP JSON-response MCP endpoint.
- `GET /api/mcp`: returns 405 because this deployment does not provide a server-to-client SSE stream.
- `GET /api/health`: health check.

## Environment

`WEB_EMBEDDING_MCP_ALLOWED_ORIGINS` can be a comma-separated allowlist. Requests without an `Origin` header are allowed for server-to-server MCP clients. Defaults include:

- `https://chatgpt.com`
- `https://chat.openai.com`
- `https://platform.openai.com`
- localhost development origins

## Local Smoke

```bash
cd deploy/vercel-mcp
npm run smoke
```

## Deploy

```bash
cd deploy/vercel-mcp
vercel --prod
```

After deployment, add the public `/api/mcp` URL in ChatGPT Developer Mode. Do not submit the app for public review until the prompts in `evals/apps-sdk/submission-test-prompts.json` have been tested against the deployed endpoint.
