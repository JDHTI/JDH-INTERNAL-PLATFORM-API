"""Opaque cursor encoding for stable client pagination."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json


class InvalidCursor(ValueError):
    """Raised when a cursor is malformed, stale, or belongs to another provider."""


class ClientCursorCodec:
    """Encode and validate versioned cursors for one data provider."""

    _VERSION = 1

    def __init__(self, secret: str, provider_name: str) -> None:
        self._secret = secret.encode()
        self._provider_name = provider_name.strip().lower()

    def encode(self, key: str) -> str:
        """Create a signed opaque cursor for the last returned client key.

        Args:
            key (str): Stable source key at the end of the current page.

        Returns:
            str: URL-safe cursor that can be sent as a query parameter.
        """

        payload = {"v": self._VERSION, "p": self._provider_name, "k": key}
        encoded = self._encode_payload(payload)
        return f"{encoded}.{self._signature(encoded)}"

    def decode(self, cursor: str) -> str:
        """Validate a cursor and return its source key.

        Args:
            cursor (str): Opaque cursor received from a client.

        Returns:
            str: Stable source key to use as an exclusive lower bound.

        Raises:
            InvalidCursor: If the cursor cannot be trusted or decoded.
        """

        try:
            encoded, signature = cursor.split(".", 1)
            if not encoded or not hmac.compare_digest(signature, self._signature(encoded)):
                raise InvalidCursor("Invalid cursor signature")
            padding = "=" * (-len(encoded) % 4)
            raw_payload = base64.urlsafe_b64decode(f"{encoded}{padding}").decode()
            payload = json.loads(raw_payload)
        except (AttributeError, ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise InvalidCursor("Invalid cursor") from exc

        if (
            not isinstance(payload, dict)
            or payload.get("v") != self._VERSION
            or payload.get("p") != self._provider_name
            or not isinstance(payload.get("k"), str)
            or not payload["k"]
        ):
            raise InvalidCursor("Cursor does not belong to this provider")
        return payload["k"]

    def _encode_payload(self, payload: dict[str, object]) -> str:
        raw_payload = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode()
        return base64.urlsafe_b64encode(raw_payload).rstrip(b"=").decode()

    def _signature(self, encoded: str) -> str:
        digest = hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
