#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, "..");
const installerPath = path.join(repoRoot, "python", "web_embedding", "installer.py");

const pythonCandidates = process.env.WEB_EMBEDDING_PYTHON
  ? [process.env.WEB_EMBEDDING_PYTHON]
  : ["python3", "python"];

for (const candidate of pythonCandidates) {
  const result = spawnSync(candidate, [installerPath, ...process.argv.slice(2)], {
    cwd: repoRoot,
    stdio: "inherit"
  });

  if (!result.error) {
    process.exit(result.status ?? 0);
  }

  if (result.error.code !== "ENOENT") {
    console.error(`Failed to launch ${candidate}: ${result.error.message}`);
    process.exit(1);
  }
}

console.error(
  "web-embedding requires Python 3. Set WEB_EMBEDDING_PYTHON or install python3, then try again."
);
process.exit(1);

