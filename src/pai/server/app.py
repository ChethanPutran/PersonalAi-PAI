"""Importable ASGI entry point for uvicorn.

uvicorn can only hot-reload or fork workers when it is handed a *string*
("module:attribute"), not an already-constructed app object. This module
exists so that the runner can pass such a string.

Use `uvicorn pai.server.app:create_app --factory` on the command line, or
`uvicorn.run("pai.server.app:create_app", factory=True, ...)` from Python.
"""
from pai.config.loader import load_config
from pai.server.factory import create_application


def create_app():
    """Zero-argument factory so uvicorn --factory works."""
    return create_application(load_config())