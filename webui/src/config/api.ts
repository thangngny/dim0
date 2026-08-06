declare global {
  interface Window {
    __APP_CONFIG__?: {
      apiBase?: string
      billingEnabled?: string
      /** Mini-app iframe runtime origin (set per-env by docker-entrypoint.sh). */
      miniAppOrigin?: string
      /** Host app origin — read by the iframe runtime, never the host. */
      hostOrigin?: string
    }
  }
}

const cfgBase =
  typeof window !== "undefined" ? window.__APP_CONFIG__?.apiBase : undefined

// Resolve the absolute API base URL.
//
// `apiBase: ""` in config.js is INTENTIONAL — it means "the API is served
// from the same origin as the SPA" (single-origin deploy, including behind a
// reverse proxy like the Cloudflare Worker). An empty string is falsy, so a
// naive `cfgBase || fallback` would skip it and fall back to the build-time
// VITE_API_URL (often `http://localhost:8899` from the dev .env), making the
// browser ping the user's own localhost and wrongly report "offline".
//
// Therefore: when config.js is present (cfgBase !== undefined), honour it
// exactly — an empty apiBase resolves to the current page origin so all
// `new URL(path, API_URL)` calls produce same-origin requests. Only when
// config.js is absent entirely (local dev without a config.js) do we fall
// back to the build-time / localhost default.
const sameOrigin = typeof window !== "undefined" ? window.location.origin : ""

export const API_URL =
  cfgBase !== undefined
    ? cfgBase || sameOrigin
    : import.meta.env.VITE_API_URL || "http://localhost:8888"
