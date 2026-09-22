# Personal AI System (PAI)

**Context-Aware Autonomous Personal Agent System**

A distributed, multimodal personal AI platform that understands user goals, builds execution plans, decomposes them into tasks, selects the appropriate authorized device, and executes actions across servers, desktops, mobile devices, and remote nodes.

---

## Table of Contents

* Vision
* Architecture
* Core Execution Flow
* Core Modules
* Distributed Device Model
* Source Device vs Execution Device
* Authorization and Security
* Security Boundary
* Planning and Task Execution
* Plugin System
* Plugin Lifecycle
* Plugin Registry
* Native Plugin ABI
* Device Plugin State
* Plugin Reconciliation
* Admin Panel
* Executors
* Memory
* Event Bus
* Context Management
* Agents
* Features
* Project Structure
* Getting Started
* Installation
* Database Setup
* Configuration
* Running the System
* Distributed Execution
* Examples
* Testing
* Extending PAI
* API
* System Design Principles
* Troubleshooting
* Docker Infrastructure
* Environment
* Final Architecture

---

## Vision

PAI is designed as a **distributed cognitive architecture** rather than a traditional voice assistant.

The system separates:

* understanding the user's goal,
* planning what needs to happen,
* determining which capabilities are required,
* checking authorization,
* selecting the correct device,
* creating runtime tasks,
* executing those tasks,
* maintaining memory and context.

This allows PAI to operate across multiple connected devices while keeping the user's intent and permissions at the center of execution.

For example:

> "Open Calculator on my phone."

PAI identifies the mobile device as the execution target.

While:

> "Open Chrome on my desktop."

PAI selects the desktop even if the request itself originated from the user's phone.

The **source device and execution device are independent concepts**.

---

## Architecture

```
                           USER
                            │
                            ▼
                    ┌─────────────────┐
                    │    AI Kernel    │
                    │ System Runtime  │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  Orchestrator   │
                    └────────┬────────┘
                             │
                 ┌───────────┴───────────┐
                 ▼                       ▼
          Context Manager          Memory Manager
                 │
                 ▼
          ┌───────────────┐
          │    Planner    │
          └───────┬───────┘
                  ▼
             ┌─────────┐
             │  Plan   │
             └────┬────┘
                  ▼
          ┌────────────────┐
          │  DAG Scheduler │
          └───────┬────────┘
                  ▼
            ┌───────────┐
            │TaskRunner │
            └─────┬─────┘
                  │
       ┌──────────┼───────────┐
       ▼          ▼           ▼
 Capability   Authorization  Device
 Resolver      Manager       Selector
       │          │           │
       └──────────┼───────────┘
                  ▼
           ┌─────────────┐
           │ TaskManager │
           └──────┬──────┘
                  ▼
          ┌────────────────┐
          │Executor Manager│
          └───────┬────────┘
                  │
        ┌─────────┼──────────┐
        ▼         ▼          ▼
     Server     Desktop    Android
    Executor    Executor   Executor
        │         │          │
        ▼         ▼          ▼
     Server     Desktop     Mobile
```

---

## Core Execution Flow

```
User Goal
   ▼
AI Kernel
   ▼
Orchestrator
   ▼
Create Execution Context
   ▼
Planner
   ▼
Plan (Task A, Task B, Task C)
   ▼
DAG Scheduler
   ▼
TaskRunner
   ├── Capability Resolver
   ├── Authorization Manager
   └── Device Selector
   ▼
TaskManager
   ▼
Executor Manager
   ▼
Actual Executor
   ▼
Physical / Remote Device
```

The system deliberately separates **planning** from **execution**.

* The planner creates `TaskSpec` objects.
* The task system creates runtime `Task` objects.
* The executor performs the actual action.

---

## Core Modules

### AI Kernel

The application's runtime container. Initializes and manages major subsystems. Does **not** directly execute individual tasks.

Responsibilities: subsystem lifecycle, configuration, dependency wiring, startup/shutdown, exposing the top-level goal-processing API.

The kernel delegates actual goal execution to the Orchestrator.

### Orchestrator

The **central execution coordinator**.

Responsibilities: create execution context, receive user goals, invoke planning, coordinate plan execution, resolve capabilities, enforce authorization, select devices, create runtime tasks, invoke task execution, publish execution events, persist interaction results.

The Orchestrator does not implement individual device actions.

### Planning

Converts a natural-language goal into a declarative execution plan.

```
Goal → Goal Decomposer → TaskSpec[] → Plan → Plan Verifier
```

A `TaskSpec` describes **what should happen**, not how. Fields: id, type, capability, parameters, preferred_device.

The planning layer does not directly invoke executors.

### Task System

Converts planning-level `TaskSpec` objects into runtime tasks.

```
TaskSpec → TaskManager → Runtime Task → TaskStore → Task Lifecycle → Executor
```

Responsibilities: task creation, persistence, lifecycle, status, retries, execution, results, errors, history.

### DAG Scheduler

Handles dependencies between tasks. Independent tasks execute concurrently. The scheduler does not know how a device executes a task — it only knows the TaskSpec → TaskRunner contract.

```
             ┌── Search A ──┐
Goal ────────┼── Search B ──┼──► Summarize
             └── Search C ──┘
```

---

## Capability Resolution

Determines which plugin/provider can satisfy a task capability.

```
Task → Capability → Plugin Registry → Plugin Provider
```

Capability resolution is separate from device selection. Examples: `browser_navigate` resolves to the browser plugin; `calendar_event` resolves to the calendar plugin.

---

## Device Selection

Determines **where** a task should execute. Considers:

* explicit device requested by the user
* planner-provided preferred device
* device capability
* device authorization
* device connectivity
* device availability
* executor availability

The source device does not determine the execution device.

---

## Distributed Device Model

```
                    PAI Server
                       │
         ┌─────────────┼──────────────┐
         ▼             ▼              ▼
      Desktop        Mobile        Remote
       Device        Device        Device
         ▼             ▼              ▼
     Desktop        Android        Remote
     Executor       Executor       Executor
```

A user may connect: Android phone, Linux desktop, Windows desktop, macOS desktop, remote server, or another PAI node.

Each device advertises capabilities. Examples:

* **Android** — notification, camera capture, voice synthesis, sensor access, open app
* **Desktop** — browser navigation, browser clicks, screenshot, file operations, open app, desktop clicks
* **Server** — LLM reasoning, search, data processing

---

## Source Device vs Execution Device

PAI explicitly separates the device from which the user sends a request from the device that performs the action.

```
                    User
                     │
                  Phone
                     │ "Open Chrome on my desktop"
                     ▼
                 PAI Server
                     ▼
               Orchestrator
                     ▼
              Device Selector
                     ▼
                  Desktop
                     ▼
             Desktop Executor
                     ▼
                 Chrome
```

The phone is the **source device**. The desktop is the **execution target**.

---

## Authorization and Security

No task reaches an executor before authorization succeeds.

```
Task → Required Capability → Plugin Provider
   ▼
Is Plugin Enabled?  ──No──► DENY
   ▼
Is User Authorized? ──No──► DENY
   ▼
Is Device Authorized? ──No──► DENY
   ▼
Is Device Online? ──No──► DENY
   ▼
Does Device Support Capability? ──No──► DENY
   ▼
Is Executor Available? ──No──► DENY
   ▼
Execute
```

Two important invariants:

* **installed ≠ enabled.** A plugin present on disk is not authorized to run.
* **valid plan ≠ permission.** A plan that references a capability still needs the user and device to permit it.

---

## Security Boundary

Security is enforced at **execution time**, not at planning time.

This matters because plans can originate from: user input, agents, memory, scheduled tasks, plugins, external events, remote devices.

Therefore:

```
Plan ≠ Permission
```

A valid plan still needs authorization before execution. The planner may propose an action, but the authorization pipeline decides whether it runs.

---

## Planning and Task Execution

The Planner's output is a `Plan`, which is an array of `TaskSpec` objects. The DAG Scheduler is responsible for ordering them by dependency. The TaskRunner resolves each task's capability, checks authorization, selects a device, and hands the task to the TaskManager.

The TaskManager owns the runtime lifecycle: creation, persistence, execution, retries, and completion. It does not know about devices. It delegates to the Executor Manager, which routes to the correct executor — Server, Desktop, Android, or Remote.

---

## Plugin System

PAI's plugin system is **fully dynamic**. The app ships a runtime, not a fixed set of plugins. Plugins are downloaded from a registry on demand, verified, installed into a per-user store, and loaded at runtime by the native host.

Nothing about a specific plugin — "camera", "browser", "whatsapp" — is compiled into the app or the backend. Only the *mechanism* for loading plugins is.

There are two distinct plugin categories:

| Category | Runs on | Loaded by | Examples |
|---|---|---|---|
| **Server plugins** | The PAI backend | Python importlib | browser (Playwright), planner |
| **Device plugins** | The user's device | Native `dlopen` / JNI | camera, notifications, whatsapp |

They share a manifest format but are otherwise independent. Server plugins provide backend-side capabilities; device plugins provide capabilities on phones, desktops, and remote nodes.

### Package Format

A plugin is a signed, versioned archive containing:

* `manifest.json` — identity, capabilities, permissions, platform artifacts
* `icon.png` (optional)
* `README.md` (optional)
* `native/<platform>/<artifact>` — one binary per platform

The host downloads **only the artifact for its current platform**.

### Manifest Fields — Device Plugin

| Field | Purpose |
|---|---|
| `schemaVersion` | Manifest schema version |
| `id`, `name`, `version`, `description` | Identity |
| `author`, `license`, `icon` | Presentation |
| `runtime.kind` | `native` for device plugins |
| `runtime.nativeModule` | Logical module name used by MethodChannel routing |
| `runtime.minPaiVersion` | Minimum host version required |
| `platforms.<os>.artifact` | Relative path to the binary inside the package |
| `platforms.<os>.entrypoint` | Exported symbol name (`pai_plugin_module_create`) |
| `permissions` | Union of all capabilities' permissions |
| `capabilities[].id` | Capability identifier (e.g. `camera.capture`) |
| `capabilities[].parameters` | JSON schema for the parameter map |

### Manifest Fields — Server Plugin

Same identity fields, but `runtime.kind` is `server`, and the runtime block points at a Python entry point.

| Field | Purpose |
|---|---|
| `runtime.entry_point` | Path to the Python file |
| `runtime.class` | Class name inside the file |
| `dependencies` | Python packages the plugin needs |

The `runtime.kind` field determines which loader picks the plugin up.

### Capability Declaration

A plugin declares every capability it provides. Each capability has an id, a human-readable description, the permissions it needs, and a parameters schema. The capability id is the routing key — when the server invokes `camera.capture`, the system looks up which loaded module provides that capability and routes accordingly.

---

## Plugin Lifecycle

```
     not-installed
          │ install()
          ▼
       installed
          │ enable()
          ▼
       enabled
          │ disable()
          ▼
       disabled
          │ uninstall()
          ▼
     not-installed
```

Transient states: `downloading`, `verifying`, `unavailable`, `error`.

### Install

The app fetches the registry index, resolves the manifest URL and platform artifact URL, downloads the manifest, streams the artifact while computing SHA-256, verifies the hash against the registry's declared value, writes the manifest into the plugin store, and reports success to the backend.

### Enable

The app calls the native host's `loadNativeModule` over the plugin MethodChannel. The native host `dlopen`s the artifact, resolves the create symbol, checks the ABI version, and registers the module. The app then POSTs the enable endpoint — the backend records user authorization in `user_plugins` and updates `device_plugins.enabled_on_device`.

### Disable

The app calls native `unloadNativeModule`. The native host calls the plugin's destroy function and `dlclose`s the artifact. The app POSTs the disable endpoint — the backend flips `user_plugins.is_enabled` and `device_plugins.enabled_on_device` to false.

### Uninstall

Disable, then delete the plugin directory from disk, then POST the uninstall report. The backend marks the row `is_installed = false`.

---

## Plugin Registry

The registry is an HTTP service that serves plugin packages.

### Index

`GET /api/v1/plugins/index.json` — returns the full catalog. Public, no auth required (like an app store index).

Response shape:

* `schemaVersion`
* `plugins[]` — one entry per plugin id
* Each plugin has: `id`, `name`, `latest`, `versions`
* Each version has: `manifestUrl`, `artifacts[platform]`, `sha256[platform]`, `sizeBytes[platform]`

### Manifest

`GET /api/v1/plugins/{id}/{version}/manifest.json` — returns the manifest. Public.

### Artifact

`GET /api/v1/plugins/{id}/{version}/artifact/{platform}` — returns the binary. Public. Sent as `application/octet-stream`.

### On-Disk Layout

The backend scans a directory on each index request:

```
backend/pai/plugin_registry/
├── camera/
│   └── 1.0.0/
│       ├── manifest.json
│       ├── linux/
│       │   └── libpai_camera.so
│       └── android/
│           └── arm64-v8a/
│               └── libpai_camera.so
└── hello/
    └── 1.0.0/
        ├── manifest.json
        └── linux/
            └── libpai_hello.so
```

The index endpoint computes SHA-256 on the fly for each artifact. The version's directory structure must match the `platforms.<os>.artifact` paths declared in the manifest.

---

## Native Plugin ABI

Every native plugin exports a **single C symbol** — a function that returns a vtable of function pointers. This makes the ABI language-neutral: plugins can be written in C, C++, Rust, Zig, Swift (via C bridging), or Kotlin (JNI-wrapped).

### The Vtable

The vtable contains:

| Function | Purpose |
|---|---|
| `name` | Returns the module name (e.g. `pai.camera`) |
| `capabilities` | Returns an array of capability strings |
| `permissions` | Returns an array of logical permission strings |
| `invoke` | Runs a capability with parameters, returns a result map |
| `request_permissions` | Prompts for OS permissions |
| `free_kv_list` | Frees a key-value list returned by the plugin |
| `free_string` | Frees an error string |

Plus an `abi_version` integer that must match the host's `PAI_PLUGIN_ABI_VERSION`.

### Entry Points

Every plugin must export exactly two C symbols:

* `pai_plugin_module_create` — returns a vtable pointer
* `pai_plugin_module_destroy` — frees the module

Both must be marked with default visibility. Because the host builds with `-fvisibility=hidden` (via CMake's `CXX_VISIBILITY_PRESET hidden`), each entry point must be explicitly exported:

* Linux/macOS: `__attribute__((visibility("default")))`
* Windows: `__declspec(dllexport)`

Without this attribute, `dlopen` succeeds but `dlsym` returns null, and the loader reports `"plugin missing create/destroy symbols"`.

### Memory Ownership

| Data | Owned by | Freed by |
|---|---|---|
| `name`, `capabilities`, `permissions` return values | Plugin | Plugin (must stay valid for the module's lifetime) |
| Strings inside the key-value list returned by `invoke` | Plugin | Host, via `free_kv_list` |
| Error strings | Plugin | Host, via `free_string` |

Every cross-ABI string is UTF-8, NUL-terminated.

### Platform Loaders

| Platform | Loader | Notes |
|---|---|---|
| Linux | `dlopen` + `dlsym` | Standard ELF shared objects |
| Android | JNI wrapper + `dlopen` inside the host library | Requires `extractNativeLibs=true`; artifact lives in app data dir |
| Windows | `LoadLibraryW` + `GetProcAddress` | Standard PE DLL |
| macOS | `dlopen` + `dlsym` | Requires hardened-runtime entitlements; plugin must share Team ID |
| iOS | Not supported | iOS forbids unsigned native code; use Dart plugins instead |

### Method Channel Protocol

Dart talks to the native host through a single channel: `pai/plugin_runtime`.

| Method | Args | Returns |
|---|---|---|
| `listModules` | — | List of registered module names |
| `isModuleAvailable` | nativeModule | bool |
| `loadNativeModule` | nativeModule, artifactPath, entrypoint, capabilities, permissions | bool |
| `unloadNativeModule` | nativeModule | null |
| `invoke` | nativeModule, capability, parameters | result map |
| `requestPermissions` | nativeModule, permissions | result map |

Every call is routed through the native `PluginChannel`, which dispatches to `PluginManager` → `NativeModuleRegistry` → `DynamicLoader`.

---

## Device Plugin State

Two SQL tables track plugin state. They update independently.

### `user_plugins` — Per-User Authorization

| Column | Meaning |
|---|---|
| `id` | Row id |
| `user_id` | User |
| `plugin_id` | Plugin |
| `is_enabled` | "User X has authorized this plugin" |
| `metadata` | User-specific plugin configuration (JSON) |
| `created_at`, `updated_at` | Timestamps |

`is_enabled` does **not** mean the plugin is installed on any device. It's a user authorization flag.

Written by the enable and disable endpoints. Read by the authorization pipeline and by the admin panel's Users tab.

### `device_plugins` — Per-Device Installation

| Column | Meaning |
|---|---|
| `id` | Row id |
| `device_id` | Device |
| `plugin_id` | Plugin |
| `version` | Installed version |
| `is_installed` | The binary is present on disk |
| `enabled_on_device` | The module is loaded in the host process right now |
| `artifact_sha256` | Hash of the artifact the device reports |
| `last_error` | Last failure message, or a transient marker like `install_requested` |
| `installed_at`, `updated_at` | Timestamps |

Written by: report-install, report-uninstall, enable (with device id), disable (with device id), and reconcile.

### Why Two Tables

Authorization and installation are **independent**.

* A user can authorize Camera (row in `user_plugins`) on a phone where Camera isn't installed (`device_plugins` has no row). The app then offers to install it.
* A plugin can be installed on a device (row in `device_plugins`) while the user hasn't authorized it (no `user_plugins` row). The app shows it as installed but disabled.

Neither table is derived from the other.

---

## Plugin Reconciliation

State can drift between the app's local `installed.json` and the backend's `device_plugins`. Drift happens when:

* a report POST was lost in transit
* the backend's DB was rolled back
* the user wiped the app's data
* the plugin directory was deleted manually

When drift happens, **the device is authoritative** for install state — the backend cannot see the filesystem.

On every WebSocket connect, the app posts its local snapshot to the reconcile endpoint. The backend compares this against `device_plugins` and:

* **Removes** rows the device no longer reports.
* **Adds** rows the device reports but the backend doesn't have.
* **Updates** version, `enabled_on_device`, and `artifact_sha256` when they differ.

Reconciliation is a read + record operation. It does not send commands to the device.

---

## Admin Panel

A single-page HTML panel at `GET /api/v1/system/admin` shows a live view of the system.

### Data Source

The panel fetches from `/api/v1/system/admin/data` with an `Authorization: Bearer <admin-jwt>` header.

The response contains:

* `summary` — uptime, device counts, plugin counts, user counts
* `devices` — every registered device with platform, status, last seen, connected flag
* `plugins` — every plugin in the registry with installation and enabled-on-device counts, plus per-device detail
* `users` — every user with `is_admin` flag and per-user plugin authorizations

The panel polls every 5 seconds.

### Panels

* **Devices** — name, id, platform/architecture, online status, last seen
* **Plugins** — id, version, supported platforms, install count, enabled-on-device count, expandable per-device detail including any `last_error`
* **Users** — email, admin badge, plugins-enabled count, plugins-total count, expandable per-plugin detail

### Authentication

The HTML shell is public (`/api/v1/system/admin`) — it contains no data. Every data endpoint requires a valid JWT with `is_admin = true`.

Login flow:

1. User visits the panel URL.
2. Overlay prompts for email and password.
3. Panel posts to the login endpoint.
4. Response includes `is_admin`. If false, the overlay rejects with "This account does not have admin access".
5. On success, the token is stored in `localStorage`. Every subsequent request sends `Authorization: Bearer <jwt>`.
6. On 401 or 403, the token is cleared and the overlay reappears.

Admin status is re-checked against the DB on every request, so revoking `is_admin` takes effect immediately — no waiting for token expiry.

---

## Executors

Executors perform actual actions.

### Server Executor

Used for server-side operations such as LLM reasoning, search, data processing, and server-side tools.

### Desktop Executor

Used for browser automation, opening applications, file operations, mouse/keyboard interaction, screenshots, and desktop automation.

### Android Executor

Used for notifications, camera, sensors, mobile applications, voice, and Android-specific actions.

### Remote Executor

Allows PAI to delegate work to another connected PAI node or remote execution service.

---

## Memory

PAI maintains multiple forms of memory:

```
                 Memory Manager
                       │
        ┌──────────────┼───────────────┐
        ▼              ▼               ▼
   Short-Term      Long-Term       Episodic
        │              │               │
        └──────────────┼───────────────┘
                       ▼
                 Vector Memory
                       ▼
                 Knowledge Graph
```

Memory is used for: conversation history, persistent user information, previous interactions, semantic retrieval, procedures and workflows, relationships and entities.

Memory is complementary to execution; it does not bypass authorization.

---

## Event Bus

The Event Bus provides asynchronous system communication.

Example events:

```
agent.goal
task.created
task.completed
task.failed
memory.updated
plugin.loaded
plan.created
plan.completed
plan.failed
```

Flow:

```
TaskManager
    │ task.created
    ▼
Event Bus
    ├── UI
    ├── Logging
    ├── Memory
    ├── Monitoring
    └── Agents
```

NATS can be used for distributed event communication.

---

## Context Management

Each user request receives an execution context containing:

* request id
* user id
* session id
* agent id
* source device id
* goal
* conversation history
* user preferences
* metadata

This context travels through the orchestration pipeline. It prevents concurrent requests from accidentally sharing mutable request state.

---

## Agents

Agents provide specialized reasoning or domain-specific behavior.

Examples: Research Agent, Productivity Agent, Travel Agent, Coding Agent, Communication Agent, Automation Agent, Finance Agent, Learning Agent.

Agents may assist in producing goals, context, or plans, but execution still passes through the normal authorization and orchestration pipeline.

---

## Features

| Category | Capabilities |
|---|---|
| **Multimodal AI** | LLM, STT, TTS, vision, OCR |
| **Context** | Session context, user preferences, source device |
| **Memory** | Short-term, long-term, episodic, vector, knowledge graph |
| **Planning** | Goal decomposition, plan verification, task DAG |
| **Tasks** | Persistent runtime tasks, lifecycle, retries, results |
| **Authorization** | User permissions, plugin enablement, device authorization |
| **Device Management** | Online/offline detection, capability discovery |
| **Device Selection** | User-targeted and capability-based routing |
| **Server Plugins** | Browser, calendar, maps, notification, vision |
| **Device Plugins** | Camera, notifications, WhatsApp, app launcher |
| **Dynamic Loading** | Signed packages, `dlopen`, SHA-256 verification |
| **Reconciliation** | Device-authoritative install state |
| **Executors** | Server, desktop, Android, remote |
| **Admin Panel** | Live devices, plugins, users |
| **Auth** | JWT + bcrypt, admin flag, per-request authorization |
| **Events** | Async event-driven communication |
| **API** | REST + WebSocket |

---

## Project Structure

```
src/pai/
│
├── agents/                 base, manager
├── orchestration/          authorization, capability_resolver,
│                           device_selector, execution_context,
│                           orchestrator, task_runner
├── planning/               models, goal_decomposer, planner,
│                           plan_verifier, dag_scheduler
├── tasks/                  models, lifecycle, manager, store
├── devices/                models, capabilities, connection,
│                           registry, websocket, manager
├── executors/              base, manager, scheduler,
│                           server, desktop, android, remote
├── plugins/
│   ├── base
│   ├── models
│   ├── manager
│   ├── registry
│   └── catalog             server-side plugin catalog
│
├── plugin_registry/        device plugin packages (on-disk)
│   └── camera/1.0.0/
│       ├── manifest.json
│       ├── linux/libpai_camera.so
│       └── android/arm64-v8a/libpai_camera.so
│
├── security/               manager, auth, audit,
│                           permissions, policies
├── memory/                 manager, short_term, long_term,
│                           episodic, procedural, vector_store,
│                           knowledge_graph
├── event_bus/              bus, events
├── llm/                    models, provider, router
│
├── api/
│   ├── routes/             auth, chat, devices, plugins,
│   │                       system, tasks
│   ├── middleware/         auth, logging
│   ├── dependencies
│   └── admin/panel.html
│
├── storage/
│   ├── models, database
│   └── repositories/       user_plugin, device_plugin, device
│
├── config/
└── main
```

---

## Getting Started

### Prerequisites

* Python 3.10+
* `uv` or `pip`
* Flutter SDK for the mobile/desktop frontend
* Docker and Docker Compose for optional infrastructure
* Android SDK/ADB for Android execution
* CMake + a C++ toolchain for native plugin development
* Playwright for browser automation
* `sqlite3` (optional, for inspecting the DB)

---

## Installation

1. Clone the repository.
2. Create a Python environment with `uv venv` or `python -m venv`.
3. Activate it and install dependencies from `requirements.txt`.
4. For Android plugin development, set `ANDROID_HOME` and `ANDROID_NDK`, then install an NDK via `sdkmanager`.

---

## Database Setup

Initialize local storage. Depending on configuration, PAI can maintain:

```
data/
├── pai.db
├── long_term.db
├── episodic.db
└── chroma/
```

Neo4j can be used for the knowledge graph. Example command to start it in Docker:

```
docker run -d \
    --name neo4j \
    --publish 7474:7474 \
    --publish 7687:7687 \
    --volume=$HOME/neo4j/data:/data \
    neo4j:latest
```

## `.env.example` – Environment template

```ini
# PAI Environment
PAI_ENV=development
PAI_DEBUG=true
PAI_HOST=0.0.0.0
PAI_PORT=8000

# Databases
DATABASE_URL=sqlite+aiosqlite:///./data/pai.db
REDIS_HOST=localhost
REDIS_PORT=6379
NATS_SERVERS=nats://localhost:4222

# LLM Provider (openai or local)
LLM_PROVIDER=openai
LLM_MODEL=gpt-4
OPENAI_API_KEY=your_openai_key_here

# External APIs
GOOGLE_MAPS_API_KEY=your_google_maps_key
UBER_API_KEY=your_uber_api_key
GOOGLE_CALENDAR_CREDENTIALS=path/to/credentials.json
```

---
flutter run -d linux --verbose --target lib/main.dart

## Final Steps

1. Create the `.env` file from the example.
2. Run `python scripts/setup_db.py` to initialize databases.
3. Execute `./scripts/run_tests.sh` to verify everything works.
4. Start the system with `python -m pai.main` or `docker-compose up`.

The Personal AI System is now fully operational, with all components, tests, and documentation ready.

# RUn backend
source .env && PYTHONPATH=src uvicorn pai.main:app --reload --host 0.0.0.0 --port 8000

# RUn frontend
cd frontend/ui/apps/pai_mobile && flutter pub get && flutter run -d <device-id>

Backend                       Dart (host)                  Native (host)              Plugin .so
   │                             │                             │                        │
   │── catalog (REST) ──────────▶│                             │                        │
   │                             │                             │                        │
   │                             │── index.json (REST) ──▶ Registry                     │
   │                             │◀── manifest + artifact ── Registry                    │
   │                             │                             │                        │
   │                             │  write to disk, verify SHA  │                        │
   │                             │                             │                        │
   │── plugin.install (WS) ─────▶│                             │                        │
   │                             │── loadNativeModule (MC) ──▶ dlopen ────────────────▶ │
   │                             │◀── ok ───────────────────── │                        │
   │◀── result (WS) ─────────────│                             │                        │
   │                             │                             │                        │
   │── capability.invoke (WS) ──▶│                             │                        │
   │                             │── invoke (MC) ────────────▶ module->invoke ────────▶ │
   │                             │◀── map ─────────────────── │                        │
   │◀── result (WS) ─────────────│                             │                        │

### Install the command-line tools
   mkdir -p ~/Android/Sdk/cmdline-tools
cd ~/Android/Sdk/cmdline-tools

# Latest Linux command-line tools (adjust URL if a newer one exists)
wget https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip
unzip commandlinetools-linux-11076708_latest.zip
mv cmdline-tools latest


# Set environment variables
export ANDROID_HOME="$HOME/Android/Sdk"
export PATH="$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$PATH"


# Install the NDK
sdkmanager --install "ndk;26.1.10909125"


# Verify
ls ~/Android/Sdk/ndk
# should print: 26.1.10909125

ls ~/Android/Sdk/ndk/26.1.10909125/build/cmake/android.toolchain.cmake
# should print the path