from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Union,
)
from uuid import uuid4

import nats
from nats.aio.client import Client as NATSClient
from nats.errors import Error as NATSError

from pai.config.models.event_bus import EventBusConfig
from pai.event_bus.events import Event, EventResponse, EventType


logger = logging.getLogger(__name__)


EventHandler = Callable[
    [Event],
    Union[Awaitable[None], None],
]


@dataclass(slots=True)
class Subscription:
    """Represents a registered local event subscription."""

    subscription_id: str
    event_type: str
    handler: EventHandler


class EventBus:
    """
    Asynchronous application event bus.

    Architecture:

        Publisher
            |
            v
        EventBus
         /    \
        /      \
    Local      NATS
    queue      transport
        |          |
        +----------+
             |
             v
        Local handlers

    NATS is optional. If NATS is unavailable, the bus continues operating
    in local-only mode.

    Important:
    Events received from NATS are marked as remote and are NOT republished
    to NATS, preventing an event loop/duplicate delivery.
    """

    def __init__(
        self,config: EventBusConfig,
    ):
        self.config = config
        self.nc: Optional[NATSClient] = None

        self._event_queue: asyncio.Queue[Event] = asyncio.Queue(
            maxsize=config.max_queue_size
        )

        self._subscriptions: Dict[
            str,
            Dict[str, Subscription],
        ] = defaultdict(dict)

        self._worker_task: Optional[asyncio.Task] = None

        self._running = False
        self._initialized = False

        self._response_queues: Dict[
            str,
            asyncio.Queue[Event],
        ] = {}

        self._nats_subscription = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """
        Initialize the event bus.

        NATS is optional. Failure to connect does not fail the event bus.
        """

        if self._initialized:
            return

        self._initialized = True

        if not self.config.nats_enabled:
            logger.info("EventBus initialized with NATS disabled")
            return

        if not self.config.nats.url:
            logger.info("EventBus initialized in local-only mode")
            return

        try:
            self.nc = await asyncio.wait_for(
                nats.connect(
                    self.config.nats.url,
                    allow_reconnect=True,
                    max_reconnect_attempts=3,
                    connect_timeout=1,
                ),
                timeout=2.0,
            )

            logger.info(
                "EventBus connected to NATS: %s",
                self.config.nats.url,
            )

        except (
            NATSError,
            OSError,
            asyncio.TimeoutError,
        ) as exc:
            self.nc = None

            logger.warning(
                "NATS unavailable; using local-only EventBus: %s",
                exc,
            )

    async def start(self) -> None:
        """Start event processing."""

        if self._running:
            return

        if not self._initialized:
            await self.initialize()

        self._running = True

        self._worker_task = asyncio.create_task(
            self._process_events(),
            name="pai-event-bus-worker",
        )

        if self.nc:
            await self._start_nats_listener()

        logger.info("EventBus started")

    async def shutdown(self) -> None:
        """Gracefully stop the event bus."""

        if not self._running and not self._initialized:
            return

        self._running = False

        if self._worker_task:
            self._worker_task.cancel()

            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

            self._worker_task = None

        if self.nc:
            try:
                await self.nc.drain()
            except Exception:
                logger.exception("Failed to drain NATS connection")

            self.nc = None

        self._subscriptions.clear()
        self._response_queues.clear()

        logger.info("EventBus stopped")

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    async def publish(
        self,
        event_type: EventType,
        data: Optional[Dict[str, Any]] = None,
        *,
        source: Optional[str] = None,
        correlation_id: Optional[str] = None,
        distributed: bool = True,
    ) -> str:
        """
        Publish an event.

        The event is always processed locally.

        If NATS is enabled, it is additionally published to NATS.

        Returns:
            Event ID.
        """
        if not isinstance(event_type, EventType):
            # Try to coerce a plain string to a known EventType; if unknown,
            # allow it through so callers aren't required to enumerate every
            # event type in the enum before they can publish.
            try:
                event_type = EventType(event_type)
            except ValueError:
                pass  # Unknown/custom event type — publish as-is

        event = Event(
            event_type=event_type,
            data=data or {},
            source=source ,
            correlation_id=correlation_id,
        )

        await self._enqueue_local(event)

        if (
            distributed
            and self.nc is not None
        ):
            await self._publish_nats(event)

        logger.debug(
            "Published event %s (%s)",
            event.event_id,
            event.event_type,
        )

        return event.event_id

    async def _enqueue_local(self, event: Event) -> None:
        """Put an event into the local processing queue."""

        await self._event_queue.put(event)

    async def _publish_nats(self, event: Event) -> None:
        """Publish an event to NATS."""

        if self.nc is None:
            return

        try:
            payload = json.dumps(
                event.to_dict(),
                default=str,
            ).encode("utf-8")

            await self.nc.publish(
                event.event_type,
                payload,
            )

        except Exception:
            logger.exception(
                "Failed to publish event to NATS: %s",
                event.event_type,
            )

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------

    async def subscribe(
        self,
        event_type: EventType | str,
        handler: EventHandler,
    ) -> str:
        """
        Register an event handler.

        Supported patterns:
            task.created  -> exact match
            task.*        -> one-level hierarchical wildcard
            *             -> all events
        """

        pattern = (
            event_type.value
            if isinstance(event_type, EventType)
            else event_type
        )

        if not isinstance(pattern, str) or not pattern:
            raise ValueError("event_type must be a non-empty string")

        # Only allow '*' as a complete segment.
        if "*" in pattern and pattern != "*":
            if not pattern.endswith(".*") or pattern.count("*") != 1:
                raise ValueError(f"Invalid event subscription pattern: {pattern}")

        subscription_id = str(uuid4())

        self._subscriptions[pattern][subscription_id] = Subscription(
            subscription_id=subscription_id,
            event_type=pattern,
            handler=handler,
        )

        logger.debug("Subscribed %s to %s", subscription_id, pattern)

        return subscription_id

    async def unsubscribe(
        self,
        subscription_id: str,
    ) -> bool:
        """Remove a subscription by ID."""

        for event_type, subscriptions in self._subscriptions.items():

            if subscription_id in subscriptions:
                del subscriptions[subscription_id]

                if not subscriptions:
                    self._subscriptions.pop(
                        event_type,
                        None,
                    )

                logger.debug(
                    "Unsubscribed %s",
                    subscription_id,
                )

                return True

        return False

    # ------------------------------------------------------------------
    # Event processing
    # ------------------------------------------------------------------

    async def _process_events(self) -> None:
        """Background local event dispatcher."""

        while self._running:

            try:
                event = await self._event_queue.get()

                try:
                    await self._dispatch(event)
                finally:
                    self._event_queue.task_done()

            except asyncio.CancelledError:
                break

            except Exception:
                logger.exception(
                    "Unexpected EventBus processing error"
                )

    async def _dispatch(self, event: Event) -> None:
        """Dispatch one event to matching subscribers."""

        handlers = self._get_handlers(event.event_type)

        if not handlers:
            logger.debug(
                "No handlers registered for %s",
                event.event_type,
            )
            return

        results = await asyncio.gather(
            *[
                self._safe_call_handler(
                    subscription,
                    event,
                )
                for subscription in handlers
            ],
            return_exceptions=True,
        )

        for result in results:

            if isinstance(result, Exception):
                logger.error(
                    "Event handler failed: %s",
                    result,
                )

        # Request/response support.
        if event.correlation_id:
            response_queue = self._response_queues.get(
                event.correlation_id
            )

            if response_queue:
                await response_queue.put(event)

    def _get_handlers(
        self,
        event_type: str,
    ) -> List[Subscription]:
        """Return exact and wildcard matching handlers."""

        handlers: Dict[str, Subscription] = {}

        # Exact match.
        handlers.update(self._subscriptions.get(event_type, {}))

        # Global wildcard.
        handlers.update(self._subscriptions.get("*", {}))

        # Hierarchical wildcard.
        #
        # task.created       -> task.*
        # task.execution.completed -> task.execution.*
        parts = event_type.split(".")

        for i in range(1, len(parts)):
            wildcard = ".".join(parts[:i]) + ".*"

            handlers.update(
                self._subscriptions.get(wildcard, {})
            )

        return list(handlers.values())


    async def _safe_call_handler(
        self,
        subscription: Subscription,
        event: Event,
    ) -> None:
        """Execute a handler without allowing failures to kill dispatch."""

        try:

            result = subscription.handler(event)

            if inspect.isawaitable(result):
                await result

        except Exception:
            logger.exception(
                "Handler %s failed for event %s",
                subscription.subscription_id,
                event.event_type,
            )

    # ------------------------------------------------------------------
    # NATS
    # ------------------------------------------------------------------

    async def _start_nats_listener(self) -> None:
        """Subscribe to distributed events."""

        if self.nc is None:
            return

        async def message_handler(msg) -> None:
            try:
                payload = json.loads(
                    msg.data.decode("utf-8")
                )

                event = Event.from_dict(payload)

                # IMPORTANT:
                # Do not republish this event to NATS.
                #
                # It has already originated from another process.
                await self._enqueue_local(event)

            except Exception:
                logger.exception(
                    "Failed to process NATS event"
                )

        self._nats_subscription = await self.nc.subscribe(
            ">",
            cb=message_handler,
        )

        logger.info("NATS event listener started")

    # ------------------------------------------------------------------
    # Request / Response
    # ------------------------------------------------------------------

    async def emit_and_wait(
        self,
        event_type: EventType,
        data: Optional[Dict[str, Any]] = None,
        *,
        source: Optional[str] = None,
        timeout: float = 5.0,
        distributed: bool = True,
    ) -> List[Event]:
        """
        Publish an event and collect responses sharing its correlation ID.

        This is useful for things such as:

            execution.request
                -> executor
                -> execution.response

        The response handler should publish an event with the same
        correlation_id.
        """

        correlation_id = str(uuid4())

        response_queue: asyncio.Queue[Event] = asyncio.Queue()

        self._response_queues[correlation_id] = response_queue

        try:

            await self.publish(
                event_type,
                data,
                source=source,
                correlation_id=correlation_id,
                distributed=distributed,
            )

            responses: List[Event] = []

            while True:

                try:
                    response = await asyncio.wait_for(
                        response_queue.get(),
                        timeout=timeout,
                    )

                except asyncio.TimeoutError:
                    break

                responses.append(response)

                # Give other responders a chance to respond.
                await asyncio.sleep(0)

            return responses

        finally:
            self._response_queues.pop(
                correlation_id,
                None,
            )

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether the event bus is currently running."""

        return self._running

    @property
    def distributed(self) -> bool:
        """Whether distributed NATS transport is available."""

        return self.nc is not None