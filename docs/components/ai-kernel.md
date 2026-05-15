# AI kernel

The AI kernel is the orchestration center of the system.

## Responsibilities

- manage context
- route capabilities
- coordinate memory
- plan actions
- enforce permissions
- orchestrate execution

## Current code mapping

The closest implementation is `src/graph.py`, where `AgentGraph` builds the planner/executor/verifier/responder workflow.

## Target role

In the full PAI architecture, the kernel becomes the central coordinator for agents, plugins, devices, and event routing.
