import os
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class AdminConfig(BaseModel):
    """Admin panel (see pai/api/admin)."""
    enabled: bool = True
    # Register the device-free `admin.test.*` capabilities (sleep / echo / fail / stream) so
    # DAGs, long-running tasks and kill can be demonstrated without a phone. Off by default:
    # enable in config.yaml (or PAI_ADMIN_TEST_CAPABILITIES=1) for development only.
    test_capabilities: bool = False
    # Finished DAG runs kept in memory for the admin DAG view (bounded).
    dag_history_size: int = Field(default=200, ge=1, le=10000)
    # Persistent history of task/DAG events and finished DAG runs (JSONL, one file per UTC day).
    history_enabled: bool = True
    history_dir: str = "./data/admin_history"
    # Days of events and finished DAG runs kept; older day files are deleted. (tasks.cleanup_interval_seconds is
    # how OFTEN cleanup runs, not how long things are kept, so it is not used for this.)
    history_retention_days: int = Field(default=7, ge=1, le=365)
    max_active_page_size: int = Field(default=500, ge=1)
    max_test_upload_bytes: int = Field(default=2_000_000, ge=1000)
    max_test_text_chars: int = Field(default=1000, ge=10)

    @model_validator(mode="after")
    def _env_override(self) -> "AdminConfig":
        # Config reads only YAML (no env). This one switch also honours the environment so the
        # test capabilities can be turned on for a dev run without editing config.yaml.
        if os.environ.get("PAI_ADMIN_TEST_CAPABILITIES", "").strip().lower() in ("1", "true", "yes", "on"):
            self.test_capabilities = True
        return self
