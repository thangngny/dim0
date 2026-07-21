# Board Chat Bot — Rich Structural Edits

**Date:** 2026-07-21
**Approach:** C — Hybrid (backend primitive shared by chat-agent tools + integration/MCP)
**Status:** Approved (Sections 1 & 2)

## Problem

Users want to message a bot *on the dim0 board* to drill deeper into a part of the
graph and edit nodes on request. Deep-research of the codebase found this is **~80%
already built**:

- `CopilotSheet` (`webui/src/features/board/components/flow/copilot-sheet.tsx`) embeds the
  full `Chat` (agent feature) as a non-modal right-side "Board Assistant" panel, passing
  `initialBoardId` and `enableSelectionContext`.
- Selection context (`webui/src/features/agent/components/chat/input-settings/message-board-context.tsx`)
  feeds the currently-selected canvas nodes into the message context.
- The in-app chat agent already edits the board via tool outputs:
  `create_note` / `write_note` / `edit_note` / `link_notes` → applied to the canvas by
  `webui/src/features/board/harness/agent/apply-tool-output.ts` (as `node.add/update`,
  `edge.add/update` remote batches) and the module-level `agent-bridge.ts`.
- Research-handoff drafts (`webui/src/features/board/lib/research-handoff.ts`) already
  include a `focus` canned prompt that targets selected nodes and edits the branch.
- A second path — external `claude` CLI via MCP — already writes the board through
  `backend/topix/api/router/integration.py` → `AgentBoardBridge` → WebSocket broadcast
  (`research_runner.py` spawns the CLI with the Dim0 MCP config).

Research-graph nodes (question / finding / evidence / …) are **ordinary `Note`s** on the
board: `_build_note` in `integration.py` constructs `Note` instances from `NodeInput`.
The canvas renders the same `Note` model for both chat edits and research-mode writes —
there is no parallel storage.

### The gap

The chat agent's board toolset is limited to **content + link** edits. It cannot, via
chat, perform **structural** edits:

- change a node's kind (e.g. question → finding) with shape/color re-style,
- re-parent a node (move it under a different parent),
- delete a subtree (a node and its descendants),
- merge several nodes into one (fold content + edges),
- split one node into several,
- relayout a branch or the whole board.

The user requested all six. This spec adds them.

## Architecture

The codebase already uses `AgentBoardBridge` as the single mutation primitive beneath
**both** the in-app chat-agent edits and the integration/MCP API. This design follows
that pattern: the six structural ops are implemented **once** on the bridge (+ `apply_ops`
wire protocol), then exposed through two thin surfaces — chat-agent tools (conversational,
in-app) and integration/MCP endpoints (token-authenticated, for external `claude` CLI /
research mode). The frontend consumes chat-agent tool outputs exactly as it does today.

### Layers

**1. Backend primitives — `backend/topix/collab/agent_bridge.py` + `apply_ops.py`**
(the only place with real logic)

New `AgentBoardBridge` methods:

| Method | Effect |
|---|---|
| `change_note_kind(board_id, node_id, kind)` | patch `metadata.kind`, re-apply `build_research_style(kind)` (shape/color), re-fit size |
| `reparent_note(board_id, node_id, new_parent_id)` | patch `parent_id`; **reject on cycle** (422) |
| `delete_subtree(board_id, node_id)` | collect descendants via `GraphStore`, delete node + descendants + internal edges |
| `merge_notes(board_id, node_ids, target_id)` | fold content + edges of others into `target_id`, delete the rest |
| `split_note(board_id, node_id, parts[])` | create N new notes from content chunks, inherit parent/edges of original, optionally delete original |
| `relayout(board_id, scope_ids, mode)` | reuse `rearrange_created_notes` / `apply_research_layout` |

`apply_ops.py` wire-op dispatch — add new op types **only where unavoidable**:

- `node.merge`, `node.split`, `subtree.remove`, `layout.apply` — new op types.
- `change_note_kind` and `reparent_note` **reuse `node.update`** (patch `metadata.kind` /
  `parent_id` + style) so the dispatcher stays minimal.

Each bridge method: validate → mutate `GraphStore` → broadcast WS op → return result.

**2. Chat-agent tools — `backend/topix/agents/notes/tools.py` +
`tool_handler.py` + `datatypes/tool_outputs.py`** (thin)

- Six tool schemas. Node targeting via **selected-node IDs** (from message-board-context)
  or explicit IDs.
- `tool_handler` dispatches to the new bridge methods and returns
  `ChangeKindOutput / ReparentOutput / MergeOutput / SplitOutput /
  DeleteSubtreeOutput / RelayoutOutput`.
- Prompt additions teach the agent *when* to use each op, to scope to selected nodes, and
  to **ask for confirmation before destructive ops** (delete subtree, merge, split).

**3. Integration/MCP surface — `backend/topix/api/router/integration.py` +
`backend/topix/integrations/dim0_mcp/server.py`** (thin)

New endpoints (token-auth + redaction + `research_scope` guards, same as existing):

- `POST /integration/boards/{id}/nodes/{nid}:set-kind`
- `POST /integration/boards/{id}/nodes/{nid}:reparent`
- `DELETE /integration/boards/{id}/nodes/{nid}:subtree`
- `POST /integration/boards/{id}/nodes:merge`
- `POST /integration/boards/{id}/nodes/{nid}:split`
- `POST /integration/boards/{id}/layout` — already exists; extend `mode` set if needed.

New MCP tools in `dim0_mcp/server.py` mirror these, so external `claude` CLI / research
mode can also perform structural edits. Each calls the same bridge method.

**4. Frontend apply — `webui/src/features/board/harness/agent/apply-tool-output.ts` +
`agent-bridge.ts`** (extend)

- Handlers for the six new tool outputs: fetch fresh notes, apply `node.update/add/remove`
  + `edge.*` as remote batches (mirrors the existing `applyNoteOutput` / `applyLinkOutput`).
- Merge / split / subtree also arrive via WS broadcast, so all viewers update; the
  agent-bridge apply path gives the acting client immediate local application.

### Boundaries

Each unit has one responsibility and a well-defined interface:

- **`AgentBoardBridge`** — the only mutation primitive (validate → mutate → broadcast).
- **`apply_ops.py`** — wire-op dispatcher; new op types only where needed.
- **Chat-agent tool layer / integration router / MCP server** — thin surfaces, no
  business logic; changing bridge internals does not break them.
- **`apply-tool-output`** — tool output → canvas ops.

## Data flow — "merge these 3 selected nodes"

1. User selects 3 nodes on the canvas, opens CopilotSheet, types
   "gộp 3 node này, giữ node đầu làm gốc".
2. Frontend `use-submit-prompt` attaches the selected node IDs (message-board-context) →
   `POST /chats/{id}/messages` (existing endpoint, unchanged).
3. Backend agent loop runs; the model emits a `merge_notes` tool call with
   `node_ids = [the 3 selected]`, `target_id = <first>`.
4. `tool_handler` → `bridge.merge_notes(board_id, node_ids, target_id)` → `GraphStore`
   folds content + edges into the target, deletes the other two → broadcasts
   `node.merge` (or a `node.update` + `node.remove` batch) over WebSocket.
5. WS broadcast → every viewer (including the acting client) receives a peer-op → canvas
   updates. The agent-bridge apply path also applies locally for immediacy.
6. The agent streams a short confirmation; the tool-step widget renders
   "Merged 3 → {target label}".

The other ops follow the same pattern: agent tool call → bridge method → WS broadcast →
canvas apply. Relayout goes through the existing `apply_research_layout` /
`rearrange_created_notes`.

## Error handling

- **Reparent cycle** — detect a cycle in `reparent_note`, reject 422 with an
  agent-facing message; the agent surfaces it to the user.
- **Destructive ops (delete subtree, merge, split)** — two-phase confirm:
  1. agent calls with `confirm: false` → backend returns a preview (count of nodes/edges
     affected + a short diff);
  2. agent shows the preview to the user;
  3. on user confirmation the agent calls with `confirm: true`, which executes.
  This realizes the "drill deeper into that part / edit per my request" UX.
- **Validation** — merge/split require `node_ids` on the same board, non-empty, target
  exists; otherwise 422.
- **Redaction** (existing `_REDACT_PATTERNS`) applies to new content from merge/split.
- **`research_scope` guards** (existing) apply to the new mutations.
- **WS `op-rejected`** (existing) surfaces failures to the agent and client.
- **Idempotency** (integration path) — reuse `idempotency_key` for the merge/split/reparent
  endpoints.

## Testing

- **Backend unit** — `apply_ops` new op types (`node.merge`, `node.split`,
  `subtree.remove`, `layout.apply`) and the six `AgentBoardBridge` methods (cycle reject,
  subtree collect, merge fold, split chunk, kind style re-apply) with fixture boards, under
  `backend/test/unit/`.
- **Integration router** — token-auth + redaction + scope-guard for the five new
  endpoints.
- **Chat-agent `tool_handler`** — dispatch + output shape for the six tools.
- **Frontend** — `apply-tool-output` for the six new outputs (remote batch ops), mirroring
  the existing test pattern (`apply-icon-update-to-board-contents.test.ts`).
- **E2E (via the `run` skill)** — select 3 nodes → "gộp lại" → canvas shows one node with
  merged content; change a node's kind → shape/color changes; reparent → no cycle; delete
  subtree with confirm.

## Out of scope (YAGNI)

- Per-node chat threads (the user did not choose this).
- Relocating the floating-island assistant (not chosen).
- Bulk multi-board operations.