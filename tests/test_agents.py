import pytest
from pai.agents.research_agent import ResearchAgent
from pai.agents.productivity_agent import ProductivityAgent
from pai.agents.communication_agent import CommunicationAgent
from pai.agents.travel_agent import TravelAgent
from pai.agents.health_agent import HealthAgent

@pytest.mark.asyncio
async def test_research_agent(kernel):
    agent = ResearchAgent(kernel)
    await agent.initialize()
    result = await agent.process_goal("search for AI news", {"timestamp": "now"})
    assert "query" in result or "error" not in result

@pytest.mark.asyncio
async def test_productivity_agent(kernel):
    agent = ProductivityAgent(kernel)
    await agent.initialize()
    result = await agent.process_goal("schedule a meeting", {"title": "test", "start": "2025-01-01T10:00"})
    assert "event_id" in result or "mock_id" in result

@pytest.mark.asyncio
async def test_communication_agent(kernel):
    agent = CommunicationAgent(kernel)
    await agent.initialize()
    result = await agent.process_goal("translate to French", {"text": "Hello", "target_lang": "fr"})
    assert "translated" in result

@pytest.mark.asyncio
async def test_travel_agent(kernel):
    agent = TravelAgent(kernel)
    await agent.initialize()
    result = await agent.process_goal("book a taxi", {"pickup": "A", "dropoff": "B"})
    assert "booking_id" in result or "ride_id" in result

@pytest.mark.asyncio
async def test_health_agent(kernel):
    agent = HealthAgent(kernel)
    await agent.initialize()
    result = await agent.process_goal("track exercise", {"exercise": "squat"})
    assert "reps" in result