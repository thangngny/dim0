import type { Node } from "@canvas-harness/core"


/**
 * Pure node-search helpers for the "Find on board" dialog. Kept out of
 * the React component so the matching + snippet logic is unit-testable
 * without mounting the canvas.
 *
 * A node matches when the query (case-insensitive, diacritic-insensitive)
 * appears in its label (`data.label.markdown`) or body (`node.content`).
 * Label hits rank first so titling a node remains the fastest way to
 * find it. Vietnamese diacritics are stripped for matching so "tone"
 * matches "tone" AND "töne"/"tón" — a user typing without tone marks
 * still finds accented content.
 */


export type NodeSearchResult = {
  id: string
  /** Display title — falls back to a truncated body or "Untitled". */
  label: string
  /** Short context window around the first match in the body. */
  snippet: string
  /** Where the match was found; label hits rank above content hits. */
  match: "label" | "content"
}


/**
 * Strip Vietnamese tone marks + diacritics for accent-insensitive
 * matching. "đ" → "d", "á à ả ã ạ â ấ…" → "a", etc. Also lowercases.
 * Uses NFD decomposition + a Vietnamese-specific đ pass.
 */
const normalize = (s: string): string =>
  s
    .toLowerCase()
    .replace(/đ/g, "d")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")


/** Pull the two searchable text fields off a canvas-harness Node. */
const nodeText = (n: Node): { label: string; body: string } => {
  const data = (n.data ?? {}) as {
    label?: { markdown?: string }
  }
  const label = data.label?.markdown ?? ""
  const body = typeof n.content === "string" ? n.content : ""
  return { label, body }
}


const truncate = (s: string, max: number): string =>
  s.length <= max ? s : `${s.slice(0, max - 1)}…`


/**
 * Build a short snippet around the first occurrence of `needle` in
 * `haystack`. Matching is diacritic-insensitive; the snippet preserves
 * the original (accented) text so it reads naturally. Returns "" when
 * there's no match or the body is empty.
 */
const makeSnippet = (haystack: string, needle: string, radius = 28): string => {
  if (!haystack) return ""
  const idx = normalize(haystack).indexOf(normalize(needle))
  if (idx < 0) return ""
  const start = Math.max(0, idx - radius)
  const end = Math.min(haystack.length, idx + needle.length + radius)
  const prefix = start > 0 ? "…" : ""
  const suffix = end < haystack.length ? "…" : ""
  // Collapse newlines so the snippet reads as one line in the result list.
  return `${prefix}${haystack.slice(start, end).replace(/\s+/g, " ")}${suffix}`
}


/**
 * Search a list of nodes by query. Returns up to `limit` results sorted
 * label-hits first, then by first-match position in the body. Empty /
 * whitespace-only query returns [].
 */
export const searchNodes = (
  nodes: Node[],
  query: string,
  limit = 20,
): NodeSearchResult[] => {
  const q = normalize(query.trim())
  if (!q) return []
  const labelHits: NodeSearchResult[] = []
  const contentHits: NodeSearchResult[] = []
  for (const n of nodes) {
    const { label, body } = nodeText(n)
    const labelHit = normalize(label).includes(q)
    const contentHit = normalize(body).includes(q)
    if (!labelHit && !contentHit) continue
    const snippet = makeSnippet(body, query.trim())
    const displayLabel = label || truncate(body.replace(/\s+/g, " "), 48) || "Untitled"
    const result: NodeSearchResult = {
      id: String(n.id),
      label: displayLabel,
      snippet: snippet || truncate(label, 60),
      match: labelHit ? "label" : "content",
    }
    if (labelHit) labelHits.push(result)
    else contentHits.push(result)
    if (labelHits.length + contentHits.length >= limit) break
  }
  return [...labelHits, ...contentHits]
}