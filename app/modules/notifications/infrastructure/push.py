"""Web Push with VAPID (pywebpush), off until the keys are configured."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pywebpush import WebPushException, webpush

from app.modules.notifications.domain.entities import PushSubscription

logger = logging.getLogger(__name__)


class WebPushSender:
    """Send one encrypted push message to a browser."""

    def __init__(self, public_key: str, private_key: str, subject: str) -> None:
        """Create the sender.

        Args:
            public_key (str): VAPID public key (URL-safe base64), the one browsers use.
            private_key (str): VAPID private key (URL-safe base64 or PEM).
            subject (str): ``mailto:`` contact the push services may reach.
        """

        self._public_key = public_key.strip() or None
        self._private_key = private_key.strip()
        self._subject = subject.strip()

    @property
    def public_key(self) -> str | None:
        """VAPID public key, or ``None`` when push is not configured."""

        if self._public_key and self._private_key and self._subject:
            return self._public_key
        return None

    async def send(self, subscription: PushSubscription, payload: dict[str, Any]) -> int | None:
        """Send a message to one browser.

        Args:
            subscription (PushSubscription): The browser.
            payload (dict[str, Any]): JSON the service worker receives.

        Returns:
            int | None: ``None`` when delivered, else the HTTP status of the push service
                (404 and 410 mean the browser dropped the subscription).
        """

        if self.public_key is None:
            return None
        return await asyncio.to_thread(self._send, subscription, json.dumps(payload))

    def _send(self, subscription: PushSubscription, data: str) -> int | None:
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                },
                data=data,
                vapid_private_key=self._private_key,
                vapid_claims={"sub": self._subject},
                timeout=10,
            )
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", 0) or 0
            if status not in (404, 410):
                logger.warning("Push to subscription %s failed: %s", subscription.id, status)
            return status
        except Exception:
            logger.warning("Push to subscription %s failed", subscription.id, exc_info=True)
            return 0
        return None
