from prometheus_client import Counter, Histogram, generate_latest, REGISTRY
from fastapi import Response
from fastapi import APIRouter

router = APIRouter()

REQUEST_COUNT = Counter("pai_requests_total", "Total requests", ["method", "endpoint"])
REQUEST_LATENCY = Histogram("pai_request_latency_seconds", "Request latency", ["method", "endpoint"])
GOAL_PROCESSING_TIME = Histogram("pai_goal_seconds", "Time to process a goal")
PLUGIN_EXECUTION_TIME = Histogram("pai_plugin_seconds", "Plugin execution time", ["plugin"])

@router.get("/metrics")
async def metrics():
    return Response(content=generate_latest(REGISTRY), media_type="text/plain")