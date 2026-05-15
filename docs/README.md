# Personal AI documentation

This folder describes the current Personal AI (PAI) system and the target modular architecture captured in `.idea/`.

## Start here

- `docs/codebase-map.md` - what exists in the repo today
- `docs/architecture.md` - the layered system design
- `docs/tech-stack.md` - the active and planned stack
- `docs/components/` - core subsystems

## Current shape

The repository already contains:

- a LangGraph-based assistant in `src/`
- persistent memory and checkpointing in `src/memory.py`
- dynamic skills in `src/skills/`
- MCP examples in `src/mcp_servers/`
- mobile/UI prototypes under `frontend/ui/`

The docs below connect those implementation pieces to the larger system vision.
