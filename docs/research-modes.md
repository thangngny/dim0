# Multi-mode board research

Dim0 treats the **board as persistent research memory**. Agents iterate in modes instead of one-shot reports.

## Modes

| Mode | When | Effort default | Writes |
| --- | --- | --- | --- |
| `explore` | First pass / broad scan | `ultracode` | New structured graph |
| `reframe` | Change taxonomy (brand → storytelling…) | `ultracode` | Remap whole board (delta) |
| `expand` | Deepen one node/cluster | `xhigh` | Children under focus only |
| `critique` | Gaps, contradictions, weak evidence | `high` | Unknown / Contradiction / Finding |

## API

```http
POST /integration/boards/{board_id}/research
X-Integration-Token: <token>
Content-Type: application/json

{
  "mode": "expand",
  "instruction": "…",
  "language": "vi",
  "focus_node_ids": ["node-id"],
  "session_id": "uuid",
  "use_web_evidence": true,
  "budget": { "max_new_nodes": 16, "effort": "xhigh" }
}
```

SSE events: `starting` → `running` → `progress` → `done` | `error`.

`POST /integration/boards/{board_id}/generate` remains a thin **explore** wrapper for older clients.

## Web evidence

Before Claude runs, the server tries web search (first available key):

1. `LINKUP_API_KEY`
2. `TAVILY_API_KEY`
3. `PERPLEXITY_API_KEY`
4. `EXA_API_KEY`

Results are injected as `WEB_EVIDENCE` into the prompt. Source nodes should cite real URLs. If no key is set, research still runs (model knowledge only) and prompts demand lower confidence / Unknown nodes.

## Expand scope gate

While `mode=expand` is running:

- `PATCH/DELETE` on nodes **outside** focus + session-created ids → **403**
- Creates count toward `max_new_nodes`
- Scope clears when the run finishes

## Node metadata

Integration node create stamps content with:

- Kind, Phase, Session
- optional Brand, Campaign, Year, Tags, Confidence, Citations

MCP `dim0_upsert_research_graph` auto-stamps `phase` + `session_id` into node metadata.

## Launcher

`/launcher.html` steps: Login → Explore → Result → Refine (reframe/expand/critique/explore+).

## Canvas agent

Selecting a node shows `@selection: …` and **Expand selected** / **Reframe board** starter pills (selection context attached to the next message).
