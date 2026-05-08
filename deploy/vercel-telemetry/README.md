# webEmbedding Telemetry Collector

Minimal Vercel endpoint for opt-in webEmbedding telemetry.

- `GET /api/events` returns health status.
- `POST /api/events` accepts anonymous command-completion telemetry and writes a sanitized JSON record to Vercel logs with the `WEB_EMBEDDING_TELEMETRY` prefix.

The endpoint is intentionally lightweight. For long-term retention, forward the prefixed log records to a database or log drain.
