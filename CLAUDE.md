# CLAUDE.md — Personal AI Agent (PAI)

## Project Overview

**PAI** is a distributed personal AI runtime that accepts natural-language goals, decomposes them into a dependency graph of tasks (via LLM), and executes them across connected devices (Android, Linux desktop, server). It runs as a FastAPI server backend with a Flutter mobile/desktop frontend.

---

## Commands

```bash
# Install dependencies
uv sync

# Run the server
.venv/bin/pai
# or
.venv/bin/uvicorn pai.server.app:create_app --factory --reload
# (`pai-server` / `pai-device` is the DEVICE AGENT entry point, not the API)

# Device agent tests (separate package, `device/`)
.venv/bin/python -m pytest device/tests -q

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
  agent/            — Long-running ReAct agents: runtime, loop, tools, events (see its README)
  agents/           — BaseAgent, ConversationAgent, ProductivityAgent (older stubs)
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
  server/           — FastAPI app factory, lifespan, shutdown (backend only)
  session/          — AsyncSession management
  storage/          — SQLAlchemy ORM models, async database, repositories
  tasks/            — TaskManager, state machine, TaskStore
  utils/            — Logger helpers, error hierarchy, misc utilities

device/               — SEPARATE installable package `pai-device` (import name `pai_device`): the device agent
  src/pai_device/   — agent/, sandbox/, transport (HTTP/WS client), plugins, config, cli, _entry. Imports NOTHING from `pai`;
                      it only speaks the backend's public API. Install it alone on a device: `pip install ./device`
  tests/            — device tests (run with the device package alone)

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

tests/                 (one folder per area; no test files at the top level)
  admin/ agent/ chat/ common/ consent/ llm/ memory/ native/ orchestration/ planning/ plugins/
  prompts/          — Prompt rendering tests (always runnable, no external deps)
  scheduler/ security/ server/ storage/ task_events/ tasks/ utils/ voice/
  security/         — LLM guard / prompt injection tests
  integration/      — Full orchestration flow (require DB)
  leaks/            — Memory-growth / resource-leak checks
  conftest.py       — Shared fixtures (in-memory SQLite `session_factory`, ...)

config/
  config.yaml       — Root configuration (every key must match a field in src/pai/config/: `pai-validate-config`)
  models.yaml       — Model registry: capabilities + prices (see "Models and prices")
  overrides.yaml    — Written by the admin panel (Settings); merged OVER config.yaml at startup. Gitignored; delete a key to fall back
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

Main config: `config/config.yaml`; settings saved from the admin panel go to `config/overrides.yaml` (deep-merged over it); secrets stay in `.env`. An unknown key anywhere is an error at startup (`pai-validate-config` checks the files without starting the server).

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

### Settings editor and reload (admin panel)

- **Settings** (admin panel) edits every setting from the Config schema: enums are dropdowns, each field shows its default, secrets are locked. Edits are validated with the real `Config` model, then saved to `config/overrides.yaml` (never `config.yaml`); `PUT /system/settings`. Secrets (API keys, passwords, JWT secret) live in `.env` and are never shown or saved here.
- **Reload system** (`POST /system/reload`, `pai.config.reload`) applies saved settings to the RUNNING server without a restart and without touching the admin session: models/routes/prices (`llm_provider`, `llm_routes`, `llm_pricing`, `models_file`), `voice`, `llm_cost.initial_credits_usd`, `admin.history_retention_days`. Everything else is reported as needing a restart. To make another setting reloadable, add a group to `LIVE_GROUPS` and a reloader in `pai/config/reload.py` (swap the live object; never half-apply).
- An unknown key anywhere in the YAML is an error (`pai.config.strict`): `pai-validate-config` checks the files, CI runs it. Aliased keys (`llm_provider.model` = `model_name`) are accepted but shown under the field name.

### Models and prices: `config/models.yaml`

The model registry (`src/pai/config/model_registry.py`) is the one place for which models exist, what they can do
(`tool_use`, `reasoning`, `vision`, context) and what they cost: `llm[]` (USD per million tokens), `speech.stt[]`
(`per_minute_cost`), `speech.tts[]` (`per_1k_chars_cost`), `embeddings[]`, `decision[]`. It feeds LLM and voice cost
tracking (`llm_pricing` in config.yaml overrides a price; `voice.airouter.*_cost_*: null` = take it from the registry),
a startup check that configured models exist and that the planner/agent models support tools, and the admin
"Models & prices" table (`GET /system/models`). A model missing from the file still works: its cost is whatever the
provider reports, else unknown.

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

The legacy test files whose imports no longer existed (`test_agents`, `test_event_bus`, `test_executors`,
`test_integration`, `test_integration_e2e`, `test_kernel`, `test_planning`, `test_plugins`, `llm/test_llm_providers`,
`performance/test_load`)
were deleted; every remaining file collects. One file still fails because it calls APIs whose signatures changed:
`tests/memory/test_memory.py` (old store constructors). Running several test directories in ONE pytest process can
also trip `tests/consent` on a `conftest` module-name clash (it passes alone); run areas separately.

**Always-runnable tests** (no broken imports, no external services):
```bash
.venv/bin/python -m pytest tests/prompts/ tests/security/ tests/utils/test_timezone.py tests/tasks/test_task_repository.py -q
```

---

## Security Notes

- `requires_confirmation` on `TaskSpec` is **always read from the capability manifest**, never from LLM output — an adversarial LLM cannot skip confirmation gates
- `PromptGuard` sanitizes all memory/history before it enters the planning prompt
- Shell plugin capabilities enforce a whitelist prefix check at both plan validation time and plugin invoke time
- `shell.exec` is HIGH risk, not hard-denied: it asks **every time** ("once"), or once per **session** for that exact command. Never "always", never "any command": `consent.binding.BOUND_PARAMETERS` makes the server refuse (`binding_too_broad`) and ignore any grant that doesn't pin `command`, and the dialog hides "Any parameters" (`required_binding_keys`). It can't be scheduled (nobody to approve; `ScheduleValidator.preflight`). Hard denies are now only `SystemRestrictionsPolicy.BLOCKED` and CRITICAL-risk actions (e.g. `system.execute`).
- All authorization decisions are written to the audit log
- Device-reported permissions are **advisory**: the app reports, per installed plugin, what the user allowed on that device (`POST /plugins/device/{id}/permissions`, owner only, stored in `device_plugins.permissions`). The router stops offering capabilities whose declared permission is `denied`/`pending` there and explains why (`permission_not_allowed_on_device`). The device still re-checks before every invoke; an unreported permission is unknown and blocks nothing. Only Linux reports so far (Android prompts on request, so it has no read-only query; other platforms have no `permissionStatus` yet).
- Keyboard/mouse control (`input` plugin) can only be approved **once per action** by default. `security.authorization.input_grant_scope: session` (config.yaml) additionally offers "approve for this session" (never "always" for high-risk actions). It is enforced server-side from one derived set (`ApprovalPolicy.always_approve_for`, via `always_approve_from_config`); camera control stays once-only either way. A capability that declares the `input` permission always asks, so `input.status`/`input.stop` deliberately declare no permission (ending control must never need a prompt).

- **Remote access is per device and off by default.** Acting on a device from OUTSIDE it (another of the user's devices, the web, a schedule, an agent) needs the owner's switch on THAT device: `allow_remote_install` (install/uninstall/enable/disable plugins) and `allow_remote_invoke` (run plugin capabilities). One rule, `pai.security.remote_access.remote_access_denial`, fail-closed (someone else's/unknown device = "not found"); it runs in `SecurityManager.authorize` (every plugin run: chat, schedules, agents, the tester) and in the `/plugins/*` install/uninstall/enable/disable routes. "From the device itself" = the request's `X-PAI-Device-Id` header (`get_source_device_id`) equals the target; every client must send it. The device re-checks on its own side (the `connected` frame and `device.remote_access` frames carry its switches; it fails closed until told; invoke frames carry `source_device_id`, install commands `requested_by_device`). Owner-only `GET/PUT /devices/{id}/remote-access`, audited. `/plugins/{id}/report-*` and `/plugins/device/{id}/installed` are owner-only too.
- **Remote installs are tracked operations, not polling.** `POST /plugins/{id}/install|uninstall` returns an `operation` (`pai.plugins.operations.PluginOperations`: requested → delivered → downloading → verifying → installing [→ awaiting_permission] → succeeded | failed(code, message, hint)). The target device sends `plugin.progress` WS frames (and the final `report-install`); every change is pushed to the owner's connected devices as `{"type": "plugin.operation", "operation": ...}` and on the event bus; a watchdog fails operations whose device went offline or silent; `GET /plugins/operations[/{id}]` is for first load/reconnect. Raw device text stays in `error_detail`; clients see the stable code + plain message + hint.
- **User Settings page:** `GET /api/v1/account/settings` (alias `/settings`), a static shell; profile/password/devices/plugins/activity come from `/account/*`.

---

## Memory

- Memory (short-term, episodic, vector, long-term, knowledge graph, procedural) is written by `MemoryManager` only, and **every write goes through `MemoryGuard`** (`src/pai/memory/safety/`, see its README). Tool/web/assistant text can never become a preference, procedure or graph fact.
- Reads are per-user: stores raise `ValueError` without a `user_id`; recalled items are re-sanitized and quarantined data is never returned.
- Use typed models (`RecallResult`, `ToolOutcome`, `Provenance`, `ProcedureStep`) — do not pass raw dicts between memory components.
- Tests: `.venv/bin/python -m pytest tests/memory -q`.

## Voice & streaming

- Realtime voice lives in `src/pai/voice/` (see its README for the wire protocol and security model); `WS /voice/stream`.
- Replies stream everywhere: the orchestrator pushes sanitized text into a `ReplySink`; `POST /chat/stream` is SSE. Never bypass `StreamingSanitizer` when streaming model output.
- Voice requests carry `Origin.VOICE`; never accept spoken confirmation for a risky action.
- Tests: `.venv/bin/python -m pytest tests/voice tests/chat tests/llm -q`.

## Latency (chat and voice)

- **Fast conversational path** (`pai/orchestration/fast_route.py`, `orchestrator.fast_chat_path`, on by default): before planning, `FastRouter` decides CHAT or ACT. Rules first (small talk with no action wording = CHAT; any action/device/data wording = ACT), then at most one tiny classifier call on the fast `chat` route (hard timeout); any doubt or failure = ACT. CHAT skips the planner LLM call (`Planner.chat_only_plan`, the plan the planner would have returned) so the reply streams after ONE fast-model round trip instead of planner (3.5 to 6 s) + reply. The trace has `orchestrator.route` (`route`, `how`) and `orchestrator.planner` (`fast_chat_path`).
- **Voice**: the trace's `voice.stt` span starts when the user began talking; the wait that matters is `finalize_ms` on it and the `voice.latency` span (`stt_finalize_ms`, `first_audio_after_speech_end_ms`). TTS segments are synthesized `voice.tts_prefetch` at a time (default 2) while earlier ones play, playback stays in order. AIRouter TTS cannot stream or return raw PCM (it returns a whole WAV per request): for lower first-audio latency use a local engine (`voice.tts: piper|espeak`) or a provider that streams.

## Agent plugin and computer control

- The agent is a **built-in plugin** (`pai.agent.plugin`, id `agent`): backend-provided, nothing to install, shown in the plugin lists (`builtin: true`), off until the user enables it (`POST /plugins/agent/enable`, Settings page, plugin screens). Its capabilities (`agent.*`) carry `plugin_id="agent"`, `requires_user_enable=True`; `CapabilityRouter._build_index` only offers them to users who enabled it, and `POST /agents` returns 403 `agent_plugin_disabled` otherwise.
- **Computer control**: the capability contract every platform implements is `src/pai/agent/COMPUTER_CONTROL.md` (`computer.observe/click/type/key/scroll/swipe/long_press/apps.*/status`, reusing the existing `screen` / `input` permissions). The agent perceives through `pai.agent.computer.format_observation` (a compact element list, never raw JSON/base64) and follows the COMPUTER CONTROL rules in `prompts/agent/react` v1.1.0.

## Agents

- Long-running, spawnable, talk-to-able ReAct agents live in `src/pai/agent/` (see its README); the system starts them via the `agent.start` builtin capability or `POST /agents`, and users converse over `WS /agents/{id}/ws` or `POST /agents/{id}/messages`.
- An agent's tools are the user's own capabilities, run **only** through `orchestrator.execute_action` (authorization, confirmation, audit unchanged). Give every call a unique request id (the orchestrator de-duplicates identical actions per request id). Agents can never be granted `agent.*`/`chat.*`.
- Tool output is untrusted DATA: sanitised by `PromptGuard`, size-bounded, boundary-wrapped. Never put it in the instruction part of a prompt. Never send empty text to `sanitize_response` (it returns a canned reply).
- Prompt: `prompts/agent/react` (versioned like any other; see Prompt Versioning).
- Tests: `.venv/bin/python -m pytest tests/agent tests/prompts/test_agent_react_prompt.py -q`.

## Versioning (runtime, manifest schema, app, plugins)

- Single source of truth: `[tool.pai.versions]` and `[tool.pai.plugins]` in `pyproject.toml`. Edit there, then `pai-sync-versions --write` (updates `pai/version.py`, `[project].version`, the `schemaVersion` range in `manifest.schema.json`, `frontend/pubspec.yaml`, `frontend/lib/core/app_version.dart`). Never edit those copies by hand.
- A released plugin version (one that exists on `origin/main`) is **immutable**: any change to its folder is a new version (`python scripts/bump_plugin.py <id> patch`), which needs `changelog.summary`. Checked by `pai-check-plugin-versions`.
- `src/pai/plugins/manifest.schema.json` mirrors the Pydantic models in `manifest_models.py`; change both together (`tests/plugins/test_manifest_schema.py` fails on drift). Validate with `pai-validate-manifests plugin_registry/plugins`.
- CI: `.github/workflows/{plugin-manifests,versions,release-plugin,tests}.yml`. The tooling modules import without the heavy runtime (`pai/__init__` loads the orchestrator lazily): keep `pai.plugins.{versioning,release,manifest_install}` free of heavy imports.
- Tests: `.venv/bin/python -m pytest tests/plugins/test_version_policy.py tests/plugins/test_manifest_schema.py -q`.

## Prompt Versioning

When editing a prompt template:
1. Create a new version file: `prompts/.../vX.Y.Z.j2`
2. Add the version entry to `_meta.yaml` under `versions:`
3. Update `current:` in `_meta.yaml` to the new version
4. Update any test assertions in `tests/prompts/` that check for specific strings

The registry warns (but does not fail) if the file on disk changes without a version bump. In production (`frozen=True`) the cache is locked at startup.
