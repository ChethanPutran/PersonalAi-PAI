# Memory

Memory is split into semantic storage, conversation history, and execution checkpoints.

## Current implementation

| File | Role |
|---|---|
| `src/memory.py` | ChromaDB memory collections and checkpoint manager |
| `src/models.py` | Message and memory data structures |

## Stored memory types

- conversation memory
- skill execution memory
- episodic memory
- checkpoint history

## Behavior

The assistant stores conversation results and skill outputs, then uses LangGraph checkpointing to resume or inspect prior runs.
