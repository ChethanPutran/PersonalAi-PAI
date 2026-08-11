# Personal AI System (PAI)

**Context-Aware Autonomous Personal Agent System**

A distributed, multimodal AI ecosystem capable of understanding the user, learning continuously, planning autonomously, controlling devices, and executing real-world actions across multiple devices.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

---

## 📖 Table of Contents

- [Vision](#vision)
- [Architecture](#architecture)
- [Features](#features)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [Running the System](#running-the-system)
- [Testing](#testing)
- [Extending](#extending)
- [API Documentation](#api-documentation)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## 🚀 Vision

The Personal AI System transforms from a simple voice assistant into a **distributed cognitive architecture** – a modular AI operating system that becomes a persistent, personalized companion capable of autonomous workflows and real-world interaction.

---

## 🧠 Architecture

```text
                 ┌─────────────────────┐
                 │      AI Kernel      │
                 ├─────────────────────┤
                 │ Memory System       │
                 │ Context Manager     │
                 │ Planning Engine     │
                 │ Capability Router   │
                 │ Event Bus           │
                 │ Security Manager    │
                 └─────────┬───────────┘
                           │
      ┌────────────────────┼────────────────────┐
      │                    │                    │
┌─────▼─────┐      ┌──────▼──────┐      ┌──────▼──────┐
│  Agents   │      │   Plugins   │      │  Executors  │
└───────────┘      └─────────────┘      └─────────────┘
```

- **AI Kernel** – Central coordinator (context, memory, planning, routing)
- **Agents** – Autonomous decision‑makers (travel, research, productivity, etc.)
- **Plugins** – Modular capabilities (browser, vision, speech, calendar, etc.)
- **Executors** – Distributed execution (server, desktop, mobile)

---

## ✨ Features

| Category | Capabilities |
|----------|--------------|
| **Voice & Vision** | STT (Whisper), TTS (Piper), Object detection (YOLO), OCR (PaddleOCR) |
| **Memory** | Short‑term, Long‑term, Episodic, Vector (ChromaDB) |
| **Planning** | Goal decomposition, Workflow execution, Retries |
| **Agents** | Research, Productivity, Communication, Travel, Health, Coding, Finance, Automation |
| **Plugins** | Browser, Calendar, Maps, Transport, Notification, Translation, Form automation, Learning |
| **Executors** | Server (LLM), Desktop (browser, files), Android (sensors, camera) |
| **Event Bus** | NATS / Redis Pub/Sub, async events |
| **API** | REST + WebSocket, real‑time interaction |

---

## 🛠️ Getting Started

### Prerequisites

- Python 3.10 or higher
- Docker & Docker Compose (optional, for full stack)
- `pip` and `virtualenv` (recommended)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/your-org/pai-system.git
   cd pai-system
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate   # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up databases**
   ```bash
   python scripts/setup_db.py
   ```
   This creates:
   - `data/long_term.db` – key‑value memory
   - `data/episodic.db` – episode storage
   - `data/pai.db` – relational tables (users, tasks)
   - `data/chroma/` – vector embeddings

5. **Configure environment** (copy and edit)
   ```bash
   cp .env.example .env
   ```
   Edit `.env` with your API keys (OpenAI, Google Maps, Uber, etc.)

---

## ⚙️ Configuration

Configuration is managed via `config/development.yaml` and environment variables.

Key settings in `.env`:
```ini
PAI_ENV=development
PAI_DEBUG=true
OPENAI_API_KEY=your_key
GOOGLE_MAPS_API_KEY=your_key
UBER_API_KEY=your_key
REDIS_HOST=localhost
NATS_SERVERS=nats://localhost:4222
```

For production, set `PAI_ENV=production` and use a PostgreSQL URL.

---

## 🏃 Running the System

### Using Docker Compose (recommended for full stack)
```bash
docker-compose -f docker/docker-compose.yml up --build
```
Services started:
- Redis (cache / pub/sub)
- NATS (event bus)
- ChromaDB (vector store)
- PAI API (FastAPI on port 8000)

### Running locally (development)
```bash
python -m pai.main
```

The API will be available at `http://localhost:8000`.

### Running as a distributed system

Start each executor separately (example):
```bash
# Server executor (heavy AI)
python -m pai.executors.server_executor --connect ws://localhost:8000

# Desktop executor (browser automation)
python -m pai.executors.desktop_executor --connect ws://localhost:8000

# Android executor (via ADB)
python -m pai.executors.android_executor --device-id emulator-5554
```

---

## 🧪 Testing

Run the full test suite:
```bash
./scripts/run_tests.sh
```

Or with `pytest` directly:
```bash
pytest tests/ -v --asyncio-mode=auto
```

With coverage:
```bash
pytest --cov=src/pai --cov-report=html tests/
open htmlcov/index.html
```

---

## 🔧 Extending

### Creating a New Plugin

1. Create `src/pai/plugins/my_plugin.py`:
```python
from pai.plugins.base_plugin import BasePlugin

class MyPlugin(BasePlugin):
    name = "my_plugin"
    
    async def initialize(self):
        pass
    
    def get_capabilities(self):
        return ["my_plugin.do_something"]
    
    async def execute(self, action, params):
        if action == "my_plugin.do_something":
            return {"result": "done"}
```

2. The `PluginManager` auto‑discovers it.

### Creating a New Agent

1. Create `src/pai/agents/my_agent.py`:
```python
from pai.agents.base_agent import BaseAgent

class MyAgent(BaseAgent):
    name = "my_agent"
    
    async def process_goal(self, goal, context):
        # Use plugins via self.use_plugin(...)
        return {"response": f"Processed: {goal}"}
```

2. Register it in `AgentManager.__init__`.

### Adding a New Executor

Implement `BaseExecutor` and register it with `ExecutorScheduler`.

---

## 📡 API Documentation

Once running, visit:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

### WebSocket Endpoint

Connect to `ws://localhost:8000/ws` and send:

```json
{
  "type": "goal",
  "goal": "Search for latest AI papers",
  "context": { "topic": "transformers" }
}
```

Receive results as JSON events.

### REST Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | System info |
| GET | `/health` | Health check |
| POST | `/api/agents/{name}/goal` | Send goal to specific agent |
| GET | `/api/plugins` | List loaded plugins |
| POST | `/api/memory/store` | Store a memory |
| GET | `/api/memory/recall` | Recall memories |

---

## 🐛 Troubleshooting

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` again. |
| `NATS connection failed` | Start NATS: `docker run -p 4222:4222 nats` or use Docker Compose. |
| `ChromaDB errors` | Ensure `chromadb` is installed and `data/chroma` is writable. |
| `Vision plugin fails` | Install `ultralytics` and `paddlepaddle` (optional). |
| `ADB device not found` | Install Android SDK, enable USB debugging, or use an emulator. |

For more help, open an issue on GitHub.

---

## 📄 License

This project is licensed under the MIT License – see the [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgements

- [LangGraph](https://github.com/langchain-ai/langgraph) – agent orchestration
- [FastAPI](https://fastapi.tiangolo.com/) – API framework
- [Playwright](https://playwright.dev/) – browser automation
- [ChromaDB](https://www.trychroma.com/) – vector memory
- [NATS](https://nats.io/) – event bus

---

*Built with ❤️ for a truly personal AI.*
```

---

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

## Final Steps

1. Create the `.env` file from the example.
2. Run `python scripts/setup_db.py` to initialize databases.
3. Execute `./scripts/run_tests.sh` to verify everything works.
4. Start the system with `python -m pai.main` or `docker-compose up`.

The Personal AI System is now fully operational, with all components, tests, and documentation ready.