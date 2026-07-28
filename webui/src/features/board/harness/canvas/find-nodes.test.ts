import { describe, expect, it } from "vitest"
import type { Node } from "@canvas-harness/core"
import { searchNodes } from "./find-nodes"


/** Minimal Node factory — only the fields `searchNodes` reads. */
const mkNode = (
  id: string,
  content: string,
  labelMarkdown?: string,
): Node =>
  ({
    id,
    type: "rect",
    x: 0,
    y: 0,
    w: 100,
    h: 100,
    content,
    data: labelMarkdown ? { label: { markdown: labelMarkdown } } : {},
  }) as unknown as Node


describe("searchNodes", () => {
  it("returns nothing for an empty / whitespace query", () => {
    const nodes = [mkNode("1", "hello world")]
    expect(searchNodes(nodes, "")).toEqual([])
    expect(searchNodes(nodes, "   ")).toEqual([])
  })

  it("matches case-insensitively in body content", () => {
    const nodes = [mkNode("1", "Campaign BHNT gia đình"), mkNode("2", "other")]
    const r = searchNodes(nodes, "BHNT")
    expect(r).toHaveLength(1)
    expect(r[0].id).toBe("1")
    expect(r[0].match).toBe("content")
  })

  it("matches in label and ranks label hits above content hits", () => {
    const nodes = [
      mkNode("a", "mention tone here", "Tone & Mood"),
      mkNode("b", "Tone reference list"),
    ]
    const r = searchNodes(nodes, "tone")
    expect(r).toHaveLength(2)
    expect(r[0].id).toBe("a")
    expect(r[0].match).toBe("label")
    expect(r[1].match).toBe("content")
  })

  it("builds a snippet windowed around the match", () => {
    const body = "lorem ipsum dolor sit amet tone amet".repeat(4)
    const nodes = [mkNode("1", body)]
    const r = searchNodes(nodes, "tone")
    expect(r[0].snippet).toContain("tone")
    // Windowed — not the whole body.
    expect(r[0].snippet.length).toBeLessThan(body.length)
  })

  it("falls back to label text when body has no match", () => {
    const nodes = [mkNode("1", "short body", "Storyline")]
    const r = searchNodes(nodes, "story")
    expect(r[0].label).toBe("Storyline")
  })

  it("respects the limit argument", () => {
    const nodes = Array.from({ length: 30 }, (_, i) =>
      mkNode(String(i), `match ${i} tone`),
    )
    expect(searchNodes(nodes, "tone", 5)).toHaveLength(5)
  })

  it("uses a truncated body as the display label when no label is set", () => {
    const nodes = [mkNode("1", "Untitled body text here")]
    const r = searchNodes(nodes, "untitled")
    expect(r[0].label).toContain("Untitled body")
  })

  it("matches Vietnamese content without tone marks (diacritic-insensitive)", () => {
    // Body has accented "tóm tắt"; query is plain "tom tat".
    const nodes = [mkNode("1", "Đây là tóm tắt chiến dịch BHNT")]
    const r = searchNodes(nodes, "tom tat")
    expect(r).toHaveLength(1)
    expect(r[0].id).toBe("1")
  })

  it("matches accented query against plain content", () => {
    const nodes = [mkNode("1", "Tom tat chien dich")]
    const r = searchNodes(nodes, "tóm tắt")
    expect(r).toHaveLength(1)
  })

  it("matches the đ → d fold in both directions", () => {
    const nodes = [mkNode("1", "Đọc tài liệu")]
    expect(searchNodes(nodes, "doc")).toHaveLength(1)
    expect(searchNodes(nodes, "đọc")).toHaveLength(1)
  })
})