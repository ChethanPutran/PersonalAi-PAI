import asyncio
import socket
from contextlib import closing
from typing import Dict, Optional

from loguru import logger

from zeroconf import (
    IPVersion,
    ServiceBrowser,
    ServiceInfo,
    ServiceListener,
    Zeroconf,
)


class ExecutorDiscovery(ServiceListener):
    """
    Distributed executor discovery using mDNS/Zeroconf.
    """

    SERVICE_TYPE = "_pai_executor._tcp.local."

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

        self.capabilities = (
            capabilities or ""
        )

        self.zeroconf = Zeroconf(
            ip_version=IPVersion.V4Only
        )

        self.browser: Optional[
            ServiceBrowser
        ] = None

        self.service_info: Optional[
            ServiceInfo
        ] = None

        self.executors: Dict[
            str,
            Dict,
        ] = {}

        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """
        Start discovery service.
        """

        logger.info(
            "Starting executor discovery..."
        )

        await self._register_self()

        self.browser = ServiceBrowser(
            self.zeroconf,
            self.SERVICE_TYPE,
            self,
        )

        logger.info(
            "Executor discovery started"
        )

    async def stop(self) -> None:
        """
        Stop discovery service.
        """

        logger.info(
            "Stopping executor discovery..."
        )

        try:
            if self.service_info:
                self.zeroconf.unregister_service(
                    self.service_info
                )

            self.zeroconf.close()

        except Exception as e:
            logger.error(
                f"Discovery shutdown error: {e}"
            )

    async def _register_self(
        self,
    ) -> None:
        """
        Broadcast this executor.
        """

        ip = self._get_local_ip()

        self.service_info = ServiceInfo(
            type_=self.SERVICE_TYPE,
            name=(
                f"{self.executor_id}."
                f"{self.SERVICE_TYPE}"
            ),
            addresses=[
                socket.inet_aton(ip)
            ],
            port=self.port,
            properties={
                "executor_id": self.executor_id,
                "type": "server",
                "capabilities": self.capabilities,
            },
            server=f"{self.executor_id}.local.",
        )

        self.zeroconf.register_service(
            self.service_info
        )

        logger.info(
            f"Registered executor: "
            f"{self.executor_id} "
            f"at {ip}:{self.port}"
        )

    def add_service(
        self,
        zc: Zeroconf,
        service_type: str,
        name: str,
    ) -> None:
        """
        Called when executor discovered.
        """

        asyncio.create_task(
            self._handle_add_service(
                zc,
                service_type,
                name,
            )
        )

    async def _handle_add_service(
        self,
        zc: Zeroconf,
        service_type: str,
        name: str,
    ) -> None:
        try:
            info = zc.get_service_info(
                service_type,
                name,
            )

            if not info:
                return

            executor_id = (
                info.properties.get(
                    b"executor_id",
                    b"",
                )
                .decode()
            )

            if (
                not executor_id
                or executor_id
                == self.executor_id
            ):
                return

            ip = socket.inet_ntoa(
                info.addresses[0]
            )

            executor_data = {
                "id": executor_id,
                "address": (
                    f"{ip}:{info.port}"
                ),
                "type": (
                    info.properties.get(
                        b"type",
                        b"unknown",
                    ).decode()
                ),
                "capabilities": (
                    info.properties.get(
                        b"capabilities",
                        b"",
                    ).decode()
                ),
            }

            async with self._lock:
                self.executors[
                    executor_id
                ] = executor_data

            logger.info(
                f"Discovered executor: "
                f"{executor_id} "
                f"at {ip}:{info.port}"
            )

            await self.kernel.executor_scheduler.register_executor(
                executor_id,
                executor_data,
            )

        except Exception as e:
            logger.error(
                f"Add service error: {e}"
            )

    def remove_service(
        self,
        zc: Zeroconf,
        service_type: str,
        name: str,
    ) -> None:
        """
        Called when executor disappears.
        """

        asyncio.create_task(
            self._handle_remove_service(
                name
            )
        )

    async def _handle_remove_service(
        self,
        name: str,
    ) -> None:
        try:
            executor_id = (
                name.split(".")[0]
            )

            async with self._lock:
                if (
                    executor_id
                    in self.executors
                ):
                    del self.executors[
                        executor_id
                    ]

            logger.info(
                f"Executor removed: "
                f"{executor_id}"
            )

            await self.kernel.executor_scheduler.unregister_executor(
                executor_id
            )

        except Exception as e:
            logger.error(
                f"Remove service error: {e}"
            )

    def update_service(
        self,
        zc: Zeroconf,
        service_type: str,
        name: str,
    ) -> None:
        """
        Called when service updated.
        """

        logger.debug(
            f"Service updated: {name}"
        )

    @staticmethod
    def _get_local_ip() -> str:
        """
        Get best local interface IP.
        """

        try:
            with closing(
                socket.socket(
                    socket.AF_INET,
                    socket.SOCK_DGRAM,
                )
            ) as s:
                s.connect(
                    ("8.8.8.8", 80)
                )

                return s.getsockname()[0]

        except Exception:
            return "127.0.0.1"