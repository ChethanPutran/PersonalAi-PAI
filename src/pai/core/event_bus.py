"""
Event Bus - Distributed messaging system using NATS
"""
import logging
import json
from typing import Callable, Dict, List, Any, Optional
import asyncio
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class Event:
    """Event data structure"""
    id: str
    topic: str
    data: Dict[str, Any]
    timestamp: datetime
    source: str
    correlation_id: Optional[str] = None


class EventBus:
    """
    Central event bus for distributed messaging.
    
    Responsibilities:
    - Publish/subscribe messaging
    - Event routing
    - Distributed communication
    - Event replay
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        """Initialize event bus"""
        self.config = config or {}
        self.subscribers: Dict[str, List[Callable]] = {}
        self.event_history: List[Event] = []
        self.is_connected = False
        logger.info("Event Bus initialized")
    
    async def connect(self, nats_url: str = "nats://localhost:4222"):
        """Connect to NATS server"""
        try:
            # This would connect to actual NATS in production
            # For now, using in-memory pub/sub
            self.is_connected = True
            logger.info(f"Connected to event bus: {nats_url}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to event bus: {e}")
            return False
    
    async def disconnect(self):
        """Disconnect from event bus"""
        self.is_connected = False
        logger.info("Disconnected from event bus")
    
    async def subscribe(self, 
                       topic: str,
                       callback: Callable,
                       queue: Optional[str] = None):
        """Subscribe to topic"""
        if topic not in self.subscribers:
            self.subscribers[topic] = []
        
        self.subscribers[topic].append(callback)
        logger.info(f"Subscribed to topic: {topic}")
    
    async def unsubscribe(self, topic: str, callback: Callable):
        """Unsubscribe from topic"""
        if topic in self.subscribers and callback in self.subscribers[topic]:
            self.subscribers[topic].remove(callback)
            logger.info(f"Unsubscribed from topic: {topic}")
    
    async def publish(self,
                     topic: str,
                     data: Dict[str, Any],
                     source: str = "system",
                     correlation_id: Optional[str] = None):
        """Publish event to topic"""
        import uuid
        
        event = Event(
            id=str(uuid.uuid4()),
            topic=topic,
            data=data,
            timestamp=datetime.utcnow(),
            source=source,
            correlation_id=correlation_id
        )
        
        self.event_history.append(event)
        
        # Call all subscribers
        if topic in self.subscribers:
            tasks = []
            for callback in self.subscribers[topic]:
                if asyncio.iscoroutinefunction(callback):
                    tasks.append(callback(event))
                else:
                    callback(event)
            
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        
        # Wildcard subscribers
        wildcard_topic = topic.rsplit('/', 1)[0] + "/*"
        if wildcard_topic in self.subscribers:
            tasks = []
            for callback in self.subscribers[wildcard_topic]:
                if asyncio.iscoroutinefunction(callback):
                    tasks.append(callback(event))
                else:
                    callback(event)
            
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        
        logger.debug(f"Event published: {topic} - {event.id}")
    
    async def request_reply(self,
                           topic: str,
                           data: Dict[str, Any],
                           timeout: int = 5) -> Optional[Dict[str, Any]]:
        """Send request and wait for reply"""
        import uuid
        
        request_id = str(uuid.uuid4())
        reply_topic = f"{topic}.reply.{request_id}"
        response_future = asyncio.Future()
        
        async def reply_handler(event: Event):
            if not response_future.done():
                response_future.set_result(event.data)
        
        await self.subscribe(reply_topic, reply_handler)
        
        try:
            # Publish request
            data["request_id"] = request_id
            data["reply_to"] = reply_topic
            await self.publish(topic, data)
            
            # Wait for response
            response = await asyncio.wait_for(response_future, timeout=timeout)
            return response
        except asyncio.TimeoutError:
            logger.warning(f"Request timeout: {topic}")
            return None
        finally:
            await self.unsubscribe(reply_topic, reply_handler)
    
    def get_event_history(self, 
                         topic: Optional[str] = None,
                         limit: int = 100) -> List[Event]:
        """Get event history"""
        if topic:
            return [e for e in self.event_history[-limit:] if e.topic == topic]
        return self.event_history[-limit:]
    
    async def get_status(self) -> Dict[str, Any]:
        """Get event bus status"""
        return {
            "connected": self.is_connected,
            "topics": len(self.subscribers),
            "subscribers": sum(len(subs) for subs in self.subscribers.values()),
            "total_events": len(self.event_history)
        }
