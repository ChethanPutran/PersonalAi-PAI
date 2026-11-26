# Personal AI System (PAI)

[![tests](https://github.com/ChethanPutran/PersonalAi-PAI/actions/workflows/tests.yml/badge.svg)](https://github.com/ChethanPutran/PersonalAi-PAI/actions/workflows/tests.yml)
[![versions](https://github.com/ChethanPutran/PersonalAi-PAI/actions/workflows/versions.yml/badge.svg)](https://github.com/ChethanPutran/PersonalAi-PAI/actions/workflows/versions.yml)
[![plugin-manifests](https://github.com/ChethanPutran/PersonalAi-PAI/actions/workflows/plugin-manifests.yml/badge.svg)](https://github.com/ChethanPutran/PersonalAi-PAI/actions/workflows/plugin-manifests.yml)

**A distributed personal AI runtime: you state a goal in words, PAI plans it as a graph of tasks, and runs them on whichever
of your devices (phone, desktop, server) can do the job.**

PAI is an *execution system*, not just a chatbot. Understanding, planning, authorization, device selection, plugins, memory
and voice are separate components, and the device that receives a request does not have to be the one that carries it out:

```text
Phone: "open Spotify on my desktop and play something calm"
   │
   ▼
PAI backend ── plan ──► [desktop.app.open] → [screen.capture] → [input.click] → [chat.reply]
                                │
                      authorization + confirmation
                                │
                                ▼
                       Desktop device runs the plugins
```

---

## Contents

- [Features](#features) · [How it works](#how-it-works) · [Quick start](#quick-start)
- [Configuration](#configuration) · [Admin panel](#admin-panel) · [LLM providers, models and cost](#llm-providers-models-and-cost)
- [Voice](#voice) · [Plugins](#plugins) · [Versioning and releases](#versioning-and-releases) · [Security](#security)
- [Development](#development) · [Repository layout](#repository-layout) · [Documentation](#documentation) · [License](#license)

---

## Features

- **Goal → plan → execution.** An LLM turns a request into a validated dependency graph (`TaskSpec`s); the runner executes
  it respecting `depends_on`, retries, pausing and cancelling.
- **Multi-device.** Capabilities are routed to the best connected device (Android, Linux desktop, server) over WebSocket.
- **Plugins.** Capabilities come from versioned plugins (browser, camera, desktop, files, input, screen, shell) with
  manifests, risk levels and per-action consent. Native plugins load through a C-compatible ABI.
- **Authorization you can audit.** Plan ≠ permission: every action passes policy, risk and consent checks, and every
  decision is written to the audit log. Confirmation flags come from the plugin manifest, never from LLM output.
- **ReAct agents.** Long-running agents that observe, act and repeat (e.g. drive an app through screenshots and input),
  using only the user's own capabilities and the same authorization path.
- **Real-time voice.** Streaming speech in and out (WebSocket), with barge-in; AIRouter, OpenAI or free local engines.
- **Memory.** Short-term, episodic, long-term, vector, procedural and knowledge-graph memory, all written through a safety
  guard that stops tool/web text from becoming a "preference".
- **Cost-aware.** Token, voice and credit tracking, with prices from a model registry.
- **Admin panel.** Live DAGs, traces with token/cost charts, usage and credits, persistent event history, and a Settings
  editor with a "Reload system" button.
- **Flutter client** for Android and desktop that acts both as a UI and as a device that executes plugins.

## How it works

```text
User goal (text / voice)
   │
   ▼
GoalDecomposer ── LLM → List[TaskSpec] (a DAG)
   │
   ▼
PlanVerifier ──── capabilities exist? parameters match the schema? confirmation from the manifest
   │
   ▼
TaskOrchestrator ─┬─ AuthorizationManager   JWT → permissions → policy → consent → audit
                  ├─ CapabilityRouter       capability → plugin → executor (device-aware, version-aware)
                  ├─ DeviceSelector         best connected device for the capability
                  ├─ TaskRunner / DAG       dependencies, retries, pause/cancel
                  └─ MemoryManager          recall for the prompt, guarded writes
   │
   ▼
ExecutorManager ─┬─ DeviceExecutor   invoke/result frames over WebSocket to phone/desktop
                 ├─ ServerExecutor   in-process Python plugins
                 └─ BuiltinExecutor  chat.reply, task/schedule capabilities, agents
```

Prompts are versioned Jinja2 templates (`prompts/`); every LLM call goes through `PromptGuard`; replies stream through a
sanitizer; tool output is always treated as untrusted data.

---

## Quick start

**Prerequisites:** Python 3.12, [uv](https://docs.astral.sh/uv/), and (for the client) Flutter. NATS and Neo4j are optional
services; SQLite is the default database.

```bash
git clone https://github.com/ChethanPutran/PersonalAi-PAI.git
cd PersonalAi-PAI

uv sync                       # installs the locked dependencies into .venv
cp example.env .env           # then fill in the secrets (see "Configuration")
uv run pai                    # starts the API on http://localhost:8000
```

Useful URLs once it is running:

| What | URL |
|------|-----|
| API docs (Swagger) | http://localhost:8000/docs |
| Health | http://localhost:8000/api/v1/system/health |
| **Admin panel** | http://localhost:8000/api/v1/system/admin |

**First admin account.** Register a user (`POST /api/v1/auth/register`) whose email is listed in `PAI_ADMIN_EMAILS`
(comma-separated, in `.env`); that user is promoted to admin on login and can open the admin panel.

**Flutter client**

```bash
cd frontend
flutter pub get
flutter run -d linux          # or an Android device id
```

> `pai-server` is the *device agent* entry point (the device-side runtime: the separate `pai-device` package in `device/`), not the API. The API is
> `uv run pai` (or `uvicorn pai.server.app:create_app --factory --reload`).

---

## Configuration

Everything non-secret is YAML in `config/`; secrets are environment variables.

| File | Purpose |
|------|---------|
| `config/config.yaml` | The main configuration. Every key must be a real field of the models in `src/pai/config/`. |
| `config/models.yaml` | **Model registry**: which models exist, what they can do (`tool_use`, `reasoning`, `vision`, context) and what they cost (LLM per million tokens, STT per minute, TTS per 1K characters). |
| `config/overrides.yaml` | Written by the admin panel (**Settings**), deep-merged *over* `config.yaml` at startup. Git-ignored; delete a key to fall back. |
| `.env` | Secrets only (`LLM_PROVIDER__API_KEY`, `SECURITY__JWT_SECRET`, `SECURITY__ENCRYPTION_KEY`, DB/Redis/NATS/Neo4j credentials). Copy from `example.env`. |

- **A typo is an error, not a silent no-op.** Unknown keys anywhere in the YAML fail at startup with a "did you mean…?"
  hint. Check the files without starting the server: `uv run pai-validate-config`.
- **Your LLM provider** is `llm_provider` (provider + default model), with a model per role in `llm_routes`
  (`planner`, `chat`, `agent`). Prices come from `models.yaml`; `llm_pricing` overrides them per deployment.
- **Credits:** set `llm_cost.initial_credits_usd`, or set the balance from the admin panel (that wins until you reset it).

## Admin panel

`/api/v1/system/admin` (admin users only):

| Page | What you get |
|------|--------------|
| Overview · Live DAGs · Active Tasks · Tasks | Runtime health, DAGs with per-node status and kill, task tables. DAG and event **history** is kept for `admin.history_retention_days` (default 7), so it survives restarts. |
| Traces | Span tree for each goal, with prompts/responses, **tokens and cost per LLM call**, the prompt split by part, and charts. |
| **Usage & Cost** | Credits remaining (settable), voice cost in real time, tokens and cost over time, per goal, per role/model, and a **Models & prices** table. |
| Events | Task/DAG events with history and live updates. |
| Devices · Plugins · Users | Management. |
| **Settings** | Every setting from the config schema: enums are dropdowns, each field shows its default and can be reset, secrets are locked. Saves to `config/overrides.yaml` after validating with the real config model. |
| **Reload system** | Applies saved settings to the *running* server without a restart or touching the admin session: models/routes/prices, voice, starting credits, history retention. Anything else is listed as "needs a restart". |

## LLM providers, models and cost

Providers: **AIRouter** (OpenAI-compatible gateway, default), OpenAI, Gemini, OpenRouter, Kimi. Model ids are gateway slugs such
as `anthropic/claude-haiku-5.5`; any id is accepted, and `models.yaml` supplies prices and capabilities.

- Per-role models: a strong model for planning/agents, a fast one for chat (see `llm_routes`).
- Startup checks warn when a configured model is missing from `models.yaml` or lacks tool support for a role that needs it.
- Every call records tokens and cost (configured price, else the cost the gateway reports, else "unknown") to a JSONL
  ledger, and deducts it from your credits. `GET /system/llm/usage` (admin) returns the totals.

## Voice

`WS /voice/stream` carries realtime audio. Configure the engines under `voice:` in `config.yaml`:

| | Options |
|---|---------|
| Speech-to-text | `airouter` (per utterance), `openai_realtime`, `openai_batch`, `vosk` (free, offline), `auto` |
| Text-to-speech | `airouter`, `openai`, `piper` (free, offline), `espeak`, `auto` |

AIRouter speech uses the models and prices in `voice.airouter` / `models.yaml`. Free local engines:
`uv sync --extra voice-local`. See [`src/pai/voice/README.md`](src/pai/voice/README.md) for the wire protocol.

## Plugins

Plugins live in `plugin_registry/plugins/<id>/<version>/<platform>/` with a `manifest.json` and an `artifact/` folder
(the only thing that gets packaged). Current plugins: `browser`, `camera`, `desktop`, `files`, `input`, `screen`, `shell`.

```bash
uv run pai-validate-manifests plugin_registry/plugins     # JSON Schema + Pydantic validation
plugin_registry/shared/build_linux.sh                     # build the native Linux plugins into artifact/
```

A capability declares its risk, whether it needs confirmation, how long consent may be remembered, its task type
(one-shot / long-running / stream) and its parameter schema. Full format and authoring guide:
[`plugin_registry/README.md`](plugin_registry/README.md).

## Versioning and releases

Three independent version axes, configured in one place — `[tool.pai.versions]` and `[tool.pai.plugins]` in
`pyproject.toml`: the **runtime**, the **manifest schema** range, and the **app**.

```bash
uv run pai-sync-versions --write         # update every copy of a version (pai/version.py, pubspec.yaml, …)
uv run pai-check-plugin-versions         # manifests valid; a released plugin version never changes
python scripts/bump_plugin.py desktop patch   # publish a change as a NEW plugin version
uv run pai-package-plugin plugin/desktop/v1.2.0 --out dist   # what a release publishes
```

A plugin version that exists on `main` is immutable; a change is a new version with a `changelog.summary`. Pushing the tag
`plugin/<id>/v<version>` from `main` publishes the packages as a GitHub release (`release-plugin.yml`).

## Security

- `requires_confirmation` is read from the manifest, never from LLM output; shell commands are checked against a prefix
  whitelist at plan time and again at invoke time.
- Input control (keyboard/mouse) is approved once per action by default (`security.authorization.input_grant_scope`).
- Device-reported permissions are advisory (the device re-checks before every invoke).
- Memory writes go through `MemoryGuard`; voice requests can never confirm a risky action by speech.
- Secrets never appear in the admin Settings page or in `config/overrides.yaml`.

Details: [`src/pai/security/README.md`](src/pai/security/README.md).

---

## Development

```bash
uv run pytest tests -q -p no:cacheprovider \
  --ignore=tests/leaks --ignore=tests/memory/test_memory.py --ignore=tests/tasks/test_task_fail_undelivered.py
uv run pytest tests/leaks -q          # heap-growth checks: run in their own process
cd frontend && flutter test           # Flutter tests
```

- `tests/memory/test_memory.py` and `tests/tasks/test_task_fail_undelivered.py` call APIs whose signatures changed and
  currently fail; one consent test (`test_preflight_device_scope`) is order-dependent in full runs.
- **CI** (`.github/workflows`): `tests` (backend + admin UI unit tests), `versions` (version drift, config/YAML validity,
  plugin release rules), `plugin-manifests` (schema, models, deterministic packaging), `release-plugin` (tag → GitHub
  release). Dependabot keeps actions and dependencies current.
- The CLI tools (`pai-validate-config`, `pai-sync-versions`, `pai-check-plugin-versions`, `pai-package-plugin`,
  `pai-validate-manifests`) need only pydantic, jsonschema, packaging, PyYAML and loguru, so CI does not install the full runtime.
- Project conventions and "how do I add X" recipes (endpoint, capability, prompt, manager, memory type): [`CLAUDE.md`](CLAUDE.md).

## Repository layout

```text
src/pai/            backend (api, orchestration, planning, plugins, devices, executors, memory, voice, agent,
                    security, llm, config, storage, tasks, observability, server)
prompts/            versioned Jinja2 prompt templates
config/             config.yaml, models.yaml (and overrides.yaml, written by the admin panel)
plugin_registry/    plugins (manifests + native sources), shared build tooling, plugin tests
plugin-api/         Kotlin plugin API for Android
frontend/           Flutter app (Android, Linux, macOS, Windows, web)
tests/              per-area test folders (admin, config, llm, voice, plugins, tasks, security, …)
scripts/            helper scripts (bump_plugin.py, run_backend.sh, create_admin.sh, …)
docker/             Dockerfile and docker-compose.yml
docs/               earlier design notes and status documents
```

## Documentation

| Topic | Where |
|-------|-------|
| Plugin format and authoring | [`plugin_registry/README.md`](plugin_registry/README.md) |
| Realtime voice protocol | [`src/pai/voice/README.md`](src/pai/voice/README.md) |
| Agents | [`src/pai/agent/README.md`](src/pai/agent/README.md) |
| Security model | [`src/pai/security/README.md`](src/pai/security/README.md) |
| Device agent | [`device/README.md`](device/README.md) |
| Flutter client | [`frontend/README.md`](frontend/README.md) |
| Architecture and conventions | [`CLAUDE.md`](CLAUDE.md), [`docs/`](docs/) |

## License

[MIT](LICENSE) © 2026 Chethan
