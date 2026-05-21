export default function handler(_request, response) {
  response.statusCode = 200;
  response.setHeader("Content-Type", "application/json; charset=utf-8");
  response.end(JSON.stringify({ ok: true, service: "webembedding-remote-mcp", version: "0.3.9" }));
}
