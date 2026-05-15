# Executors

Executors are the runtime hosts that run workloads on the best device or service.

## Responsibilities

- execute tasks
- host local plugins
- report status
- manage resources
- stream events

## Target executor types

| Executor | Best for |
|---|---|
| Mobile | Sensors, camera, notifications |
| Desktop | Automation, productivity, file tasks |
| Server | Heavy inference and orchestration |
| Edge | Real-time control |

## Current code mapping

The repo does not yet implement a full distributed executor layer. The current code runs locally and can be expanded later using the architecture in `.idea/`.
