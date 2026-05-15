# Technology stack

## Active Python stack

| Area | Technologies |
|---|---|
| Core runtime | Python 3.12 |
| Graph/orchestration | LangGraph |
| LLM integrations | LangChain, Google GenAI, NVIDIA endpoints |
| Memory | ChromaDB, PostgreSQL checkpointing |
| Validation | Pydantic |
| Env config | python-dotenv |
| Search/data tools | ddgs, requests |

## Planned or referenced stack

| Area | Technologies |
|---|---|
| API layer | FastAPI |
| Realtime messaging | WebSockets, NATS, Kafka |
| Distributed RPC | gRPC |
| Voice | Whisper, Piper TTS |
| Vision | OpenCV, YOLO, SAM2 |
| Frontend | Flutter, React Native |
| Automation | Playwright, ADB |
| DevOps | Docker, Kubernetes, GitHub Actions |

## Local persistence

The codebase uses:

- ChromaDB for semantic memory
- SQLite for local state
- PostgreSQL checkpoint storage via LangGraph

## Notes

`pyproject.toml` lists the installed Python dependencies that currently power the assistant runtime.
