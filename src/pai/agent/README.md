# pai.agent — long-running ReAct agents

A PAI **agent** works toward one goal by looping *think → act → observe*, using a set of tools
the system gives it, until it finishes, is stopped, or runs out of budget. The system spawns
agents on demand (e.g. "find a specific playlist", "fill in this web form") and the user can
**talk to a running agent in real time**.

> `pai/agents/` (plural) is the older `BaseAgent`/`AgentManager` stub. This package
> (`pai/agent`, singular) is the runtime; the two are independent.

```
user / planner ──► agent.start (builtin capability)  or  POST /agents
                        │
                  AgentRuntime.spawn()  ── resolves tools ∩ the user's live capabilities
                        │                  enforces per-user / global limits
                        ▼
                  AgentRun ── ReactLoop ── LLM (one JSON action per step)
                     ▲  │ events             │
   user messages ────┘  ▼                    ▼ tool call
   (inbox)         EventLog ──► SSE / WS    CapabilityTool ──► orchestrator.execute_action()
                                                              (resolve → select device → authorize
                                                               → task → result; same as any request)
```

## The loop (`loop.py`)

Every reply is **one JSON object** — it works with every provider (the LLM layer has no native
tool calling):

```json
{"thought": "why", "action": {"tool": "browser.open", "args": {"url": "https://…"}}}
```

Control tools handled by the loop itself: `say` (tell the user), `ask_user` (**block until the
user answers**), `finish` (the result). Everything else is one of the user's capabilities.

Per step: honour pause/cancel → inject any live user messages → call the model → parse (bad
JSON comes back as an error to fix, up to `max_format_errors`) → run the tool → add the result
to the history. Old results are collapsed so the prompt stays small.

## Tools (`tools.py`)

* An agent only gets the capabilities named in `spec.tools` (ids or globs like `browser.*`),
  intersected with what the user actually has *at spawn time*. Unknown names fail the spawn with
  the list of what is available.
* `agent.*` and `chat.*` are never grantable (no agents spawning agents yet; `chat.reply` is not
  an action).
* Arguments are validated against the capability's JSON schema before anything runs.
* Each call passes a **unique call id** as the orchestrator request id. `execute_action`
  de-duplicates identical actions within one request id, so reusing it would replay an old
  result instead of acting again.
* Built-in capabilities (`task.*`, `schedule.*`) work like on the planner path (no plugin
  authorization); plugin capabilities are authorized as an autonomous agent
  (`Origin.AGENT_AUTONOMOUS`: sensitive actions need on-screen approval, a denial comes back to
  the model as `DENIED … do not retry`).

## Safety

| Concern | How |
|---|---|
| Prompt injection via tool output (web pages, files, screens) | Results are size-bounded, passed through `PromptGuard`, and wrapped in `<<<BEGIN_TOOL_RESULT>>>` boundaries the model is told are **data**; our boundary markers inside any untrusted text are neutralised. Only the goal and `USER MESSAGE` lines are instructions. |
| Secrets / canned replies | Everything the agent says (`thought`, `say`, `ask_user`, `finish`) goes through `PromptGuard.sanitize_response`; empty text is never sent to it. |
| Runaway agents | Hard budgets in `AgentLimits`: steps (≤100), wall clock (≤4h), per-tool timeout, wait-for-user timeout. Per-user (3) and global (20) concurrency limits. |
| Cross-user access | Every runtime method is scoped to the owner; another user's agent is a plain "not found". |
| Confirmations | Unchanged: they ride the normal task flow (`task.awaiting_confirmation`). |

## Talking to an agent

| | |
|---|---|
| `POST /agents` | start `{goal, tools[], name?, device_id?, session_id?, max_steps?, max_runtime_seconds?}` |
| `GET /agents`, `GET /agents/{id}` | list / state + progress |
| `POST /agents/{id}/messages` | say something (answers its question or changes its course) |
| `POST /agents/{id}/pause` `/resume` `/cancel` | pause is cooperative (next step boundary); cancel interrupts a running tool wait |
| `GET /agents/{id}/events` | SSE; resume with `Last-Event-ID` or `?after=<seq>` |
| `WS /agents/{id}/ws?token=…&after=<seq>` | both ways: events out; `{"type":"message","text"}`, `pause`, `resume`, `cancel`, `ping` in |

Events: `state`, `thought`, `action`, `observation`, `message`, `question`, `user`, `final`,
`error`. Low-volume ones (`state`, `message`, `question`, `final`; never tool arguments) are also
published on the event bus as `agent.<kind>`.

Planner-facing builtin capabilities (so the *system* can start and steer agents):
`agent.start`, `agent.message`, `agent.status`, `agent.cancel`, `agent.list`.

## Extending

* **A new kind of tool** — implement `Tool` (`spec` + `async run(args, ctx)`) and add it to the
  `ToolRegistry`; no change to the loop. Capability tools are just one implementation.
* **Another source of capabilities** — implement `CapabilitySource` (`describe`, `execute`).
* **Multiple agents working together (future)** — `AgentRuntime.spawn/send` are the seams: a
  coordinator is an agent whose tools are `agent.start`/`agent.message` (currently denied by
  `DENY_PREFIXES` on purpose until quotas and cycle protection exist).
* **Screen/GUI agents** — add input and screenshot capabilities as plugins; the loop is unchanged.
  Image input is not supported by the LLM layer yet.

## Known limits (v1)

* **In memory only**: a server restart ends running agents (they are cancelled at shutdown) and
  history is gone. Persisting transcripts/state is the next step.
* One process: runs are not distributed across servers.
* Cancelling an agent stops *waiting* for an in-flight tool; the underlying task may still finish
  on the device.
* A tool call includes any wait for the user's on-screen confirmation. If that exceeds
  `tool_timeout_seconds` (default 300 s) the call is cancelled together with its pending
  confirmation, and the agent is told it did not finish.
* No streaming of the model's tokens (each step is one completion) and no native tool-calling API.

## Tests

```bash
.venv/bin/python -m pytest tests/agent tests/prompts/test_agent_react_prompt.py -q
```
`tests/agent` uses a scripted LLM and a recording capability source; `test_wiring.py` boots the
real app and runs an agent through the real orchestrator.
