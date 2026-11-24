"""Errors raised by the agent runtime (each maps to one HTTP status in the API layer)."""


class AgentError(Exception):
    """Base class."""


class AgentNotFound(AgentError):
    """Unknown id, or an agent that belongs to someone else (indistinguishable on purpose)."""


class AgentLimitError(AgentError):
    """Too many agents, or too many unread messages."""


class AgentSpawnError(AgentError):
    """The request cannot start an agent (unknown/denied tools, bad spec)."""


class AgentStateError(AgentError):
    """The action does not make sense in the agent's current state (e.g. message a finished agent)."""
