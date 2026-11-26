"""Plain conversation skips the planner LLM call; anything that might need a tool does not."""
import asyncio
from types import SimpleNamespace

import pytest

from pai.orchestration.fast_route import FastRouter, TurnRoute, rule_route


@pytest.mark.parametrize("goal", [
    "Hi", "hello there", "Hi, how are you doing?", "Hey, how are you doing?", "thanks!", "Thank you so much",
    "what can you do for me?", "who are you", "good morning", "ok cool", "bye",
])
def test_small_talk_is_chat_by_rule_without_any_llm_call(goal):
    assert rule_route(goal) is TurnRoute.CHAT


@pytest.mark.parametrize("goal", [
    "Take my photo and save it to desktop", "Remind me to call Mom at 6pm", "Can you check my ip address",
    "open the browser", "set a timer for 5 minutes", "what's the weather today", "send an email to Sam",
    "play some music", "hi, take a screenshot", "hello, remind me to drink water", "turn off the wifi",
    "what is on my calendar", "search for pizza near me", "",
])
def test_anything_that_may_need_a_tool_goes_to_the_planner(goal):
    assert rule_route(goal) is TurnRoute.ACT


@pytest.mark.parametrize("goal", ["why is the sky blue", "tell me a joke about cats", "explain recursion simply"])
def test_unclear_cases_are_left_to_the_classifier(goal):
    assert rule_route(goal) is None


class FakeLLM:
    def __init__(self, answer="CHAT", delay=0.0, exc=None):
        self.answer, self.delay, self.exc, self.calls = answer, delay, exc, []

    async def generate_request(self, request, *, route=None):
        self.calls.append((request, route))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return SimpleNamespace(content=self.answer)


@pytest.mark.asyncio
async def test_rules_decide_without_calling_the_llm():
    llm = FakeLLM()
    router = FastRouter(llm)
    assert await router.decide("Hi, how are you?") == (TurnRoute.CHAT, "rule")
    assert await router.decide("take a photo") == (TurnRoute.ACT, "rule")
    assert llm.calls == []


@pytest.mark.asyncio
async def test_classifier_uses_the_fast_route_with_a_tiny_deterministic_call():
    llm = FakeLLM("CHAT")
    route, how = await FastRouter(llm).decide("why is the sky blue")
    assert (route, how) == (TurnRoute.CHAT, "llm")
    request, used_route = llm.calls[0]
    assert used_route == "chat" and request.max_tokens <= 32 and request.temperature == 0.0
    assert (await FastRouter(FakeLLM("ACT")).decide("why is the sky blue"))[0] is TurnRoute.ACT


@pytest.mark.asyncio
@pytest.mark.parametrize("llm", [FakeLLM(exc=RuntimeError("provider down")), FakeLLM(delay=1.0), FakeLLM("maybe?"),
                                 FakeLLM("")])
async def test_every_failure_or_unclear_answer_falls_back_to_the_planner(llm):
    route, _ = await FastRouter(llm, timeout_seconds=0.05).decide("why is the sky blue")
    assert route is TurnRoute.ACT


@pytest.mark.asyncio
async def test_disabled_or_missing_llm_means_the_full_pipeline():
    assert (await FastRouter(FakeLLM(), enabled=False).decide("hi"))[0] is TurnRoute.ACT
    assert (await FastRouter(None).decide("hi"))[0] is TurnRoute.ACT


def test_the_chat_only_plan_is_the_plan_the_planner_would_have_made():
    from pai.planning.goal_decomposer import CHAT_CAPABILITY_ID
    from pai.planning.models import PlanStatus
    from pai.planning.planner import Planner
    plan = Planner.chat_only_plan(SimpleNamespace(), "hi", {"session_id": "s"})
    assert [t.capability for t in plan.tasks] == [CHAT_CAPABILITY_ID] and plan.goal == "hi"
    assert plan.status is PlanStatus.VERIFIED and plan.metadata["planner"] == "fast_chat"
