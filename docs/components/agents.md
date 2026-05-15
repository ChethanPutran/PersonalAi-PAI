# Agents

Agents are autonomous workers that reason about goals and coordinate tools.

## Responsibilities

- interpret user goals
- break work into steps
- choose skills and plugins
- handle retries and approvals
- summarize outcomes

## Current code mapping

`src/graph.py` implements the current agent flow:

- planner node
- executor node
- verifier node
- responder node
- cleanup node

## Direction

The `.idea` notes describe a future multi-agent architecture where specialized agents handle research, productivity, communication, vision, and automation tasks.
