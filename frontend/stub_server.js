// TEMPORAL: sirve una página mínima hasta que exista Next.js.
require("http")
  .createServer((_req, res) => {
    res.setHeader("Content-Type", "text/html; charset=utf-8");
    res.end("<h1>Sistema de metrología (en construcción)</h1>");
  })
  .listen(3000, "0.0.0.0");
