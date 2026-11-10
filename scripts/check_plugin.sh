# Check 1 — is the camera plugin in the catalog?
PYTHONPATH=src python - <<'PY'
import asyncio
from pai.plugins.manager import PluginManager
from pai.plugins.registry import PluginRegistry

async def main():
    pm = PluginManager(registry=PluginRegistry())
    await pm.initialize()
    for p in pm.get_catalog():
        print(f"{p.id:20} caps={p.capabilities}")

asyncio.run(main())
PY


# Check 2 — is the device ONLINE in the database?
PYTHONPATH=src python - <<'PY'
import asyncio
from sqlalchemy import select
from pai.config import config
from pai.storage.database import Database
from pai.storage.models import DeviceModel

DEVICE_ID = "63c755b3-1aca-499e-b014-5ed7d97e2309"

async def main():
    db = Database(config.database.url)
    async with db.session() as s:
        d = (await s.execute(
            select(DeviceModel).where(DeviceModel.id == DEVICE_ID)
        )).scalar_one_or_none()
        if not d:
            print("DEVICE NOT FOUND"); return
        print(f"status:    {d.status}")
        print(f"last_seen: {d.last_seen}")
        print(f"user_id:   {d.user_id}")
    await db.close()

asyncio.run(main())
PY

# Check 3 — is camera installed AND enabled on that device?
PYTHONPATH=src python - <<'PY'
import asyncio
from sqlalchemy import select
from pai.config import config
from pai.storage.database import Database
from pai.storage.models import DevicePluginModel

DEVICE_ID = "63c755b3-1aca-499e-b014-5ed7d97e2309"

async def main():
    db = Database(config.database.url)
    async with db.session() as s:
        rows = (await s.execute(
            select(DevicePluginModel).where(
                DevicePluginModel.device_id == DEVICE_ID
            )
        )).scalars().all()
        if not rows:
            print("NO device_plugins rows for this device"); return
        for r in rows:
            print(f"{r.plugin_id:20} installed={r.is_installed} enabled={r.enabled_on_device}")
    await db.close()

asyncio.run(main())
PY


# Check 4 — is the router wired to the plugin manager?
PYTHONPATH=src python - <<'PY'
import asyncio
from pai.app_context import create_app_context
from pai.config import config

async def main():
    ctx = create_app_context(config)
    pm = ctx.orchestrator.plugin_manager
    dr = ctx.orchestrator.device_manager
    cr = ctx.orchestrator.capability_router

    print("plugin_manager._capability_router  =", pm._capability_router is not None)
    print("device_manager._capability_router  =", dr._capability_router is not None)
    print("router cache keys:                 =", list(cr._cache.keys()))

asyncio.run(main())
PY