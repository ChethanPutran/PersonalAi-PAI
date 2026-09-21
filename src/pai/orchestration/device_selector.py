"""
Device selection for task execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional


class DeviceSelectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeviceTarget:
    device_id: str
    executor_id: str
    device: Any


class DeviceSelector:

    def __init__(
        self,
        device_manager: Any,
        executor_manager: Any = None,
    ):
        self.device_manager = device_manager
        self.executor_manager = executor_manager


    def get_status(self) -> dict[str, Any]:
        return {
            "device_manager": str(self.device_manager),
            "executor_manager": str(self.executor_manager),
        }

    async def select(
        self,
        *,
        user_id: str,
        capability: str,
        task: Any = None,
        preferred_device: Optional[str] = None,
    ) -> DeviceTarget:

        task_dict = {}

        if task is not None:
            if hasattr(task, "to_dict"):
                task_dict = task.to_dict()
            elif isinstance(task, dict):
                task_dict = task

        # ---------------------------------------------------------
        # Determine requested device
        # ---------------------------------------------------------

        requested_device = (
            preferred_device
            or task_dict.get("preferred_device")
            or task_dict.get("device_id")
        )

        parameters = task_dict.get(
            "parameters",
            {},
        ) or {}

        metadata = task_dict.get(
            "metadata",
            {},
        ) or {}

        requested_device = (
            requested_device
            or parameters.get("device_id")
            or metadata.get("device_id")
        )

        # ---------------------------------------------------------
        # Get candidate devices
        # ---------------------------------------------------------

        devices = await self._get_devices(user_id)

        if not devices:
            raise DeviceSelectionError(
                "No devices are registered for this user"
            )

        # ---------------------------------------------------------
        # Explicit device
        # ---------------------------------------------------------

        if requested_device:
            for device in devices:
                device_id = self._device_id(device)

                if device_id != requested_device:
                    continue

                await self._validate_device(
                    user_id=user_id,
                    device=device,
                    capability=capability,
                )

                return self._target(device)

            raise DeviceSelectionError(
                f"Requested device '{requested_device}' "
                f"is unavailable or unauthorized"
            )

        # ---------------------------------------------------------
        # Automatic selection
        # ---------------------------------------------------------

        candidates = []

        for device in devices:

            try:
                self._check_online(device)

                if not self._supports(
                    device,
                    capability,
                ):
                    continue

                await self._validate_device(
                    user_id=user_id,
                    device=device,
                    capability=capability,
                )

                candidates.append(device)

            except Exception:
                continue

        if not candidates:
            raise DeviceSelectionError(
                f"No authorized online device supports "
                f"'{capability}'"
            )

        selected = self._rank(
            candidates,
            capability,
        )[0]

        return self._target(selected)

    async def _get_devices(
        self,
        user_id: str,
    ) -> list[Any]:

        method = getattr(
            self.device_manager,
            "get_user_devices",
            None,
        )

        if method:
            return await method(user_id)

        method = getattr(
            self.device_manager,
            "list_devices",
            None,
        )

        if method:
            result = await method(user_id)
            return list(result)

        registry = getattr(
            self.device_manager,
            "registry",
            None,
        )

        if registry:
            method = getattr(
                registry,
                "get_user_devices",
                None,
            )

            if method:
                return await method(user_id)

        raise DeviceSelectionError(
            "DeviceManager does not expose a device listing API"
        )

    async def _validate_device(
        self,
        *,
        user_id: str,
        device: Any,
        capability: str,
    ) -> None:

        self._check_online(device)

        if not self._supports(
            device,
            capability,
        ):
            raise DeviceSelectionError(
                f"Device '{self._device_id(device)}' "
                f"does not support '{capability}'"
            )

        method = getattr(
            self.device_manager,
            "is_authorized",
            None,
        )

        if method:
            allowed = await method(
                user_id,
                self._device_id(device),
            )

            if not allowed:
                raise DeviceSelectionError(
                    f"Device '{self._device_id(device)}' "
                    "is not authorized"
                )

    def _check_online(self, device: Any) -> None:

        status = getattr(
            device,
            "status",
            None,
        )

        if status is not None:
            status = str(status).lower()

            if status not in {
                "online",
                "connected",
                "ready",
                "available",
            }:
                raise DeviceSelectionError(
                    f"Device is not online: {status}"
                )

            return

        online = getattr(
            device,
            "online",
            getattr(device, "is_online", True),
        )

        if not online:
            raise DeviceSelectionError(
                "Device is offline"
            )

    def _supports(
        self,
        device: Any,
        capability: str,
    ) -> bool:

        capabilities = getattr(
            device,
            "capabilities",
            [],
        )

        if isinstance(capabilities, dict):
            capabilities = capabilities.keys()

        return capability in capabilities

    def _device_id(self, device: Any) -> str:
        return str(
            getattr(
                device,
                "id",
                getattr(device, "device_id", ""),
            )
        )

    def _executor_id(self, device: Any) -> str:
        return str(
            getattr(
                device,
                "executor_id",
                "",
            )
        )

    def _target(self, device: Any) -> DeviceTarget:
        return DeviceTarget(
            device_id=self._device_id(device),
            executor_id=self._executor_id(device),
            device=device,
        )

    def _rank(
        self,
        devices: Iterable[Any],
        capability: str,
    ) -> list[Any]:

        def score(device: Any) -> int:

            value = 0

            # Prefer devices with explicit executor.
            if self._executor_id(device):
                value += 10

            # Prefer ready/connected devices.
            status = str(
                getattr(
                    device,
                    "status",
                    "",
                )
            ).lower()

            if status in {"ready", "connected"}:
                value += 5

            return value

        return sorted(
            devices,
            key=score,
            reverse=True,
        )