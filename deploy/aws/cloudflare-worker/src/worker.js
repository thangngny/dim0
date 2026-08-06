// dim0 reverse proxy on a Cloudflare Worker (workers.dev — stable, free URL).
//
// Why this exists: the EC2 origin (HTTP on a bare IP) is reached over the
// VNPT<->AWS-SG direct route, which flaps — users on VN intermittently can't
// connect. This Worker fronts the origin: browsers hit the Worker on a stable
// *.workers.dev HTTPS URL, and the Worker fetches the origin from inside
// Cloudflare's network (not from the user's ISP), so the flaky route is
// bypassed entirely. WebSocket (canvas collab) is proxied too.
//
// No domain needed — the workers.dev subdomain is stable per Cloudflare account.

// EC2 origin reached via a hostname that resolves to its public IP, not a bare
// IP literal: Cloudflare blocks Worker fetch() to bare IPs (error 1003,
// "Direct IP access not allowed"). nip.io maps <anything>.<ip>.nip.io → <ip>.
// Origin is an Elastic IP (stable across instance stop/start — an OOM restart
// previously changed the public IP and broke the proxy). EC2 EIP:
// 52.220.166.153 (ap-southeast-1), HTTP port 80.
const ORIGIN_HOST = "dim0.52.220.166.153.nip.io"

export default {
  async fetch(request) {
    const inUrl = new URL(request.url)
    const originUrl = `http://${ORIGIN_HOST}${inUrl.pathname}${inUrl.search}`

    // WebSocket upgrade: forward the original request so Upgrade/Connection
    // carry through. fetch() returns a Response whose .webSocket is the origin
    // side; returning it wires the client WebSocket to the origin end-to-end.
    const upgrade = request.headers.get("Upgrade") || ""
    if (upgrade.toLowerCase() === "websocket") {
      return fetch(originUrl, request)
    }

    // Plain HTTP: rebuild against the origin. manual redirect so the browser
    // follows Location, not the Worker (we rewrite the Location below to keep
    // the browser on the proxy host instead of jumping to the bare IP).
    const init = {
      method: request.method,
      headers: request.headers,
      redirect: "manual",
    }
    // GET/HEAD must not have a body.
    if (!["GET", "HEAD"].includes(request.method)) {
      init.body = request.body
    }

    const resp = await fetch(originUrl, init)

    // Rewrite any Location that points at the bare origin IP back to the
    // proxy origin, so redirects never send the browser off the proxy onto
    // the flaky direct-IP route.
    const outHeaders = new Headers(resp.headers)
    const loc = outHeaders.get("location")
    if (loc) {
      // Rewrite any Location pointing at the bare origin IP or its nip.io
      // hostname back to the proxy origin, so redirects never send the browser
      // off the proxy onto the flaky direct-IP route.
      outHeaders.set(
        "location",
        loc.replace(
          /https?:\/\/(?:52\.220\.166\.153|dim0\.52\.220\.166\.153\.nip\.io)(?::\d+)?/g,
          inUrl.origin,
        ),
      )
    }
    return new Response(resp.body, {
      status: resp.status,
      statusText: resp.statusText,
      headers: outHeaders,
    })
  },
}