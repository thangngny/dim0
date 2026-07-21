# Claude CLI × Ollama × Dim0 Integration Guide

> **Không cần** Anthropic API key, OpenAI API key, hay OpenRouter API key.  
> Claude CLI dùng Ollama Cloud làm LLM backend. Dim0 dùng `nomic-embed-text` (local Ollama) cho embeddings.

---

## Kiến trúc

```
Claude CLI (ultracode)
    ↓  stdio
MCP Server (dim0_mcp)
    ↓  HTTP + X-Integration-Token
Integration API (/integration/*)
    ↓  in-process
AgentBoardBridge
    ↓  GraphStore + RoomRegistry
Canvas WebSocket (peer-op broadcast)
    ↓  realtime
Dim0 Browser Canvas
```

---

## Phase 0: Cài đặt lần đầu

### 1. Pull embedding model

```bash
ollama pull nomic-embed-text   # ~274MB, chỉ cần làm 1 lần
```

### 2. Tạo `.env`

```bash
cp .env.sample .env
# Thêm vào .env:
JWT_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
DIM0_INTEGRATION_TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(32))")
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBED_MODEL=nomic-embed-text
```

### 3. Khởi động databases

```bash
make up-db PROFILE=dev   # Qdrant:6334, Postgres:5433, Redis:6380
```

### 4. Khởi động backend

```bash
cd backend
uv run python -m topix.api.app --stage dev
# hoặc
nohup .venv/bin/python -m topix.api.app --stage dev > /tmp/dim0.log 2>&1 &
```

### 5. Tạo tài khoản và board

```bash
# Đăng ký
curl -X POST http://localhost:8888/users/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"yourpass","name":"Your Name","username":"yourname"}'

# Đăng nhập (form data)
curl -X POST http://localhost:8888/users/signin \
  -F "username=you@example.com" \
  -F "password=yourpass"
# → copy access_token

# Tạo board
curl -X PUT http://localhost:8888/boards \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"My Research Board"}'
# → copy graph_id
```

---

## Phase 1: Cấu hình MCP cho Claude CLI

### Tạo `.mcp.json` (ở thư mục làm việc của Claude CLI)

```json
{
  "mcpServers": {
    "dim0": {
      "command": "python3",
      "args": ["-m", "topix.integrations.dim0_mcp.server"],
      "cwd": "/path/to/dim0/backend",
      "env": {
        "DIM0_BASE_URL": "http://localhost:8888",
        "DIM0_INTEGRATION_TOKEN": "your_integration_token_from_env",
        "DIM0_DEFAULT_BOARD_ID": "your_board_id_from_step_5",
        "PYTHONPATH": "/path/to/dim0/backend/.venv/lib/python3.13/site-packages:/path/to/dim0/backend"
      }
    }
  }
}
```

> **Tip**: `DIM0_INTEGRATION_TOKEN` lấy từ `.env` của Dim0. `DIM0_DEFAULT_BOARD_ID` là `graph_id` từ bước tạo board.

### Khởi động Claude CLI với MCP

```bash
claude --mcp-config .mcp.json
```

---

## Phase 2: Sử dụng trong Claude CLI

### Lệnh hệ thống (system prompt) khuyến nghị

```
Bạn là research agent. Khi nghiên cứu, hãy:
1. Dùng dim0_emit_research_event với event_type="planning" để bắt đầu
2. Dùng dim0_upsert_research_graph để tạo nodes và edges theo từng phase
3. Dùng dim0_layout_nodes sau khi tạo một nhóm nodes mới
4. Kết thúc với dim0_emit_research_event event_type="completed" và một summary node

Ontology node kinds:
- question: câu hỏi nghiên cứu
- workstream: nhánh nghiên cứu
- source: nguồn tài liệu
- evidence: bằng chứng
- finding: phát hiện
- hypothesis: giả thuyết
- contradiction: mâu thuẫn
- decision: quyết định
- summary: tóm tắt
```

### Ví dụ cuộc hội thoại

```
User: Nghiên cứu về tác động của AI đối với thị trường lao động Việt Nam

Claude:
1. [gọi dim0_emit_research_event event_type=planning]
2. [gọi dim0_upsert_research_graph với question node "Tác động AI → lao động VN?"]
3. ... thu thập thông tin ...
4. [gọi dim0_upsert_research_graph với source, finding, contradiction nodes]
5. [gọi dim0_layout_nodes]
6. [gọi dim0_upsert_research_graph với summary node]
7. [gọi dim0_emit_research_event event_type=completed]
```

---

## Phase 3: Tools MCP có sẵn

| Tool | Mô tả |
|------|-------|
| `dim0_health` | Kiểm tra backend có chạy không |
| `dim0_get_board` | Đọc toàn bộ board (nodes + edges) |
| `dim0_list_nodes` | List tất cả nodes |
| `dim0_create_nodes` | Tạo nodes + edges (có idempotency) |
| `dim0_update_node` | Cập nhật title/content một node |
| `dim0_delete_node` | Xóa node |
| `dim0_delete_edge` | Xóa edge |
| `dim0_layout_nodes` | Auto-layout (Sugiyama algorithm) |
| `dim0_emit_research_event` | Log sự kiện nghiên cứu |
| `dim0_upsert_research_graph` | **Tool chính**: tạo toàn bộ phase (nodes + edges + layout) |

---

## Smoke Test

```bash
DIM0_BASE_URL=http://localhost:8888 \
DIM0_INTEGRATION_TOKEN=$(grep DIM0_INTEGRATION_TOKEN .env | cut -d= -f2) \
DIM0_TEST_BOARD_ID=YOUR_BOARD_ID \
python3 smoke_test_integration.py
```

---

## Troubleshooting

### Backend không start

```bash
tail -50 /tmp/dim0.log
```

Lỗi thường gặp:
- `SubscriptionStore failed` → bình thường, bị tắt khi không có OpenAI key
- `ParsingPipeline failed` → bình thường, không ảnh hưởng canvas
- Qdrant/Postgres/Redis connection failed → kiểm tra port trong `.env`

### Ollama embed lỗi 401

```bash
# Kiểm tra nomic-embed-text đã pull chưa
ollama list | grep nomic
# Nếu chưa:
ollama pull nomic-embed-text
```

### MCP server không kết nối được

```bash
# Test thủ công
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | \
  DIM0_BASE_URL=http://localhost:8888 \
  DIM0_INTEGRATION_TOKEN=your_token \
  python3 -m topix.integrations.dim0_mcp.server
```

---

## Files đã thêm/sửa

```
backend/topix/
├── nlp/embed.py                          ← Thêm OllamaEmbedder
├── config/catalog.py                     ← Thêm ollama provider support
├── store/qdrant/store.py                 ← Dùng OllamaEmbedder khi available
├── api/app.py                            ← Register integration router, graceful degradation
├── api/router/integration.py             ← MỚI: Integration HTTP API
└── integrations/dim0_mcp/
    └── server.py                         ← MỚI: MCP stdio server

backend/topix/models.yml                   ← Thêm ollama provider + nomic-embed-text
.env                                       ← MỚI: Local env (JWT, tokens, DB ports)
.mcp.json.example                          ← MỚI: MCP config template
smoke_test_integration.py                  ← MỚI: Integration smoke tests
```
