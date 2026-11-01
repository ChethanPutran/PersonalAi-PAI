# Personal AI System (PAI)

**A distributed personal AI runtime for planning, orchestrating, and executing tasks across connected devices.**

PAI is a modular personal AI system designed to go beyond a traditional chatbot. It separates **goal understanding, planning, task execution, device selection, authorization, plugins, memory, and communication** into independent components.

A request can originate from one device and be executed on another.

```text
                         User
                          │
                          ▼
                    PAI Client
                   Mobile / Web
                          │
                    HTTP / WebSocket
                          │
                          ▼
                  FastAPI Backend
                          │
                          ▼
                  Task Orchestrator
                          │
          ┌───────────────┼────────────────┐
          ▼               ▼                ▼
       Context          Planner          Memory
       Manager        + Task Graph      Manager
                          │
                          ▼
                     Task Runner
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
        Authorization  Capability   Device
          Manager       Resolver    Selector
                          │
                          ▼
                  Executor Scheduler
                          │
             ┌────────────┼────────────┐
             ▼            ▼            ▼
          Server       Desktop      Android
         Executor      Executor     Executor
```

## Core Idea

PAI treats an AI assistant as an **execution system**, not just a conversational interface.

```text
User Goal
    ↓
Context
    ↓
Planning
    ↓
Task Graph
    ↓
Authorization
    ↓
Capability Resolution
    ↓
Device Selection
    ↓
Execution
    ↓
Result + Memory
```

The key architectural principle is:

> **The device that receives a request does not have to be the device that executes it.**

For example:

```text
Phone
  │
  │ "Open Chrome on my desktop"
  ▼
PAI Backend
  │
  ▼
Planner
  │
  ▼
Device Selection
  │
  ▼
Desktop Executor
  │
  ▼
Chrome
```

---

## Architecture

### Task Orchestration

The `TaskOrchestrator` coordinates the major stages of execution:

* context management
* planning
* task execution
* capability resolution
* authorization
* device selection
* executor scheduling
* event handling

The orchestration layer remains independent of device-specific implementations.

### Planning

Natural-language goals are converted into structured tasks and dependency graphs.

```text
Natural Language Goal
        ↓
    Goal Decomposition
        ↓
       TaskSpec
        ↓
        Plan
        ↓
    Plan Verification
        ↓
     Task Graph
```

Tasks can contain dependencies, allowing independent operations to execute separately while dependent tasks wait for their prerequisites.

### Capability-Based Execution

Tasks request **capabilities** rather than directly depending on a particular device.

```text
Task
  ↓
Capability
  ↓
Provider
  ↓
Executor
```

Examples include:

```text
browser.navigate
camera.capture
calendar.event
notification.send
```

This allows capabilities to be extended through plugins without tightly coupling them to the orchestration layer.

### Multi-Device Execution

PAI separates the **source device** from the **execution device**.

```text
Source Device
      ↓
     PAI
      ↓
Device Selection
      ↓
Execution Device
```

The system currently includes execution targets for:

* Server
* Desktop
* Android

---

## Plugin System

PAI uses a plugin-oriented architecture for extending system capabilities.

Plugins can provide:

* new capabilities
* permissions
* platform-specific functionality
* device-side functionality

The project also includes a native plugin runtime based on a **C-compatible interface**, allowing native modules to be dynamically loaded by the system.

```text
Plugin Registry
      ↓
Plugin Manager
      ↓
Capability
      ↓
Execution
```

---

## Authorization

Authorization is integrated into the execution pipeline.

```text
Task
 ↓
Capability
 ↓
Authorization
 ↓
Device Selection
 ↓
Execution
```

A plan describes what the system intends to do; authorization determines whether the action is permitted.

> **Plan ≠ Permission**

---

## Memory

PAI includes a modular memory system designed to support persistent context and retrieval.

```text
                    Memory Manager
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
      Short-Term      Long-Term        Episodic
          │               │               │
          └───────────────┼───────────────┘
                          ▼
                    Vector Retrieval
                          │
                          ▼
                    Knowledge Graph
```

The architecture supports short-term, long-term, episodic, procedural, and semantic/vector-based memory.

---

## Agents and Events

PAI separates agents from task execution.

```text
Agent
  ↓
Goal
  ↓
Planner
  ↓
Tasks
  ↓
Executors
```

The system also uses an event-driven architecture for task and system lifecycle events, enabling clients and other components to observe execution progress.

---

## API & Client

The backend is built with **FastAPI** and provides REST and WebSocket interfaces.

The repository also contains a **Flutter mobile client** that communicates with the backend and participates as a client/device within the PAI ecosystem.

```text
Flutter Client
      │
      │ REST / WebSocket
      ▼
FastAPI Backend
      │
      ▼
PAI Runtime
```

---

## Technology Stack

| Area           | Technologies                                           |
| -------------- | ------------------------------------------------------ |
| Backend        | Python, FastAPI, Uvicorn, Pydantic                     |
| AI / Agents    | OpenAI, Google Generative AI, LangChain, LangGraph     |
| Storage        | SQLAlchemy, SQLite, PostgreSQL-compatible architecture |
| Memory         | ChromaDB, Sentence Transformers, Neo4j                 |
| Automation     | Playwright, OpenCV, PyAudio                            |
| Communication  | REST, WebSocket, HTTPX, Redis, NATS                    |
| Client         | Flutter / Dart                                         |
| Native Runtime | C/C++ compatible plugin interface                      |
| Development    | uv, pytest, Black                                      |

---

## Project Structure

```text
PersonalAi-PAI/
│
├── src/pai/
│   ├── agents/
│   ├── api/
│   ├── devices/
│   ├── executors/
│   ├── event_bus/
│   ├── memory/
│   ├── orchestration/
│   ├── planning/
│   ├── plugins/
│   ├── security/
│   ├── storage/
│   └── tasks/
│
├── frontend/
│   └── pai_mobile/
│
├── tests/
├── pyproject.toml
└── uv.lock
```

---

## Getting Started

### Backend

```bash
git clone https://github.com/ChethanPutran/PersonalAi-PAI.git
cd PersonalAi-PAI

uv sync
uv run uvicorn pai.main:app --host 0.0.0.0 --port 8000
```

API documentation:

```text
http://localhost:8000/docs
```

Health check:

```text
/api/v1/health
```

### Mobile Client

```bash
cd frontend/pai_mobile

flutter pub get
flutter run
```

Additional environment configuration may be required depending on the devices and services being used.

---

## Example

A request such as:

```text
"Open Chrome on my desktop"
```

can be processed as:

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