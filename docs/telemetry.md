# Opt-in Telemetry Collection

webEmbedding telemetry is disabled by default. When a user opts in, the installer sends a small anonymous JSON command-completion event to the configured endpoint. The default endpoint is `https://vercel-telemetry-rho.vercel.app/api/events`. The payload is designed for usage counting only: it does not require tokens and should not include target URLs, local paths, captured HTML, screenshots, storage state, environment variables, API keys, or command output. It includes a coarse execution-context label such as `local`, `ci`, `github-actions`, `codex`, `claude-code`, or `cursor`; it does not send the underlying environment variable names or values.

## Run A Local Collector

Start the self-hosted collector:

```bash
python3 ./scripts/telemetry_collector.py --host 127.0.0.1 --port 8765 --out ./telemetry.jsonl
```

The collector accepts `POST /events` with an `application/json` body and appends one JSON object per line to the output file. It also exposes `GET /health`.

Point an opted-in install at the collector:

```bash
WEB_EMBEDDING_TELEMETRY=1 \
WEB_EMBEDDING_TELEMETRY_ENDPOINT=http://127.0.0.1:8765/events \
web-embedding doctor
```

You can also enable telemetry persistently:

```bash
web-embedding telemetry enable --endpoint http://127.0.0.1:8765/events
```

For local-only testing without an HTTP endpoint, write events directly to JSONL:

```bash
WEB_EMBEDDING_TELEMETRY=1 \
WEB_EMBEDDING_TELEMETRY_LOG=./telemetry.jsonl \
web-embedding doctor
```

## Analyze JSONL

Summarize the collected events:

```bash
python3 ./scripts/summarize_telemetry.py ./telemetry.jsonl
```

The analyzer prints total event count, install execution count, clone execution count, total command execution count, unique anonymous install IDs, command counts, and package version counts. Use `--json` for machine-readable output:

```bash
python3 ./scripts/summarize_telemetry.py ./telemetry.jsonl --json
```

The analyzer also accepts Vercel JSON logs from the deployed collector:

```bash
vercel logs https://vercel-telemetry-rho.vercel.app \
  --since 24h \
  --no-follow \
  --json \
  --expand \
  | python3 ./scripts/summarize_telemetry.py /dev/stdin --json
```

Collector records are logged with the `WEB_EMBEDDING_TELEMETRY` prefix.

## Privacy Notes

Telemetry is opt-in and anonymous. Users can disable it with:

```bash
web-embedding telemetry disable
```

The collector does not require authentication tokens or user identity fields. Store only the posted JSON event and a server-side `received_at` timestamp; do not add request IP addresses or headers to the JSONL file unless your deployment has a separate privacy review and retention policy.
