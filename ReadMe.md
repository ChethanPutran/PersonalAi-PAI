# Personal AI (PAI)

A personal AI assistant built around a **LangGraph agent** that plans, executes tool calls, verifies results, and responds — with persistent memory, a mobile-ready FastAPI backend, and both CLI and voice front-ends.

The assistant ("Chvis" by default) uses a Gemini model by default and can decompose a request into a step-by-step plan, invoke skills/tools, pause for human approval when a step is flagged as sensitive, and store the interaction in long-term memory for later retrieval.

---

## Table of Contents

- [Overview](#overview)
- [How It Works](#how-it-works)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Assistant](#running-the-assistant)
- [Running the Mobile Backend](#running-the-mobile-backend)
- [Tools & Skills](#tools--skills)
- [Memory](#memory)
- [Adding a New Skill](#adding-a-new-skill)
- [Testing](#testing)
- [Notes & Known Issues](#notes--known-issues)

---

## Overview

| Component | What it does |
|---|---|
| `src/graph.py` | LangGraph agent: Planner → Executor → Verifier → Responder → Cleanup, with human-in-the-loop approval |
| `src/memory.py` | ChromaDB long-term memory (conversations, skill results, episodic) + conversation history manager |
| `src/tools.py` / `src/tools2.py` | Built-in tools (search, weather, time, camera description, human assistance) and the skill loader |
| `src/skills/` | Dynamically loaded skill modules (calculator, file ops, web search) |
| `backend/` | FastAPI + WebSocket server for mobile clients (sessions, files, push notifications, background tasks) |
| `frontend/ui/mobile_client/` | React Native mobile client ("OpenClawMobile") |
| `frontend/ui/app/android/` | Kivy/Buildozer Android app |
| `frontend/ui/web/` | Voice assistant loop + Bluetooth media-key listener utilities |

---

## How It Works

```
user input
    │
    ▼
┌──────────┐   plan / answer   ┌───────────┐
│ PLANNER  │ ────────────────► │ RESPONDER │──► final answer
│          │   (no tools)      └─────┬─────┘
└────┬─────┘   (tools needed)        │
     │ plan steps                    ▼
     ▼                          ┌──────────┐
┌──────────┐  per step          │ CLEANUP  │──► END
│ EXECUTOR │ ──► skill/tool ──► └──────────┘
│          │      (may pause for human approval)
└────┬─────┘
     ▼
┌──────────┐
│ VERIFIER │──► back to RESPONDER
└──────────┘
```

1. **Planner** — decides whether the request needs tools. If not, it answers directly. If yes, it produces a list of steps.
2. **Executor** — for each step, asks the LLM which skill to call with which parameters, executes it, and records the result in memory. A step can set `requires_approval`, in which case the graph pauses and waits for a human `yes`/`no` before continuing or skipping the step.
3. **Verifier** — summarizes what was accomplished and whether any issues occurred.
4. **Responder** — generates the final user-facing answer.
5. **Cleanup** — compacts the context window: old messages are summarized into a running summary so long conversations stay within token limits.

The graph is checkpointed with LangGraph's `PostgresSaver`, so conversation state survives restarts and interrupted (approval-pending) runs can be resumed.

---

## Project Structure

```
├── main.py                      # re-exports / simple entry
├── run.py                       # one-file voice wrapper around the agent
├── run_backend.py               # starts the FastAPI mobile backend
├── requirements.txt             # pinned Python dependencies
├── pyproject.toml               # uv project definition
├── Dockerfile                   # backend container image
├── docker-compose.yml           # Postgres (backend/redis/nginx commented out)
├── scripts/
│   ├── run_backend.sh
│   ├── setup_frontend.sh
│   └── test_backend.sh
│
├── src/
│   ├── main.py                  # interactive CLI chat loop
│   ├── graph.py                 # AgentGraph: LangGraph workflow + LLM wiring
│   ├── memory.py                # ChromaDB memory + PersistenceManager (history/export/clear)
│   ├── models.py                # AgentState, ToolCall, MemoryEntry
│   ├── enums.py                 # node names / state transitions
│   ├── tools.py                 # SkillManager (dynamic skill loading) + built-in tools
│   ├── tools2.py                # tool implementations (search, weather, time, capdesc, human_assistance)
│   ├── tasks.py                 # task helpers
│   ├── skills/                  # drop-in skill modules
│   │   ├── calculator.py
│   │   ├── file_ops.py
│   │   ├── web_search.py
│   │   └── expense_tracker.py   # (stub)
│   ├── mcp_servers/             # MCP server stubs (math, weather)
│   └── orchestrator/            # (placeholder)
│
├── backend/                     # FastAPI backend for mobile clients
│   ├── server.py                # app factory, lifespan, /ws/{session_id}
│   ├── api.py                   # REST routes (sessions, upload/download, notifications, tasks)
│   ├── websocket_handler.py     # WebSocket session handling
│   ├── session_manager.py       # session lifecycle (in-memory, optional Redis)
│   ├── background_tasks.py      # queued background task processor
│   ├── file_handler.py          # upload/download management
│   ├── push_notifications.py    # Firebase Cloud Messaging
│   ├── vad.py                   # voice activity detection
│   └── models.py                # request/response schemas
│
└── frontend/
    └── ui/
        ├── mobile_client/       # React Native app (voice, push, file picker)
        ├── app/android/         # Kivy + Buildozer Android app
        └── web/                 # voice loop + Bluetooth media-key utilities
```

---

## Prerequisites

- **Python ≥ 3.12** (per `pyproject.toml`)
- **[uv](https://docs.astral.sh/uv/)** (recommended) or `pip`
- **PostgreSQL** — used both for the LangGraph checkpointer and (optionally) sessions
- API keys (see [Configuration](#configuration)):
  - `GOOGLE_API_KEY` — Gemini LLM + embeddings (default provider)
  - `NVIDIA_API_KEY` — validated at startup (NVIDIA NIM provider option)
  - `OPENAI_API_KEY` — validated at startup
- Optional: Redis (session storage fallback), Android SDK/Buildozer (Android app), Node ≥ 16 (React Native app)

---

## Installation

```bash
git clone https://github.com/ChethanPutran/PersonalAi-PAI.git
cd PersonalAi-PAI

# With uv
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt

# Or with plain pip
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Additional installs depending on what you run:

```bash
# Backend (FastAPI etc. — these are commented out in requirements.txt,
# so install them explicitly)
pip install fastapi uvicorn websockets python-socketio \
            python-multipart httpx aiofiles pyfcm

# Camera-description tool (capdesc_tool)
pip install transformers torch opencv-python pillow
```

Start Postgres (needed by the agent's checkpointer):

```bash
docker compose up -d postgres
```

---

## Configuration

Create a `.env` file in the project root:

```bash
# Required (validated at startup by tools2.validate_environment)
GOOGLE_API_KEY=your_google_key          # Gemini LLM + embeddings
NVIDIA_API_KEY=your_nvidia_key          # validated even if unused
OPENAI_API_KEY=your_openai_key          # validated even if unused

# Optional
WEATHER_API_KEY=your_openweathermap_key # enables the weather tool
```

The default agent uses `gemini-2.5-flash` with Google as provider. `AgentGraph._init_llm()` also supports an NVIDIA provider via `ChatNVIDIA`.

ChromaDB data and the SQLite `agent.db` live under `./data/`, created automatically on first run.

---

## Running the Assistant

### CLI chat

```bash
python src/main.py
```

Type your message at the `You:` prompt; type `exit` to quit.

### Programmatic use

```python
from src.graph import AgentGraph

agent = AgentGraph()
answer = agent.run("What's the weather in Berlin?")
print(answer)
```

### Voice wrapper

```bash
python run.py
```

Speaks responses with pyttsx3 and listens via SpeechRecognition. (Note: `run.py` imports `src.voice.input` / `src.voice.output`, which are not present in this tree yet — see [Notes](#notes--known-issues).)

---

## Running the Mobile Backend

The backend (internally called "OpenClaw Mobile Backend") exposes a REST API plus a WebSocket endpoint so mobile clients can talk to the same agent.

```bash
python run_backend.py --host 0.0.0.0 --port 8000

# or
bash scripts/run_backend.sh

# with Redis-backed sessions
python run_backend.py --redis
```

| Endpoint | Purpose |
|---|---|
| `GET /docs` | OpenAPI/Swagger UI |
| `WS /ws/{session_id}` | Real-time session channel |
| `POST /api/v1/session/create` | Create a session |
| `GET /api/v1/session/{id}/status` | Session status |
| `POST /api/v1/session/{id}/command` | HTTP fallback command |
| `POST /api/v1/upload` / `GET /api/v1/download/{file_id}` | File transfer |
| `POST /api/v1/notifications/register` | Register an FCM push token |
| `GET /api/v1/notifications/pending` | Offline notification queue |
| `GET/POST /api/v1/tasks/{task_id}/...` | Background task status/cancel |
| `GET /api/v1/health` | Health check |

The agent (`AgentGraph`) is initialized once at startup and shared by all sessions. CORS is open (`allow_origins=["*"]`) — tighten this before deploying anywhere real.

### React Native client

```bash
cd frontend/ui/mobile_client
npm install
npm start          # metro
npm run android    # or: npm run ios
```

Uses `@react-native-voice/voice` for speech input, `react-native-push-notification` for FCM, and `react-native-document-picker`/`react-native-fs` for files.

---

## Tools & Skills

**Built-in tools** (bound to the LLM via `bind_tools`):

| Tool | Description |
|---|---|
| `search_tool` | DuckDuckGo web search (`ddgs`) |
| `weather_tool` | OpenWeatherMap current weather (needs `WEATHER_API_KEY`) |
| `time_tool` | Current time, local or per-timezone |
| `capdesc_tool` | Captures a webcam frame and captions it with the BLIP vision model |
| `human_assistance` | Interrupts the run and waits for a human to answer |

**Dynamically loaded skills** — every `*.py` file in `src/skills/` exposing an `execute(**params)` function is auto-discovered by `SkillManager` and listed in the planner/executor prompts:

- `calculator` — safe arithmetic eval (character-whitelisted)
- `file_ops` — `file_read_skill` / `file_write_skill` (approval-gated)
- `web_search` — free weather lookup via wttr.in
- `expense_tracker` — stub

---

## Memory

`src/memory.py` provides three ChromaDB collections (cosine similarity, Google `gemini-embedding-001` embeddings):

| Collection | Stores |
|---|---|
| `conversation_memory` | user/assistant exchanges, retrieved by semantic similarity |
| `skill_memory` | successful skill executions (name, input, output) |
| `episodic_memory` | episodic events |

On top of that, `PersistenceManager` wraps the LangGraph `PostgresSaver` checkpointer with conversation-history utilities:

- `get_conversation_history(thread_id)` / `print_history(thread_id)`
- `export_history(thread_id, format="json" | "markdown" | "text")`
- `get_summary(thread_id)` — message counts, avg response length
- `list_threads()` — all known threads with previews
- `clear_history(thread_id=None)` — wipe one thread or all

Long conversations are compacted in the `cleanup` node: once the message list exceeds `max_context_window * scale_factor`, older messages are removed and folded into a running summary that is prepended to future prompts.

---

## Adding a New Skill

Create `src/skills/my_skill.py`:

```python
def execute(query: str) -> str:
    """Do something useful."""
    return f"Result for {query}"
```

Restart the agent — `SkillManager.load_skills()` discovers it automatically, and the planner/executor will now see `my_skill` in the available-skills list. Skill results are automatically stored in `skill_memory`.

For tools that need to block on human confirmation (like `human_assistance`), use LangGraph's `interrupt()` inside the tool function.

---

## Testing

```bash
bash scripts/test_backend.sh        # curl-based smoke tests against the backend
```

The script covers: health check, session creation, WebSocket connect (via `wscat`), VAD, file upload, and push notification.

---

## Notes & Known Issues

- **Voice modules missing**: `run.py` and `frontend/ui/web/main.py` import `src.voice.*` / `src.agent.*`, which are not in this tree. The CLI (`src/main.py`) and backend paths are the ones that run as-is.
- **Backend requirements**: `requirements.txt` has all backend/web dependencies commented out, and `scripts/run_backend.sh` / the `Dockerfile` reference a `requirements-backend.txt` that does not exist in the repo. Install FastAPI/uvicorn manually for now.
- **Checkpointer connection string**: `src/memory.py` builds its Postgres URI as `postgresql://postgres:postgres@localhost:postgres` — the *database name* is used as the *port*. It works only if your environment compensates (e.g., an alias/解析到 5432); otherwise change it to `@localhost:5432`.
- **Dockerfile paths**: it copies `skills/` from the repo root, but skills live in `src/skills/`; the image build will fail until that's fixed.
- **CORS** is `allow_origins=["*"]` — fine for development only.
- **Kivy Android app** (`frontend/ui/app/android`) is a standalone demo (login + mood quotes backed by `users.json`) and not wired to the assistant backend.

---

## License

No license file is present in the repository yet.
