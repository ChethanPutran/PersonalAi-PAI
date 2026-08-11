# Example: Using the Personal AI System via API

import asyncio
import websockets
import json

async def use_pai_system():
    # Connect to WebSocket
    uri = "ws://localhost:8000/ws"
    async with websockets.connect(uri) as websocket:
        
        # Send a goal
        await websocket.send(json.dumps({
            "type": "goal",
            "goal": "Find the latest AI research papers and summarize them",
            "context": {
                "topic": "Large Language Models",
                "max_papers": 5
            }
        }))
        
        # Receive results
        response = await websocket.recv()
        result = json.loads(response)
        print(f"Result: {result}")

# Example: Programmatic usage
async def programmatic_usage():
    from pai.kernel.ai_kernel import AIKernel
    
    kernel = AIKernel()
    await kernel.start()
    
    result = await kernel.process_goal(
        "Schedule a meeting with the team tomorrow at 10 AM",
        context={"user_id": "user123"}
    )
    
    print(f"Planning result: {result}")
    
    await kernel.stop()