import cv2
import asyncio
import numpy as np

from typing import (
    Dict,
    Any,
    List
)

from loguru import logger

from pai.plugins.base_plugin import (
    BasePlugin,
)


try:
    import mediapipe as mp

    from mediapipe.tasks.python import (
        vision,
    )

    from mediapipe.tasks.python import (
        BaseOptions,
    )

    MP_AVAILABLE = True

except ImportError:
    MP_AVAILABLE = False


class HealthPlugin(BasePlugin):
    """
    AI fitness / pose tracking plugin.
    """

    MODEL_PATH = (
        "models/pose_landmarker.task"
    )

    def __init__(self):
        super().__init__()

        self.name = "health"

        self.pose_detector = None

        self._reps = {
            "squat": 0,
            "pushup": 0,
            "pullup": 0,
        }

        self._stage = {}

    async def initialize(self) -> None:
        """
        Initialize MediaPipe Tasks API.
        """

        if not MP_AVAILABLE:
            logger.warning(
                "MediaPipe not installed"
            )
            return

        base_options = BaseOptions(
            model_asset_path=self.MODEL_PATH
        )

        options = (
            vision.PoseLandmarkerOptions(
                base_options=base_options,
                running_mode=vision.RunningMode.VIDEO,
                num_poses=1,
                min_pose_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
        )

        self.pose_detector = (
            vision.PoseLandmarker.create_from_options(
                options
            )
        )

        logger.info(
            "HealthPlugin initialized"
        )

    def get_capabilities(
        self,
    ) -> List[str]:
        return [
            "health.count_reps",
            "health.check_form",
            "health.track_activity",
            "health.get_daily_summary",
        ]

    async def check_permissions(
        self,
        action: str,
    ) -> bool:
        # For simplicity, allow all actions. Implement your own permission logic here.
        return True

    async def start(self) -> None:
        self._running = True
        # Implement any startup logic here

    async def execute(
        self,
        action: str,
        params: Dict[str, Any],
    ) -> Any:

        if (
            action
            == "health.count_reps"
        ):
            return await self._count_reps(
                params.get("image"),
                params.get(
                    "exercise_type",
                    "squat",
                ),
            )

        elif (
            action
            == "health.check_form"
        ):
            return await self._check_form(
                params.get("image"),
                params.get(
                    "exercise_type"
                ),
            )

        elif (
            action
            == "health.track_activity"
        ):
            return await self._track_activity(
                params.get(
                    "sensor_data",
                    {},
                )
            )

        elif (
            action
            == "health.get_daily_summary"
        ):
            return await self._get_summary(
                params.get("date")
            )

        raise ValueError(
            f"Unknown action: {action}"
        )

    async def _count_reps(
        self,
        image: np.ndarray,
        exercise: str,
    ) -> Dict[str, Any]:

        if not MP_AVAILABLE:
            return {
                "error":
                    "MediaPipe unavailable"
            }

        if image is None:
            return {
                "error":
                    "No image provided"
            }

        rgb = cv2.cvtColor(
            image,
            cv2.COLOR_BGR2RGB,
        )

        mp_image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=rgb,
        )

        timestamp = int(
            asyncio.get_event_loop()
            .time()
            * 1000
        )

        result = (
            self.pose_detector.detect_for_video(
                mp_image,
                timestamp,
            )
        )

        if not result.pose_landmarks:
            return {
                "detected": False,
                "reps": self._reps.get(
                    exercise,
                    0,
                ),
            }

        landmarks = (
            result.pose_landmarks[0]
        )

        angle = None

        if exercise == "squat":

            hip = landmarks[23]
            knee = landmarks[25]
            ankle = landmarks[27]

            angle = self._calculate_angle(
                hip,
                knee,
                ankle,
            )

            stage = self._stage.get(
                exercise
            )

            if (
                angle < 90
                and stage != "down"
            ):
                self._stage[
                    exercise
                ] = "down"

            elif (
                angle > 160
                and stage == "down"
            ):
                self._stage[
                    exercise
                ] = "up"

                self._reps[
                    exercise
                ] += 1

        return {
            "exercise": exercise,
            "reps": self._reps[
                exercise
            ],
            "angle": angle,
            "detected": True,
        }

    async def _check_form(
        self,
        image: np.ndarray,
        exercise: str,
    ) -> Dict[str, Any]:

        return {
            "score": 85,
            "suggestions": [
                "Keep back straight",
                "Lower hips more",
            ],
        }

    async def _track_activity(
        self,
        sensor_data: Dict,
    ) -> Dict:

        steps = sensor_data.get(
            "steps",
            0,
        )

        calories = steps * 0.04

        return {
            "steps": steps,
            "calories": calories,
            "active_minutes":
                steps / 100,
        }

    async def _get_summary(
        self,
        date: str,
    ) -> Dict:

        return {
            "date": date,
            "total_reps": sum(
                self._reps.values()
            ),
            "activities":
                self._reps,
        }

    def _calculate_angle(
        self,
        a,
        b,
        c,
    ) -> float:

        a = np.array([a.x, a.y])
        b = np.array([b.x, b.y])
        c = np.array([c.x, c.y])

        radians = (
            np.arctan2(
                c[1] - b[1],
                c[0] - b[0],
            )
            - np.arctan2(
                a[1] - b[1],
                a[0] - b[0],
            )
        )

        angle = np.abs(
            radians * 180.0 / np.pi
        )

        if angle > 180:
            angle = 360 - angle

        return angle

    async def shutdown(self) -> None:

        if self.pose_detector:
            self.pose_detector.close()

        logger.info(
            "HealthPlugin shutdown"
        )

    async def handle_event(
        self,
        event: str,
        data: Dict[str, Any],
    ) -> None:

        logger.info(
            f"Health plugin received event: {event} with data: {data}"
        )