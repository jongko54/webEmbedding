const MAX_BODY_BYTES = 256 * 1024;

function sendJson(response, statusCode, payload) {
  response.statusCode = statusCode;
  response.setHeader("Content-Type", "application/json; charset=utf-8");
  response.end(JSON.stringify(payload));
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

function sanitizePayload(payload) {
  const properties = payload && typeof payload.properties === "object" ? payload.properties : {};
  return {
    received_at: new Date().toISOString(),
    schema_version: payload.schema_version,
    event: payload.event,
    anonymous_id: payload.anonymous_id,
    timestamp: payload.timestamp,
    app: payload.app,
    runtime: payload.runtime,
    properties
  };
}

export default async function handler(request, response) {
  if (request.method === "GET") {
    sendJson(response, 200, { ok: true });
    return;
  }

  if (request.method !== "POST") {
    response.setHeader("Allow", "GET, POST");
    sendJson(response, 405, { error: "method_not_allowed" });
    return;
  }

  const contentType = request.headers["content-type"] || "";
  if (!contentType.toLowerCase().includes("application/json")) {
    sendJson(response, 415, { error: "expected_json" });
    return;
  }

  let payload;
  try {
    const body = await readBody(request);
    if (!body.trim()) {
      sendJson(response, 400, { error: "empty_body" });
      return;
    }
    payload = JSON.parse(body);
  } catch (error) {
    const statusCode = Number.isInteger(error.statusCode) ? error.statusCode : 400;
    sendJson(response, statusCode, { error: error.message === "body_too_large" ? "body_too_large" : "invalid_json" });
    return;
  }

  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    sendJson(response, 400, { error: "expected_json_object" });
    return;
  }

  console.log(`WEB_EMBEDDING_TELEMETRY ${JSON.stringify(sanitizePayload(payload))}`);
  sendJson(response, 202, { ok: true });
}
