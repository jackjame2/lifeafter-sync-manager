// Minimal reverse proxy for the GameSync license server.
//
// Purpose: give mainland-China clients a China-reachable front door (deployed on
// Zeabur's Hong Kong region) instead of hitting Cloudflare's IPs directly, which
// the GFW throttles/blocks. Every request is forwarded verbatim to the existing
// Cloudflare Worker, so the Worker + D1 database + admin panel are unchanged.
//
//   client (CN) --> this proxy (Zeabur HK) --> https://<UPSTREAM_HOST> (Cloudflare Worker)
//
// Zeabur terminates TLS at its edge and forwards plain HTTP to this app on $PORT.

const http = require('http');
const https = require('https');

const UPSTREAM = process.env.UPSTREAM_HOST || 'license-server.cdjjdfkdjd.workers.dev';
const PORT = process.env.PORT || 8080;

const server = http.createServer((req, res) => {
  // Force the Host header to the Worker hostname so Cloudflare routes correctly.
  const headers = { ...req.headers, host: UPSTREAM };

  const upReq = https.request(
    { hostname: UPSTREAM, port: 443, path: req.url, method: req.method, headers },
    (upRes) => {
      res.writeHead(upRes.statusCode || 502, upRes.headers);
      upRes.pipe(res);
    }
  );

  upReq.setTimeout(20000, () => upReq.destroy(new Error('upstream timeout')));
  upReq.on('error', (err) => {
    if (!res.headersSent) res.writeHead(502, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ error: 'proxy_upstream_error', detail: String((err && err.message) || err) }));
  });

  req.pipe(upReq);
});

server.listen(PORT, () => {
  console.log(`license-proxy listening on :${PORT} -> https://${UPSTREAM}`);
});
