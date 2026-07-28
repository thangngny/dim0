/**
 * Pure markdown URL extraction for the canvas link overlay (feature E).
 *
 * Research nodes store their body as markdown on `node.content`. Source
 * links appear either as markdown links `[text](url)` or as bare
 * `https://…` URLs. This helper pulls them out, dedupes by URL, and
 * returns a display label + the clickable URL so the overlay can render
 * a clickable checklist below the node.
 *
 * Kept pure (no React, no store) so the parsing + dedupe logic is
 * unit-testable in isolation.
 */


export type LinkItem = {
  url: string
  /** Display text — the markdown link text, or the URL host for bare URLs. */
  label: string
}


/** Markdown link: `[text](url)` — text may not contain `]`. */
const MD_LINK_RE = /\[([^\]]*)\]\((https?:\/\/[^\s)]+)\)/g


/** Bare URL not already wrapped in a markdown link. */
const BARE_URL_RE = /(?<![\(\]])(?<![a-z0-9])https?:\/\/[^\s<>\)]+[a-z0-9]/gi


/** Trim trailing punctuation that's not part of the URL. */
const trimTrailingPunct = (url: string): string =>
  url.replace(/[.,;:!?\)\]]+$/u, "")


/** Best-effort host extraction for a bare-URL label. */
const hostOf = (url: string): string => {
  try {
    return new URL(url).hostname.replace(/^www\./u, "")
  } catch {
    return url
  }
}


/**
 * Extract clickable links from a markdown string. Markdown-link text
 * wins as the label; bare URLs fall back to their host. Dedupes by URL
 * (first occurrence wins) so a source listed twice renders once.
 *
 * Returns up to `limit` items (default 12) — a research node can cite
 * many sources but the overlay checklist shouldn't grow unbounded.
 */
export const extractLinks = (content: string, limit = 12): LinkItem[] => {
  if (!content) return []
  const seen = new Set<string>()
  const out: LinkItem[] = []

  // First pass: markdown links (they carry a human label).
  const mdMatches: Array<{ url: string; label: string }> = []
  for (const m of content.matchAll(MD_LINK_RE)) {
    const label = (m[1] ?? "").trim()
    const url = trimTrailingPunct(m[2] ?? "")
    if (!url) continue
    mdMatches.push({ url, label })
  }

  // Second pass: bare URLs, skipping spans already inside a markdown link.
  const bareMatches: Array<{ url: string; label: string }> = []
  // Build a set of markdown-link URL char ranges so bare-URL regex
  // doesn't double-count the URL inside `[t](url)`.
  const mdSpans: Array<[number, number]> = []
  MD_LINK_RE.lastIndex = 0
  let mm: RegExpExecArray | null
  while ((mm = MD_LINK_RE.exec(content)) !== null) {
    mdSpans.push([mm.index, mm.index + mm[0].length])
  }
  for (const m of content.matchAll(BARE_URL_RE)) {
    const start = m.index ?? 0
    const insideMd = mdSpans.some(([s, e]) => start >= s && start < e)
    if (insideMd) continue
    const url = trimTrailingPunct(m[0])
    if (!url) continue
    bareMatches.push({ url, label: hostOf(url) })
  }

  for (const { url, label } of [...mdMatches, ...bareMatches]) {
    if (seen.has(url)) continue
    seen.add(url)
    out.push({ url, label: label || hostOf(url) })
    if (out.length >= limit) break
  }
  return out
}