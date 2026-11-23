# CLAUDE.md — Personal AI Agent (PAI)

## Project Overview

**PAI** is a distributed personal AI runtime that accepts natural-language goals, decomposes them into a dependency graph of tasks (via LLM), and executes them across connected devices (Android, Linux desktop, server). It runs as a FastAPI server backend with a Flutter mobile/desktop frontend.

---

## Commands

```bash
# Install dependencies
uv sync

# Run the server
.venv/bin/pai-server
# or
.venv/bin/uvicorn pai.server.app:create_app --factory --reload

# Run all tests (use the venv python)
.venv/bin/python -m pytest tests/ -q

# Run specific test directories
.venv/bin/python -m pytest tests/prompts/ tests/security/ -q

# Validate plugin manifests
.venv/bin/pai-validate-manifests
```

> **Note:** `python` is not directly available — always use `.venv/bin/python` or `.venv/bin/pytest`.

---

## Architecture

```
User Goal (text/voice)
    ↓
GoalDecomposer          — LLM converts goal → List[TaskSpec] (DAG)
    ↓
PlanVerifier            — Validates all capabilities exist and params match schema
    ↓
TaskOrchestrator        — Central hub: owns context, task runner, event bus
    ├── AuthorizationManager    — JWT → user permissions → policy engine
    ├── CapabilityRouter        — Maps TaskSpec.capability → executor
    ├── DeviceSelector          — Picks best connected device for the capability
    ├── TaskRunner              — Runs the DAG respecting depends_on
    └── MemoryManager           — Recalls prior interactions for context
         ↓
ExecutorManager
    ├── DeviceExecutor      — WebSocket invoke/result frames to mobile/desktop
    ├── ServerExecutor      — In-process Python plugin execution
    └── BuiltinExecutor     — System capabilities (chat.reply, task CRUD, schedules)
```

**Core principle:** The device that *receives* a request does not have to *execute* it. A goal received on the phone may be executed by the desktop.

---

## Project Layout

```
src/pai/
  api/              — FastAPI routes, middleware, dependencies
  agents/           — BaseAgent, ConversationAgent, ProductivityAgent
  common/           — Shared enums, constants, base Manager class
  config/           — Pydantic config models + YAML/env loader
  core/             — Lifecycle protocol, batch init/shutdown helpers
  devices/          — Device registry, WebSocket connections, capabilities
  event_bus/        — Async pub/sub (local queue + optional NATS transport)
  executors/        — DeviceExecutor, ServerExecutor, BuiltinExecutor, Scheduler
  llm/              — LLMRouter, provider implementations (OpenAI/Gemini/Anthropic)
  memory/           — EpisodicMemory, LongTermMemory, KnowledgeGraph, ShortTermMemory
  models/           — Shared Pydantic API models
  notifications/    — Firebase push notifications
  observability/    — Tracing (OpenTelemetry), structured logging (loguru)
  orchestration/    — TaskOrchestrator, CapabilityRouter, TaskRunner, DAGScheduler
  planning/         — GoalDecomposer, PlanVerifier, DAGScheduler, models
  plugins/          — PluginManager, PluginRegistry, manifest parsing/validation
  prompts/          — PromptRegistry (versioned Jinja2), PromptVersionTracker
  scheduler/        — SchedulerService (cron, interval, one-shot)
  security/         — SecurityManager, JWT auth, PromptGuard, PluginGuard, AuditLogger
  server/           — FastAPI app factory, lifespan, CLI, embedded agent
  session/          — AsyncSession management
  storage/          — SQLAlchemy ORM models, async database, repositories
  tasks/            — TaskManager, state machine, TaskStore
  utils/            — Logger helpers, error hierarchy, misc utilities

prompts/
  chat/reply/       — v1.0.0.j2, v1.1.0.j2 (current), _meta.yaml
  planning/goal_decomposition/  — v1.0.0.j2 (current), _meta.yaml

frontend/
  lib/
    ui/             — Flutter screens: chat, devices, plugins, tasks, schedules, auth
    models/         — Dart data models
    services/       — API client, state management
  android/          — Android-specific build + Kotlin native plugin system
  linux/            — Linux desktop entry

tests/
  prompts/          — Prompt rendering tests (always runnable, no external deps)
  security/         — LLM guard / prompt injection tests
  integration/      — Full orchestration flow (require DB)
  performance/      — Load tests
  test_*.py         — Unit tests (many have stale imports — see Known Issues)

config/
  config.yaml       — Root configuration
  development.yaml  — Dev overrides (currently active)
```

---

## Key Abstractions

### Lifecycle Protocol (`src/pai/core/lifecycle.py`)
Every long-lived component implements:
```python
async def initialize(self) -> None: ...
async def shutdown(self) -> None: ...
def is_initialized(self) -> bool: ...
```
Batch helpers: `initialize_all(components)`, `shutdown_all(components)`.

### Manager Pattern (`src/pai/common/manager.py`)
All managers inherit `Manager(Lifecycle)`:
- Automatic session factory and event bus integration
- Owns sub-managers (composition)
- `_on_initialize()` / `_on_shutdown()` hooks

### TaskSpec (`src/pai/planning/models.py`)
The currency between planner and executor:
```python
TaskSpec(
    id="task_1",
    capability="notification.send",   # must match a registered capability id
    parameters={"title": "...", "message": "..."},
    depends_on=["task_0"],            # DAG edges
    requires_confirmation=False,      # from capability manifest, not LLM output
)
```

### Capabilities
A capability is a named, schema-described action a device/server can perform. Registered via plugin manifests. The special sentinel `"chat.reply"` is always available and handled by `BuiltinExecutor`.

### Prompt Registry (`src/pai/prompts/registry.py`)
- Loads versioned Jinja2 templates from `prompts/<id.replace('.','/')>/<version>.j2`
- Constructor takes `PromptRegistryConfig`, not `Path` — use `PromptRegistryConfig(root="prompts", frozen=True)`
- Templates use `StrictUndefined` — missing variables raise `ValueError` immediately
- `_meta.yaml` declares: `current`, `variables`, `versions`

### PromptGuard (`src/pai/security/llm_guard.py`)
Wraps every LLM call: inspects request (memory/history blocks) for injection, sanitizes response. Mode: `off | warn | sanitize | reject`.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | FastAPI 0.110 + Uvicorn |
| Language | Python 3.12 |
| Database | SQLAlchemy 2.0 + aiosqlite (SQLite default) |
| LLM | OpenAI / Gemini / Anthropic (router selects) |
| Vector DB | ChromaDB 0.5 + Sentence Transformers |
| Knowledge Graph | Neo4j 5.22 |
| Pub/Sub | asyncio queue + optional NATS |
| Config | Pydantic v2 + pydantic-settings |
| Logging | Loguru |
| Tracing | OpenTelemetry |
| Auth | PyJWT |
| Frontend | Flutter (Dart) |
| Android native | Kotlin + JNI (C++ plugin ABI bridge) |
| Package manager | uv |

---

## Configuration

Main config: `config/config.yaml`, dev overrides: `config/development.yaml`.

Key env vars (`.env` or environment):
```
DATABASE__URL=sqlite+aiosqlite:///./pai.db
LLM_PROVIDER__PROVIDER=openai
LLM_PROVIDER__API_KEY=sk-...
LLM_PROVIDER__MODEL=gpt-4o

# Optional services
NATS__URL=nats://localhost:4222
NEO4J__URL=bolt://localhost:7687
NEO4J__USERNAME=neo4j
NEO4J__PASSWORD=...

# Embedded device agent (optional)
PAI_EMBED_AGENT=true
PAI_BACKEND_URL=http://localhost:8000
PAI_EMAIL=user@example.com
PAI_PASSWORD=...
```

---

## Adding Things

### New API endpoint
1. Add route function in `src/pai/api/routes/<domain>.py`
2. Use `Depends(get_current_user)` for auth
3. Access `request.app.state.ctx` (`PAIAppContext`) for managers
4. Return a Pydantic model

### New capability (server-side)
1. Register in `src/pai/orchestration/builtin_capabilities.py` or a plugin manifest
2. Implement handler in `src/pai/orchestration/builtins/`
3. Add to `BuiltinExecutor.execute()` dispatch

### New prompt
1. Create `prompts/<category>/<name>/v1.0.0.j2`
2. Create `prompts/<category>/<name>/_meta.yaml` with `id`, `current`, `variables`, `versions`
3. Access via `prompt_registry.get("<category>.<name>")`
4. Declare all Jinja2 variables in `_meta.yaml` — `GoalDecomposer.initialize()` validates this at startup

### New manager
1. Inherit from `Manager`
2. Override `_on_initialize()` and `_on_shutdown()`
3. Wire into `app_context.py` and add to `initialize_all()`

### New memory type
1. Implement `initialize()`, `shutdown()` lifecycle
2. Add to `MemoryManager.__init__()` and call from `recall_for_prompt()`

---

## Known Issues / Stale Tests

Several test files import modules that have been renamed or removed. They fail at collection time:

| Test file | Broken import |
|-----------|--------------|
| `tests/test_agents.py` | `pai.agents.*` — restructured |
| `tests/test_event_bus.py` | stale path |
| `tests/test_executors.py` | `pai.executors.server_executor` removed |
| `tests/test_integration.py` | `pai.main.app` renamed |
| `tests/test_integration_e2e.py` | same |
| `tests/test_kernel.py` | `pai.kernel` removed |
| `tests/test_planning.py` | `pai.planning.planning_engine` removed |
| `tests/test_plugins.py` | `pai.plugins.browser_plugin` removed |
| `tests/llm/test_llm_providers.py` | `LLMProviderConfig` removed from `pai.llm.models` |

**Always-runnable tests** (no broken imports, no external services):
```bash
.venv/bin/python -m pytest tests/prompts/ tests/security/ tests/test_timezone.py tests/test_task_repository.py -q
```

---

## Security Notes

- `requires_confirmation` on `TaskSpec` is **always read from the capability manifest**, never from LLM output — an adversarial LLM cannot skip confirmation gates
- `PromptGuard` sanitizes all memory/history before it enters the planning prompt
- Shell plugin capabilities enforce a whitelist prefix check at both plan validation time and plugin invoke time
- All authorization decisions are written to the audit log

---

## Memory

- Memory (short-term, episodic, vector, long-term, knowledge graph, procedural) is written by `MemoryManager` only, and **every write goes through `MemoryGuard`** (`src/pai/memory/safety/`, see its README). Tool/web/assistant text can never become a preference, procedure or graph fact.
- Reads are per-user: stores raise `ValueError` without a `user_id`; recalled items are re-sanitized and quarantined data is never returned.
- Use typed models (`RecallResult`, `ToolOutcome`, `Provenance`, `ProcedureStep`) — do not pass raw dicts between memory components.
- Tests: `.venv/bin/python -m pytest tests/memory -q`.

## Prompt Versioning

When editing a prompt template:
1. Create a new version file: `prompts/.../vX.Y.Z.j2`
2. Add the version entry to `_meta.yaml` under `versions:`
3. Update `current:` in `_meta.yaml` to the new version
4. Update any test assertions in `tests/prompts/` that check for specific strings

The registry warns (but does not fail) if the file on disk changes without a version bump. In production (`frozen=True`) the cache is locked at startup.
