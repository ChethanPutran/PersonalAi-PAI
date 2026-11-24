
"""PluginArtifactStore — resolves PackageArtifact objects to downloadable URLs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from pai.config.models.plugin import PluginConfig
from pai.plugins.packager import PackageArtifact
from pai.common.constants import API_PREFIX


class PluginArtifactStore(ABC):
    @abstractmethod
    def url_for(self, artifact: PackageArtifact) -> str:
        """Return a downloadable URL for an artifact."""
        ...


class LocalArtifactStore(PluginArtifactStore):
    """Serve local artifacts through the application's HTTP API."""

    def __init__(
        self,
        packages_root: Path,
        base_url: str = API_PREFIX + "/plugins/artifacts/",
    ) -> None:
        self.packages_root = packages_root.resolve()
        self.base_url = base_url.rstrip("/") + "/"

    def url_for(self, artifact: PackageArtifact) -> str:
        artifact_path = artifact.packagePath.resolve()

        if not artifact_path.is_relative_to(self.packages_root):
            raise ValueError("Artifact is outside the packages directory")

        relative_path = artifact_path.relative_to(self.packages_root)

        return f"{self.base_url}{relative_path.as_posix()}"

    def get_artifact_path(self, artifact_path: str) -> Path:
        """Resolve a URL path to a local artifact securely."""
        path = (self.packages_root / artifact_path).resolve()

        if not path.is_relative_to(self.packages_root):
            raise ValueError("Invalid artifact path")

        return path


class FileArtifactStore(PluginArtifactStore):
    """Expose artifacts through file:// URLs."""

    def url_for(self, artifact: PackageArtifact) -> str:
        return artifact.packagePath.resolve().as_uri()


class S3ArtifactStore(PluginArtifactStore):
    """Upload artifacts to S3 and generate presigned URLs."""

    def __init__(
        self,
        bucket: str,
        prefix: str ,
        ttl_seconds: int = 3600,
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.ttl = ttl_seconds

    def url_for(self, artifact: PackageArtifact) -> str:
        import boto3

        # Use a stable relative key instead of the local absolute path.
        key = f"{self.prefix}/{artifact.packagePath.name}"

        client = boto3.client("s3")
        client.upload_file(str(artifact.packagePath), self.bucket, key)

        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=self.ttl,
        )


def create_artifact_store(config: PluginConfig) -> PluginArtifactStore:
    if config.plugin_artifact_store == "local":
        return LocalArtifactStore(
            packages_root=config.plugin_local_artifact_path,
        )

    if config.plugin_artifact_store == "s3":
        if not config.s3_bucket:
            raise ValueError("s3_bucket is required when using S3 storage")

        return S3ArtifactStore(
            bucket=config.s3_bucket,
            prefix=config.s3_prefix,
            ttl_seconds=config.s3_ttl_seconds,
        )

    raise ValueError(
        f"Unsupported artifact store: {config.plugin_artifact_store}"
    )
