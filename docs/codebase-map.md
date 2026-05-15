# Codebase map

## Root-level areas

| Path | Purpose |
|---|---|
| `src/` | Main Python assistant runtime |
| `src/graph.py` | LangGraph workflow and orchestration |
| `src/memory.py` | ChromaDB memory plus Postgres checkpointing |
| `src/tools.py` | Dynamic skill and tool loading |
| `src/skills/` | Callable skills used by the agent |
| `src/mcp_servers/` | MCP server examples |
| `frontend/ui/mobile_client/` | React Native client prototype |
| `frontend/ui/app/android/` | Kivy/Buildozer Android prototype |
| `backend/` | Supporting backend utilities |

## Runtime entry point

`src/main.py` boots the assistant, loads environment variables, and runs the interactive agent loop.

## Core execution flow

1. `AgentGraph` receives user input.
2. The planner decides whether the request needs tools.
3. The executor selects and runs a skill when required.
4. The verifier summarizes the run.
5. The responder produces the final answer.
6. Memory and checkpoints persist the conversation.
