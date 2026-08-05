import { describe, expect, it } from "vitest"
import { asGroupId } from "@canvas-harness/core"
import type { Node } from "@canvas-harness/core"
import { isGroupCollapsed } from "./cluster-overlay-utils"


const mk = (id: string, groups: string[] = [], hidden = false): Node =>
  ({
    id,
    type: "rect",
    x: 0,
    y: 0,
    w: 100,
    h: 100,
    angle: 0,
    z: 0,
    groups: groups.map((g) => asGroupId(g)),
    hidden,
  }) as unknown as Node


describe("isGroupCollapsed", () => {
  it("is false when the group has no members", () => {
    expect(isGroupCollapsed(asGroupId("g1"), [])).toBe(false)
    expect(isGroupCollapsed(asGroupId("g1"), [mk("a", ["g2"])] as never)).toBe(false)
  })

  it("is true when every member is hidden", () => {
    const gid = asGroupId("g1")
    const nodes = [mk("a", ["g1"], true), mk("b", ["g1"], true)]
    expect(isGroupCollapsed(gid, nodes)).toBe(true)
  })

  it("is false when any member is still visible", () => {
    const gid = asGroupId("g1")
    const nodes = [mk("a", ["g1"], true), mk("b", ["g1"], false)]
    expect(isGroupCollapsed(gid, nodes)).toBe(false)
  })

  it("ignores nodes that aren't members of the group", () => {
    const gid = asGroupId("g1")
    const nodes = [
      mk("a", ["g1"], true),
      mk("b", ["g1"], true),
      mk("c", ["g2"], false), // different group, visible — shouldn't affect g1
    ]
    expect(isGroupCollapsed(gid, nodes)).toBe(true)
  })
})