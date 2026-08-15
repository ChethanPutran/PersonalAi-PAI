"""Plugin Manager for dynamic capability loading."""

import importlib
import importlib.util
import inspect
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Type

from loguru import logger
from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from pai.plugins.base_plugin import BasePlugin

Base = declarative_base()


class UserPluginRecord(Base):
    """Tracks which plugins are currently loaded and used by each user."""

    __tablename__ = "user_plugin_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, index=True, nullable=False)
    plugin_name = Column(String, index=True, nullable=False)
    plugin_id = Column(String, nullable=True)
    plugin_type = Column(String, nullable=True)
    is_enabled = Column(Boolean, default=True, nullable=False)
    is_loaded = Column(Boolean, default=True, nullable=False)
    plugin_metadata = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)
    

class FilePermissionRecord(Base):
    """Tracks file access permissions per user."""

    __tablename__ = "file_permissions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, index=True, nullable=False)
    path = Column(String, nullable=False)
    allowed = Column(Boolean, default=False, nullable=False)
    granted_by = Column(String, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    meta = Column('metadata', JSON, default=dict)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class FilePermissionRequest(Base):
    """Logs file access requests that may require user approval."""

    __tablename__ = "file_permission_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, index=True, nullable=False)
    path = Column(String, nullable=False)
    requester = Column(String, nullable=True)  # agent or system
    status = Column(String, default="pending")  # pending, approved, denied
    meta = Column('metadata', JSON, default=dict)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class UserPreference(Base):
    """Simple key/value preferences stored per user."""

    __tablename__ = 'user_preferences'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, index=True, nullable=False)
    key = Column(String, nullable=False)
    value = Column(JSON, default={})
    meta = Column('metadata', JSON, default={})
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class PluginManager:
    """
    Manages plugin lifecycle, discovery, and execution.
    
    Responsibilities:
    - Plugin installation/uninstallation
    - Dynamic loading
    - Permission management
    - Capability registry
    """
    
    def __init__(
        self,
        kernel=None,
        db_path: Optional[str] = None,
        enabled_plugins: Optional[List[str]] = None,
    ):
        self.kernel = kernel
        self._plugins: Dict[str, BasePlugin] = {}
        self._plugin_manifest: Dict[str, Dict[str, Any]] = {}
        self._capability_registry: Dict[str, str] = {}
        self._initialized = False

        # Only these plugins will be loaded.
        # None means load all plugins.
        self.enabled_plugins = (
            set(enabled_plugins) if enabled_plugins is not None else None
        )

        self.db_path = db_path or "./data/user_plugin_usage.db"
        self.db_path = str(Path(self.db_path))

        self._db_dir = Path(self.db_path).parent
        self._db_dir.mkdir(parents=True, exist_ok=True)

        self.engine = create_engine(f"sqlite:///{self.db_path}")

        Base.metadata.create_all(self.engine)

        self.SessionLocal = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
        )
        
    async def initialize(self) -> None:
        """Initialize the plugin manager by discovering and initializing plugins."""
        await self.discover_plugins()
        self._initialized = True
        logger.info(f"Plugin manager initialized with {len(self._plugins)} plugins")

    def _get_plugin_files(self) -> List[Path]:
        """Return plugin files in both the legacy root layout and per-plugin folder layout."""
        plugins_dir = Path(__file__).parent
        candidates: List[Path] = []
        seen_stems: set[str] = set()

        for plugin_dir in sorted(plugins_dir.iterdir(), key=lambda p: p.name):
            if not plugin_dir.is_dir() or plugin_dir.name.startswith("__"):
                continue
            for file_path in sorted(plugin_dir.glob("*.py"), key=lambda p: p.name):
                if file_path.name.startswith("__") or file_path.stem == "base_plugin":
                    continue
                if file_path.stem not in seen_stems:
                    seen_stems.add(file_path.stem)
                    candidates.append(file_path)

        for file_path in sorted(plugins_dir.glob("*_plugin.py"), key=lambda p: p.name):
            if file_path.stem == "base_plugin":
                continue
            if file_path.stem in seen_stems:
                continue
            candidates.append(file_path)

        return candidates

    async def discover_plugins(self) -> None:
        """Discover and load plugins, reading manifest.json for each."""
        plugins_dir = Path(__file__).parent

        for file_path in self._get_plugin_files():
            # Determine plugin key
            if file_path.parent != plugins_dir:
                plugin_key = file_path.parent.name.replace("-", "_")
            else:
                plugin_key = file_path.stem.replace("-", "_")

            if self.enabled_plugins is not None and plugin_key not in self.enabled_plugins:
                logger.debug(f"Skipping disabled plugin: {plugin_key}")
                continue

            module_name = f"pai.plugins.{file_path.stem}"
            if file_path.parent != plugins_dir:
                folder_name = file_path.parent.name.replace("-", "_")
                module_name = f"pai.plugins.{folder_name}.{file_path.stem}"

            # ---------- 1. Load manifest ----------
            manifest_data = {}
            manifest_path = file_path.parent / "manifest.json"
            if manifest_path.exists():
                try:
                    import json
                    with open(manifest_path, 'r') as f:
                        manifest_data = json.load(f)
                    logger.info(f"Loaded manifest for {manifest_data.get('name', file_path.stem)}")
                except Exception as e:
                    logger.warning(f"Failed to parse manifest for {file_path}: {e}")
            else:
                logger.warning(f"No manifest.json found for {file_path.parent}")

            # ---------- 2. Import module & instantiate ----------
            try:
                if file_path.parent != plugins_dir:
                    spec = importlib.util.spec_from_file_location(module_name, file_path)
                    if spec is None or spec.loader is None:
                        continue
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = module
                    spec.loader.exec_module(module)
                else:
                    module = importlib.import_module(module_name)

                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, BasePlugin) and obj != BasePlugin:
                        try:
                            plugin_instance = obj()
                            plugin_instance.kernel = self.kernel
                            await plugin_instance.initialize()
                            # Store plugin instance and manifest
                            self._plugins[plugin_instance.name] = plugin_instance
                            self._plugin_manifest[plugin_instance.name] = manifest_data
                            for capability in plugin_instance.get_capabilities():
                                self._capability_registry[capability] = plugin_instance.name
                            logger.info(f"Loaded plugin: {plugin_instance.name}")
                        except Exception as e:
                            logger.error(f"Failed to initialize plugin {name}: {e}")
            except Exception as e:
                logger.error(f"Failed to load plugin {module_name}: {e}")
    async def install_plugin(self, plugin_path: str) -> bool:
        """
        Install a new plugin dynamically.
        
        Args:
            plugin_path: Path to plugin module or package
            
        Returns:
            True if installation successful
        """
        # TODO: Implement plugin installation from package
        logger.info(f"Installing plugin from {plugin_path}")
        return False
    
    async def uninstall_plugin(self, plugin_name: str) -> bool:
        """
        Uninstall a plugin.
        
        Args:
            plugin_name: Name of plugin to uninstall
            
        Returns:
            True if uninstallation successful
        """
        if plugin_name not in self._plugins:
            logger.warning(f"Plugin {plugin_name} not found")
            return False
        
        plugin = self._plugins[plugin_name]
        await plugin.shutdown()
        
        # Remove capabilities
        for capability, p_name in list(self._capability_registry.items()):
            if p_name == plugin_name:
                del self._capability_registry[capability]
        
        del self._plugins[plugin_name]
        logger.info(f"Uninstalled plugin: {plugin_name}")
        return True
    async def set_user_plugin_enabled(self, user_id: str, plugin_name: str, enabled: bool) -> None:
        """Update or create a UserPluginRecord for this plugin."""
        session = self.SessionLocal()
        try:
            record = session.query(UserPluginRecord).filter_by(user_id=user_id, plugin_name=plugin_name).first()
            if record is None:
                record = UserPluginRecord(
                    user_id=user_id,
                    plugin_name=plugin_name,
                    is_enabled=enabled,
                    is_loaded=True,
                    plugin_metadata={}
                )
                session.add(record)
            else:
                record.is_enabled = enabled
                record.updated_at = datetime.now()
            session.commit()
            
            # Now also update the plugin runtime state
            plugin = self._plugins.get(plugin_name)
            if plugin is not None:
                if enabled:
                    # Optionally start the plugin if it's not running
                    if not plugin._running:
                        await plugin.start()  # implement start method in base plugin
                        plugin._running = True
                else:
                    # Optionally stop the plugin if it's running
                    if plugin._running:
                        await plugin.shutdown()  # or stop()
                        plugin._running = False
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

        

    async def record_user_plugin(
        self,
        user_id: str,
        plugin_name: str,
        plugin_id: Optional[str] = None,
        plugin_type: Optional[str] = None,
        is_enabled: bool = True,
        is_loaded: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[UserPluginRecord]:
        """Persist the plugin state for a specific user in the database."""
        if not user_id or not plugin_name:
            return None

        session = self.SessionLocal()
        try:
            record = (
                session.query(UserPluginRecord)
                .filter_by(user_id=user_id, plugin_name=plugin_name)
                .order_by(UserPluginRecord.updated_at.desc())
                .first()
            )

            if record is None:
                record = UserPluginRecord(
                    user_id=user_id,
                    plugin_name=plugin_name,
                    plugin_id=plugin_id,
                    plugin_type=plugin_type,
                    is_enabled=is_enabled,
                    is_loaded=is_loaded,
                    plugin_metadata=metadata or {},
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                session.add(record)
            else:
                record.plugin_id = plugin_id or record.plugin_id
                record.plugin_type = plugin_type or record.plugin_type
                record.is_enabled = is_enabled
                record.is_loaded = is_loaded
                record.plugin_metadata = metadata or record.plugin_metadata or {}
                record.updated_at = datetime.utcnow()

            session.commit()
            session.refresh(record)
            logger.info(f"Recorded user plugin usage: user={user_id}, plugin={plugin_name}")
            # If this plugin is loaded, attempt to apply the stored config immediately
            try:
                plugin_instance = self._plugins.get(plugin_name)
                if plugin_instance and metadata:
                    # call apply_config if plugin implements it
                    apply = getattr(plugin_instance, "apply_config", None)
                    if apply is not None:
                        # apply_config may be async
                        if inspect.iscoroutinefunction(apply):
                            try:
                                await apply(metadata)
                            except Exception as e:
                                logger.error(f"Error applying config to plugin {plugin_name}: {e}")
                        else:
                            try:
                                apply(metadata)
                            except Exception as e:
                                logger.error(f"Error applying config to plugin {plugin_name}: {e}")
            except Exception as exc:
                logger.error(f"Failed to apply plugin config for {user_id}/{plugin_name}: {exc}")
            return record
        except Exception as exc:
            session.rollback()
            logger.error(f"Failed to record user plugin usage for {user_id}/{plugin_name}: {exc}")
            return None
        finally:
            session.close()

    async def get_user_plugins(self, user_id: str) -> List[Dict[str, Any]]:
        """Return all tracked plugins for a user."""
        session = self.SessionLocal()
        try:
            records = (
                session.query(UserPluginRecord)
                .filter_by(user_id=user_id)
                .order_by(UserPluginRecord.updated_at.desc())
                .all()
            )
            return [
                {
                    "id": record.id,
                    "user_id": record.user_id,
                    "plugin_name": record.plugin_name,
                    "plugin_id": record.plugin_id,
                    "plugin_type": record.plugin_type,
                    "is_enabled": record.is_enabled,
                    "is_loaded": record.is_loaded,
                    "metadata": record.plugin_metadata or {},
                    "created_at": record.created_at.isoformat() if record.created_at else None,
                    "updated_at": record.updated_at.isoformat() if record.updated_at else None,
                }
                for record in records
            ]
        finally:
            session.close()

    # ---- File permissions ----
    async def grant_file_permission(
        self, user_id: str, path: str, granted_by: Optional[str] = None, expires_at: Optional[Any] = None, metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[FilePermissionRecord]:
        session = self.SessionLocal()
        try:
            rec = (
                session.query(FilePermissionRecord)
                .filter_by(user_id=user_id, path=path)
                .order_by(FilePermissionRecord.updated_at.desc())
                .first()
            )

            if rec is None:
                rec = FilePermissionRecord(
                    user_id=user_id,
                    path=path,
                    allowed=True,
                    granted_by=granted_by,
                    expires_at=expires_at,
                    meta=metadata or {},
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                session.add(rec)
            else:
                rec.allowed = True
                rec.granted_by = granted_by or rec.granted_by
                rec.expires_at = expires_at or rec.expires_at
                rec.meta = metadata or rec.meta or {}
                rec.updated_at = datetime.utcnow()

            session.commit()
            session.refresh(rec)
            return rec
        except Exception:
            session.rollback()
            return None
        finally:
            session.close()

    async def revoke_file_permission(self, user_id: str, path: str) -> bool:
        session = self.SessionLocal()
        try:
            rec = (
                session.query(FilePermissionRecord)
                .filter_by(user_id=user_id, path=path)
                .order_by(FilePermissionRecord.updated_at.desc())
                .first()
            )
            if rec is None:
                return False
            rec.allowed = False
            rec.updated_at = datetime.utcnow()
            session.commit()
            return True
        except Exception:
            session.rollback()
            return False
        finally:
            session.close()

    async def list_file_permissions(self, user_id: str) -> List[Dict[str, Any]]:
        session = self.SessionLocal()
        try:
            recs = (
                session.query(FilePermissionRecord)
                .filter_by(user_id=user_id)
                .order_by(FilePermissionRecord.updated_at.desc())
                .all()
            )
            return [
                {
                    'id': r.id,
                    'user_id': r.user_id,
                    'path': r.path,
                    'allowed': r.allowed,
                    'granted_by': r.granted_by,
                    'expires_at': r.expires_at.isoformat() if r.expires_at else None,
                    'metadata': r.meta or {},
                    'created_at': r.created_at.isoformat() if r.created_at else None,
                    'updated_at': r.updated_at.isoformat() if r.updated_at else None,
                }
                for r in recs
            ]
        finally:
            session.close()

    async def create_file_permission_request(self, user_id: str, path: str, requester: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> Optional[FilePermissionRequest]:
        session = self.SessionLocal()
        try:
            req = FilePermissionRequest(
                user_id=user_id,
                path=path,
                requester=requester,
                status='pending',
                meta=metadata or {},
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            session.add(req)
            session.commit()
            session.refresh(req)
            # publish event if kernel available
            if self.kernel and getattr(self.kernel, 'event_bus', None):
                try:
                    await self.kernel.event_bus.publish('file.access_request', {
                        'user_id': user_id,
                        'path': path,
                        'requester': requester,
                        'request_id': req.id,
                    })
                except Exception:
                    pass
            return req
        except Exception:
            session.rollback()
            return None
        finally:
            session.close()

    async def list_permission_requests(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        session = self.SessionLocal()
        try:
            q = session.query(FilePermissionRequest)
            if user_id:
                q = q.filter_by(user_id=user_id)
            recs = q.order_by(FilePermissionRequest.created_at.desc()).all()
            return [
                {
                    'id': r.id,
                    'user_id': r.user_id,
                    'path': r.path,
                    'requester': r.requester,
                    'status': r.status,
                    'metadata': r.meta or {},
                    'created_at': r.created_at.isoformat() if r.created_at else None,
                    'updated_at': r.updated_at.isoformat() if r.updated_at else None,
                }
                for r in recs
            ]
        finally:
            session.close()

    async def update_permission_request_status(self, request_id: int, status: str) -> bool:
        session = self.SessionLocal()
        try:
            rec = session.query(FilePermissionRequest).filter_by(id=request_id).first()
            if not rec:
                return False
            rec.status = status
            rec.updated_at = datetime.utcnow()
            session.commit()
            # publish event
            if self.kernel and getattr(self.kernel, 'event_bus', None):
                try:
                    await self.kernel.event_bus.publish('file.access_request.updated', {
                        'request_id': rec.id,
                        'status': status,
                        'user_id': rec.user_id,
                        'path': rec.path,
                    })
                except Exception:
                    pass
            return True
        except Exception:
            session.rollback()
            return False
        finally:
            session.close()

    # ---- User preferences ----
    async def set_user_preference(self, user_id: str, key: str, value: Any, metadata: Optional[Dict[str, Any]] = None) -> Optional[UserPreference]:
        session = self.SessionLocal()
        try:
            rec = (
                session.query(UserPreference)
                .filter_by(user_id=user_id, key=key)
                .order_by(UserPreference.updated_at.desc())
                .first()
            )
            if rec is None:
                rec = UserPreference(
                    user_id=user_id,
                    key=key,
                    value=value,
                    meta=metadata or {},
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                session.add(rec)
            else:
                rec.value = value
                rec.meta = metadata or rec.meta or {}
                rec.updated_at = datetime.utcnow()

            session.commit()
            session.refresh(rec)
            return rec
        except Exception:
            session.rollback()
            return None
        finally:
            session.close()

    async def get_user_preferences(self, user_id: str) -> Dict[str, Any]:
        session = self.SessionLocal()
        try:
            recs = (
                session.query(UserPreference)
                .filter_by(user_id=user_id)
                .order_by(UserPreference.updated_at.desc())
                .all()
            )
            return {r.key: r.value for r in recs}
        finally:
            session.close()

    # ---- Device registry ----
    async def register_device(self, user_id: str, device_id: str, device_name: Optional[str] = None, device_type: Optional[str] = None, platform: Optional[str] = None, capabilities: Optional[Dict[str, Any]] = None, config: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Register or update a device for a user."""
        if not user_id or not device_id:
            return None

        session = self.SessionLocal()
        try:
            rec = session.query(DeviceRecord).filter_by(id=device_id).first()
            if rec is None:
                rec = DeviceRecord(
                    id=device_id,
                    user_id=user_id,
                    device_name=device_name,
                    device_type=device_type,
                    platform=platform,
                    is_online=True,
                    last_heartbeat=datetime.utcnow(),
                    capabilities=capabilities or {},
                    config=config or {},
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                session.add(rec)
            else:
                rec.user_id = user_id
                rec.device_name = device_name or rec.device_name
                rec.device_type = device_type or rec.device_type
                rec.platform = platform or rec.platform
                rec.capabilities = capabilities or rec.capabilities or {}
                rec.config = config or rec.config or {}
                rec.is_online = True
                rec.last_heartbeat = datetime.utcnow()
                rec.updated_at = datetime.utcnow()

            session.commit()
            session.refresh(rec)

            # publish event
            if self.kernel and getattr(self.kernel, 'event_bus', None):
                try:
                    await self.kernel.event_bus.publish('device.registered', {'device_id': rec.id, 'user_id': rec.user_id, 'device_name': rec.device_name})
                except Exception:
                    pass

            return rec
        except Exception:
            session.rollback()
            return None
        finally:
            session.close()

    async def list_user_devices(self, user_id: str) -> List[Dict[str, Any]]:
        session = self.SessionLocal()
        try:
            recs = session.query(DeviceRecord).filter_by(user_id=user_id).order_by(DeviceRecord.updated_at.desc()).all()
            return [
                {
                    'id': r.id,
                    'user_id': r.user_id,
                    'device_name': r.device_name,
                    'device_type': r.device_type,
                    'platform': r.platform,
                    'is_online': r.is_online,
                    'last_heartbeat': r.last_heartbeat.isoformat() if r.last_heartbeat else None,
                    'capabilities': r.capabilities or {},
                    'config': r.config or {},
                    'created_at': r.created_at.isoformat() if r.created_at else None,
                    'updated_at': r.updated_at.isoformat() if r.updated_at else None,
                }
                for r in recs
            ]
        finally:
            session.close()

    async def update_device_heartbeat(self, device_id: str, is_online: bool = True) -> bool:
        session = self.SessionLocal()
        try:
            rec = session.query(DeviceRecord).filter_by(id=device_id).first()
            if not rec:
                return False
            rec.is_online = is_online
            rec.last_heartbeat = datetime.utcnow()
            rec.updated_at = datetime.utcnow()
            session.commit()

            if self.kernel and getattr(self.kernel, 'event_bus', None):
                try:
                    await self.kernel.event_bus.publish('device.updated', {'device_id': rec.id, 'user_id': rec.user_id, 'is_online': rec.is_online})
                except Exception:
                    pass

            return True
        except Exception:
            session.rollback()
            return False
        finally:
            session.close()

    async def get_device(self, device_id: str) -> Optional[Dict[str, Any]]:
        session = self.SessionLocal()
        try:
            rec = session.query(DeviceRecord).filter_by(id=device_id).first()
            if not rec:
                return None
            return {
                'id': rec.id,
                'user_id': rec.user_id,
                'device_name': rec.device_name,
                'device_type': rec.device_type,
                'platform': rec.platform,
                'is_online': rec.is_online,
                'last_heartbeat': rec.last_heartbeat.isoformat() if rec.last_heartbeat else None,
                'capabilities': rec.capabilities or {},
                'config': rec.config or {},
                'created_at': rec.created_at.isoformat() if rec.created_at else None,
                'updated_at': rec.updated_at.isoformat() if rec.updated_at else None,
            }
        finally:
            session.close()

    async def execute_plugin(self, plugin_name: str, action: str, params: Dict[str, Any]) -> Any:
        """
        Execute a plugin action.
        
        Args:
            plugin_name: Name of the plugin
            action: Action to execute
            params: Action parameters
            
        Returns:
            Action result
        """
        if plugin_name not in self._plugins:
            raise ValueError(f"Plugin {plugin_name} not found")
        
        plugin = self._plugins[plugin_name]
        
        # Check permissions
        if self.kernel:
            has_permission = await self.kernel.security_manager.check_permission(
                plugin_name, action
            )
            if not has_permission:
                raise PermissionError(f"No permission for {plugin_name}.{action}")

        user_id = None
        if getattr(self.kernel, "context_manager", None):
            user_id = getattr(self.kernel.context_manager, "user_id", None)

        result = await plugin.execute(action, params)

        if user_id:
            await self.record_user_plugin(
                user_id=user_id,
                plugin_name=plugin_name,
                plugin_id=getattr(plugin, "name", plugin_name),
                plugin_type=getattr(getattr(plugin, "__class__", None), "__name__", None),
                is_enabled=True,
                is_loaded=True,
                metadata={"action": action, "params": params},
            )

        return result
    
    async def get_capability(self, capability: str) -> Optional[str]:
        """
        Get plugin name for a capability.
        
        Args:
            capability: Requested capability
            
        Returns:
            Plugin name or None
        """
        return self._capability_registry.get(capability)
    
    async def list_plugins(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        user_enabled = {}
        if user_id:
            session = self.SessionLocal()
            try:
                records = session.query(UserPluginRecord).filter_by(user_id=user_id).all()
                user_enabled = {r.plugin_name: r.is_enabled for r in records}
            finally:
                session.close()

        plugin_list = []
        for name, plugin in self._plugins.items():
            manifest = self._plugin_manifest.get(name, {})
            is_enabled = user_enabled.get(name, plugin._running)
            plugin_list.append({
                "id": manifest.get("id", name),
                "name": manifest.get("name", name),
                "version": manifest.get("version", "1.0.0"),
                "type": manifest.get("plugin_type", manifest.get("type", "unknown")),
                "description": manifest.get("description", ""),
                "is_enabled": is_enabled,
                "capabilities": plugin.get_capabilities(),
                "running": plugin._running,
            })
        return plugin_list
    
    async def handle_plugin_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Route events to appropriate plugins."""
        for plugin in self._plugins.values():
            try:
                await plugin.handle_event(event_type, data)
            except Exception as e:
                logger.error(f"Error in plugin {plugin.name} handling event: {e}")
    
    async def shutdown(self) -> None:
        """Shutdown all plugins."""
        for plugin in self._plugins.values():
            try:
                await plugin.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down plugin {plugin.name}: {e}")
        
        self._plugins.clear()
        self._capability_registry.clear()
        logger.info("Plugin manager shutdown")

    async def get_plugin(self, plugin_name: str) -> Optional[BasePlugin]:
        """Get a plugin instance by name."""
        return self._plugins.get(plugin_name)

    async def get_plugins_by_capability(self, capability: str) -> List[BasePlugin]:
        """Get all plugins that provide a specific capability."""
        plugin_name = self._capability_registry.get(capability)
        if plugin_name:
            plugin = self._plugins.get(plugin_name)
            return [plugin] if plugin else []
        return []

    async def get_all_capabilities(self) -> Dict[str, str]:
        """Get a mapping of all capabilities to their providing plugins."""
        return self._capability_registry.copy()

    async def reload_plugin(self, plugin_name: str) -> bool:
        """
        Reload a plugin dynamically.
        
        Args:
            plugin_name: Name of the plugin to reload
            
        Returns:
            True if reload successful
        """
        if plugin_name not in self._plugins:
            logger.warning(f"Plugin {plugin_name} not found for reload")
            return False
        
        # Unload the existing plugin
        await self.uninstall_plugin(plugin_name)
        
        # Re-discover and load the plugin
        await self.discover_plugins()
        
        logger.info(f"Reloaded plugin: {plugin_name}")
        return True

    async def get_plugin_info(self, plugin_name: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific plugin."""
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            return None
        manifest = self._plugin_manifest.get(plugin_name, {})
        return {
            "name": plugin.name,
            "capabilities": plugin.get_capabilities(),
            "running": plugin._running,
            "class": plugin.__class__.__name__,
            "module": plugin.__class__.__module__,
            "id": manifest.get("id", plugin_name),
            "version": manifest.get("version", "1.0.0"),
            "type": manifest.get("plugin_type", manifest.get("type", "unknown")),
            "description": manifest.get("description", ""),
            "config_schema": manifest.get("config_schema", {}),
            "permissions": manifest.get("permissions", []),
            "dependencies": manifest.get("dependencies", []),
        }

    async def get_all_plugins_info(self) -> List[Dict[str, Any]]:
        """Get detailed information about all loaded plugins."""
        return [await self.get_plugin_info(plugin.name) for plugin in self._plugins.values()]


class DeviceRecord(Base):
    """Tracks devices registered by users."""

    __tablename__ = "registered_devices"

    id = Column(String, primary_key=True)
    user_id = Column(String, index=True, nullable=False)
    device_name = Column(String, nullable=True)
    device_type = Column(String, nullable=True)
    platform = Column(String, nullable=True)
    is_online = Column(Boolean, default=False, nullable=False)
    last_heartbeat = Column(DateTime, nullable=True)
    capabilities = Column(JSON, default={})
    config = Column(JSON, default={})
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)