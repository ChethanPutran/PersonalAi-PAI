"""The real app boots: every manager initializes, routes mount, shutdown is clean."""
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "leaks"))
from helpers import isolated_config  # noqa: E402

from pai.server.factory import create_application  # noqa: E402


def test_app_starts_and_stops_with_all_managers_wired(tmp_path):
    # Every writable path goes to tmp_path. (Environment variables do NOT override config: the
    # YAML is the only source of non-secret settings, so a DATABASE__URL env var used to be
    # silently ignored and this test ran against the developer's real database.)
    app = create_application(isolated_config(tmp_path))
    with TestClient(app) as client:                       # runs lifespan: startup ... shutdown
        orch = app.state.context.orchestrator
        # SecurityManager refuses to initialize without these (this was the startup crash)
        assert orch.security_manager.plugin_manager is orch.plugin_manager
        assert orch.security_manager.device_manager is orch.device_manager
        assert orch.security_manager.is_initialized()
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/v1/chat/stream" in paths and "/api/v1/voice/capabilities" in paths
    assert (tmp_path / "pai.db").exists()                 # it really used the isolated DB
