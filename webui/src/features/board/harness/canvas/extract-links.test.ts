import { describe, expect, it } from "vitest"
import { extractLinks } from "./extract-links"


describe("extractLinks", () => {
  it("returns nothing for empty content", () => {
    expect(extractLinks("")).toEqual([])
    expect(extractLinks("no links here at all")).toEqual([])
  })

  it("extracts markdown links with their text as label", () => {
    const r = extractLinks("See [Behance case](https://behance.net/example)")
    expect(r).toHaveLength(1)
    expect(r[0].url).toBe("https://behance.net/example")
    expect(r[0].label).toBe("Behance case")
  })

  it("extracts bare URLs and uses the host as label", () => {
    const r = extractLinks("Reference: https://www.youtube.com/watch?v=abc123")
    expect(r).toHaveLength(1)
    expect(r[0].url).toBe("https://www.youtube.com/watch?v=abc123")
    expect(r[0].label).toBe("youtube.com")
  })

  it("does not double-count a url that appears in a markdown link", () => {
    const r = extractLinks("Watch [the video](https://youtu.be/abc) now")
    expect(r).toHaveLength(1)
    expect(r[0].label).toBe("the video")
  })

  it("dedupes by URL across markdown + bare occurrences", () => {
    const r = extractLinks(
      "[source](https://example.com/a) and also https://example.com/a again",
    )
    expect(r).toHaveLength(1)
  })

  it("trims trailing punctuation off bare URLs", () => {
    const r = extractLinks("see https://example.com/page, then continue.")
    expect(r[0].url).toBe("https://example.com/page")
  })

  it("respects the limit argument", () => {
    const md = Array.from(
      { length: 20 },
      (_, i) => `[s${i}](https://example.com/${i})`,
    ).join(" ")
    expect(extractLinks(md, 5)).toHaveLength(5)
  })

  it("falls back to host label when markdown text is empty", () => {
    const r = extractLinks("[](https://example.com/x)")
    expect(r).toHaveLength(1)
    expect(r[0].label).toBe("example.com")
  })
})