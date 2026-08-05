// Receiver-side guard: incoming remote ops for preview-rendered custom
// types (folder, code-sandbox, widget, mini-app, document) must carry
// `style.autoFit = false`, so an agent node from an un-upgraded backend
// can't grow unbounded on the next edit. `sheet` is intentionally NOT
// covered: the sheet view renders the FULL markdown body (not a
// preview), so autoFit stays on to let long content grow instead of
// clipping — see commit b425e86.
import { describe, expect, it } from "vitest"
import {
  asNodeId,
  createCanvasStore,
  type CanvasStore,
  type Node,
  type Op,
  type OpBatch,
} from "@canvas-harness/core"
import { normalizeBatchAutoFit } from "./normalize-autofit"


/** Wrap ops in a minimal remote batch — the normalizer only reads `ops`. */
const remoteBatch = (ops: Op[]): OpBatch =>
  ({ ops } as unknown as OpBatch)


/** A bare node.add op, the way an agent-broadcast wire node arrives. */
const addOp = (type: string, style?: Record<string, unknown>): Op =>
  ({
    type: "node.add",
    node: {
      id: asNodeId("n-" + type),
      type,
      x: 0,
      y: 0,
      w: 560,
      h: 320,
      angle: 0,
      z: 0,
      groups: [],
      content: "",
      ...(style ? { style } : {}),
    } as unknown as Node,
  } as Op)


describe("normalizeBatchAutoFit", () => {
  const store: CanvasStore = createCanvasStore()


  it("forces autoFit:false on an incoming folder node.add with no style", () => {
    const batch = remoteBatch([addOp("folder")])
    normalizeBatchAutoFit(batch, store)
    const op = batch.ops[0]
    expect(op.type === "node.add" && op.node.style?.autoFit).toBe(false)
  })


  it("preserves other style fields while adding autoFit:false", () => {
    const batch = remoteBatch([addOp("folder", { strokeColor: "#abcdef" })])
    normalizeBatchAutoFit(batch, store)
    const op = batch.ops[0]
    if (op.type !== "node.add") throw new Error("expected node.add")
    expect(op.node.style?.autoFit).toBe(false)
    expect(op.node.style?.strokeColor).toBe("#abcdef")
  })


  it("leaves built-in primitive node.add untouched", () => {
    const batch = remoteBatch([addOp("rect")])
    normalizeBatchAutoFit(batch, store)
    const op = batch.ops[0]
    expect(op.type === "node.add" && op.node.style?.autoFit).toBeUndefined()
  })


  it("leaves an incoming sheet node.add untouched (autoFit on by design)", () => {
    // `sheet` renders the full markdown body, so it is NOT in the
    // autofit-disabled set (commit b425e86) — the normalizer must not
    // force autoFit:false on it.
    const batch = remoteBatch([addOp("sheet")])
    normalizeBatchAutoFit(batch, store)
    const op = batch.ops[0]
    expect(op.type === "node.add" && op.node.style?.autoFit).toBeUndefined()
  })


  it("covers every autofit-disabled custom type on node.add", () => {
    for (const type of ["folder", "code-sandbox", "widget", "mini-app", "document"]) {
      const batch = remoteBatch([addOp(type)])
      normalizeBatchAutoFit(batch, store)
      const op = batch.ops[0]
      expect(op.type === "node.add" && op.node.style?.autoFit).toBe(false)
    }
  })


  it("injects autoFit:false on a style-bearing node.update for an existing folder", () => {
    const s = createCanvasStore()
    const id = asNodeId("folder-1")
    s.addNode({
      id,
      type: "folder",
      x: 0,
      y: 0,
      w: 560,
      h: 320,
      angle: 0,
      z: 0,
      groups: [],
      content: "",
      style: { autoFit: false },
    } as unknown as Parameters<typeof s.addNode>[0])

    const batch = remoteBatch([
      { type: "node.update", id, patch: { style: { strokeColor: "#111" } } } as Op,
    ])
    normalizeBatchAutoFit(batch, s)
    const op = batch.ops[0]
    if (op.type !== "node.update") throw new Error("expected node.update")
    expect((op.patch as Partial<Node>).style?.autoFit).toBe(false)
  })


  it("leaves a node.update with no style patch untouched (no style key created)", () => {
    const s = createCanvasStore()
    const id = asNodeId("folder-2")
    s.addNode({
      id,
      type: "folder",
      x: 0,
      y: 0,
      w: 560,
      h: 320,
      angle: 0,
      z: 0,
      groups: [],
      content: "",
      style: { autoFit: false },
    } as unknown as Parameters<typeof s.addNode>[0])

    const batch = remoteBatch([
      { type: "node.update", id, patch: { x: 50 } } as Op,
    ])
    normalizeBatchAutoFit(batch, s)
    const op = batch.ops[0]
    if (op.type !== "node.update") throw new Error("expected node.update")
    expect((op.patch as Partial<Node>).style).toBeUndefined()
  })
})