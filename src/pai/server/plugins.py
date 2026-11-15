"""Plugin loading + lifecycle for the device agent.

Two adapters are provided:

  * NativePluginAdapter — ctypes, for .so/.dll/.dylib artifacts
  * PythonPluginAdapter — direct import, for .py artifacts (dev/testing)

Customize the ABI symbol names / Python plugin protocol to match your
actual plugins.
"""
from __future__ import annotations

import asyncio
import ctypes
import hashlib
import importlib.util
import json
import logging
import shutil
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# Adapters
# ============================================================================

class PluginAdapter(ABC):
    """Abstract plugin runtime.

    A concrete adapter is chosen per installed artifact by
    :func:`pick_adapter` based on file extension. Each adapter owns the
    full lifecycle of a single plugin:

      * :meth:`start` — load and initialise (may be slow, may do I/O).
      * :meth:`invoke` — run a capability and return its result. Adapters
        should serialise concurrent invocations via an internal lock
        unless they can guarantee their backend is reentrant.
      * :meth:`stop` — release resources.

    All three are coroutines so adapters can bridge to sync backends
    via ``asyncio.to_thread`` / ``run_in_executor`` if needed.
    """

    @abstractmethod
    async def start(
        self, manifest: Dict[str, Any], artifact: Path, config: Dict[str, Any]
    ) -> None:
        """Load ``artifact`` and initialise the plugin with ``config``."""
        ...

    @abstractmethod
    async def invoke(self, capability: str,
                     parameters: Dict[str, Any]) -> Any:
        """Execute ``capability`` with ``parameters``; return any JSON-able value."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Release plugin resources. Safe to call when not started."""
        ...


class NativePluginAdapter(PluginAdapter):
    """
    ctypes-based adapter. Adjust the symbol names + signatures to match
    your native plugins.

    Expected ABI (customize as needed):

        int32_t pai_plugin_init(const char* config_json, void** out_handle);
        int32_t pai_plugin_invoke(
            void* handle,
            const char* capability,
            const char* params_json,
            char** out_result_json
        );
        void    pai_plugin_free_string(char* s);
        void    pai_plugin_shutdown(void* handle);

    Return 0 on success. Result JSON on success should be the raw
    plugin output (any JSON value).
    """

    INIT_SYM = "pai_plugin_init"
    INVOKE_SYM = "pai_plugin_invoke"
    FREE_SYM = "pai_plugin_free_string"
    SHUTDOWN_SYM = "pai_plugin_shutdown"

    def __init__(self) -> None:
        # Populated by start(); all None until the library is loaded.
        self._lib: Optional[ctypes.CDLL] = None
        self._handle = ctypes.c_void_p()
        self._init_fn = None
        self._invoke_fn = None
        self._free_fn = None
        self._shutdown_fn = None
        # Serialises invoke() calls — most native ABI's are not reentrant.
        self._lock = asyncio.Lock()

    async def start(
        self, manifest: Dict[str, Any], artifact: Path, config: Dict[str, Any]
    ) -> None:
        """Load the shared library and call ``pai_plugin_init``."""
        logger.debug(
            "Loading native plugin %s from %s",
            manifest.get("id"), artifact,
        )
        self._lib = ctypes.CDLL(str(artifact))

        self._init_fn = getattr(self._lib, self.INIT_SYM)
        self._init_fn.argtypes = [
            ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)
        ]
        self._init_fn.restype = ctypes.c_int32

        self._invoke_fn = getattr(self._lib, self.INVOKE_SYM)
        self._invoke_fn.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_char_p),
        ]
        self._invoke_fn.restype = ctypes.c_int32

        # ``pai_plugin_free_string`` is optional — some plugins may not
        # allocate strings at all.
        try:
            self._free_fn = getattr(self._lib, self.FREE_SYM)
            self._free_fn.argtypes = [ctypes.c_char_p]
            self._free_fn.restype = None
        except AttributeError:
            self._free_fn = None

        # ``pai_plugin_shutdown`` is optional — allow fire-and-forget plugins.
        try:
            self._shutdown_fn = getattr(self._lib, self.SHUTDOWN_SYM)
            self._shutdown_fn.argtypes = [ctypes.c_void_p]
            self._shutdown_fn.restype = None
        except AttributeError:
            self._shutdown_fn = None

        rc = self._init_fn(json.dumps(config).encode(),
                           ctypes.byref(self._handle))
        if rc != 0:
            raise RuntimeError(
                f"{self.INIT_SYM} returned {rc} for {manifest.get('id')}"
            )
        logger.info("Native plugin initialised: %s", manifest.get("id"))

    async def invoke(
        self, capability: str, parameters: Dict[str, Any]
    ) -> Any:
        """Call ``pai_plugin_invoke`` and decode the returned JSON."""
        if self._lib is None:
            raise RuntimeError("Plugin not started")

        async with self._lock:
            out = ctypes.c_char_p()
            rc = self._invoke_fn(
                self._handle,
                capability.encode(),
                json.dumps(parameters).encode(),
                ctypes.byref(out),
            )
            if rc != 0:
                raise RuntimeError(f"{self.INVOKE_SYM} returned {rc}")
            try:
                raw = ctypes.cast(out, ctypes.c_char_p).value
                return json.loads(raw.decode()) if raw else None
            finally:
                # Always free the plugin-allocated result string, even if
                # JSON decoding raised.
                if self._free_fn is not None and out:
                    self._free_fn(out)

    async def stop(self) -> None:
        """Call ``pai_plugin_shutdown`` (if present) and drop the library."""
        if self._shutdown_fn is not None and self._handle:
            try:
                self._shutdown_fn(self._handle)
            except Exception:
                logger.exception("native plugin shutdown failed")
        self._lib = None
        self._handle = ctypes.c_void_p()
        logger.debug("Native plugin stopped")


class PythonPluginAdapter(PluginAdapter):
    """
    Loads a Python module and instantiates either:

        create_plugin(config) -> Plugin-like object
        Plugin(config)        -> Plugin-like object

    Plugin-like object must expose:

        invoke(capability: str, parameters: dict) -> Any
        shutdown() -> None          # optional
    """

    def __init__(self) -> None:
        # Populated by start(); all None until the module is imported.
        self._module = None
        self._plugin = None
        self._plugin_dir: Optional[Path] = None
        # Serialises invoke() calls — user plugins are not required to be
        # reentrant.
        self._lock = asyncio.Lock()

    async def _install_dependencies(self, manifest: Dict[str, Any]) -> None:
        """
        Concurrency caveat: two plugins with overlapping dep ranges can fight. 
        If plugin A needs httpx<0.28 and plugin B needs httpx>=0.28, the second 
        pip install downgrades or upgrades the shared site-packages, silently
        breaking the first. This is the strongest argument for eventually moving
        to per-plugin venvs (Part 2, Level 2). For now, keep plugin dep ranges
        non-overlapping and pin tightly.
        """
        deps: List[str] = list(manifest.get("dependencies") or [])
        post: List[List[str]] = list(manifest.get("post_install") or [])

        # Hash based idempotency: skip if we've already installed these deps
        # for this plugin directory. Delete this file (or bump the plugin
        # version) to force a re-install.
        marker = self._plugin_dir

        # inside _install_dependencies
        fingerprint = hashlib.sha256(
            json.dumps({"deps": deps, "post": post}, sort_keys=True).encode()
        ).hexdigest()

        stamp = None
        if marker is not None:
            stamp = marker / ".deps-installed"
            if stamp.exists() and stamp.read_text().strip() == fingerprint:
                logger.info("Deps already installed for %s — skipping", marker)
                return

        # Install deps first, then run post_install scripts. If post_install fails,
        # we don't write the fingerprint, so the next agent restart will retry.
        if deps:
            await self._pip_install(deps)

        for argv in post:
            argv = [sys.executable if a == "python" else a for a in argv]
            await self._run_streamed(argv, "post_install")

        # at the end, after post_install succeeds:
        if marker is not None and stamp is not None:
            stamp.write_text(fingerprint + "\n")
            logger.debug("Wrote dependency fingerprint to %s", stamp)

    async def _run_streamed(
        self, argv: List[str], label: str
    ) -> None:
        """Run a subprocess, streaming each output line to the logger."""
        logger.info("%s: starting (%s)", label, " ".join(argv))
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        lines: List[str] = []
        assert proc.stdout is not None

        async for raw in proc.stdout:
            text = raw.decode(errors="replace").rstrip()
            if not text:
                continue
            lines.append(text)
            logger.info("  %s: %s", label, text)

        rc = await proc.wait()
        if rc != 0:
            raise RuntimeError(
                f"{label} failed ({rc}):\n"
                + "\n".join(lines[-40:])
            )
        logger.info("%s: done", label)

    async def _pip_install(self, deps: List[str]) -> None:
        """
        Install packages into the agent's own interpreter.

        uv-created venvs do not contain pip, so `python -m pip` fails there.
        Prefer `uv pip install --python <sys.executable>` — that targets the
        venv the agent is actually running in, without needing pip inside it.
        Fall back to the classic pip invocation for non-uv environments.
        """
        uv_path = shutil.which("uv")
        if uv_path:
            argv = [
                uv_path, "pip", "install",
                "--python", sys.executable,
                *deps,
            ]
        else:
            argv = [
                sys.executable, "-m", "pip", "install",
                "--disable-pip-version-check",
                "--no-input",
                *deps,
            ]

        logger.info("Installing plugin deps: %s", deps)
        await self._run_streamed(argv, "pip")

    async def start(
        self, manifest: Dict[str, Any], artifact: Path, config: Dict[str, Any]
    ) -> None:
        """Install deps, import the entry module, and instantiate the plugin."""
        self._plugin_dir = artifact.parent
        logger.debug(
            "Starting python plugin %s from %s",
            manifest.get("id"), artifact,
        )

        # 1. install declared dependencies into the agent's interpreter
        await self._install_dependencies(manifest)

        # 2. locate + load the module
        runtime = manifest.get("runtime") or {}
        entry = runtime.get("entry_point") or "plugin.py"

        if artifact.suffix == ".py":
            plugin_path = artifact
        else:
            plugin_path = artifact.parent / entry
            if not plugin_path.exists():
                raise FileNotFoundError(
                    f"Python entry not found: {plugin_path}")

        module_name = f"pai_device_plugin_{manifest['id'].replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(module_name, plugin_path)

        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load {plugin_path}")

        self._module = importlib.util.module_from_spec(spec)
        # Register under sys.modules so pickling / introspection / relative
        # imports from within the plugin work.
        sys.modules[module_name] = self._module
        spec.loader.exec_module(self._module)

        if not hasattr(self._module, "create_plugin"):
            raise RuntimeError(
                f"{plugin_path} must expose create_plugin(config). "
                f"Found: {[n for n in dir(self._module) if not n.startswith('_')]}"
            )
        result = self._module.create_plugin(config)
        # Support both ``def create_plugin(...)`` and ``async def ...``.
        self._plugin = await result if hasattr(result, "__await__") else result
        logger.info("Python plugin initialised: %s", manifest.get("id"))

    async def invoke(
        self, capability: str, parameters: Dict[str, Any]
    ) -> Any:
        """Call ``plugin.invoke(capability, parameters)`` under the lock."""
        if self._plugin is None:
            raise RuntimeError("Plugin not started")
        async with self._lock:
            result = self._plugin.invoke(capability, parameters)
            # Support both sync and async invoke implementations.
            if hasattr(result, "__await__"):
                result = await result
            return result

    async def stop(self) -> None:
        """Call ``plugin.shutdown()`` (if defined) and drop references."""
        if self._plugin is not None and hasattr(self._plugin, "shutdown"):
            try:
                r = self._plugin.shutdown()
                if hasattr(r, "__await__"):
                    await r
            except Exception:
                logger.exception("python plugin shutdown failed")
        self._plugin = None
        self._module = None
        logger.debug("Python plugin stopped")


def pick_adapter(artifact: Path) -> PluginAdapter:
    """Choose a plugin adapter based on the artifact's file extension.

    * ``.so`` / ``.dylib`` / ``.dll`` → :class:`NativePluginAdapter`
    * ``.py``                          → :class:`PythonPluginAdapter`

    Raises :class:`RuntimeError` for any other extension.
    """
    suffix = artifact.suffix.lower()
    if suffix in (".so", ".dylib", ".dll"):
        return NativePluginAdapter()
    if suffix == ".py":
        return PythonPluginAdapter()
    raise RuntimeError(f"Unsupported plugin artifact: {artifact}")


# ============================================================================
# Installed plugin record
# ============================================================================

@dataclass
class InstalledPlugin:
    """A plugin that has been installed on this device.

    ``manifest`` is the full plugin manifest from the registry; ``dir`` is
    the on-disk install directory (``plugin_root / id / version``);
    ``artifact`` is the actual file (library or script) inside that dir;
    ``adapter`` is populated lazily on first :meth:`DevicePluginManager.start`.
    """
    id: str
    version: str
    dir: Path
    manifest: Dict[str, Any]
    artifact: Path
    sha256: Optional[str]
    adapter: Optional[PluginAdapter] = None
    capabilities: List[str] = field(default_factory=list)
    running: bool = False

    def __post_init__(self) -> None:
        # Capabilities in the manifest may be plain strings (newer format) or
        # dicts with an ``id``/``name`` (older format). Normalise to strings.
        if not self.capabilities:
            caps = self.manifest.get("capabilities") or []
            for c in caps:
                if isinstance(c, str):
                    self.capabilities.append(c)
                elif isinstance(c, dict):
                    cid = c.get("id") or c.get("name")
                    if cid:
                        self.capabilities.append(cid)


# ============================================================================
# Manager
# ============================================================================

class DevicePluginManager:
    """Owns the installed plugin set + dispatch by capability."""

    def __init__(self, plugin_root: Path) -> None:
        """Create a manager rooted at ``plugin_root``.

        The directory is created if it does not exist. ``_plugins`` is the
        authoritative in-memory registry; ``_cap_index`` is a fast lookup
        from capability name to owning plugin id.
        """
        self.plugin_root = plugin_root
        self.plugin_root.mkdir(parents=True, exist_ok=True)
        self._plugins: Dict[str, InstalledPlugin] = {}
        self._cap_index: Dict[str, str] = {}
        # Serialises install / stop / uninstall so they cannot interleave.
        self._lock = asyncio.Lock()

    # -------- introspection --------

    def has(self, plugin_id: str) -> bool:
        """True if a plugin with this id is installed (regardless of running state)."""
        return plugin_id in self._plugins

    def is_running(self, plugin_id: str) -> bool:
        """True if the plugin is installed *and* currently started."""
        p = self._plugins.get(plugin_id)
        return bool(p and p.running)

    def list_ids(self) -> List[str]:
        """Return the ids of every installed plugin."""
        return list(self._plugins.keys())

    def list_all(self) -> List[InstalledPlugin]:
        """Return the full ``InstalledPlugin`` records."""
        return list(self._plugins.values())

    def find_by_capability(self, capability: str) -> Optional[InstalledPlugin]:
        """Return the plugin that provides ``capability``, or None if none does."""
        pid = self._cap_index.get(capability)
        return self._plugins.get(pid) if pid else None

    # -------- lifecycle --------

    async def load_existing(self) -> None:
        """
        Rebuild in-memory state from disk on startup.

        Registers every on-disk plugin into the capability index, but
        does NOT start them. Startup policy is decided by the backend
        during the first _sync_once() — which knows what the user has
        enabled. This avoids the flap where load_existing() starts a
        plugin the sync loop then immediately stops (because
        enabled_on_device=False in the backend).
        """
        if not self.plugin_root.exists():
            return
        for plugin_dir in self.plugin_root.iterdir():
            if not plugin_dir.is_dir():
                continue
            for version_dir in plugin_dir.iterdir():
                manifest_path = version_dir / "manifest.json"
                if not manifest_path.exists():
                    continue
                try:
                    manifest = json.loads(manifest_path.read_text())
                    artifact = self._find_artifact(version_dir)
                    if artifact is None:
                        logger.warning("No artifact in %s", version_dir)
                        continue
                    plugin = InstalledPlugin(
                        id=manifest["id"],
                        version=manifest.get("version", version_dir.name),
                        dir=version_dir,
                        manifest=manifest,
                        artifact=artifact,
                        sha256=self._read_sha(version_dir),
                    )
                    self._register(plugin)
                    logger.info(
                        "Registered plugin from disk (not started): %s@%s",
                        plugin.id, plugin.version,
                    )
                except Exception:
                    logger.exception(
                        "Failed to load plugin at %s", version_dir)

    async def install(
        self,
        plugin_id: str,
        version: str,
        manifest: Dict[str, Any],
        artifact: Path,
        sha256: Optional[str],
    ) -> InstalledPlugin:
        """Move ``artifact`` into the plugin root and start the plugin.

        If a previous version is installed under the same id, it is stopped
        and forgotten first. The manifest and sha256 (when supplied) are
        persisted alongside the artifact so :meth:`load_existing` can
        rebuild state on restart.
        """
        async with self._lock:
            if plugin_id in self._plugins:
                logger.info(
                    "Replacing existing %s@%s with %s@%s",
                    plugin_id, self._plugins[plugin_id].version,
                    plugin_id, version,
                )
                await self._stop_locked(plugin_id)
                self._forget(plugin_id)

            # Move artifact into place
            target_dir = self.plugin_root / plugin_id / version
            target_dir.mkdir(parents=True, exist_ok=True)
            target_artifact = target_dir / artifact.name
            if artifact.resolve() != target_artifact.resolve():
                shutil.copy2(artifact, target_artifact)
                try:
                    artifact.unlink()
                except OSError:
                    # Non-fatal: leftover in _downloads will be overwritten.
                    logger.debug(
                        "Could not remove temp artifact %s", artifact
                    )

            (target_dir / "manifest.json").write_text(
                json.dumps(manifest, indent=2)
            )
            if sha256:
                (target_dir / "sha256.txt").write_text(sha256)

            plugin = InstalledPlugin(
                id=plugin_id,
                version=version,
                dir=target_dir,
                manifest=manifest,
                artifact=target_artifact,
                sha256=sha256,
            )
            self._register(plugin)
            logger.info("Installed %s@%s at %s", plugin_id, version, target_dir)

        # Start outside the lock so a slow adapter (dep install, etc.) does
        # not block other lifecycle operations.
        await self.start(plugin_id)
        return plugin

    async def start(self, plugin_id: str) -> None:
        """Start an installed plugin if it is not already running.

        The adapter is lazily instantiated on first start, so a plugin
        loaded from disk but never started pays the cost of adapter
        construction only when it is actually needed.
        """
        plugin = self._plugins.get(plugin_id)
        if plugin is None or plugin.running:
            return
        if plugin.adapter is None:
            plugin.adapter = pick_adapter(plugin.artifact)
        await plugin.adapter.start(
            plugin.manifest, plugin.artifact, config={}
        )
        plugin.running = True
        logger.info("Plugin started: %s@%s", plugin.id, plugin.version)

    async def stop(self, plugin_id: str) -> None:
        """Stop a running plugin. No-op if not installed / not running."""
        async with self._lock:
            await self._stop_locked(plugin_id)

    async def _stop_locked(self, plugin_id: str) -> None:
        """Implementation of :meth:`stop`, assuming the caller holds ``_lock``."""
        plugin = self._plugins.get(plugin_id)
        if plugin is None or not plugin.running:
            return
        if plugin.adapter is not None:
            try:
                await plugin.adapter.stop()
            except Exception:
                logger.exception("stop failed for %s", plugin_id)
        plugin.running = False
        logger.info("Plugin stopped: %s@%s", plugin.id, plugin.version)

    async def uninstall(self, plugin_id: str) -> None:
        """Stop the plugin (if running) and remove its files from disk."""
        async with self._lock:
            plugin = self._plugins.get(plugin_id)
            if plugin is None:
                return
            await self._stop_locked(plugin_id)
            try:
                shutil.rmtree(plugin.dir.parent, ignore_errors=True)
            except Exception:
                logger.exception("Failed to remove %s", plugin.dir.parent)
            self._forget(plugin_id)
            logger.info("Plugin uninstalled: %s", plugin_id)

    async def shutdown(self) -> None:
        """Stop every running plugin. Called once during agent teardown."""
        for pid in list(self._plugins.keys()):
            await self.stop(pid)
            logger.info("Plugin shutdown: %s", pid)

    # -------- invoke --------

    async def invoke(
        self, capability: str, parameters: Dict[str, Any]
    ) -> Any:
        """Dispatch ``capability`` to the plugin that provides it.

        Raises :class:`RuntimeError` if no plugin provides the capability,
        the plugin is not running, or its adapter is missing.
        """
        plugin = self.find_by_capability(capability)
        if plugin is None:
            raise RuntimeError(f"No plugin provides '{capability}'")
        if not plugin.running:
            raise RuntimeError(f"Plugin '{plugin.id}' is not running")
        if plugin.adapter is None:
            raise RuntimeError(f"Plugin '{plugin.id}' has no adapter")
        return await plugin.adapter.invoke(capability, parameters)

    # -------- internal --------

    def _register(self, plugin: InstalledPlugin) -> None:
        """Add ``plugin`` to the registry and capability index."""
        self._plugins[plugin.id] = plugin
        for cap in plugin.capabilities:
            self._cap_index[cap] = plugin.id
        logger.debug(
            "Registered plugin %s with capabilities %s",
            plugin.id, plugin.capabilities,
        )

    def _forget(self, plugin_id: str) -> None:
        """Remove ``plugin_id`` from the registry and prune its capabilities."""
        plugin = self._plugins.pop(plugin_id, None)
        if plugin is None:
            return
        for cap in plugin.capabilities:
            if self._cap_index.get(cap) == plugin_id:
                self._cap_index.pop(cap, None)
        logger.debug("Forgot plugin %s", plugin_id)

    def _find_artifact(self, version_dir: Path) -> Optional[Path]:
        """Return the first supported artifact file found under ``version_dir``."""
        for suffix in (".so", ".dylib", ".dll", ".py"):
            for p in version_dir.rglob(f"*{suffix}"):
                return p
        return None

    def _read_sha(self, version_dir: Path) -> Optional[str]:
        """Read the persisted ``sha256.txt`` under ``version_dir``, if present."""
        f = version_dir / "sha256.txt"
        return f.read_text().strip() if f.exists() else None