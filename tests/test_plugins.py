import pytest
from pai.plugins.browser_plugin import BrowserPlugin
from pai.plugins.vision_plugin import VisionPlugin
from pai.plugins.calendar_plugin import CalendarPlugin
from pai.plugins.notification_plugin import NotificationPlugin

@pytest.mark.asyncio
async def test_browser_plugin():
    plugin = BrowserPlugin()
    await plugin.initialize()
    caps = plugin.get_capabilities()
    assert "browser.navigate" in caps
    # Mock navigate (no real browser in test)
    result = await plugin.execute("browser.navigate", {"url": "about:blank"})
    assert "url" in result
    await plugin.shutdown()

@pytest.mark.asyncio
async def test_vision_plugin():
    plugin = VisionPlugin()
    await plugin.initialize()
    # Use a dummy image (black 100x100)
    import numpy as np
    dummy_image = np.zeros((100,100,3), dtype=np.uint8)
    result = await plugin.execute("vision.detect_objects", {"image": dummy_image})
    assert isinstance(result, list)
    await plugin.shutdown()

@pytest.mark.asyncio
async def test_calendar_plugin():
    plugin = CalendarPlugin()
    await plugin.initialize()
    result = await plugin.execute("calendar.create_event", {"title": "Test", "start": "now", "end": "later"})
    assert "event_id" in result

@pytest.mark.asyncio
async def test_notification_plugin():
    plugin = NotificationPlugin()
    await plugin.initialize()
    result = await plugin.execute("notification.send", {"title": "Test", "message": "Hello"})
    assert result["sent"] is True


@pytest.mark.asyncio
async def test_user_plugin_tracking_persists_in_db(tmp_path):
    from pai.plugins.plugin_manager import PluginManager

    manager = PluginManager(db_path=str(tmp_path / "plugins.db"))

    result = await manager.record_user_plugin(
        user_id="user-123",
        plugin_name="browser",
        plugin_id="plugin-browser",
        plugin_type="action",
        is_enabled=True,
        is_loaded=True,
        metadata={"capabilities": ["browser.navigate"]},
    )

    assert result is not None
    assert result.plugin_name == "browser"

    persisted = await manager.get_user_plugins("user-123")
    assert any(item["plugin_name"] == "browser" for item in persisted)