import { describe, expect, it } from "vitest"
import type { ToolOutput } from "@/features/agent/types/tool-outputs"
import {
  type ChangeNoteKindOutput,
  type DeleteSubtreeOutput,
  type MergeNotesOutput,
  type RelayoutOutput,
  type ReparentNoteOutput,
  type SplitNoteOutput,
} from "@/features/agent/types/tool-outputs"


describe("struct output types", () => {
  it("declares the six new output type literals", async () => {
    const mod = await import("@/features/agent/types/tool-outputs")
    const literals = [
      "change_note_kind", "reparent_note", "delete_subtree",
      "merge_notes", "split_note", "relayout_board",
    ]
    for (const t of literals) {
      const out = { type: t, graphUid: "b", noteId: "n", kind: "finding" } as unknown
      expect((out as { type: string }).type).toBe(t)
    }
    expect(typeof mod).toBe("object")
  })


  it("assigns each struct output to the ToolOutput union", () => {
    const change: ChangeNoteKindOutput = {
      type: "change_note_kind", noteId: "n", graphUid: "b", kind: "finding",
    }
    const reparent: ReparentNoteOutput = {
      type: "reparent_note", noteId: "n", graphUid: "b", parentId: null,
    }
    const subtree: DeleteSubtreeOutput = {
      type: "delete_subtree", graphUid: "b", deletedNodes: 3, deletedEdges: 2,
    }
    const merge: MergeNotesOutput = {
      type: "merge_notes", targetId: "n", graphUid: "b", absorbed: 2,
    }
    const split: SplitNoteOutput = {
      type: "split_note", graphUid: "b", createdIds: ["a", "b"], originalDeleted: true,
    }
    const relayout: RelayoutOutput = {
      type: "relayout_board", graphUid: "b", moved: 5, mode: "default",
    }

    // Each struct output must be assignable to the ToolOutput union.
    const all: ToolOutput[] = [change, reparent, subtree, merge, split, relayout]
    const types = all.map((o) => (typeof o === "string" ? o : o.type))
    expect(types).toEqual([
      "change_note_kind", "reparent_note", "delete_subtree",
      "merge_notes", "split_note", "relayout_board",
    ])
  })
})