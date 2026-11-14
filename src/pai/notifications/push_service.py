"""Firebase Cloud Messaging wrapper."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.notifications.push_models import PushTokenModel


logger = logging.getLogger(__name__)


class PushService:
    """Wraps firebase-admin. No-ops if unconfigured so the app still boots."""

    def __init__(self) -> None:
        self._ready = False
        self._app = None
        creds_path = os.environ.get("FIREBASE_CREDENTIALS")
        if not creds_path or not os.path.exists(creds_path):
            logger.warning(
                "FCM disabled: FIREBASE_CREDENTIALS not set or missing "
                "(path=%s). Push notifications will not be sent.",
                creds_path,
            )
            return
        try:
            import firebase_admin
            from firebase_admin import credentials

            cred = credentials.Certificate(creds_path)
            if not firebase_admin._apps:
                self._app = firebase_admin.initialize_app(cred)
            else:
                self._app = firebase_admin.get_app()
            self._ready = True
            logger.info("FCM initialized from %s", creds_path)
        except Exception:
            logger.exception("FCM init failed")

    @property
    def enabled(self) -> bool:
        return self._ready

    async def send(
        self,
        tokens: list[str],
        title: str,
        body: str,
        data: Optional[dict] = None,
        *,
        high_priority: bool = True,
    ) -> int:
        """Returns count of successfully delivered notifications."""
        if not self._ready or not tokens:
            return 0

        from firebase_admin import messaging

        android_cfg = messaging.AndroidConfig(
            priority="high" if high_priority else "normal",
            notification=messaging.AndroidNotification(
                channel_id="pai_reminders",
                sound="default",
            ),
        ) if high_priority else None

        apns_cfg = messaging.APNSConfig(
            headers={"apns-priority": "10" if high_priority else "5"},
            payload=messaging.APNSPayload(
                aps=messaging.Aps(sound="default", content_available=True),
            ),
        )

        message = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            tokens=tokens,
            android=android_cfg,
            apns=apns_cfg,
        )

        def _send():
            return messaging.send_each_for_multicast(message)

        try:
            resp = await asyncio.to_thread(_send)
            logger.info(
                "FCM: %d/%d delivered (title=%r)",
                resp.success_count, len(tokens), title,
            )
            return resp.success_count
        except Exception:
            logger.exception("FCM send failed")
            return 0


class PushTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self,
        *,
        device_id: str,
        user_id: str,
        token: str,
        platform: str,
    ) -> PushTokenModel:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)

        existing = (await self.session.execute(
            select(PushTokenModel).where(PushTokenModel.token == token)
        )).scalar_one_or_none()

        if existing is not None:
            existing.device_id = device_id
            existing.user_id = user_id
            existing.platform = platform
            existing.last_seen_at = now
            await self.session.flush()
            return existing

        row = PushTokenModel(
            device_id=device_id,
            user_id=user_id,
            token=token,
            platform=platform,
            created_at=now,
            last_seen_at=now,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_for_device(self, device_id: str) -> list[str]:
        result = await self.session.execute(
            select(PushTokenModel.token).where(
                PushTokenModel.device_id == device_id,
            )
        )
        return [r[0] for r in result.all()]

    async def delete_token(self, token: str) -> bool:
        result = await self.session.execute(
            delete(PushTokenModel).where(PushTokenModel.token == token)
        )
        return result.rowcount > 0