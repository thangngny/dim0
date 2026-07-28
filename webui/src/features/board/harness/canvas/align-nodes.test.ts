import { describe, expect, it } from "vitest"
import type { Node } from "@canvas-harness/core"
import { alignNodes, minNodesFor } from "./align-nodes"


const mk = (id: string, x: number, y: number, w = 100, h = 60): Node =>
  ({ id, type: "rect", x, y, w, h, angle: 0, z: 0, groups: [] }) as unknown as Node


describe("minNodesFor", () => {
  it("requires 2 for align modes and 3 for distribute", () => {
    expect(minNodesFor("left")).toBe(2)
    expect(minNodesFor("middle-v")).toBe(2)
    expect(minNodesFor("distribute-h")).toBe(3)
    expect(minNodesFor("distribute-v")).toBe(3)
  })
})


describe("alignNodes", () => {
  it("returns empty when fewer than the minimum are given", () => {
    expect(alignNodes([mk("a", 0, 0)], "left").size).toBe(0)
    expect(alignNodes([mk("a", 0, 0), mk("b", 1, 1)], "distribute-h").size).toBe(0)
  })

  it("aligns left edges to the minimum x", () => {
    const nodes = [mk("a", 10, 0), mk("b", 200, 0), mk("c", 60, 0)]
    const r = alignNodes(nodes, "left")
    expect(r.get("a")?.x).toBe(10)
    expect(r.get("b")?.x).toBe(10)
    expect(r.get("c")?.x).toBe(10)
    // y untouched.
    expect(r.get("a")?.y).toBeUndefined()
  })

  it("aligns right edges to the maximum right", () => {
    const nodes = [mk("a", 10, 0, 100), mk("b", 200, 0, 50)]
    const r = alignNodes(nodes, "right")
    // max right = 200 + 50 = 250
    expect(r.get("a")?.x).toBe(250 - 100)
    expect(r.get("b")?.x).toBe(250 - 50)
  })

  it("aligns horizontal centers to the bbox center", () => {
    const nodes = [mk("a", 0, 0, 100), mk("b", 300, 0, 100)]
    // bbox: minX=0, maxRight=400, center=200
    const r = alignNodes(nodes, "center-h")
    expect(r.get("a")?.x).toBe(200 - 50)
    expect(r.get("b")?.x).toBe(200 - 50)
  })

  it("aligns top edges to the minimum y", () => {
    const nodes = [mk("a", 0, 100), mk("b", 0, 30)]
    const r = alignNodes(nodes, "top")
    expect(r.get("a")?.y).toBe(30)
    expect(r.get("b")?.y).toBe(30)
    expect(r.get("a")?.x).toBeUndefined()
  })

  it("aligns bottom edges to the maximum bottom", () => {
    const nodes = [mk("a", 0, 10, 100, 60), mk("b", 0, 200, 100, 40)]
    // max bottom = 200 + 40 = 240
    const r = alignNodes(nodes, "bottom")
    expect(r.get("a")?.y).toBe(240 - 60)
    expect(r.get("b")?.y).toBe(240 - 40)
  })

  it("aligns vertical centers to the bbox center", () => {
    const nodes = [mk("a", 0, 0, 100, 60), mk("b", 0, 300, 100, 60)]
    // minY=0, maxBottom=360, center=180
    const r = alignNodes(nodes, "middle-v")
    expect(r.get("a")?.y).toBe(180 - 30)
    expect(r.get("b")?.y).toBe(180 - 30)
  })

  it("distributes horizontally with even left-edge spacing", () => {
    const nodes = [mk("a", 0, 0), mk("b", 100, 0), mk("c", 500, 0)]
    const r = alignNodes(nodes, "distribute-h")
    // first=0, last=500, step=250
    expect(r.get("a")?.x).toBe(0)
    expect(r.get("b")?.x).toBe(250)
    expect(r.get("c")?.x).toBe(500)
  })

  it("distributes vertically with even top-edge spacing", () => {
    const nodes = [mk("a", 0, 0), mk("b", 0, 100), mk("c", 0, 500)]
    const r = alignNodes(nodes, "distribute-v")
    expect(r.get("a")?.y).toBe(0)
    expect(r.get("b")?.y).toBe(250)
    expect(r.get("c")?.y).toBe(500)
  })
})