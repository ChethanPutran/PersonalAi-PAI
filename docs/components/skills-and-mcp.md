# Skills and MCP servers

## Skills

Skills are Python callables loaded dynamically from `src/skills/`.

### Current skills

- calculator
- web_search
- file_ops
- expense_tracker

### Loading model

`src/tools.py` loads each module that defines `execute(...)` and exposes it to the agent.

## MCP servers

The repo also contains MCP examples under `src/mcp_servers/`.

### Current examples

- math MCP server
- weather MCP server
- expense tracker MCP server

## Purpose

Skills are used by the local assistant runtime, while MCP servers show how the system can expose capabilities as standardized remote tools.
