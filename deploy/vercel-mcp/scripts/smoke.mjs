import assert from "node:assert/strict";
import { handleMcpPayload } from "../api/mcp.js";

const init = await handleMcpPayload({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} });
assert.equal(init.result.serverInfo.name, "webembedding-remote-intake");

const listed = await handleMcpPayload({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} });
const tools = listed.result.tools.map((tool) => tool.name);
assert.ok(tools.includes("inspect_url"));
assert.ok(tools.includes("generate_embed_snippet"));
assert.ok(!tools.includes("clone_reference_url"));

const inspection = await handleMcpPayload({
  jsonrpc: "2.0",
  id: 21,
  method: "tools/call",
  params: {
    name: "inspect_url",
    arguments: { url: "https://example.com", timeout_seconds: 10 }
  }
});
assert.ok(inspection.result.structuredContent.readiness);
assert.match(inspection.result.structuredContent.readiness_report, /Local command:/);
assert.equal(inspection.result.structuredContent.readiness.status, "exact-embed-possible");
assert.ok(inspection.result.structuredContent.candidate_urls.some((candidate) => candidate.kind === "direct-iframe"));

const directIframeClassification = await handleMcpPayload({
  jsonrpc: "2.0",
  id: 22,
  method: "tools/call",
  params: {
    name: "classify_clone_mode",
    arguments: {
      exact_requested: true,
      license_text: "MIT",
      candidates: [{ kind: "direct-iframe", url: "https://example.com/" }],
      source_signals: ["public"],
      site_profile: { frame_policy: { embeddable: true } }
    }
  }
});
assert.equal(directIframeClassification.result.structuredContent.mode, "exact-or-embed-reuse");

const snippet = await handleMcpPayload({
  jsonrpc: "2.0",
  id: 3,
  method: "tools/call",
  params: {
    name: "generate_embed_snippet",
    arguments: { url: "https://example.com", framework: "html" }
  }
});
assert.match(snippet.result.structuredContent.snippet, /iframe/);
assert.equal(snippet.result.content[0].type, "text");

const resource = await handleMcpPayload({ jsonrpc: "2.0", id: 4, method: "resources/read", params: { uri: "ui://webembedding/intake.html" } });
assert.equal(resource.result.contents[0]._meta.ui.domain, "https://webembedding-jongkos-mcp.vercel.app");

console.log("Remote MCP smoke passed.");
