// Bootstraps Swagger UI on /docs. FastAPI's built-in page does this from an inline script,
// which the CSP (no 'unsafe-inline') blocks. Deferred after swagger-ui-bundle.js.
document.addEventListener("DOMContentLoaded", () => {
  SwaggerUIBundle({ url: "/openapi.json", dom_id: "#swagger-ui" });
});
