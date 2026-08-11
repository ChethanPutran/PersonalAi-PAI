import pytest
from pai.event_bus.event_bus import EventBus

@pytest.mark.asyncio
async def test_event_bus_pub_sub(event_bus):
    received = []

    async def handler(evt_type, data):
        received.append((evt_type, data))

    await event_bus.subscribe("test.event", handler)
    await event_bus.publish("test.event", {"key": "value"})
    # Allow worker to process
    import asyncio
    await asyncio.sleep(0.1)
    assert len(received) == 1
    assert received[0][1]["key"] == "value"

@pytest.mark.asyncio
async def test_event_bus_wildcard(event_bus):
    received = []

    async def wildcard_handler(evt_type, data):
        received.append(evt_type)

    await event_bus.subscribe("*", wildcard_handler)
    await event_bus.publish("any.event", {})
    await asyncio.sleep(0.1)
    assert "any.event" in received