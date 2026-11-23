from pathlib import Path

from pydantic import BaseModel, Field

from pai.common.enums.plugins import ArtifactStoreType

class PluginSecurityConfig(BaseModel):
    """Plugin security configuration."""
    require_signature: bool = Field(default=True)
    verify_signature: bool = Field(default=True)
    allow_unsigned_plugins: bool = Field(default=False)
    execution_timeout_seconds: int = Field(default=60)
    allow_remote_plugins: bool = Field(default=True)
    allow_local_plugins: bool = Field(default=True)
    allow_third_party_plugins: bool = Field(default=True)

class S3Config(BaseModel):
    """S3 configuration for plugin storage."""
    bucket: str | None = None
    prefix: str = Field(default="pai-plugins")
    ttl_seconds: int = Field(default=3600)

class PluginConfig(BaseModel):
    """Plugin configuration."""

    plugin_registry_path: Path = Path("./plugin_registry/plugins")
    plugin_artifact_store: ArtifactStoreType = ArtifactStoreType.LOCAL
    plugin_local_artifact_path: Path = Path("./plugin_registry/.packages")
    s3_config: S3Config = Field(default_factory=S3Config)
    enabled_plugins: list[str] = Field(
        default_factory=lambda: [
            "browser",
            "desktop",
            "files",
            "screen",
            "camera",
            "shell",
        ]
    )

    security: PluginSecurityConfig = Field(default_factory=PluginSecurityConfig)