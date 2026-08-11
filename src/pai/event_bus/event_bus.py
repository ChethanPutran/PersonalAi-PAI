"""Event Bus for asynchronous communication."""

import asyncio
from typing import Dict, List, Callable, Any, Awaitable, Optional
from collections import defaultdict
import logging

from pai.event_bus.event_types import EventType

import asyncio
import nats
from nats.aio.client import Client as NATSClient
from typing import Dict, Any, Callable, Awaitable


import asyncio
import json
import logging
from collections import defaultdict
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
)

import nats
from nats.aio.client import Client as NATSClient


class EventBus:
    """
    Hybrid local + distributed event bus.

    Features:
    - Local async event processing
    - Distributed NATS messaging
    - Wildcard subscriptions
    - Request-response support
    - Async worker architecture
    """

    def __init__(
        self,
        nats_url: str = "nats://localhost:4222",
    ):
        self.nats_url = nats_url
        self.nc: Optional[NATSClient] = None
        self._subscribers: Dict[
            str,
            List[
                Callable[
                    [str, Dict],
                    Awaitable[None],
                ]
            ],
        ] = defaultdict(list)
        self._event_queue: asyncio.Queue = (
            asyncio.Queue()
        )
        self._running = False
        self._worker_task: Optional[
            asyncio.Task
        ] = None

    async def initialize(self) -> None:
        """Initialize NATS connection."""

        self.nc = await nats.connect(
            self.nats_url
        )

        logging.info(
            f"Connected to NATS: {self.nats_url}"
        )

    async def start(self) -> None:
        """Start event processing."""

        self._running = True

        self._worker_task = (
            asyncio.create_task(
                self._process_events()
            )
        )

        await self._start_nats_listener()

        logging.info("EventBus started")

    async def stop(self) -> None:
        """Shutdown event bus."""

        self._running = False

        if self._worker_task:
            self._worker_task.cancel()

            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

        if self.nc:
            await self.nc.drain()

        logging.info("EventBus stopped")

    async def publish(
        self,
        event_type: str,
        data: Dict[str, Any],
        distributed: bool = True,
    ) -> None:
        """
        Publish event locally and optionally to NATS.
        """

        event = {
            "type": event_type,
            "data": data,
            "timestamp": (
                asyncio.get_event_loop().time()
            ),
        }

        # Local queue
        await self._event_queue.put(event)

        # Distributed publish
        if distributed and self.nc:
            await self.nc.publish(
                event_type,
                json.dumps(event).encode(),
            )

        logging.debug(
            f"Published event: {event_type}"
        )

    async def subscribe(
        self,
        event_type: str,
        handler: Callable[
            [str, Dict],
            Awaitable[None],
        ],
    ) -> None:
        """
        Subscribe local handler.
        """

        self._subscribers[event_type].append(
            handler
        )

        logging.debug(
            f"Subscribed: {event_type}"
        )

    async def unsubscribe(
        self,
        event_type: str,
        handler: Callable,
    ) -> None:
        """
        Remove handler subscription.
        """

        if event_type in self._subscribers:
            self._subscribers[event_type] = [
                h
                for h in self._subscribers[
                    event_type
                ]
                if h != handler
            ]

    async def _start_nats_listener(
        self,
    ) -> None:
        """
        Listen to distributed NATS events.
        """
        assert self.nc is not None, "NATS client not initialized"
        async def message_handler(msg):
            try:
                event = json.loads(
                    msg.data.decode()
                )

                await self._event_queue.put(
                    event
                )

            except Exception as e:
                logging.error(
                    f"NATS handler error: {e}"
                )

        await self.nc.subscribe(
            ">",
            cb=message_handler,
        )

    async def _process_events(
        self,
    ) -> None:
        """
        Background event dispatcher.
        """

        while self._running:
            try:
                event = (
                    await self._event_queue.get()
                )

                event_type = event["type"]
                data = event["data"]

                handlers = list(
                    self._subscribers.get(
                        event_type,
                        [],
                    )
                )

                # Wildcard handlers
                handlers.extend(
                    self._subscribers.get(
                        "*",
                        [],
                    )
                )

                if handlers:
                    results = (
                        await asyncio.gather(
                            *[
                                handler(
                                    event_type,
                                    data,
                                )
                                for handler in handlers
                            ],
                            return_exceptions=True,
                        )
                    )

                    for result in results:
                        if isinstance(
                            result,
                            Exception,
                        ):
                            logging.error(
                                f"Handler error: {result}"
                            )

                else:
                    logging.debug(
                        f"No handlers for {event_type}"
                    )

            except asyncio.CancelledError:
                break

            except Exception as e:
                logging.error(
                    f"Event processing error: {e}"
                )

    async def emit_and_wait(
        self,
        event_type: str,
        data: Dict[str, Any],
        timeout: float = 5.0,
    ) -> List[Any]:
        """
        Publish event and wait for responses.
        """

        response_queue: asyncio.Queue = (
            asyncio.Queue()
        )

        async def response_handler(
            evt_type: str,
            evt_data: Dict,
        ) -> None:
            await response_queue.put(
                evt_data
            )

        response_event = (
            f"{event_type}.response"
        )

        await self.subscribe(
            response_event,
            response_handler,
        )

        await self.publish(
            event_type,
            data,
        )

        responses = []

        try:
            while True:
                response = (
                    await asyncio.wait_for(
                        response_queue.get(),
                        timeout,
                    )
                )

                responses.append(response)

        except asyncio.TimeoutError:
            pass

        finally:
            await self.unsubscribe(
                response_event,
                response_handler,
            )

        return responses