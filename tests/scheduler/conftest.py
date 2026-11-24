import pytest
import pytest_asyncio

from pai.config.models.database import DatabaseConfig
from pai.storage.database import Database

from pai.scheduler.scheduler_service import SchedulerService
from pai.scheduler.validation import ScheduleValidator
from support import FakeDevices, FakeRouter, FakeTaskManager, make_security


@pytest.fixture
def security(monkeypatch):
    return make_security(monkeypatch)


@pytest.fixture
def router():
    return FakeRouter()


@pytest.fixture
def devices():
    return FakeDevices()


@pytest.fixture
def validator(router, devices, security):
    return ScheduleValidator(capability_router=router, device_manager=devices, security_manager=security)


@pytest.fixture
def tm():
    return FakeTaskManager()


@pytest.fixture
def scheduler(session_factory, tm, devices, validator, security):
    return SchedulerService(
        session_factory=session_factory, task_manager=tm, executor=None, device_manager=devices,
        validator=validator, audit_logger=security.audit_logger, device_executor_name="device_executor")


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    """A real file-backed SQLite through the real Database class (WAL, one connection per session).

    The shared in-memory StaticPool of the global fixture funnels every session through ONE
    connection, so one session closing rolls back another's uncommitted work — which is not how
    production behaves, and the scheduler really does run a background session next to the tick's.
    """
    db = Database(DatabaseConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'sched.db'}"))
    await db.initialize()
    yield db.sessionmaker
    await db.engine.dispose()
