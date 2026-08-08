import index from "./index.html";

const API = "http://localhost:8000";

// "/api/*" must be its own routes entry, not a fallback inside `fetch`:
// Bun.serve matches `routes` by specificity before ever calling `fetch`, so
// a "/*" -> index entry alone would swallow every request, /api/* included,
// and `fetch` would never run.
Bun.serve({
  port: 3000,
  routes: {
    "/*": index,
    "/api/*": (request: Request) => {
      const url = new URL(request.url);
      return fetch(new Request(`${API}${url.pathname}${url.search}`, request));
    },
  },
});
console.log("dev server on http://localhost:3000 (API → :8000)");
