import asyncio
import socket
from contextlib import closing
from typing import Dict, Optional
import time

from loguru import logger

from zeroconf import (
    IPVersion,
    ServiceInfo,
    ServiceListener,
    Zeroconf,
    NonUniqueNameException,
)
from zeroconf.asyncio import (
    AsyncServiceBrowser,
    AsyncZeroconf,
)


class ExecutorDiscovery(ServiceListener):
    """
    Distributed executor discovery using mDNS/Zeroconf.

    Each executor:
      1. Registers itself using mDNS.
      2. Browses for other PAI executors.
      3. Registers discovered executors with the scheduler.
      4. Removes executors when they disappear.
    """

    SERVICE_TYPE = "_pai-executor._tcp.local."

    def __init__(
        self,
        kernel,
        executor_id: str,
        port: int = 8000,
        capabilities: Optional[str] = None,
    ):
        self.kernel = kernel
        self.executor_id = executor_id
        self.port = port

        self.capabilities = capabilities or ""

        # Async Zeroconf for asyncio/FastAPI applications.
        self.zeroconf = AsyncZeroconf(
            ip_version=IPVersion.V4Only
        )

        self.browser: Optional[
            AsyncServiceBrowser
        ] = None

        self.service_info: Optional[
            ServiceInfo
        ] = None

        self.executors: Dict[
            str,
            Dict,
        ] = {}

        self._lock = asyncio.Lock()

        self._started = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """
        Start mDNS/Zeroconf discovery.
        """

        if self._started:
            logger.warning(
                "Executor discovery is already running"
            )
            return

        logger.info(
            f"Starting executor discovery: "
            f"{self.executor_id}"
        )

        try:
            # Register this executor first.
            await self._register_self()

            # Start browsing for other executors.
            self.browser = AsyncServiceBrowser(
                self.zeroconf.zeroconf,
                self.SERVICE_TYPE,
                listener=self,
            )

            self._started = True

            logger.info(
                "Executor discovery started successfully"
            )

        except Exception:
            logger.exception(
                "Failed to start executor discovery"
            )

            # Clean up partially initialized resources.
            await self._cleanup()

            raise

    async def stop(self) -> None:
        """
        Stop mDNS/Zeroconf discovery.
        """

        if not self._started:
            # Still clean up in case start() failed partially.
            await self._cleanup()
            return

        logger.info(
            f"Stopping executor discovery: "
            f"{self.executor_id}"
        )

        await self._cleanup()

        self._started = False

        logger.info(
            "Executor discovery stopped"
        )

    async def _cleanup(self) -> None:
        """
        Clean up browser, registered service and Zeroconf.
        """

        # --------------------------------------------------------------
        # Stop service browser
        # --------------------------------------------------------------

        if self.browser is not None:
            try:
                await self.browser.async_cancel()

                logger.debug(
                    "Zeroconf service browser cancelled"
                )

            except Exception:
                logger.exception(
                    "Failed to cancel Zeroconf browser"
                )

            finally:
                self.browser = None

        # --------------------------------------------------------------
        # Unregister this executor
        # --------------------------------------------------------------

        if self.service_info is not None:
            try:
                await self.zeroconf.async_unregister_service(
                    self.service_info
                )

                logger.debug(
                    f"Unregistered executor: "
                    f"{self.executor_id}"
                )

            except Exception:
                logger.exception(
                    "Failed to unregister executor"
                )

            finally:
                self.service_info = None

        # --------------------------------------------------------------
        # Close Zeroconf
        # --------------------------------------------------------------

        try:
            await self.zeroconf.async_close()

            logger.debug(
                "Zeroconf closed"
            )

        except Exception:
            logger.exception(
                "Failed to close Zeroconf"
            )

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    async def _register_self(self) -> None:
        """
        Register this executor on the local network.
        """

        ip = self._get_local_ip()

        logger.debug(
            f"Local executor IP: {ip}"
        )

        # Try registering; if the chosen name is already in use via mDNS, retry
        base_id = self.executor_id
        attempt = 0
        max_attempts = 5
        while attempt < max_attempts:
            name = f"{self.executor_id}.{self.SERVICE_TYPE}"
            self.service_info = ServiceInfo(
                type_=self.SERVICE_TYPE,
                name=name,
                addresses=[socket.inet_aton(ip)],
                port=self.port,
                properties={
                    "executor_id": self.executor_id,
                    "type": "server",
                    "capabilities": self.capabilities,
                },
                server=(f"{self.executor_id}.local."),
            )

            try:
                await self.zeroconf.async_register_service(self.service_info)
                logger.info(f"Registered executor: {self.executor_id} at {ip}:{self.port}")
                return
            except NonUniqueNameException:
                # Choose a new executor id and retry
                attempt += 1
                suffix = f"{int(time.time())}_{attempt}"
                self.executor_id = f"{base_id}_{suffix}"
                logger.warning(f"Executor name conflict, retrying with id: {self.executor_id}")
            except Exception:
                logger.exception("Failed to register executor via zeroconf")
                raise

        logger.error("Could not register executor after multiple attempts; continuing without zeroconf registration")

    # ------------------------------------------------------------------
    # Service discovery callbacks
    # ------------------------------------------------------------------

    def add_service(
        self,
        zc: Zeroconf,
        service_type: str,
        name: str,
    ) -> None:
        """
        Called by Zeroconf when a new executor is discovered.

        This callback itself is synchronous, so we schedule the
        asynchronous processing on the running asyncio event loop.
        """

        asyncio.create_task(
            self._handle_add_service(
                service_type,
                name,
            )
        )

    async def _handle_add_service(
        self,
        service_type: str,
        name: str,
    ) -> None:
        """
        Resolve and process a newly discovered executor.
        """

        try:
            logger.debug(
                f"Resolving discovered service: {name}"
            )

            # IMPORTANT:
            # Use the async API rather than zc.get_service_info().
            info = await self.zeroconf.async_get_service_info(
                service_type,
                name,
            )

            if info is None:
                logger.debug(
                    f"Could not resolve service: {name}"
                )
                return

            # ----------------------------------------------------------
            # Extract executor ID
            # ----------------------------------------------------------

            executor_id = self._decode_property(
                info.properties,
                "executor_id",
            )

            if not executor_id:
                logger.warning(
                    f"Discovered service without "
                    f"executor_id: {name}"
                )
                return

            # Ignore ourselves.
            if executor_id == self.executor_id:
                return

            # ----------------------------------------------------------
            # Extract address
            # ----------------------------------------------------------

            if not info.addresses:
                logger.warning(
                    f"Executor has no IP address: "
                    f"{executor_id}"
                )
                return

            ip = socket.inet_ntoa(
                info.addresses[0]
            )

            # ----------------------------------------------------------
            # Extract metadata
            # ----------------------------------------------------------

            executor_type = self._decode_property(
                info.properties,
                "type",
                default="unknown",
            )

            capabilities = self._decode_property(
                info.properties,
                "capabilities",
                default="",
            )

            executor_data = {
                "id": executor_id,
                "address": (
                    f"{ip}:{info.port}"
                ),
                "type": executor_type,
                "capabilities": capabilities,
            }

            # ----------------------------------------------------------
            # Update local discovery registry
            # ----------------------------------------------------------

            async with self._lock:
                already_known = (
                    executor_id
                    in self.executors
                )

                self.executors[
                    executor_id
                ] = executor_data

            if already_known:
                logger.debug(
                    f"Executor already known, "
                    f"updated: {executor_id}"
                )
            else:
                logger.info(
                    f"Discovered executor: "
                    f"{executor_id} "
                    f"at {ip}:{info.port}"
                )

            # ----------------------------------------------------------
            # Register with scheduler
            # ----------------------------------------------------------

            await (
                self.kernel
                .executor_scheduler
                .register_executor(
                    executor_id,
                    executor_data,
                )
            )

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                f"Add service error: {name}"
            )

    # ------------------------------------------------------------------
    # Service removal
    # ------------------------------------------------------------------

    def remove_service(
        self,
        zc: Zeroconf,
        service_type: str,
        name: str,
    ) -> None:
        """
        Called by Zeroconf when an executor disappears.
        """

        asyncio.create_task(
            self._handle_remove_service(name)
        )

    async def _handle_remove_service(
        self,
        name: str,
    ) -> None:
        """
        Remove an executor from the local registry.
        """

        try:
            # Service names look like:
            #
            # executor-123._pai_executor._tcp.local.
            #
            executor_id = name.split(".")[0]

            # Don't accidentally remove ourselves.
            if executor_id == self.executor_id:
                return

            removed = False

            async with self._lock:
                if executor_id in self.executors:
                    del self.executors[
                        executor_id
                    ]
                    removed = True

            if not removed:
                logger.debug(
                    f"Executor was not in registry: "
                    f"{executor_id}"
                )
                return

            logger.info(
                f"Executor removed: "
                f"{executor_id}"
            )

            await (
                self.kernel
                .executor_scheduler
                .unregister_executor(
                    executor_id
                )
            )

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                f"Remove service error: {name}"
            )

    # ------------------------------------------------------------------
    # Service update
    # ------------------------------------------------------------------

    def update_service(
        self,
        zc: Zeroconf,
        service_type: str,
        name: str,
    ) -> None:
        """
        Called when a discovered service changes.

        Re-resolve the service asynchronously so that changed
        metadata/address information reaches the scheduler.
        """

        logger.debug(
            f"Service updated: {name}"
        )

        asyncio.create_task(
            self._handle_add_service(
                service_type,
                name,
            )
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_property(
        properties,
        key: str,
        default: str = "",
    ) -> str:
        """
        Safely decode a Zeroconf TXT property.

        Zeroconf properties are normally returned as:
            bytes -> bytes

        but handling str as well makes this more robust.
        """

        value = properties.get(
            key.encode(),
            properties.get(
                key,
                default,
            ),
        )

        if value is None:
            return default

        if isinstance(value, bytes):
            return value.decode(
                "utf-8",
                errors="replace",
            )

        return str(value)

    @staticmethod
    def _get_local_ip() -> str:
        """
        Get the best local IPv4 address.

        This does not send application data to 8.8.8.8;
        the UDP connect is used to let the OS select the
        appropriate local interface.
        """

        try:
            with closing(
                socket.socket(
                    socket.AF_INET,
                    socket.SOCK_DGRAM,
                )
            ) as sock:

                sock.connect(
                    ("8.8.8.8", 80)
                )

                ip = sock.getsockname()[0]

                if ip:
                    return ip

        except OSError as exc:
            logger.warning(
                f"Could not determine local IP: "
                f"{exc}"
            )

        return "127.0.0.1"

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    async def get_executors(self) -> Dict[str, Dict]:
        """
        Return a snapshot of discovered executors.
        """

        async with self._lock:
            return dict(self.executors)

    async def get_executor(
        self,
        executor_id: str,
    ) -> Optional[Dict]:
        """
        Return information about one executor.
        """

        async with self._lock:
            return self.executors.get(
                executor_id
            )