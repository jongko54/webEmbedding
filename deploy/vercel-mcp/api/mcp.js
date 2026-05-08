const SERVER_NAME = "webembedding-remote-intake";
const SERVER_VERSION = "0.3.7";
const RESOURCE_URI = "ui://webembedding/intake.html";
const MAX_BODY_BYTES = 512 * 1024;
const MAX_HTML_BYTES = 512 * 1024;
const DEFAULT_ALLOWED_ORIGINS = [
  "https://chatgpt.com",
  "https://chat.openai.com",
  "https://platform.openai.com",
  "http://localhost:3000",
  "http://localhost:5173",
  "http://127.0.0.1:3000",
  "http://127.0.0.1:5173"
];

function sendJson(response, statusCode, payload, extraHeaders = {}) {
  response.statusCode = statusCode;
  response.setHeader("Content-Type", "application/json; charset=utf-8");
  for (const [key, value] of Object.entries(extraHeaders)) {
    response.setHeader(key, value);
  }
  response.end(payload === undefined ? "" : JSON.stringify(payload));
}

function allowedOrigins() {
  const configured = process.env.WEB_EMBEDDING_MCP_ALLOWED_ORIGINS;
  if (!configured) {
    return DEFAULT_ALLOWED_ORIGINS;
  }
  return configured.split(",").map((origin) => origin.trim()).filter(Boolean);
}

function corsHeaders(request) {
  const origin = request.headers.origin;
  const allowed = allowedOrigins();
  const allowAny = allowed.includes("*");
  const headers = {
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Accept, Content-Type, MCP-Protocol-Version, Mcp-Session-Id",
    "Vary": "Origin"
  };
  if (origin && (allowAny || allowed.includes(origin))) {
    headers["Access-Control-Allow-Origin"] = origin;
  }
  return headers;
}

function originIsAllowed(request) {
  const origin = request.headers.origin;
  if (!origin) {
    return true;
  }
  const allowed = allowedOrigins();
  return allowed.includes("*") || allowed.includes(origin);
}

function readBody(request) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];

    request.on("data", (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(Object.assign(new Error("body_too_large"), { statusCode: 413 }));
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });

    request.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    request.on("error", reject);
  });
}

function textFromHtml(html, pattern) {
  const match = html.match(pattern);
  return match ? decodeHtml(match[1].replace(/\s+/g, " ").trim()) : null;
}

function decodeHtml(value) {
  return String(value)
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'");
}

function absoluteUrl(value, baseUrl) {
  try {
    return new URL(value, baseUrl).toString();
  } catch {
    return null;
  }
}

function assertHttpUrl(rawUrl) {
  let parsed;
  try {
    parsed = new URL(rawUrl);
  } catch {
    throw new Error("url must be an absolute HTTP or HTTPS URL");
  }
  if (!["http:", "https:"].includes(parsed.protocol)) {
    throw new Error("url must use http or https");
  }
  return parsed.toString();
}

function extractMeta(html) {
  const meta = {};
  const pattern = /<meta\b([^>]+)>/gi;
  for (const match of html.matchAll(pattern)) {
    const attrs = match[1];
    const keyMatch = attrs.match(/\b(?:name|property)=["']([^"']+)["']/i);
    const contentMatch = attrs.match(/\bcontent=["']([^"']*)["']/i);
    if (keyMatch && contentMatch) {
      meta[keyMatch[1].toLowerCase()] = decodeHtml(contentMatch[1]);
    }
  }
  return meta;
}

function classifyCandidate(url) {
  const lowered = url.toLowerCase();
  if (lowered.includes("youtube.com/embed") || lowered.includes("player.vimeo.com")) return "media-embed";
  if (lowered.includes("figma.com/embed")) return "figma-embed";
  if (lowered.includes("spline") && /preview|viewer|community|splinecode/.test(lowered)) return "spline-preview";
  if (lowered.includes("framer.") || lowered.includes("framer.app")) return "framer-source";
  if (lowered.includes("webflow.io")) return "webflow-source";
  if (/embed|preview|viewer|iframe/.test(lowered)) return "generic-embed";
  return "link";
}

function discoverCandidatesFromHtml(html, baseUrl) {
  const candidates = [];
  const seen = new Set();
  const attrPattern = /\b(?:href|src)=["']([^"']+)["']/gi;
  const urlPattern = /https?:\/\/[^\s"'<>]+/gi;

  for (const match of html.matchAll(attrPattern)) {
    const resolved = absoluteUrl(match[1], baseUrl);
    if (resolved) {
      addCandidate(candidates, seen, resolved, "attribute");
    }
  }
  for (const match of html.matchAll(urlPattern)) {
    addCandidate(candidates, seen, match[0], "inline");
  }
  return candidates
    .filter((candidate) => candidate.kind !== "link" || /embed|preview|viewer|iframe|framer|webflow|spline|figma|youtube|vimeo/i.test(candidate.url))
    .slice(0, 40);
}

function addCandidate(candidates, seen, url, source) {
  if (seen.has(url)) {
    return;
  }
  seen.add(url);
  candidates.push({ url, source, kind: classifyCandidate(url) });
}

async function fetchHtml(url, timeoutSeconds = 12) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutSeconds * 1000);
  try {
    const response = await fetch(url, {
      redirect: "follow",
      signal: controller.signal,
      headers: {
        "User-Agent": `webEmbeddingRemoteMcp/${SERVER_VERSION}`
      }
    });
    const contentType = response.headers.get("content-type") || "";
    const bytes = Buffer.from(await response.arrayBuffer()).subarray(0, MAX_HTML_BYTES);
    const html = contentType.includes("text") || contentType.includes("html") ? bytes.toString("utf8") : "";
    return { response, html, contentType, truncated: bytes.length >= MAX_HTML_BYTES };
  } finally {
    clearTimeout(timeout);
  }
}

async function inspectUrl(arguments_) {
  const url = assertHttpUrl(arguments_.url);
  const timeoutSeconds = Number(arguments_.timeout_seconds || 12);
  const { response, html, contentType, truncated } = await fetchHtml(url, timeoutSeconds);
  const meta = extractMeta(html);
  const csp = response.headers.get("content-security-policy") || "";
  const xFrameOptions = response.headers.get("x-frame-options") || "";
  const frameAncestors = csp.match(/frame-ancestors\s+([^;]+)/i)?.[1] || null;
  const candidateUrls = discoverCandidatesFromHtml(html, response.url);
  const frameBlocked = Boolean(xFrameOptions) || Boolean(frameAncestors && !/['"]?\*['"]?/.test(frameAncestors));
  return {
    url,
    final_url: response.url,
    status: response.status,
    ok: response.ok,
    title: textFromHtml(html, /<title[^>]*>([\s\S]*?)<\/title>/i) || meta["og:title"] || null,
    description: meta.description || meta["og:description"] || null,
    content_type: contentType,
    truncated,
    frame_policy: {
      x_frame_options: xFrameOptions || null,
      frame_ancestors: frameAncestors,
      frame_blocked: frameBlocked
    },
    source_signals: {
      candidate_count: candidateUrls.length,
      candidate_kinds: [...new Set(candidateUrls.map((candidate) => candidate.kind))]
    },
    candidate_urls: candidateUrls.slice(0, 12),
    hosted_limitations: [
      "Hosted intake does not run Playwright, read local files, use browser profiles, or persist capture artifacts.",
      "Use the local stdio MCP package for full capture, rebuild, queue, HAR replay, and clone workflows."
    ]
  };
}

async function discoverEmbedCandidates(arguments_) {
  const inspection = await inspectUrl(arguments_);
  return {
    url: inspection.url,
    final_url: inspection.final_url,
    candidates: inspection.candidate_urls,
    candidate_count: inspection.source_signals.candidate_count,
    hosted_limitations: inspection.hosted_limitations
  };
}

function detectRuntimeCapabilities() {
  return {
    hosted_remote_mcp: true,
    runtime_trace: false,
    playwright: false,
    chrome: false,
    filesystem_output: false,
    local_stdio_available: false,
    local_stdio_command: "npx -y web-embedding@latest mcp",
    notes: [
      "This hosted endpoint is an Apps SDK intake surface.",
      "Run the local stdio MCP package for browser capture, filesystem artifacts, queues, HAR replay, and full clone execution."
    ]
  };
}

function classifyCloneMode(arguments_) {
  const licenseText = String(arguments_.license_text || "").toLowerCase();
  const sourceSignals = Array.isArray(arguments_.source_signals) ? arguments_.source_signals : [];
  const candidates = Array.isArray(arguments_.candidates) ? arguments_.candidates : [];
  const exactRequested = arguments_.exact_requested !== false;
  const blocked =
    /all rights reserved|do not copy|no reproduction|forbidden|not allowed|no permission/.test(licenseText) ||
    sourceSignals.some((signal) => /no-permission|private|paywall|captcha|bypass/i.test(String(signal)));

  if (blocked) {
    return {
      mode: "blocked",
      reason: "Permission, license, access-control, or bypass signals require refusal or manual review before capture or reproduction.",
      next_action: "Ask the user for proof of authorization, reusable source/export, or a permitted reference."
    };
  }

  const reusable = candidates.find((candidate) => /embed|preview|source|viewer/.test(String(candidate.kind || candidate.url || "")));
  if (reusable) {
    return {
      mode: "exact-or-embed-reuse",
      reason: "A likely embed, preview, viewer, or source candidate is present.",
      preferred_candidate: reusable,
      next_action: "Verify frame/source permission before generating an embed snippet."
    };
  }

  return {
    mode: exactRequested ? "needs-capture-or-bounded-rebuild" : "approximate-reference-ok",
    reason: "No trusted reusable source was supplied to the hosted intake endpoint.",
    next_action: exactRequested
      ? "Use the local stdio MCP package for browser evidence capture and bounded rebuild verification."
      : "Proceed with an approximate design reference only if license and permission allow it."
  };
}

function generateEmbedSnippet(arguments_) {
  const url = assertHttpUrl(arguments_.url);
  const title = arguments_.title || "Embedded reference";
  const framework = arguments_.framework === "html" ? "html" : "nextjs";
  const snippet =
    framework === "html"
      ? `<iframe src="${url}" title="${title}" style="display:block;width:100%;height:100vh;border:0" allow="fullscreen"></iframe>`
      : [
          "<iframe",
          `  src="${url}"`,
          `  title="${title}"`,
          '  allow="fullscreen"',
          '  style={{ display: "block", width: "100%", height: "100vh", border: 0 }}',
          "/>"
        ].join("\n");
  return {
    framework,
    snippet,
    assumptions: [
      "The target remains frameable at runtime.",
      "The user is authorized to embed or reuse this URL.",
      "If frame policy blocks embedding, use local capture and bounded rebuild instead."
    ]
  };
}

function planReproductionPath(arguments_) {
  const classification = classifyCloneMode(arguments_);
  const steps = [
    "Inspect the URL and frame/source policy before writing code.",
    "Prefer direct iframe, original embed, preview, remix, export, or source route when permission allows.",
    "If exact reuse is unavailable, run local browser capture with web-embedding's stdio MCP package.",
    "Use bounded rebuild artifacts and a fidelity report; do not describe bounded rebuild output as original source reuse."
  ];
  return {
    classification,
    steps,
    hosted_next_action: classification.next_action,
    local_mcp_command: "npx -y web-embedding@latest mcp"
  };
}

const TOOL_HANDLERS = {
  detect_runtime_capabilities: detectRuntimeCapabilities,
  inspect_url: inspectUrl,
  discover_embed_candidates: discoverEmbedCandidates,
  classify_clone_mode: classifyCloneMode,
  generate_embed_snippet: generateEmbedSnippet,
  plan_reproduction_path: planReproductionPath
};

const TOOLS = [
  {
    name: "detect_runtime_capabilities",
    title: "Detect Hosted Runtime Capabilities",
    description: "Report the hosted Apps SDK intake runtime capabilities and explain when the local stdio MCP is required.",
    inputSchema: { type: "object", properties: {} },
    outputSchema: { type: "object", additionalProperties: true },
    annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false, idempotentHint: true }
  },
  {
    name: "inspect_url",
    title: "Inspect URL Reuse Route",
    description: "Fetch a public or user-authorized URL and inspect title, metadata, frame policy, and likely source/embed candidates. Does not capture screenshots or persist artifacts.",
    inputSchema: {
      type: "object",
      properties: {
        url: { type: "string" },
        timeout_seconds: { type: "integer", minimum: 1, maximum: 30 }
      },
      required: ["url"]
    },
    outputSchema: { type: "object", additionalProperties: true },
    annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: true, idempotentHint: true },
    _meta: {
      ui: { resourceUri: RESOURCE_URI },
      "openai/toolInvocation/invoking": "Inspecting URL",
      "openai/toolInvocation/invoked": "Inspection ready"
    }
  },
  {
    name: "discover_embed_candidates",
    title: "Discover Embed Candidates",
    description: "Extract likely embed, preview, viewer, remix, and source URLs from a public or user-authorized page.",
    inputSchema: {
      type: "object",
      properties: {
        url: { type: "string" },
        timeout_seconds: { type: "integer", minimum: 1, maximum: 30 }
      },
      required: ["url"]
    },
    outputSchema: { type: "object", additionalProperties: true },
    annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: true, idempotentHint: true },
    _meta: {
      ui: { resourceUri: RESOURCE_URI },
      "openai/toolInvocation/invoking": "Finding embed routes",
      "openai/toolInvocation/invoked": "Embed routes ready"
    }
  },
  {
    name: "classify_clone_mode",
    title: "Classify Clone Mode",
    description: "Decide whether a reference should be embedded, sourced, locally captured, bounded-rebuilt, or blocked before reproduction.",
    inputSchema: {
      type: "object",
      properties: {
        exact_requested: { type: "boolean" },
        license_text: { type: "string" },
        candidates: { type: "array", items: { type: "object" } },
        source_signals: { type: "array", items: { type: "string" } },
        site_profile: { type: "object" }
      }
    },
    outputSchema: { type: "object", additionalProperties: true },
    annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false, idempotentHint: true }
  },
  {
    name: "generate_embed_snippet",
    title: "Generate Embed Snippet",
    description: "Generate an iframe snippet for a known frameable and authorized URL. Does not verify frameability by itself.",
    inputSchema: {
      type: "object",
      properties: {
        url: { type: "string" },
        title: { type: "string" },
        framework: { type: "string", enum: ["html", "nextjs"] }
      },
      required: ["url"]
    },
    outputSchema: { type: "object", additionalProperties: true },
    annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false, idempotentHint: true }
  },
  {
    name: "plan_reproduction_path",
    title: "Plan Reproduction Path",
    description: "Create a source-first plan that separates exact embed/source reuse from local capture and bounded rebuild work.",
    inputSchema: {
      type: "object",
      properties: {
        exact_requested: { type: "boolean" },
        license_text: { type: "string" },
        candidates: { type: "array", items: { type: "object" } },
        source_signals: { type: "array", items: { type: "string" } },
        site_profile: { type: "object" },
        capture_bundle: { type: "object" }
      }
    },
    outputSchema: { type: "object", additionalProperties: true },
    annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false, idempotentHint: true }
  }
];

function appResource() {
  return {
    uri: RESOURCE_URI,
    mimeType: "text/html",
    text: `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>webEmbedding Intake</title>
    <style>
      body { color: #111827; font: 14px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; padding: 18px; }
      h1 { font-size: 18px; margin: 0 0 8px; }
      p { margin: 0 0 10px; }
      code { background: #f3f4f6; border-radius: 4px; padding: 2px 4px; }
    </style>
  </head>
  <body>
    <h1>webEmbedding</h1>
    <p>Source-first URL intake for embed/source routing. Full browser capture and bounded rebuilds run through the local stdio MCP package.</p>
    <p><code>npx -y web-embedding@latest mcp</code></p>
  </body>
</html>`,
    _meta: {
      ui: {
        domain: "https://webembedding-mcp.vercel.app",
        csp: {
          connectDomains: ["https://webembedding-mcp.vercel.app"],
          resourceDomains: ["https://webembedding-mcp.vercel.app"]
        }
      },
      "openai/widgetDescription": "Shows webEmbedding source-first URL intake status and local MCP handoff guidance.",
      "openai/widgetCSP": {
        connect_domains: ["https://webembedding-mcp.vercel.app"],
        resource_domains: ["https://webembedding-mcp.vercel.app"]
      },
      "openai/widgetDomain": "https://webembedding-mcp.vercel.app"
    }
  };
}

async function callTool(params) {
  const name = params?.name;
  const arguments_ = params?.arguments && typeof params.arguments === "object" ? params.arguments : {};
  const handler = TOOL_HANDLERS[name];
  if (!handler) {
    throw Object.assign(new Error(`Unknown tool: ${name}`), { code: -32602 });
  }
  const result = await handler(arguments_);
  return {
    structuredContent: result,
    content: [{ type: "text", text: JSON.stringify(result, null, 2) }]
  };
}

async function handleRequest(message) {
  if (!message || typeof message !== "object" || Array.isArray(message)) {
    return { jsonrpc: "2.0", id: null, error: { code: -32600, message: "Invalid request" } };
  }

  const { id, method, params } = message;
  if (!method || typeof method !== "string") {
    return { jsonrpc: "2.0", id: id ?? null, error: { code: -32600, message: "Invalid method" } };
  }
  if (id === undefined && method.startsWith("notifications/")) {
    return null;
  }

  try {
    if (method === "initialize") {
      return {
        jsonrpc: "2.0",
        id,
        result: {
          protocolVersion: "2025-03-26",
          capabilities: { tools: {}, resources: {} },
          serverInfo: { name: SERVER_NAME, version: SERVER_VERSION }
        }
      };
    }
    if (method === "ping") {
      return { jsonrpc: "2.0", id, result: {} };
    }
    if (method === "tools/list") {
      return { jsonrpc: "2.0", id, result: { tools: TOOLS } };
    }
    if (method === "tools/call") {
      return { jsonrpc: "2.0", id, result: await callTool(params) };
    }
    if (method === "resources/list") {
      return {
        jsonrpc: "2.0",
        id,
        result: {
          resources: [
            {
              uri: RESOURCE_URI,
              name: "webembedding-intake",
              title: "webEmbedding Intake",
              description: "Small status component for source-first URL intake.",
              mimeType: "text/html"
            }
          ]
        }
      };
    }
    if (method === "resources/read") {
      if (params?.uri !== RESOURCE_URI) {
        return { jsonrpc: "2.0", id, error: { code: -32602, message: "Unknown resource URI" } };
      }
      return { jsonrpc: "2.0", id, result: { contents: [appResource()] } };
    }
    if (method === "prompts/list") {
      return { jsonrpc: "2.0", id, result: { prompts: [] } };
    }
    return { jsonrpc: "2.0", id, error: { code: -32601, message: `Unknown method: ${method}` } };
  } catch (error) {
    return {
      jsonrpc: "2.0",
      id,
      error: {
        code: Number.isInteger(error.code) ? error.code : -32000,
        message: error.message || "Tool execution failed"
      }
    };
  }
}

export async function handleMcpPayload(payload) {
  if (Array.isArray(payload)) {
    const responses = [];
    for (const message of payload) {
      const response = await handleRequest(message);
      if (response) {
        responses.push(response);
      }
    }
    return responses.length ? responses : undefined;
  }
  return await handleRequest(payload);
}

export default async function handler(request, response) {
  const headers = corsHeaders(request);

  if (!originIsAllowed(request)) {
    sendJson(response, 403, { error: "origin_not_allowed" }, headers);
    return;
  }

  if (request.method === "OPTIONS") {
    sendJson(response, 204, undefined, headers);
    return;
  }

  if (request.method === "GET") {
    response.setHeader("Allow", "POST, OPTIONS");
    sendJson(response, 405, { error: "sse_stream_not_supported", endpoint: "/api/mcp" }, headers);
    return;
  }

  if (request.method !== "POST") {
    response.setHeader("Allow", "GET, POST, OPTIONS");
    sendJson(response, 405, { error: "method_not_allowed" }, headers);
    return;
  }

  const contentType = request.headers["content-type"] || "";
  if (!contentType.toLowerCase().includes("application/json")) {
    sendJson(response, 415, { error: "expected_json" }, headers);
    return;
  }

  try {
    const body = await readBody(request);
    const payload = JSON.parse(body || "null");
    const result = await handleMcpPayload(payload);
    if (result === undefined || result === null) {
      sendJson(response, 202, undefined, headers);
      return;
    }
    sendJson(response, 200, result, headers);
  } catch (error) {
    const statusCode = Number.isInteger(error.statusCode) ? error.statusCode : 400;
    sendJson(response, statusCode, { error: error.message === "body_too_large" ? "body_too_large" : "invalid_request" }, headers);
  }
}
