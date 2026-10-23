// Storage's existing HTTP Range contract, with deterministic fixture bytes.
const http = require('http');
const payload = Buffer.alloc(1048832);
for (let i = 0; i < payload.length; i++) payload[i] = i % 256;
http.createServer((req, res) => {
  if (!['/artifact', '/wrong-range'].includes(req.url.split('?')[0])) {
    res.writeHead(501).end();
    return;
  }
  if (req.headers.authorization) {
    res.writeHead(400).end('Storage must not receive the API token');
    return;
  }
  const match = /^bytes=(\d+)-(\d+)$/.exec(req.headers.range || '');
  if (!match) {
    res.writeHead(416).end();
    return;
  }
  const start = Number(match[1]), end = Number(match[2]);
  if (start > end || end >= payload.length) {
    res.writeHead(416).end();
    return;
  }
  const rangeStart = req.url.startsWith('/wrong-range') ? start + 1 : start;
  res.writeHead(206, {
    'Content-Range': `bytes ${rangeStart}-${end}/${payload.length}`,
    'Content-Length': end - start + 1,
    'Content-Type': 'application/octet-stream',
  });
  res.end(payload.subarray(start, end + 1));
}).listen(process.env.PORT);
