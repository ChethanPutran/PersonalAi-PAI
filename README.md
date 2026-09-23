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

```text
User Goal
    ↓
Planner
    ↓
Task
    ↓
Capability Resolution
    ↓
Authorization
    ↓
Device Selection
    ↓
Desktop Executor
    ↓
Browser Action
    ↓
Result
```

The phone does not need to perform the action itself; it can act as the interface through which the distributed PAI runtime coordinates execution.

---

## Design Principles

### Separation of Planning and Execution

```text
Planner → What should happen?

Executor → How should it happen?
```

### Capability-Based Design

Tasks depend on capabilities rather than hard-coded implementations.

### Device Independence

Requests and execution targets are treated as separate concepts.

### Explicit Authorization

Actions must pass authorization before execution.

### Modular Architecture

Planning, memory, plugins, devices, executors, agents, storage, and APIs are designed as separate subsystems.

---

## Development Status

PAI is an **actively developed engineering project**.

The current implementation includes the core architecture for:

* task orchestration
* planning and task graphs
* task lifecycle management
* capability resolution
* device and executor management
* authorization
* plugins
* memory
* event-driven communication
* REST/WebSocket APIs
* Flutter client integration

The project is continuing toward more robust multi-device execution, richer plugins, improved memory, observability, and stronger end-to-end integration.

---

## Roadmap

* Advanced planning and replanning
* More device executors
* Expanded plugin ecosystem
* Improved permission management
* Better memory consolidation and retrieval
* Distributed execution and observability
* Improved mobile experience
* Deployment and infrastructure automation

---

## License

MIT License

## Author

**Chethan Putran**

PAI is an ongoing exploration of:

**AI Agents · Distributed Systems · Task Orchestration · Device Automation · Plugin Architectures · Memory Systems · AI Infrastructure**
