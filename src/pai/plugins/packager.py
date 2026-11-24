
from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from loguru import logger

from pai.common.enums.device import DevicePlatform
from pai.plugins.manifest import PluginManifest


@dataclass(frozen=True)
class PackageArtifact:
    packagePath: Path
    checksum: str
    size: int


class PluginPackager:

    def __init__(self, packages_root: Path) -> None:
        self.packages_root = packages_root


    def package(
        self,
        manifest: PluginManifest,
        version_dir: Path,
        platform: DevicePlatform,
    ) -> PackageArtifact:
        """Package the artifact declared for one platform."""

        if platform not in manifest.platforms:
            raise ValueError(
                f"Platform {platform!r} is not declared by {manifest.id}"
            )

        out_dir = (
            self.packages_root
            / manifest.id
            / manifest.version
            / platform.value
        )

        tarball = out_dir / "package.tar.gz"
        sidecar = out_dir / "package.json"

        package_files = self._collect_artifacts(
            manifest,
            version_dir,
            platform,
        )

        source_digest = self._hash_sources(package_files)

        if tarball.exists() and sidecar.exists():
            try:
                meta = json.loads(sidecar.read_text())

                if (
                    meta.get("sourceDigest") == source_digest
                    and self._verify_cached_package(tarball, meta)
                ):
                    return PackageArtifact(
                        packagePath=tarball,
                        checksum=meta["checksum"],
                        size=meta["size"],
                    )
            except (OSError, ValueError, KeyError):
                logger.warning("Invalid package cache: {}", tarball)

        out_dir.mkdir(parents=True, exist_ok=True)

        checksum, size = self._build_tarball(
            package_files,
            tarball,
        )

        metadata = {
            "checksum": checksum,
            "size": size,
            "sourceDigest": source_digest,
            "builtAt": datetime.now(timezone.utc).isoformat(),
        }

        sidecar.write_text(json.dumps(metadata, indent=2))

        logger.info(
            "Packaged {}@{} for {} ({} bytes, {})",
            manifest.id,
            manifest.version,
            platform,
            size,
            checksum,
        )

        return PackageArtifact(tarball, checksum, size)


    #: Per-platform folder that holds the build output (.so, dex, .py, assets, ...).
    ARTIFACT_DIR = "artifact"

    @staticmethod
    def _collect_artifacts(
        manifest: PluginManifest,
        version_dir: Path,
        platform: DevicePlatform,
    ) -> list[tuple[Path, str]]:
        """Collect what ships for one platform: every file in ``<version>/<platform>/artifact/``.

        Nothing outside that folder is ever packaged (no source, tests, build scripts, or a
        library left next to the manifest). A plugin can ship several files there.

        Inside the archive each file lands at ``<platform>/<path inside artifact/>``, e.g.
        ``linux/artifact/libx.so`` -> ``linux/libx.so``. The manifest's ``artifact`` is that
        packaged path (``linux/libx.so``), the same string the device resolves in the
        extracted package, and it must be one of the packaged files.

        Symlinks, paths that escape the folder and dotfiles (.gitkeep, .DS_Store) never ship.
        """

        spec = manifest.platforms[platform]
        artifact = spec.artifact
        relative = PurePosixPath(artifact)

        if (
            not artifact
            or relative.is_absolute()
            or ".." in relative.parts
            or "\\" in artifact
            or "\x00" in artifact
        ):
            raise ValueError(f"Invalid artifact path: {artifact}")
        if relative.parts[0] != platform.value or len(relative.parts) < 2:
            raise ValueError(
                f"Artifact {artifact!r} must live under '{platform.value}/' "
                f"(e.g. '{platform.value}/libplugin.so')"
            )

        root = version_dir.resolve()
        artifact_dir = root / platform.value / PluginPackager.ARTIFACT_DIR
        if artifact_dir.is_symlink():
            raise ValueError(f"Symlinked artifact folder not allowed: {artifact_dir}")
        if not artifact_dir.is_dir():
            raise FileNotFoundError(
                f"Artifact folder not found: {artifact_dir} - put the build output for "
                f"{manifest.id}@{manifest.version} ({platform.value}) there"
            )
        artifact_root = artifact_dir.resolve()

        files: list[tuple[Path, str]] = []
        for path in sorted(artifact_root.rglob("*")):
            inside = path.relative_to(artifact_root)
            if any(part.startswith(".") for part in inside.parts):
                continue                      # .gitkeep, .DS_Store, ...
            if path.is_symlink():
                raise ValueError(f"Symlink not allowed in artifact folder: {path}")
            if not path.is_file():
                continue                      # directories are implied by file paths
            if not path.resolve().is_relative_to(artifact_root):
                raise ValueError(f"Artifact file escapes artifact folder: {path}")
            files.append((path, (PurePosixPath(platform.value) / inside.as_posix()).as_posix()))

        packaged = {archive for _, archive in files}
        if relative.as_posix() not in packaged:
            raise FileNotFoundError(
                f"Declared artifact {artifact!r} not found: expected the file "
                f"{platform.value}/{PluginPackager.ARTIFACT_DIR}/"
                f"{PurePosixPath(*relative.parts[1:]).as_posix()} in {version_dir}"
            )

        logger.debug(
            "Collected {} artifact folder: {} file(s): {}",
            platform, len(files), ", ".join(sorted(packaged)),
        )
        return files

    # ---------------------------------------------------------
    # Deterministic package generation
    # ---------------------------------------------------------

    @staticmethod
    def _build_tarball(
        files: list[tuple[Path, str]],
        dest: Path,
    ) -> tuple[str, int]:
        """Create deterministic TAR.GZ and calculate checksum."""

        tar_buffer = io.BytesIO()

        with tarfile.open(
            fileobj=tar_buffer,
            mode="w",
            format=tarfile.PAX_FORMAT,
        ) as tar:

            for source, archive_path in files:
                info = tar.gettarinfo(
                    str(source),
                    arcname=archive_path,
                )

                info.mtime = 0
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                info.mode = 0o755

                with source.open("rb") as file:
                    tar.addfile(info, file)

        tar_bytes = tar_buffer.getvalue()

        compressed = io.BytesIO()

        with gzip.GzipFile(
            fileobj=compressed,
            mode="wb",
            compresslevel=6,
            mtime=0,
            filename="",
        ) as gz:
            gz.write(tar_bytes)

        payload = compressed.getvalue()

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)

        checksum = (
            f"sha256:{hashlib.sha256(payload).hexdigest()}"
        )

        return checksum, len(payload)

    # ---------------------------------------------------------
    # Cache validation
    # ---------------------------------------------------------

    @staticmethod
    def _hash_sources(
        files: list[tuple[Path, str]],
    ) -> str:
        h = hashlib.sha256()

        for source, archive_path in files:
            h.update(archive_path.encode("utf-8"))
            h.update(b"\0")

            with source.open("rb") as file:
                for chunk in iter(
                    lambda: file.read(1024 * 1024),
                    b"",
                ):
                    h.update(chunk)

        return f"sha256:{h.hexdigest()}"

    @staticmethod
    def _verify_cached_package(
        tarball: Path,
        metadata: dict[str, Any],
    ) -> bool:
        digest = hashlib.sha256()

        with tarball.open("rb") as file:
            for chunk in iter(
                lambda: file.read(1024 * 1024),
                b"",
            ):
                digest.update(chunk)

        actual_checksum = f"sha256:{digest.hexdigest()}"

        return (
            actual_checksum == metadata.get("checksum")
            and tarball.stat().st_size == metadata.get("size")
        )

    # ---------------------------------------------------------
    # Output location
    # ---------------------------------------------------------

    def _output_dir(
        self,
        manifest: PluginManifest,
        plugin_dir: Path,
    ) -> Path:
        return (
            self.packages_root
            / manifest.id
            / manifest.version
            / plugin_dir.name
        )
