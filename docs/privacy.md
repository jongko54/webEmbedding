# Privacy

`webEmbedding` is a local source-first clone, capture, rebuild, and fidelity verification workflow for AI coding agents.

## Local Data

Clone and capture commands may read the URLs, files, browser profile directories, Playwright storage state files, screenshots, HTML, CSS, assets, and network evidence that you explicitly provide to the command. These artifacts are processed locally and written to the output directory you choose.

Do not provide private browser profiles, storage state, credentials, or protected content unless you are authorized to use them for the clone or verification task.

## Telemetry

Anonymous telemetry is disabled by default. Interactive installs may ask whether to enable it. If enabled, `webEmbedding` sends command-completion metadata such as command name, package version, success or failure status, OS/runtime basics, and coarse option flags.

Telemetry does not send target URLs, local paths, captured HTML, screenshots, storage state, environment variables, API keys, command output, or generated clone artifacts.

Telemetry can be disabled with:

```bash
web-embedding telemetry disable
```

Environment controls are documented in `docs/telemetry.md`.

## Network Access

When you run capture, inspect, clone, benchmark, queue worker, or HAR replay commands, the tool may make network requests to URLs you provide and to their referenced resources. Review the target site's terms, license, and access requirements before capture or reproduction.
